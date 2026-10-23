"""
Phase 7 — Semantic PR Walkthrough schemas.

Pure data structures derived deterministically from existing PRContext (Phase 5)
and ReviewAnalysisOutput (Phase 6).
Zero new AI inference. Zero new memory retrieval. Zero hallucination surface.
"""
from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class ChangeGroupKind(str, Enum):
    """Describes the architectural role of a logical group of changed files."""
    SCHEMA = "schema"                # DB migrations, model definitions, schema files
    API = "api"                       # Route handlers, endpoint controllers, REST layer
    SERVICE = "service"               # Business logic, use-case services
    TEST = "test"                     # Unit, integration, e2e tests
    CONFIGURATION = "configuration"   # Config files, env, CI/CD, infra
    DOCUMENTATION = "documentation"   # Markdown, ADRs, READMEs
    DEPENDENCY = "dependency"         # lockfiles, pyproject.toml, package.json
    UI = "ui"                         # Frontend components, pages, styles
    TYPES = "types"                   # TypeScript types, schemas, interfaces
    UTILITY = "utility"               # Shared helpers, utils, constants
    FLOW = "flow"                     # Cross-layer semantic feature flow (e.g. controller -> service -> repo)
    UNKNOWN = "unknown"               # Could not classify


class ChangeGroupImpact(str, Enum):
    """Estimated blast radius / architectural impact level."""
    CONTAINED = "contained"          # Isolated to group/module; no external callers or consumers affected
    CROSS_CUTTING = "cross_cutting"  # Structural callers or consumers cross boundary; interfaces modified
    FOUNDATIONAL = "foundational"    # Platform, database schema, or infrastructure with broad downstream blast radius


class SemanticRelationshipType(str, Enum):
    """Verified structural relationship types connecting symbols or files."""
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    REFERENCES = "REFERENCES"
    DEFINES = "DEFINES"
    DEPENDS_ON = "DEPENDS_ON"
    TESTS = "TESTS"
    RELATED_TO = "RELATED_TO"


class SemanticRelationship(BaseModel):
    """A verified structural relationship between two entities supported by Phase 5 evidence."""
    source: str
    target: str
    relationship: SemanticRelationshipType
    detail: Optional[str] = None
    evidence_source: Literal[
        "ast", "dependency_graph", "hunk_call", "hunk_import", "related_test", "related_file"
    ] = "ast"


class GroupTestMapping(BaseModel):
    """Exposes concrete test mapping linked through file paths or symbols."""
    test_file_path: str
    target_symbol_or_file: str
    test_name: Optional[str] = None
    relationship: str = "TESTS"
    is_in_pr_diff: bool = False


class GroupFindingEntry(BaseModel):
    """Structured Phase 6 review finding attached directly to a walkthrough group."""
    id: Optional[str] = None
    category: str
    severity: str
    title: str
    message: str
    file_path: str
    line_number: Optional[int] = None
    historical_pr_ref: Optional[str] = None
    rationale: Optional[str] = None


class HunkSymbolDetail(BaseModel):
    """Detailed symbol modification summary for a specific hunk in a file."""
    file_path: str
    start_line: int
    end_line: int
    enclosing_symbol: Optional[str] = None
    changed_symbols: List[str] = Field(default_factory=list)
    calls: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)


class FileChangeEntry(BaseModel):
    """File-level summary within a walkthrough group."""
    path: str
    status: str = "modified"    # added | modified | removed | renamed
    additions: int = 0
    deletions: int = 0
    language: str = "unknown"
    affected_symbols: List[str] = Field(default_factory=list)
    hunks: List[HunkSymbolDetail] = Field(default_factory=list)
    note: Optional[str] = None   # 1-sentence human-readable observation


class ChangeGroup(BaseModel):
    """
    A logical grouping of related changed files sharing an architectural purpose.

    Derived from Phase 5 structural graph connectivity (calls, imports, references,
    tests, dependencies) with path/extension classification as secondary fallback.
    """
    id: str                          # deterministic stable slug, e.g. "group-0-payment-flow"
    kind: ChangeGroupKind
    label: str                       # Human-readable label
    description: str                 # Summary of what this group does
    purpose: str = ""                # Architectural purpose of this logical change
    observed_implementation: str = "" # Concrete structural changes observed
    files: List[FileChangeEntry] = Field(default_factory=list)
    impact: ChangeGroupImpact = ChangeGroupImpact.CONTAINED
    impact_justification: str = ""   # Structural evidence justifying the impact rating
    symbols_changed: List[str] = Field(default_factory=list)
    relationships: List[SemanticRelationship] = Field(default_factory=list)
    tests: List[GroupTestMapping] = Field(default_factory=list)
    findings: List[GroupFindingEntry] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    related_findings_count: int = 0   # Total count of actual findings touching this group
    has_test_coverage: bool = False
    ordering: int = 0

    # Truncation and bounding metadata
    total_files_count: int = 0
    total_symbols_count: int = 0
    total_relationships_count: int = 0
    truncated_files: int = 0
    truncated_symbols: int = 0
    truncated_relationships: int = 0


class WalkthroughStep(BaseModel):
    """
    A single narrative step in the semantic walkthrough.

    Ordered to tell the architectural story of the PR:
    Foundational/dependencies -> core service flow -> API/interfaces -> UI -> tests.
    """
    step_number: int
    group_id: str
    headline: str
    detail: str
    files_count: int = 0
    impact: ChangeGroupImpact = ChangeGroupImpact.CONTAINED
    primary_symbols: List[str] = Field(default_factory=list)
    key_relationships: List[str] = Field(default_factory=list)


class PRWalkthrough(BaseModel):
    """
    Complete semantic walkthrough for a pull request.

    Derived entirely from existing PRContext produced by the Phase 5 ContextEngine
    and findings produced by the Phase 6 ReviewEngine.
    """
    pr_number: int
    pr_title: str
    repository_id: str
    head_sha: str

    intent_summary: str
    declared_intent: str = ""
    observed_implementation: str = ""
    change_type: str = "UNKNOWN"

    # Ordered groups and narrative steps
    groups: List[ChangeGroup] = Field(default_factory=list)
    steps: List[WalkthroughStep] = Field(default_factory=list)

    # Aggregate stats
    total_files_changed: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    groups_count: int = 0

    # Cross-cutting flags
    has_schema_changes: bool = False
    has_api_changes: bool = False
    has_test_changes: bool = False
    has_config_changes: bool = False

    # Review findings integration
    surfaced_finding_count: int = 0

    # Payload bounding metadata
    is_truncated: bool = False
    truncation_notes: List[str] = Field(default_factory=list)

    # Provenance
    derived_from: str = "context_engine_v5_semantic"
