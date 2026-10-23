from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class HistoricalEvidenceSchema(BaseModel):
    claim: Optional[str] = Field(default=None, description="The recalled engineering rule or decision")
    rationale: Optional[str] = Field(default=None, description="Documented justification")
    evidence_prs: List[str] = Field(default_factory=list, description="List of previous PR references (e.g. ['PR #142'])")
    matched_code: Optional[str] = Field(default=None, description="Code snippet or file location matched")
    reasoning: Optional[str] = Field(default=None, description="Why this PR conflicts with or matches the memory")
    exception_notes: Optional[str] = Field(default=None, description="Why an exception applies or does not apply")


class ReviewFindingSchema(BaseModel):
    id: Optional[str] = Field(default=None, description="Stable identifier for the finding")
    category: Literal[
        "architecture_drift",
        "rejected_pattern",
        "approved_exception",
        "convention_violation",
        "note",
        "review_observation",
    ] = Field(..., description="Classification of the finding")
    severity: Literal["info", "warning", "error"] = Field(
        default="warning", description="Severity level"
    )
    file_path: str
    line_number: Optional[int] = None
    title: str
    message: str
    decision: Optional[str] = Field(default=None, description="What PRism decided for this finding")
    rationale: Optional[str] = Field(default=None, description="Why this rule or boundary exists historically")
    action: Optional[str] = Field(default=None, description="Concrete next step or remediation")
    scope: Optional[str] = Field(default=None, description="Human-readable architectural scope")
    source: Optional[str] = Field(default=None, description="Authoritative source reference")
    historical_pr_ref: Optional[str] = Field(
        default=None, description="Reference to past PR (e.g. 'PR #142')"
    )
    suggested_fix: Optional[str] = None
    historical_evidence: Optional[HistoricalEvidenceSchema] = Field(
        default=None, description="Rich historical precedent trace"
    )


class DurableMemoryCandidateSchema(BaseModel):
    claim: str = Field(..., description="Clear architectural rule or decision statement")
    type: Literal[
        "architecture_decision",
        "rejected_pattern",
        "approved_exception",
        "review_decision",
        "lesson_learned",
    ] = Field(default="architecture_decision", description="Category of the engineering memory")
    rationale: Optional[str] = Field(default=None, description="Documented justification based on PR context")
    scope: Optional[str] = Field(default=None, description="Architectural boundary, component, or file path")
    confidence: Literal["supported", "partially_supported", "insufficient_evidence"] = Field(
        default="supported", description="Confidence level of the precedent"
    )


class ApplicabilityResultSchema(BaseModel):
    repository_id: str
    repository_full_name: Optional[str] = None
    recalled_memories_count: int = 0
    applicable_memories: List[Dict[str, Any]] = Field(default_factory=list)
    rejected_memories: List[Dict[str, Any]] = Field(default_factory=list)
    matched_scopes: List[str] = Field(default_factory=list)
    applicable_rules: List[Dict[str, Any]] = Field(default_factory=list)
    applicable_exceptions: List[Dict[str, Any]] = Field(default_factory=list)
    contextual_memories: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    provenance_references: List[str] = Field(default_factory=list)
    hindsight_status: str = Field(default="empty", description="'available', 'unavailable', or 'empty'")


class ReviewAnalysisOutput(BaseModel):
    summary: str
    drift_detected: bool = False
    exception_approved: bool = False
    verdict: Literal[
        "NO_PRECEDENT",
        "CONFORMING",
        "DRIFT",
        "APPROVED_EXCEPTION",
        "CONFLICTING_GUIDANCE",
    ] = "NO_PRECEDENT"
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    findings: List[ReviewFindingSchema] = Field(default_factory=list)
    durable_memory_candidates: List[DurableMemoryCandidateSchema] = Field(default_factory=list)
    applicability_result: Optional[ApplicabilityResultSchema] = None
    pr_context: Optional[Any] = Field(default=None, description="Phase 5 deep PR context including symbols, dependencies, and bounded context")
    commit_sha: Optional[str] = Field(default=None, description="Commit SHA reviewed")
    historical_context_snapshot: Optional[List[Dict[str, Any]]] = Field(default=None, description="Captured historical context snapshot")
    raw_model_response: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    messages: List[Dict[str, str]]
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: Optional[int] = 4096


class ChatCompletionResponse(BaseModel):
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None
