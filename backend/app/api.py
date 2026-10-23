import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from bson import json_util
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pymongo.errors import PyMongoError

from app import conversations
from app.agent.graph import MAX_ATTEMPTS, new_turn
from app.agent.query_safety import InvalidPipelineError, prepare_pipeline
from app.agent.schema_context import get_dataset_stats
from app.config import get_settings
from app.db import get_collection
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationSummary,
    HealthResponse,
    ResumeRequest,
    StatsResponse,
    Suggestion,
    SuggestionsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

SUGGESTIONS = [
    Suggestion(category="Orders", question="How many orders were created in each quarter of 2014?"),
    Suggestion(category="Spending", question="Which quarter had the highest spending?"),
    Suggestion(category="Items", question="What are the most frequently ordered line items?"),
    Suggestion(category="Suppliers", question="Who are the top 10 suppliers by total spend?"),
    Suggestion(category="Trends", question="Show the monthly spending trend for IT Goods in fiscal year 2013-2014"),
    Suggestion(category="Small business", question="Which departments spend the most with small businesses (CA-SB)?"),
    Suggestion(category="Methods", question="What share of spending goes to each acquisition type?"),
    Suggestion(category="Departments", question="What is the average order value per department?"),
]


def _to_response(state: dict, conversation_id: str, started: float) -> ChatResponse:
    plan = state.get("plan") or {}
    pipeline = state.get("pipeline")
    results = state.get("results")
    failed = plan.get("needs_query") and results is None
    return ChatResponse(
        conversation_id=conversation_id,
        answer=state.get("answer", ""),
        needs_query=plan.get("needs_query", False),
        reasoning=plan.get("reasoning", ""),
        pipeline=json.loads(json_util.dumps(pipeline)) if pipeline else None,
        results=results or [],
        row_count=len(results or []),
        visualization=plan.get("visualization", "none"),
        attempts=state.get("attempts", 0),
        error=state.get("error") if failed else None,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        follow_ups=state.get("follow_ups") or [],
        cancelled=bool(state.get("cancelled")),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _start_turn(body: ChatRequest) -> tuple[str, dict]:
    conversation_id = body.conversation_id or str(uuid.uuid4())
    history = await run_in_threadpool(conversations.history_messages, conversation_id) if body.conversation_id else []
    return conversation_id, new_turn(body.message, history, validate=body.validate_query)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    try:
        documents = get_collection().estimated_document_count()
        mongodb = "ok"
    except PyMongoError as exc:
        documents, mongodb = None, f"error: {exc}"
    return HealthResponse(
        status="ok" if mongodb == "ok" and settings.llm_api_key else "degraded",
        mongodb=mongodb,
        documents=documents,
        llm_model=settings.llm_model,
        llm_provider=urlparse(settings.llm_base_url).hostname if settings.llm_base_url else "api.openai.com",
        llm_configured=bool(settings.llm_api_key),
    )


@router.get("/suggestions", response_model=SuggestionsResponse)
def suggestions() -> SuggestionsResponse:
    return SuggestionsResponse(suggestions=SUGGESTIONS)


@router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    s = get_dataset_stats()
    return StatsResponse(
        line_items=s["line_items"],
        orders=s["orders"],
        total_spend=s["total_spend"],
        departments=s["departments"],
        suppliers=s["suppliers"],
        first_date=s["first"],
        last_date=s["last"],
    )


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations() -> list[dict]:
    return conversations.list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> dict:
    conversation = conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> Response:
    if not conversations.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


async def _stream_turn(
    graph,
    graph_input: dict | Command,
    config: dict | None,
    conversation_id: str,
    question: str,
    first_status: dict,
    attempts: int = 0,
) -> AsyncIterator[str]:
    """Run (or resume) one turn and translate graph progress into SSE events.

    Ends with the saved turn, or with `approval_required` when validation mode pauses the graph.
    """
    started = time.perf_counter()
    final_state: dict = {}
    done_sent = False
    yield _sse("status", {**first_status, "conversation_id": conversation_id})
    try:
        async for mode, chunk in graph.astream(
            graph_input, config=config, stream_mode=["updates", "messages", "values"]
        ):
            if mode == "messages":
                message, metadata = chunk
                if metadata.get("langgraph_node") == "respond" and message.content:
                    yield _sse("token", {"text": message.content})
                continue

            if mode == "values":
                final_state = chunk
                if chunk.get("answer") and not done_sent:
                    done_sent = True
                    yield _sse("done", _to_response(chunk, conversation_id, started).model_dump())
                continue

            for node, update in chunk.items():
                if node == "__interrupt__":
                    review = update[0].value
                    thread_id = config["configurable"]["thread_id"]
                    await run_in_threadpool(conversations.set_pending, conversation_id, question, thread_id, review)
                    yield _sse("approval_required", {"conversation_id": conversation_id, "question": question, **review})
                    return
                if node == "generate_query":
                    attempts = update["attempts"]
                    plan = update["plan"]
                    if plan["needs_query"]:
                        yield _sse("status", {"stage": "querying", "reasoning": plan["reasoning"]})
                elif node == "execute_query":
                    if update.get("error") and attempts < MAX_ATTEMPTS:
                        yield _sse("status", {"stage": "retrying", "error": update["error"]})
                    elif update.get("results") is not None:
                        yield _sse("results", {"row_count": len(update["results"])})
                        yield _sse("status", {"stage": "answering"})
                elif node == "suggest_followups":
                    yield _sse("followups", {"questions": update["follow_ups"]})

        response = _to_response(final_state, conversation_id, started)
        await run_in_threadpool(conversations.append_turn, conversation_id, question, response.model_dump())
    except Exception as exc:
        logger.exception("Streaming chat request failed")
        yield _sse("error", {"message": f"The request failed: {exc}"})


def _sse_response(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """Server-sent events, in order:
    status (stage: planning | querying | retrying | answering), results, token (repeated),
    done (full ChatResponse), followups, then the turn is saved. `error` replaces the rest on failure.

    With `validate: true` the stream stops at `approval_required` (the proposed query) until the
    user answers through POST /api/chat/resume.
    """
    conversation_id, graph_input = await _start_turn(body)
    if body.conversation_id:
        await run_in_threadpool(conversations.clear_pending, conversation_id)

    if body.validate_query:
        graph = request.app.state.review_graph
        config = {"configurable": {"thread_id": f"{conversation_id}:{uuid.uuid4()}"}}
    else:
        graph, config = request.app.state.graph, None

    return _sse_response(
        _stream_turn(graph, graph_input, config, conversation_id, body.message, {"stage": "planning"})
    )


@router.post("/chat/resume")
async def chat_resume(body: ResumeRequest, request: Request) -> StreamingResponse:
    """Answer a validation-mode approval request; streams the rest of the turn like /chat/stream."""
    conversation = await run_in_threadpool(conversations.get_conversation, body.conversation_id)
    pending = (conversation or {}).get("pending")
    if not pending:
        raise HTTPException(status_code=409, detail="No query is waiting for approval in this conversation.")

    if body.action == "edit":
        try:
            prepare_pipeline(body.pipeline)
        except InvalidPipelineError as exc:
            raise HTTPException(status_code=422, detail=f"The edited query is not valid: {exc}") from exc

    graph = request.app.state.review_graph
    config = {"configurable": {"thread_id": pending["thread_id"]}}
    snapshot = await graph.aget_state(config)
    if not snapshot.next:
        await run_in_threadpool(conversations.clear_pending, body.conversation_id)
        raise HTTPException(status_code=409, detail="This approval request has expired. Please ask again.")

    first_status = {"stage": "planning" if body.action == "feedback" else "querying"}
    decision = body.model_dump(include={"action", "pipeline", "feedback"})
    return _sse_response(
        _stream_turn(
            graph,
            Command(resume=decision),
            config,
            body.conversation_id,
            pending["question"],
            first_status,
            attempts=snapshot.values.get("attempts", 0),
        )
    )
