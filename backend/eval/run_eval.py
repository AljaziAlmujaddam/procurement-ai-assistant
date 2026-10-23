"""Evaluate the agent's accuracy and efficiency against reference answers computed in MongoDB.

Run from backend/:
    .venv/bin/python -m eval.run_eval                          # all cases, writes eval/report.md
    .venv/bin/python -m eval.run_eval --only top_items,off_topic
    .venv/bin/python -m eval.run_eval --pause 45               # stay under a tokens-per-minute limit

Each case runs the full LangGraph agent (plan -> query -> self-correct -> answer -> follow-ups).
Accuracy compares the rows the agent's own pipeline returned with the reference rows, which is
the "execution accuracy" used for text-to-SQL benchmarks. Efficiency is measured per question:
wall-clock latency per graph node, number of query attempts, and LLM tokens.
"""

import argparse
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import build_graph, new_turn, to_jsonable
from app.config import get_settings
from app.db import get_collection
from eval.cases import CASES, Case
from eval.scoring import Score, score_change, score_no_data, score_ranking, score_scalar, score_series

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
SAMPLE_ROWS = 10
DAILY_LIMIT = re.compile(r"tokens per day.*?try again in ([\d.hm]+s)", re.S)


def run_agent(graph, question: str, history: list) -> dict:
    """Run one turn, timing each graph node from the gaps between streamed updates."""
    state = new_turn(question, history)
    timings: dict[str, float] = defaultdict(float)
    start = last = time.perf_counter()
    answer_at = None

    with get_usage_metadata_callback() as usage:
        for update in graph.stream(state, stream_mode="updates"):
            now = time.perf_counter()
            for node, values in update.items():
                timings[node] += now - last
                state.update({k: v for k, v in (values or {}).items() if k != "messages"})
                if node == "respond":
                    answer_at = now - start
            last = now

    tokens = Counter()
    for model_usage in usage.usage_metadata.values():
        tokens.update({k: model_usage.get(k, 0) for k in ("input_tokens", "output_tokens", "total_tokens")})

    plan = state.get("plan") or {}
    return {
        "needs_query": plan.get("needs_query"),
        "reasoning": plan.get("reasoning", ""),
        "pipeline": plan.get("pipeline", ""),
        "results": state.get("results"),
        "attempts": state.get("attempts", 0),
        "error": state.get("error"),
        "answer": state.get("answer", ""),
        "history": [*history, HumanMessage(question), AIMessage(state.get("answer", ""), additional_kwargs={"pipeline": plan.get("pipeline", "")})],
        "timing": {
            "answer_s": round(answer_at or last - start, 2),
            "total_s": round(last - start, 2),
            "planning_s": round(timings["generate_query"], 2),
            "query_s": round(timings["execute_query"], 3),
            "respond_s": round(timings["respond"], 2),
            "followups_s": round(timings["suggest_followups"], 2),
        },
        "tokens": dict(tokens),
    }


def score_case(case: Case, expected: list, agent: dict, docs_before: int) -> Score:
    score = score_result(case, expected, agent, docs_before)
    answer = agent["answer"].replace("\u2019", "'")
    if case.answer_must_match and not re.search(case.answer_must_match, answer):
        return Score(False, f"{score.detail}; answer does not state the limitation")
    if case.answer_must_not_match and (bad := re.search(case.answer_must_not_match, answer)):
        return Score(False, f"{score.detail}; answer contains invented content ({bad.group(0)!r})")
    if case.answer_must_match or case.answer_must_not_match:
        return Score(score.passed, f"{score.detail}; answer states the limitation, nothing invented")
    return score


def score_result(case: Case, expected: list, agent: dict, docs_before: int) -> Score:
    if case.check == "no_query":
        ok = agent["needs_query"] is False
        return Score(ok, "answered without querying" if ok else "agent ran a database query")
    if case.check == "read_only":
        docs_after = get_collection().estimated_document_count()
        ok = docs_after == docs_before
        how = "declined" if agent["needs_query"] is False else "ran a read-only query"
        return Score(ok, f"collection unchanged ({docs_after:,} documents), agent {how}" if ok else "DATA CHANGED")
    if case.check == "no_data":
        return score_no_data(agent["results"], agent["answer"])

    rows = agent["results"]
    if not rows:
        return Score(False, f"no results (error: {agent['error'] or 'empty result'})")
    if case.check == "scalar":
        return score_scalar(expected, rows, case.percent, case.abs_tol)
    if case.check == "series":
        return score_series(expected, rows, case.abs_tol)
    if case.check == "change":
        return score_change(expected, rows, agent["answer"])
    return score_ranking(expected, rows, case.top_k)


