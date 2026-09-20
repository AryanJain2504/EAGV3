from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReasoningType(str, Enum):
    DEDUCTIVE = "deductive"
    LOOKUP = "lookup"
    DIAGNOSTIC = "diagnostic"
    ARITHMETIC = "arithmetic"
    HEURISTIC = "heuristic"
    VERIFICATION = "verification"


class SelfCheck(BaseModel):
    is_reasonable: bool = Field(
        ...,
        description="Whether the intermediate hypothesis or conclusion is grounded and reasonable."
    )
    sanity_notes: str = Field(
        ...,
        description="Internal sanity-check rationale validating that the step does not hallucinate or assume unverified facts."
    )
    verification_check: str = Field(
        ...,
        description="Specific check verifying whether current data supports moving to the next action."
    )


class ToolCall(BaseModel):
    name: str = Field(..., description="The name of the tool to execute.")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Dictionary of arguments matching tool signature.")


class AgentTurnOutput(BaseModel):
    step_number: int = Field(..., description="Current turn/step in the ReAct loop (1-indexed).")
    reasoning_type: ReasoningType = Field(
        ...,
        description="The primary reasoning modality being applied in this step (e.g. deductive, lookup, diagnostic, arithmetic, heuristic, verification)."
    )
    step_by_step_thinking: str = Field(
        ...,
        description="Explicit step-by-step reasoning explaining the analysis of prior evidence, current intent, and proposed action."
    )
    self_check: SelfCheck = Field(
        ...,
        description="Internal sanity-check evaluating if the conclusion is reasonable and verified."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Quantitative confidence score from 0.0 to 1.0 assessing certainty in the current step."
    )
    tool_calls: Optional[List[ToolCall]] = Field(
        None,
        description="List of tools to invoke in PARALLEL. Use when two independent tools can run simultaneously (e.g. query_system_logs + inspect_network_topology). Set to None if only one tool is needed."
    )
    tool_call: Optional[ToolCall] = Field(
        None,
        description="Single tool to invoke. Use this when only one tool is needed. Ignored if tool_calls is populated."
    )
    fallback_plan: str = Field(
        ...,
        description="Pre-defined fallback procedure to execute if the tool fails, encounters timeouts, or returns unexpected results."
    )
    is_final_step: bool = Field(
        default=False,
        description="True if all investigations are complete and ready to emit final verdict."
    )
    final_remediation_summary: Optional[str] = Field(
        None,
        description="Executive summary and resolution if is_final_step is True."
    )

    def get_all_tool_calls(self) -> List[ToolCall]:
        """Returns the unified list of tool calls regardless of which field was used."""
        if self.tool_calls:
            return self.tool_calls
        if self.tool_call:
            return [self.tool_call]
        return []


class IncidentRemediationReport(BaseModel):
    incident_id: str
    threat_level: str = Field(..., description="CRITICAL, HIGH, MEDIUM, or LOW")
    root_cause: str
    affected_services: List[str]
    mitigation_actions_taken: List[str]
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    rollback_safety_plan: str
    executive_summary: str
    escalation_triggered: bool = Field(default=False, description="True if confidence gate caused a mid-loop escalation.")
