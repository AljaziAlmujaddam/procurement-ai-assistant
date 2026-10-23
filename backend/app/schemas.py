from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.graph import Visualization


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(
        default=None, description="Omit to start a new conversation; reuse the returned id for follow-ups."
    )
    validate_query: bool = Field(
        default=False,
        alias="validate",
        description="Validation mode: pause for approval before every query runs.",
    )


class ResumeRequest(BaseModel):
    conversation_id: str
    action: Literal["approve", "edit", "feedback", "cancel"]
    pipeline: str | None = Field(default=None, max_length=20_000, description="Edited pipeline JSON (action=edit).")
    feedback: str | None = Field(default=None, max_length=1000, description="Requested changes (action=feedback).")

    @model_validator(mode="after")
    def _check_payload(self):
        if self.action == "edit" and not (self.pipeline or "").strip():
            raise ValueError("action 'edit' needs the edited pipeline")
        if self.action == "feedback" and not (self.feedback or "").strip():
            raise ValueError("action 'feedback' needs the requested changes")
        return self


class ReviewRequest(BaseModel):
    reasoning: str
    pipeline: str
    visualization: Visualization
    attempt: int
    last_error: str | None = None


class PendingApproval(BaseModel):
    question: str
    review: ReviewRequest
    created_at: datetime


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    needs_query: bool
    reasoning: str
    pipeline: list[dict[str, Any]] | None
    results: list[dict[str, Any]]
    row_count: int
    visualization: Visualization
    attempts: int
    error: str | None
    elapsed_ms: int
    follow_ups: list[str] = []
    cancelled: bool = False


class HealthResponse(BaseModel):
    status: str
    mongodb: str
    documents: int | None
    llm_model: str
    llm_provider: str
    llm_configured: bool


class Suggestion(BaseModel):
    category: str
    question: str


class SuggestionsResponse(BaseModel):
    suggestions: list[Suggestion]


class StatsResponse(BaseModel):
    line_items: int
    orders: int
    total_spend: float
    departments: int
    suppliers: int
    first_date: datetime
    last_date: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    turn_count: int


class ConversationTurn(BaseModel):
    question: str
    response: ChatResponse


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    turns: list[ConversationTurn]
    pending: PendingApproval | None = None