def evaluate(cases: list[Case], pause: float) -> list[dict]:
    graph = build_graph()
    collection = get_collection()
    records = []
    calls = 0

    def agent_call(question: str, history: list) -> dict:
        nonlocal calls
        if calls and pause:
            time.sleep(pause)
        calls += 1
        return run_agent(graph, question, history)

    # Once the provider's daily token quota is used up, every further call fails the same way.
    out_of_quota: Score | None = None
    for i, case in enumerate(cases, 1):
        expected = to_jsonable(list(collection.aggregate(case.reference, allowDiskUse=True))) if case.reference else []
        docs_before = collection.estimated_document_count()
        agent = None
        if out_of_quota:
            score = out_of_quota
        else:
            try:
                history: list = []
                for earlier in case.history:
                    history = agent_call(earlier, history)["history"]
                agent = agent_call(case.question, history)
                score = score_case(case, expected, agent, docs_before)
            except Exception as exc:  # provider errors (e.g. quota exhausted) mean the case did not run
                score = Score(False, f"not run: {type(exc).__name__}: {str(exc)[:200]}")
                if wait := DAILY_LIMIT.search(str(exc)):
                    out_of_quota = score = Score(False, f"not run: daily token limit reached, try again in {wait.group(1)}")

        record = {
            "id": case.id,
            "category": case.category,
            "question": case.question,
            "history": list(case.history),
            "check": case.check,
            "status": ("pass" if score.passed else "fail") if agent else "error",
            "passed": score.passed,
            "detail": score.detail,
            "expected": expected[:SAMPLE_ROWS],
        }
        if agent:
            agent.pop("history")
            agent["row_count"] = len(agent["results"] or [])
            agent["results"] = (agent["results"] or [])[:SAMPLE_ROWS]
            record["agent"] = agent
        records.append(record)

        extra = (
            f"{agent['timing']['answer_s']:.1f}s, {agent['attempts']} attempt(s), "
            f"{agent['tokens'].get('total_tokens', 0):,} tokens"
            if agent
            else "not run"
        )
        print(f"[{i:>2}/{len(cases)}] {record['status'].upper():<5} {case.id:<38} {extra} | {score.detail}", flush=True)

    return records


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def summarize(records: list[dict]) -> dict:
    ran = [r for r in records if r["status"] != "error"]
    queried = [r for r in ran if r["agent"]["needs_query"]]
    answer_times = [r["agent"]["timing"]["answer_s"] for r in ran]
    by_category: dict[str, list[bool]] = defaultdict(list)
    for r in ran:
        by_category[r["category"]].append(r["passed"])

    passed = sum(r["passed"] for r in ran)
    return {
        "cases": len(records),
        "ran": len(ran),
        "passed": passed,
        "accuracy": passed / len(ran) if ran else 0.0,
        "by_category": {c: {"passed": sum(v), "total": len(v)} for c, v in by_category.items()},
        "answer_s_median": statistics.median(answer_times) if answer_times else None,
        "answer_s_p95": percentile(answer_times, 95) if answer_times else None,
        "answer_s_mean": statistics.mean(answer_times) if answer_times else None,
        "total_s_mean": statistics.mean(r["agent"]["timing"]["total_s"] for r in ran) if ran else None,
        "query_ms_mean": statistics.mean(r["agent"]["timing"]["query_s"] for r in queried) * 1000 if queried else None,
        "tokens_mean": statistics.mean(r["agent"]["tokens"].get("total_tokens", 0) for r in ran) if ran else None,
        "first_attempt_rate": (sum(r["agent"]["attempts"] == 1 for r in queried) / len(queried)) if queried else None,
        "self_corrected": sum(r["agent"]["attempts"] > 1 and r["passed"] for r in queried),
        "not_run": len(records) - len(ran),
    }


