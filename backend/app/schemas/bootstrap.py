from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
)


class BootstrapSourceRequest(BaseModel):
    """Configuration options for triggering a historical bootstrap run."""
    document_paths: Optional[List[str]] = Field(
        default=None,
        description="Explicit list of repository file paths to inspect instead of default discovery",
    )
    include_adrs: bool = Field(default=True, description="Discover Architecture Decision Records (docs/adr, adr/, etc.)")
    include_docs: bool = Field(default=True, description="Discover architecture and design docs (docs/architecture.md, etc.)")
    include_guidelines: bool = Field(default=True, description="Discover CONTRIBUTING and guideline files")
    include_prs: bool = Field(default=True, description="Inspect bounded sample of recent merged PR review decisions")
    max_prs: int = Field(default=15, ge=0, le=50, description="Bounded maximum PRs to inspect")
    inferred_conventions: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Observed convention candidates discovered via static analysis or frequency checks",
    )


class BootstrapCandidate(BaseModel):
    """Candidate memory extracted from repository evidence prior to validation and retention."""
    statement: str = Field(..., min_length=5, description="Clear architectural rule or decision statement")
    rationale: str = Field(default="Documented repository precedent.", description="Documented reason or justification")
    scope: str = Field(default="Repository architecture", description="Component, layer, or file path scope")
    type: KnowledgeType = Field(default=KnowledgeType.DOCUMENTED_DECISION)
    authority: MemoryAuthority = Field(default=MemoryAuthority.ENFORCEABLE)
    status: MemoryStatus = Field(default=MemoryStatus.ACTIVE)
    source_type: str = Field(default="adr", description="Source classification: 'adr', 'architecture_doc', 'contributing_guide', 'repo_guidelines', 'pr_review', 'code_convention'")
    source_ref: str = Field(..., min_length=2, description="Traceable source reference (e.g. 'ADR-001', 'CONTRIBUTING.md', 'PR #42')")
    source_url: Optional[str] = None
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    identity_hash: Optional[str] = None


class BootstrapSummaryResponse(BaseModel):
    """Summary of historical bootstrap execution, provenance validation, and retention metrics."""
    repository_id: str
    repository_full_name: str
    sources_discovered: List[str] = Field(default_factory=list)
    candidates_extracted: int = 0
    candidates_retained: int = 0
    candidates_deduplicated: int = 0
    candidates_superseded: int = 0
    candidates_rejected: int = 0
    conflicts_detected: int = 0
    hindsight_retained: int = 0
    hindsight_status: str = Field(default="synced", description="'synced', 'unavailable', or 'failed'")
    hindsight_error: Optional[str] = None
    memories: List[CanonicalMemory] = Field(default_factory=list)
