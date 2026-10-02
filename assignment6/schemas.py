"""Pydantic v2 contracts for the Assignment 6 MPDA loop.

Field names and types follow the instructor's contract verbatim:
MemoryItem (with id, value: dict, created_at: datetime), Artifact,
Goal (attach_artifact_id), Observation, ToolCall, DecisionOutput.

Observe / DecisionIn / HistoryItem / ActionOut are loop-internal packets
built on top of those six — same style, no free-form dicts across roles,
no regex on LLM output.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class MemoryItem(BaseModel):
    id: str = Field(default="", description="Unique handle; filled by the store if empty")
    kind: Literal["fact", "preference", "tool_outcome", "scratchpad"]
    keywords: List[str] = Field(default_factory=list)
    descriptor: str = Field(default="", description="One short human-readable line")
    value: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured payload. Facts use {entity, attribute, value}, "
                    "e.g. {entity: mom, attribute: birthday, value: 2026-05-15}",
    )
    artifact_id: Optional[str] = Field(default=None, description="Handle into the artifact store")
    source: str = Field(default="")
    run_id: str = Field(default="")
    goal_id: Optional[str] = Field(default=None)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    id: str                    # "art:<sha256-prefix>"
    content_type: str
    size_bytes: int
    source: str
    descriptor: str


class Goal(BaseModel):
    id: str
    text: str                  # short imperative description
    done: bool = False
    attach_artifact_id: Optional[str] = None


class Observation(BaseModel):
    goals: List[Goal]

    def first_unfinished(self) -> Optional[Goal]:
        for g in self.goals:
            if not g.done:
                return g
        return None


class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class DecisionOutput(BaseModel):
    answer: Optional[str] = None
    tool_call: Optional[ToolCall] = None

    @model_validator(mode="after")
    def require_one_result(self) -> "DecisionOutput":
        if (self.answer is None) == (self.tool_call is None):
            raise ValueError("Decision must set exactly one of answer/tool_call")
        return self

    def is_tool_call(self) -> bool:
        return self.tool_call is not None


# -- Loop-internal packets (same contract style) ---------------------------


class HistoryItem(BaseModel):
    seq: int
    kind: Literal["perception", "decision", "action", "memory"]
    text: str = ""
    tool: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    artifact_id: Optional[str] = None


class Observe(BaseModel):
    """Loop -> Perception packet. Hits are small text items; artifact BYTES
    are never present here, only ids inside goals."""

    query: str
    hits: List[MemoryItem] = Field(default_factory=list)
    history: List[HistoryItem] = Field(default_factory=list)
    prior_goals: List[Goal] = Field(default_factory=list)
    run_id: str = ""


class ToolDef(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    """Loop -> Decision packet. `attachment` holds artifact BYTES (not an id)
    iff goal.attach_artifact_id was set by Perception."""

    goal: Goal
    hits: List[MemoryItem] = Field(default_factory=list)
    attachment: Optional[str] = None
    history_slice: List[HistoryItem] = Field(default_factory=list)
    tools: List[ToolDef] = Field(default_factory=list)


class ActionOut(BaseModel):
    """Action -> Loop packet. Large outputs arrive as artifact_id only."""

    ok: bool = True
    text: Optional[str] = None
    artifact_id: Optional[str] = None
    tool: str = ""
    latency_ms: int = 0
    error: Optional[str] = None