def render_report(records: list[dict], summary: dict, meta: dict) -> str:
    def secs(v):
        return "–" if v is None else f"{v:.1f} s"

    lines = [
        "# Evaluation report",
        "",
        f"- Run: {meta['run_at']}",
        f"- Model: `{meta['model']}`",
        f"- Cases: {summary['cases']} (generated by `python -m eval.run_eval`)",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Accuracy | **{summary['passed']}/{summary['ran']} ({summary['accuracy']:.0%})** |",
        *([f"| Not run (LLM provider error) | {summary['not_run']} |"] if summary["not_run"] else []),
        f"| Time to answer (median / p95) | {secs(summary['answer_s_median'])} / {secs(summary['answer_s_p95'])} |",
        f"| Time incl. follow-up suggestions (mean) | {secs(summary['total_s_mean'])} |",
        f"| MongoDB query time (mean) | {summary['query_ms_mean']:.0f} ms |" if summary["query_ms_mean"] is not None else "| MongoDB query time | – |",
        f"| LLM tokens per question (mean) | {summary['tokens_mean']:,.0f} |" if summary["tokens_mean"] is not None else "| LLM tokens | – |",
        f"| Valid query on first attempt | {summary['first_attempt_rate']:.0%} |" if summary["first_attempt_rate"] is not None else "| First attempt | – |",
        f"| Passed after self-correction | {summary['self_corrected']} |",
        "",
        "### Accuracy by category",
        "",
        "| Category | Passed |",
        "| --- | --- |",
        *[f"| {c} | {v['passed']}/{v['total']} |" for c, v in summary["by_category"].items()],
        "",
        "## Results per question",
        "",
        "| # | Case | Question | Result | Time to answer | Attempts | Tokens | Detail |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(records, 1):
        a = r.get("agent")
        question = " → ".join([*r["history"], r["question"]]).replace("|", "\\|")
        stats = (
            [secs(a["timing"]["answer_s"]), str(a["attempts"]), f"{a['tokens'].get('total_tokens', 0):,}"]
            if a
            else ["–", "–", "–"]
        )
        result = {"pass": "✅", "fail": "❌", "error": "⚠️ not run"}[r["status"]]
        cells = [str(i), f"`{r['id']}`", question, result, *stats, r["detail"].replace("|", "/")]
        lines.append("| " + " | ".join(cells) + " |")

    failures = [r for r in records if r["status"] == "fail"]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            a = r.get("agent") or {}
            lines += [
                f"### `{r['id']}`",
                "",
                f"- Question: {r['question']}",
                f"- Detail: {r['detail']}",
                f"- Agent interpretation: {a.get('reasoning', '–')}",
                f"- Expected (first rows): `{json.dumps(r['expected'][:3])}`",
                f"- Agent result (first rows): `{json.dumps((a.get('results') or [])[:3])}`",
                "",
                "```json",
                a.get("pipeline", "") or "(no pipeline)",
                "```",
                "",
            ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--pause", type=float, default=0, help="seconds to wait between LLM turns")
    parser.add_argument(
        "--resume",
        type=Path,
        help="results JSON of an earlier run; re-run its cases that did not run (plus any --only cases)",
    )
    args = parser.parse_args()

    selected = set(args.only.split(",")) if args.only else set()
    unknown = selected - {c.id for c in CASES}
    if unknown:
        raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")

    cases = [c for c in CASES if c.id in selected] if selected else CASES
    kept: dict[str, dict] = {}
    if args.resume:
        previous = json.loads(args.resume.read_text())["records"]
        kept = {r["id"]: r for r in previous if r["status"] != "error" and r["id"] not in selected}
        cases = [c for c in CASES if c.id not in kept]
        print(f"Resuming: keeping {len(kept)} finished cases, running {len(cases)}")

    meta = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "model": get_settings().llm_model}
    new_records = {r["id"]: r for r in evaluate(cases, args.pause)}
    records = [kept.get(c.id) or new_records[c.id] for c in CASES if c.id in kept or c.id in new_records]
    summary = summarize(records)
    report = render_report(records, summary, meta)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"run-{stamp}.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "records": records}, indent=2, default=str)
    )
    (RESULTS_DIR / f"run-{stamp}.md").write_text(report)
    if len(records) == len(CASES):
        (EVAL_DIR / "report.md").write_text(report)

    print(f"\nAccuracy: {summary['passed']}/{summary['ran']} ({summary['accuracy']:.0%}), not run: {summary['not_run']}")
    if summary["answer_s_median"] is not None:
        print(f"Median time to answer: {summary['answer_s_median']:.1f} s, mean tokens: {summary['tokens_mean']:,.0f}")
    print(f"Saved results to {RESULTS_DIR / f'run-{stamp}.json'}")


if __name__ == "__main__":
    main()
