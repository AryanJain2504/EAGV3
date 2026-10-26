"""Typed contracts every layer in the S7 agent talks in.

One small file, read top-to-bottom. Every other module imports from here, so
the boundary between layers is a Pydantic model rather than a free-form dict.

Session 7 adds one optional field on `MemoryItem`: `embedding`. Items of
kind `fact`, `preference`, and `tool_outcome` carry a vector embedding
written by Memory at insert time. The embedding underlies FAISS vector
search. Items of kind `scratchpad` are run-scoped and skip embedding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id(prefix: str = "id") -> str:
    return f"{prefix}:{uuid4().hex[:8]}"


# ── Memory ──────────────────────────────────────────────────────────────────

MemoryKind = Literal["fact", "preference", "tool_outcome", "scratchpad"]


class MemoryItem(BaseModel):
    """One record in memory. Reads happen by vector similarity first
    (FAISS over the `embedding` field) with keyword overlap as the
    fallback when vector search returns nothing. Bytes never live here;
    they live in the artifact store."""

    id: str
    kind: MemoryKind
    keywords: list[str] = Field(default_factory=list)
    descriptor: str                              # one short human-readable line
    value: dict = Field(default_factory=dict)    # structured payload
    artifact_id: str | None = None
    embedding: list[float] | None = None         # set by Memory at write time
    source: str
    run_id: str
    goal_id: str | None = None
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(extra="forbid")


# ── Artifacts ───────────────────────────────────────────────────────────────

class Artifact(BaseModel):
    id: str
    content_type: str
    size_bytes: int
    source: str
    descriptor: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(extra="forbid")


# ── Goals & Observations ────────────────────────────────────────────────────

class Goal(BaseModel):
    """One intent-level objective. Perception owns this state.

    `attach_artifact_id` is the only channel by which raw bytes reach
    Decision, and only Perception may set it: the orchestrator never decides
    on a goal's behalf that some artifact is the right evidence.
    """

    id: str
    text: str
    done: bool = False
    attach_artifact_id: str | None = None        # Perception sets this when the goal needs raw bytes
    attach_artifact_ids: list[str] = Field(default_factory=list)  # multi-source goals (D)

    model_config = ConfigDict(extra="forbid")


class Observation(BaseModel):
    goals: list[Goal]

    model_config = ConfigDict(extra="forbid")

    @property
    def all_done(self) -> bool:
        return bool(self.goals) and all(g.done for g in self.goals)

    def next_unfinished(self) -> Goal | None:
        return next((g for g in self.goals if not g.done), None)


# ── Decision output ─────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DecisionOutput(BaseModel):
    """Decision emits exactly one of these two, and nothing else.

    `answer` carries arbitrary semantic work (summarise, extract, compare,
    translate, cite) inside its text. There is deliberately no evidence
    metadata: the reply *is* the evidence contract. A structured sub-schema
    here would only re-express what the answer already says, while giving the
    loop a second, parallel notion of "grounded" that has to be reconciled
    against the prose on every turn.
    """

    answer: str | None = None
    tool_call: ToolCall | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def exactly_one_result(self) -> "DecisionOutput":
        if (self.answer is None) == (self.tool_call is None):
            raise ValueError("Decision must contain exactly one answer or tool_call")
        return self

    @property
    def is_answer(self) -> bool:
        return self.answer is not None


class ActionResult(BaseModel):
    """Validated result crossing the MCP action boundary."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    text: str
    success: bool
    artifact_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class HistoryEvent(BaseModel):
    """Validated event retained in the current run's trace."""

    iter: int
    kind: Literal["action", "answer"]
    goal_id: str
    text: str = ""
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_descriptor: str = ""
    artifact_id: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_shape(self) -> "HistoryEvent":
        if self.kind == "action" and not self.tool:
            raise ValueError("action history requires tool")
        if self.kind == "answer" and self.tool is not None:
            raise ValueError("answer history cannot contain a tool")
        return self
