from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MemoryItemSchema(BaseModel):
    id: Optional[str] = None
    text: str = Field(description="Recalled or retained memory content")
    type: Optional[str] = None
    score: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None


class EngineeringMemorySchema(BaseModel):
    id: str
    repository_id: str
    type: str = Field(..., description="Category of memory record (canonical or legacy)")
    claim: str = Field(..., description="Clear architectural rule or decision statement")
    statement: Optional[str] = Field(default=None, description="Canonical architectural rule statement")
    authority: Optional[str] = Field(default=None, description="Authority level: ENFORCEABLE, EXCEPTION, REJECTED_PATTERN, CONTEXTUAL, NON_AUTHORITATIVE")
    rationale: Optional[str] = Field(default=None, description="Documented reason or justification")
    scope: Optional[str] = Field(default=None, description="Component, layer, or file scope")
    status: str = Field(default="ACTIVE", description="Lifecycle status: ACTIVE, SUPERSEDED, REVOKED")
    source_type: str = Field(default="manual")
    source_ref: Optional[str] = Field(default=None, description="e.g. 'PR #142'")
    source_url: Optional[str] = Field(default=None, description="URL to PR or document")
    review_id: Optional[str] = None
    pr_number: Optional[int] = None
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    identity_hash: Optional[str] = None
    repository_full_name: Optional[str] = Field(default=None, description="e.g. 'vectorize-io/hindsight'")
    github_repo_id: Optional[int] = Field(default=None, description="GitHub repository numerical ID")
    confidence: str = Field(default="supported")
    evidence_items: List[str] = Field(default_factory=list, description="Array of historical PR references or evidence items")
    hindsight_document_id: Optional[str] = None
    sync_state: Optional[str] = Field(default="SYNCED", description="Hindsight sync state: SYNCED, UNAVAILABLE, FAILED")
    observation_count: Optional[int] = Field(default=1, description="Observation frequency count")
    created_at: str
    updated_at: str


class RecordMemoryRequest(BaseModel):
    claim: Optional[str] = Field(default=None, description="Clear architectural rule or decision")
    statement: Optional[str] = Field(default=None, description="Canonical architectural rule statement")
    type: str = Field(default="DOCUMENTED_DECISION")
    authority: Optional[str] = None
    rationale: Optional[str] = Field(default=None, description="Why this was decided")
    scope: Optional[str] = Field(default=None, description="Scope or layer (e.g. Controllers, API routes)")
    source_ref: Optional[str] = Field(default=None, description="PR or Issue reference (e.g. 'PR #142')")
    source_url: Optional[str] = Field(default=None, description="Link to source")
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None
    supersedes: Optional[str] = None
    confidence: str = Field(default="supported")
    evidence_items: Optional[List[str]] = Field(default=None, description="List of evidence references")


class ListEngineeringMemoriesResponse(BaseModel):
    repository_id: str
    memories: List[EngineeringMemorySchema]
    total_count: int


class RetainMemoryRequest(BaseModel):
    content: str = Field(..., description="Knowledge, rationale, or pattern to retain")
    bank_id: Optional[str] = Field(None, description="Memory bank identifier (e.g. repo name)")
    context: Optional[str] = Field(None, description="Context such as PR title, author, or decision summary")
    document_id: Optional[str] = Field(None, description="Optional document or PR reference ID")
    metadata: Optional[Dict[str, str]] = Field(None, description="Key-value metadata (e.g. file, rule, pr_number)")
    tags: Optional[List[str]] = Field(None, description="Tags like ['architecture_drift', 'controller_direct_access']")


class RetainMemoryResponse(BaseModel):
    success: bool
    bank_id: str
    items_count: int = 1
    operation_id: Optional[str] = None


class RecallMemoryRequest(BaseModel):
    query: str = Field(..., description="Query or code diff snippet to recall past decisions for")
    bank_id: Optional[str] = Field(None, description="Memory bank identifier")
    max_tokens: int = Field(default=4096, description="Max tokens of recalled context")
    tags: Optional[List[str]] = Field(None, description="Filter memories by tags")


class RecallMemoryResponse(BaseModel):
    bank_id: str
    query: str
    results_count: int
    items: List[MemoryItemSchema] = []
    prompt_context: str = Field(default="", description="Formatted context string ready for LLM prompt injection")


class ReflectMemoryRequest(BaseModel):
    query: str = Field(..., description="Analytical question about organizational history")
    bank_id: Optional[str] = Field(None, description="Memory bank identifier")
    context: Optional[str] = Field(None, description="Optional extra context")
    budget: str = Field(default="low", description="Reflection depth budget ('low', 'mid', 'high')")
    tags: Optional[List[str]] = Field(None, description="Filter reflection by tags")


class ReflectMemoryResponse(BaseModel):
    bank_id: str
    query: str
    answer: str
    based_on: Optional[List[Any]] = None
