"""LangGraph agent that answers a question by writing and running a MongoDB aggregation pipeline.

Flow: generate_query -> review_query -> execute_query -> respond -> suggest_followups.
A failed or empty query goes back to generate_query (up to MAX_ATTEMPTS), and questions that need
no data go straight to respond. In validation mode review_query pauses with `interrupt()` until the
user approves, edits, asks for changes or cancels, which needs a checkpointer.

Earlier turns are passed in by the caller (see `new_turn`); the agent itself stores nothing.
"""

import json
import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Literal, TypedDict

from bson import ObjectId, json_util
from bson.decimal128 import Decimal128
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from app.agent.prompts import ANSWER_SYSTEM_PROMPT, FOLLOWUP_SYSTEM_PROMPT, QUERY_SYSTEM_PROMPT
from app.agent.query_safety import ALLOWED_STAGES, FORBIDDEN_OPERATORS, InvalidPipelineError, prepare_pipeline
from app.agent.schema_context import build_schema_context, data_period
from app.config import get_settings
from app.db import get_collection

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_ROWS_FOR_LLM = 50
QUERY_TIMEOUT_MS = 20_000
HISTORY_TURNS = 3

Visualization = Literal["none", "number", "bar", "line", "pie", "table"]


class QueryPlan(BaseModel):
    needs_query: bool = Field(description="False only if the message can be answered without the database.")
    reasoning: str = Field(description="One or two sentences: how the question is interpreted, filters, assumptions.")
    pipeline: str = Field(description="Aggregation pipeline as a JSON array (Extended JSON dates). Empty string if needs_query is false.")
    visualization: Visualization = Field(description="Best way to display the result.")
    direct_reply: str = Field(description="Reply to the user when needs_query is false, otherwise empty string.")


class FollowUps(BaseModel):
    questions: list[str] = Field(description="Exactly 3 short follow-up questions.")


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    plan: dict | None
    pipeline: list | None
    results: list | None
    error: str | None
    attempts: int
    answer: str
    follow_ups: list[str]
    validate: bool
    last_error: str | None
    feedback: str | None
    cancelled: bool


@lru_cache
def get_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        temperature=0,
        max_retries=5,
    )


