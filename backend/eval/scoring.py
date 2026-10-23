"""Compare the agent's query results with reference results.

Matching is by value, not by field name, because the agent is free to name its output fields
(`order_count`, `orders`, `n`, ...). Numbers match within a relative tolerance (0.5%) or an
absolute one (default one cent), so rounding to 2 decimals is accepted, while counting line
items instead of distinct orders, or using the wrong period, is not.
"""

import re
from dataclasses import dataclass

REL_TOL = 0.005


@dataclass
class Score:
    passed: bool
    detail: str


def leaves(value) -> list:
    if isinstance(value, dict):
        return [leaf for v in value.values() for leaf in leaves(v)]
    if isinstance(value, list):
        return [leaf for v in value for leaf in leaves(v)]
    return [value]


def numbers(value) -> list[float]:
    return [float(v) for v in leaves(value) if isinstance(v, int | float) and not isinstance(v, bool)]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def close(actual: float, expected: float, abs_tol: float) -> bool:
    return abs(actual - expected) <= max(REL_TOL * abs(expected), abs_tol)


def unwrap(rows: list) -> list:
    """Use the inner list when the agent returns a single $facet-style row wrapping its results."""
    if len(rows) == 1 and isinstance(rows[0], dict):
        nested = [v for v in rows[0].values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(nested) == 1:
            return nested[0]
    return rows


def token_in_row(token, row) -> bool:
    values = leaves(row)
    if isinstance(token, int | float):
        return any(
            (isinstance(v, int | float) and not isinstance(v, bool) and float(v) == float(token))
            or (isinstance(v, str) and re.search(rf"(?<!\d)0*{token}(?!\d)", v))
            for v in values
        )
    target = normalize(str(token))
    return any(isinstance(v, str) and normalize(v) == target for v in values)


def label_in_row(label, row) -> bool:
    tokens = label if isinstance(label, list) else [label]
    return all(token_in_row(t, row) for t in tokens)


def score_scalar(expected: list, actual: list, percent: bool, abs_tol: float) -> Score:
    target = expected[0]["value"]
    candidates = [target, target / 100] if percent else [target]
    found = numbers(actual)
    for value in found:
        for c in candidates:
            if close(value, c, abs_tol if c == target else abs_tol / 100):
                return Score(True, f"expected {target:,.2f}, found {value:,.2f}")
    nearest = min(found, key=lambda v: abs(v - target), default=None)
    got = f"{nearest:,.2f}" if nearest is not None else "no numbers"
    return Score(False, f"expected {target:,.2f}, nearest value in result: {got}")


def score_series(expected: list, actual: list, abs_tol: float) -> Score:
    found = numbers(actual)
    missing = [r for r in expected if not any(close(v, r["value"], abs_tol) for v in found)]
    matched = len(expected) - len(missing)
    detail = f"{matched}/{len(expected)} expected values found"
    if missing:
        detail += "; missing " + ", ".join(f"{r['label']}={r['value']:,.2f}" for r in missing[:4])
    return Score(not missing, detail)


def answer_numbers(answer: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", answer)]


def score_change(expected: list, actual: list, answer: str) -> Score:
    """Both period totals must be in the result; the percentage change in the result or the answer."""
    totals = score_series(expected, actual, abs_tol=0.01)
    before, after = expected[0]["value"], expected[1]["value"]
    change = (after - before) / before * 100
    found = [*numbers(actual), *answer_numbers(answer)]
    change_ok = any(abs(abs(v) - abs(change)) <= 0.1 for v in found)
    detail = f"{totals.detail}; {change:+.2f}% change {'found' if change_ok else 'missing'}"
    return Score(totals.passed and change_ok, detail)


def score_no_data(actual: list | None, answer: str) -> Score:
    """No matching data exists: the agent must not return or invent non-zero figures."""
    invented_rows = any(v != 0 for v in numbers(actual or []))
    amounts = [float(m.replace(",", "")) for m in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?)", answer)]
    invented_money = any(a != 0 for a in amounts)
    if invented_rows or invented_money:
        return Score(False, "returned or stated figures although no data exists")
    return Score(True, "reported that no data exists, no invented numbers")


def score_ranking(expected: list, actual: list, top_k: int) -> Score:
    rows = unwrap(actual)
    if not rows:
        return Score(False, "agent returned no rows")
    labels = [r["label"] for r in expected[:top_k]]
    top_rows = rows[:top_k]
    top1 = label_in_row(labels[0], rows[0])
    found = [label for label in labels if any(label_in_row(label, row) for row in top_rows)]
    detail = f"top-1 {'correct' if top1 else 'wrong'} (expected {labels[0]!r})"
    if top_k > 1:
        detail += f"; {len(found)}/{top_k} of the top {top_k} found"
    return Score(top1 and len(found) == len(labels), detail)
