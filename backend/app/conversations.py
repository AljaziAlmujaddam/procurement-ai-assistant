"""Conversation storage in MongoDB.

One document per conversation, `_id` = conversation id:
    {title, created_at, updated_at, turns: [{question, response, created_at}], pending?}
`response` is the full ChatResponse so the UI can re-render charts when a conversation is reopened.
`pending` is a question paused in validation mode: {question, thread_id, review, created_at}, where
`thread_id` points at the LangGraph checkpoint to resume and `review` is what the user must approve.
"""

import json
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pymongo import DESCENDING
from pymongo.collection import Collection

from app.agent.graph import HISTORY_TURNS
from app.config import get_settings
from app.db import get_client

TITLE_LENGTH = 60


def _collection() -> Collection:
    return get_client()[get_settings().mongodb_db]["conversations"]


def ensure_indexes() -> None:
    _collection().create_index([("updated_at", DESCENDING)])


def _title(question: str) -> str:
    question = " ".join(question.split())
    return question if len(question) <= TITLE_LENGTH else question[: TITLE_LENGTH - 1].rstrip() + "…"


def list_conversations(limit: int = 50) -> list[dict]:
    cursor = (
        _collection()
        .find({}, {"title": 1, "updated_at": 1, "turn_count": {"$size": "$turns"}})
        .sort("updated_at", DESCENDING)
        .limit(limit)
    )
    return [{"id": doc.pop("_id"), **doc} for doc in cursor]


def get_conversation(conversation_id: str) -> dict | None:
    doc = _collection().find_one({"_id": conversation_id})
    return {"id": doc.pop("_id"), **doc} if doc else None


def append_turn(conversation_id: str, question: str, response: dict) -> None:
    now = datetime.now(UTC)
    _collection().update_one(
        {"_id": conversation_id},
        {
            "$setOnInsert": {"title": _title(question), "created_at": now},
            "$set": {"updated_at": now},
            "$push": {"turns": {"question": question, "response": response, "created_at": now}},
            "$unset": {"pending": ""},
        },
        upsert=True,
    )


def set_pending(conversation_id: str, question: str, thread_id: str, review: dict) -> None:
    now = datetime.now(UTC)
    _collection().update_one(
        {"_id": conversation_id},
        {
            "$setOnInsert": {"title": _title(question), "created_at": now, "turns": []},
            "$set": {
                "updated_at": now,
                "pending": {"question": question, "thread_id": thread_id, "review": review, "created_at": now},
            },
        },
        upsert=True,
    )


def clear_pending(conversation_id: str) -> None:
    _collection().update_one({"_id": conversation_id}, {"$unset": {"pending": ""}})


def delete_conversation(conversation_id: str) -> bool:
    return _collection().delete_one({"_id": conversation_id}).deleted_count == 1


def history_messages(conversation_id: str) -> list[AnyMessage]:
    """Last turns as LangChain messages, in the shape the agent's `format_history` expects."""
    doc = _collection().find_one({"_id": conversation_id}, {"turns": {"$slice": -HISTORY_TURNS}})
    messages: list[AnyMessage] = []
    for turn in (doc or {}).get("turns", []):
        response = turn["response"]
        pipeline = json.dumps(response["pipeline"]) if response.get("pipeline") else ""
        messages.append(HumanMessage(turn["question"]))
        messages.append(AIMessage(response["answer"], additional_kwargs={"pipeline": pipeline}))
    return messages
