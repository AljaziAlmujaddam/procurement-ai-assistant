"""FastAPI entry point.

Run (from backend/):
    .venv/bin/uvicorn app.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.mongodb import MongoDBSaver

from app import conversations
from app.agent.graph import build_graph
from app.agent.schema_context import build_schema_context
from app.api import router
from app.config import get_settings
from app.db import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PAUSED_TURN_TTL_SECONDS = 7 * 24 * 60 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    build_schema_context()  # warm the cache so the first question is not slow
    conversations.ensure_indexes()
    app.state.graph = build_graph()
    # Validation mode pauses turns until the user approves the query; the paused state is kept in
    # MongoDB so it survives page refreshes and server restarts.
    checkpointer = MongoDBSaver(get_client(), db_name=get_settings().mongodb_db, ttl=PAUSED_TURN_TTL_SECONDS)
    app.state.review_graph = build_graph(checkpointer)
    yield


app = FastAPI(
    title="Procurement AI Assistant",
    description="Ask questions about State of California purchase orders in natural language.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