@lru_cache
def system_prompt(template: str) -> str:
    """Fill a prompt template with facts read from the database and the query safety rules."""
    values = {
        "{schema}": build_schema_context(),
        "{data_period}": data_period(),
        "{allowed_stages}": ", ".join(sorted(ALLOWED_STAGES)),
        "{forbidden_operators}": ", ".join(sorted(FORBIDDEN_OPERATORS)),
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template


def to_jsonable(value):
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    return value


def format_history(messages: list[AnyMessage]) -> str:
    lines = []
    for msg in messages[-2 * HISTORY_TURNS :]:
        if isinstance(msg, HumanMessage):
            lines.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            lines.append(f"Assistant: {msg.content[:800]}")
            if pipeline := msg.additional_kwargs.get("pipeline"):
                lines.append(f"(pipeline used: {pipeline})")
    return "\n".join(lines) or "(no previous messages)"


def generate_query(state: AgentState) -> dict:
    prompt = (
        f"Conversation so far:\n{format_history(state['messages'][:-1])}\n\n"
        f"Current question: {state['question']}"
    )
    if state.get("error") and state.get("plan"):
        prompt += (
            f"\n\nYour previous pipeline:\n{state['plan']['pipeline']}\n"
            f"Problem: {state['error']}\nReturn a corrected pipeline."
        )
    if state.get("feedback") and state.get("plan"):
        prompt += (
            f"\n\nYou proposed this pipeline:\n{state['plan']['pipeline']}\n"
            f"The user reviewed it and asked for changes: {state['feedback']}\nReturn a revised pipeline."
        )

    planner = get_llm().with_structured_output(QueryPlan)
    plan: QueryPlan = planner.invoke([SystemMessage(system_prompt(QUERY_SYSTEM_PROMPT)), HumanMessage(prompt)])
    return {
        "plan": plan.model_dump(),
        "attempts": state.get("attempts", 0) + 1,
        "error": None,
        "last_error": state.get("error"),
        "feedback": None,
    }


def review_query(state: AgentState) -> dict:
    """Validation mode: pause until a person approves, edits, revises or cancels the pipeline."""
    if not state.get("validate"):
        return {}
    plan = state["plan"]
    decision = interrupt(
        {
            "reasoning": plan["reasoning"],
            "pipeline": plan["pipeline"],
            "visualization": plan["visualization"],
            "attempt": state["attempts"],
            "last_error": state.get("last_error"),
        }
    )
    action = decision.get("action")
    if action == "edit":
        return {"plan": {**plan, "pipeline": decision["pipeline"]}}
    if action == "feedback":
        return {"feedback": decision["feedback"], "attempts": 0}
    if action == "cancel":
        return {"cancelled": True}
    return {}


def execute_query(state: AgentState) -> dict:
    try:
        pipeline = prepare_pipeline(state["plan"]["pipeline"])
        rows = list(get_collection().aggregate(pipeline, maxTimeMS=QUERY_TIMEOUT_MS, allowDiskUse=True))
    except (InvalidPipelineError, PyMongoError) as exc:
        return {"pipeline": None, "results": None, "error": f"{type(exc).__name__}: {exc}"}

    rows = to_jsonable(rows)
    if not rows and pipeline != state.get("pipeline"):
        return {
            "pipeline": pipeline,
            "results": [],
            "error": (
                "The query ran but returned no documents. Check field names and filter values "
                "(exact matches are case-sensitive; consider a case-insensitive $regex). "
                "If zero results is genuinely correct, return the same pipeline."
            ),
        }
    return {"pipeline": pipeline, "results": rows, "error": None}


def respond(state: AgentState) -> dict:
    plan = state["plan"]
    pipeline_text = plan.get("pipeline", "")

    if not plan["needs_query"]:
        answer = plan["direct_reply"]
    elif state.get("cancelled"):
        answer = "Okay, I cancelled this query. Nothing was run against the database."
    elif state.get("results") is None:
        answer = (
            "Sorry, I couldn't build a working query for that question. "
            "Could you rephrase it or add more detail (time period, department, item)?"
        )
    else:
        rows = state["results"]
        shown = rows[:MAX_ROWS_FOR_LLM]
        truncated = f" (showing first {len(shown)})" if len(rows) > len(shown) else ""
        prompt = (
            f"Question: {state['question']}\n"
            f"Interpretation: {plan['reasoning']}\n"
            f"Query: {json_util.dumps(state['pipeline'])}\n"
            f"Results: {len(rows)} rows{truncated}\n{json.dumps(shown, default=str)}"
        )
        answer = get_llm().invoke([SystemMessage(system_prompt(ANSWER_SYSTEM_PROMPT)), HumanMessage(prompt)]).content

    return {"answer": answer, "messages": [AIMessage(answer, additional_kwargs={"pipeline": pipeline_text})]}


def suggest_followups(state: AgentState) -> dict:
    if not state["plan"]["needs_query"] or not state.get("results"):
        return {"follow_ups": []}
    try:
        suggester = get_llm().with_structured_output(FollowUps)
        out: FollowUps = suggester.invoke(
            [
                SystemMessage(system_prompt(FOLLOWUP_SYSTEM_PROMPT)),
                HumanMessage(f"Question: {state['question']}\nAnswer: {state['answer'][:1500]}"),
            ]
        )
    except Exception:
        logger.warning("Follow-up suggestion failed", exc_info=True)
        return {"follow_ups": []}
    return {"follow_ups": [q.strip() for q in out.questions if q.strip()][:3]}


def route_after_generate(state: AgentState) -> str:
    return "review_query" if state["plan"]["needs_query"] else "respond"


def route_after_review(state: AgentState) -> str:
    if state.get("cancelled"):
        return "respond"
    if state.get("feedback"):
        return "generate_query"
    return "execute_query"


def route_after_execute(state: AgentState) -> str:
    if state.get("error") and state["attempts"] < MAX_ATTEMPTS:
        return "generate_query"
    return "respond"


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compile the agent. Validation mode needs a `checkpointer` to pause and resume."""
    graph = StateGraph(AgentState)
    graph.add_node("generate_query", generate_query)
    graph.add_node("review_query", review_query)
    graph.add_node("execute_query", execute_query)
    graph.add_node("respond", respond)
    graph.add_node("suggest_followups", suggest_followups)

    graph.add_edge(START, "generate_query")
    graph.add_conditional_edges("generate_query", route_after_generate, ["review_query", "respond"])
    graph.add_conditional_edges("review_query", route_after_review, ["execute_query", "generate_query", "respond"])
    graph.add_conditional_edges("execute_query", route_after_execute, ["generate_query", "respond"])
    graph.add_edge("respond", "suggest_followups")
    graph.add_edge("suggest_followups", END)

    return graph.compile(checkpointer=checkpointer)


def new_turn(question: str, history: list[AnyMessage] | None = None, validate: bool = False) -> dict:
    """Graph input for one user turn, with earlier turns of the conversation as `history`."""
    return {
        "messages": [*(history or []), HumanMessage(question)],
        "question": question,
        "plan": None,
        "pipeline": None,
        "results": None,
        "error": None,
        "attempts": 0,
        "answer": "",
        "follow_ups": [],
        "validate": validate,
        "last_error": None,
        "feedback": None,
        "cancelled": False,
    }
