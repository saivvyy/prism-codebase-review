from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ContextReason(str, Enum):
    DIRECT_CHANGE = "DIRECT_CHANGE"
    DIRECT_DEPENDENCY = "DIRECT_DEPENDENCY"
    DIRECT_CALLER = "DIRECT_CALLER"
    DIRECT_CALLEE = "DIRECT_CALLEE"
    RELATED_TEST = "RELATED_TEST"
    RELATED_DOCUMENT = "RELATED_DOCUMENT"
    RELATED_PR = "RELATED_PR"
    RELATED_ISSUE = "RELATED_ISSUE"
    ARCHITECTURAL_MEMORY = "ARCHITECTURAL_MEMORY"
    APPROVED_EXCEPTION = "APPROVED_EXCEPTION"
    HISTORICAL_PRECEDENT = "HISTORICAL_PRECEDENT"


class ChangeType(str, Enum):
    FEATURE = "FEATURE"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"
    MIGRATION = "MIGRATION"
    PERFORMANCE = "PERFORMANCE"
    SECURITY = "SECURITY"
    TESTING = "TESTING"
    DOCUMENTATION = "DOCUMENTATION"
    DEPENDENCY_UPDATE = "DEPENDENCY_UPDATE"
    CONFIGURATION = "CONFIGURATION"
    UNKNOWN = "UNKNOWN"


class PRIdentity(BaseModel):
    repository_id: str
    repository_full_name: str
    pr_number: int
    title: str
    description: Optional[str] = None
    author: str
    base_sha: Optional[str] = None
    head_sha: str
    changed_files_count: int = 0
    total_additions: int = 0
    total_deletions: int = 0


class PRIntent(BaseModel):
    summary: str
    goals: List[str] = Field(default_factory=list)
    behavioral_change: Optional[str] = None
    affected_domain: Optional[str] = None
    likely_change_type: ChangeType = ChangeType.UNKNOWN


class HunkSymbolMap(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    enclosing_class: Optional[str] = None
    enclosing_function: Optional[str] = None
    changed_symbols: List[str] = Field(default_factory=list)
    changed_calls: List[str] = Field(default_factory=list)
    changed_imports: List[str] = Field(default_factory=list)
    lines_snippet: str = ""


class ChangedFileDetail(BaseModel):
    path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    language: str = "unknown"
    changed_hunks: List[HunkSymbolMap] = Field(default_factory=list)
    affected_symbols: List[str] = Field(default_factory=list)
    classes: List[str] = Field(default_factory=list)
    functions: List[str] = Field(default_factory=list)
    methods: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    calls: List[str] = Field(default_factory=list)


class SymbolDefinitionReference(BaseModel):
    symbol: str
    file_path: str
    definition_type: Literal["class", "function", "method", "variable", "interface", "unknown"] = "unknown"
    callers: List[str] = Field(default_factory=list)
    callees: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    imported_symbols: List[str] = Field(default_factory=list)


class DependencyGraphNode(BaseModel):
    id: str
    label: str
    type: Literal["file", "class", "function", "method", "module", "service", "external"] = "function"
    file_path: Optional[str] = None


class DependencyGraphEdge(BaseModel):
    source: str
    target: str
    relationship: Literal["calls", "imports", "inherits", "depends_on"] = "depends_on"


class CodeDependencyGraph(BaseModel):
    nodes: List[DependencyGraphNode] = Field(default_factory=list)
    edges: List[DependencyGraphEdge] = Field(default_factory=list)


class RelatedFileItem(BaseModel):
    path: str
    reason: ContextReason
    reason_detail: str
    relationship: Optional[str] = None
    snippet: Optional[str] = None


class RelatedTestItem(BaseModel):
    test_file_path: str
    target_symbol_or_file: str
    reason: ContextReason = ContextReason.RELATED_TEST
    exists_in_repo: bool = True
    is_in_pr_diff: bool = False
    snippet: Optional[str] = None


class RelevantDocItem(BaseModel):
    path: str
    title: str
    reason: ContextReason = ContextReason.RELATED_DOCUMENT
    relevance_explanation: str
    excerpt: Optional[str] = None


class RelatedPRItem(BaseModel):
    pr_number: int
    title: str
    reason: ContextReason = ContextReason.RELATED_PR
    relevance_explanation: str
    overlapping_files: List[str] = Field(default_factory=list)
    verdict: Optional[str] = None
    memory_references: List[str] = Field(default_factory=list)
    discovery_sources: List[str] = Field(default_factory=list)


class RelatedIssueItem(BaseModel):
    issue_number: int
    title: str
    reason: ContextReason = ContextReason.RELATED_ISSUE
    relevance_explanation: str
    referenced_in: Literal["pr_title", "pr_body", "commit_message"] = "pr_body"


class RankedMemoryItem(BaseModel):
    memory_id: Optional[str] = None
    claim: str
    rationale: Optional[str] = None
    scope: Optional[str] = None
    category: str
    authority: str
    reason: ContextReason = ContextReason.ARCHITECTURAL_MEMORY
    relevance_score: float = 0.0
    source_pr_ref: Optional[str] = None
    provenance_status: str = "none"


# ---------------------------------------------------------------------------
# Phase 10: Deep PR Evidence & History Models
# ---------------------------------------------------------------------------

class CommitEvidence(BaseModel):
    """Normalized metadata for a single commit within the current pull request."""
    sha: str
    message: str
    author: str
    authored_at: Optional[str] = None
    committed_at: Optional[str] = None
    additions: int = 0
    deletions: int = 0
    changed_files_count: int = 0
    changed_files: List[str] = Field(default_factory=list)
    url: Optional[str] = None

    @property
    def short_sha(self) -> str:
        return self.sha[:7] if self.sha else ""



class ConversationEvidence(BaseModel):
    """Normalized discussion evidence across issue comments, review comments, and reviews."""
    id: str
    author: str
    body: str
    created_at: str
    updated_at: Optional[str] = None
    source_type: Literal["issue_comment", "review_comment", "review"] = "issue_comment"
    url: Optional[str] = None
    file_path: Optional[str] = None
    line: Optional[int] = None
    side: Optional[str] = None
    commit_sha: Optional[str] = None
    resolved: Optional[bool] = None
    review_state: Optional[str] = None


class CheckEvidence(BaseModel):
    """Normalized CI/validation check run or commit status evidence."""
    id: str
    name: str
    status: str
    conclusion: Optional[str] = None
    workflow: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    url: Optional[str] = None


class HistoricalPREvidence(BaseModel):
    """Discovered historical pull request evidence with verified deterministic relevance rationale."""
    repository_full_name: str
    pr_number: int
    title: str
    state: str
    is_merged: bool = False
    relevant_files: List[str] = Field(default_factory=list)
    relevant_symbols: List[str] = Field(default_factory=list)
    relevance_reason: str
    source_url: Optional[str] = None


class PREvidenceSummary(BaseModel):
    """Aggregate metrics and bounded truncation flags for PR deep evidence."""
    total_commits: int = 0
    total_conversation_items: int = 0
    total_checks: int = 0
    checks_success_count: int = 0
    checks_failed_count: int = 0
    checks_pending_count: int = 0
    total_historical_prs: int = 0
    commits_truncated: bool = False
    conversation_truncated: bool = False
    checks_truncated: bool = False
    historical_prs_truncated: bool = False
    checks_breakdown: Dict[str, int] = Field(default_factory=dict)


class PREvidenceResponse(BaseModel):
    """Typed API response containing comprehensive, bounded PR evidence."""
    repository_id: str
    repository_full_name: str = ""
    pr_number: int
    head_sha: str
    commits: List[CommitEvidence] = Field(default_factory=list)
    conversation: List[ConversationEvidence] = Field(default_factory=list)
    checks: List[CheckEvidence] = Field(default_factory=list)
    historical_prs: List[HistoricalPREvidence] = Field(default_factory=list)
    summary: PREvidenceSummary = Field(default_factory=PREvidenceSummary)


class ContextBudget(BaseModel):
    total_allowed_tokens: int = 6000
    used_tokens: int = 0
    truncated: bool = False
    limit_reached: bool = False
    truncated_sections: List[str] = Field(default_factory=list)
    items_removed: Dict[str, int] = Field(default_factory=dict)
    allocations: Dict[str, int] = Field(default_factory=dict)


class PRContext(BaseModel):
    """Complete, structured, and bounded context representation for PR review."""
    pr: PRIdentity
    intent: PRIntent
    changed_files: List[ChangedFileDetail] = Field(default_factory=list)
    changed_hunks: List[HunkSymbolMap] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    definitions: List[SymbolDefinitionReference] = Field(default_factory=list)
    references: List[SymbolDefinitionReference] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    calls: List[str] = Field(default_factory=list)
    dependencies: CodeDependencyGraph = Field(default_factory=CodeDependencyGraph)
    related_files: List[RelatedFileItem] = Field(default_factory=list)
    related_tests: List[RelatedTestItem] = Field(default_factory=list)
    related_prs: List[RelatedPRItem] = Field(default_factory=list)
    related_issues: List[RelatedIssueItem] = Field(default_factory=list)
    relevant_docs: List[RelevantDocItem] = Field(default_factory=list)
    relevant_memories: List[RankedMemoryItem] = Field(default_factory=list)
    context_reasons: List[ContextReason] = Field(default_factory=list)
    context_budget: ContextBudget = Field(default_factory=ContextBudget)

    # Phase 10 Deep Evidence Fields (Bounded, Typed, Deterministic)
    commits: List[CommitEvidence] = Field(default_factory=list)
    conversation: List[ConversationEvidence] = Field(default_factory=list)
    checks: List[CheckEvidence] = Field(default_factory=list)
    historical_prs: List[HistoricalPREvidence] = Field(default_factory=list)
    evidence_summary: PREvidenceSummary = Field(default_factory=PREvidenceSummary)

    def to_formatted_context_block(self) -> str:
        """Serializes the structured context into a clean, bounded markdown block for Groq review."""
        sections: List[str] = []

        # Intent
        sections.append(f"### PR INTENT & CHANGE TYPE\n"
                        f"- Type: {self.intent.likely_change_type.value}\n"
                        f"- Summary: {self.intent.summary}\n"
                        f"- Domain: {self.intent.affected_domain or 'General'}")
        if self.intent.goals:
            sections.append("- Goals:\n" + "\n".join(f"  * {g}" for g in self.intent.goals))

        # P0: Changed Hunks and Structural Symbols
        if self.changed_hunks:
            hunk_lines = ["### AFFECTED STRUCTURAL SYMBOLS & HUNKS:"]
            for h in self.changed_hunks[:15]:
                syms = ", ".join(h.changed_symbols) if h.changed_symbols else "file-level"
                calls = f" (calls: {', '.join(h.changed_calls)})" if h.changed_calls else ""
                encl = ""
                if h.enclosing_class:
                    encl += f" in class {h.enclosing_class}"
                if h.enclosing_function:
                    encl += f" in function {h.enclosing_function}"
                hunk_lines.append(f"- {h.file_path}:{h.start_line}-{h.end_line} -> {syms}{encl}{calls}")
            sections.append("\n".join(hunk_lines))

        # P1: Direct Dependencies & Call Graph
        if self.dependencies.edges:
            dep_lines = ["### DIRECT DEPENDENCIES & CALL GRAPH:"]
            for edge in self.dependencies.edges[:12]:
                dep_lines.append(f"- {edge.source} --[{edge.relationship}]--> {edge.target}")
            sections.append("\n".join(dep_lines))

        # P2: Relevant Tests
        if self.related_tests:
            test_lines = ["### RELEVANT TESTS DISCOVERED:"]
            for t in self.related_tests[:5]:
                status = "in PR diff" if t.is_in_pr_diff else "existing in repository"
                test_lines.append(f"- {t.test_file_path} (tests {t.target_symbol_or_file}, {status})")
            sections.append("\n".join(test_lines))

        # P3 & P4: Authoritative Rules and Approved Exceptions
        rules = [m for m in self.relevant_memories if m.category in ("ARCHITECTURAL_RULE", "APPROVED_EXCEPTION")]
        if rules:
            rule_lines = ["### APPLICABLE ARCHITECTURAL MEMORY:"]
            for r in rules[:6]:
                ref = f" [{r.source_pr_ref}]" if r.source_pr_ref else ""
                rule_lines.append(f"- [{r.category}] {r.claim}{ref} (Scope: {r.scope})")
                if r.rationale:
                    rule_lines.append(f"  Rationale: {r.rationale}")
            sections.append("\n".join(rule_lines))

        # P5: Relevant Historical PRs
        if self.related_prs:
            pr_lines = ["### RELEVANT HISTORICAL PRS:"]
            for p in self.related_prs[:3]:
                src_str = f" [Sources: {', '.join(p.discovery_sources)}]" if p.discovery_sources else ""
                pr_lines.append(f"- PR #{p.pr_number}: {p.title} (Reason: {p.relevance_explanation}{src_str})")
            sections.append("\n".join(pr_lines))

        # P6: Relevant Documentation
        if self.relevant_docs:
            doc_lines = ["### RELEVANT ARCHITECTURAL DOCUMENTATION:"]
            for d in self.relevant_docs[:3]:
                doc_lines.append(f"- {d.title} ({d.path}): {d.relevance_explanation}")
                if d.excerpt:
                    doc_lines.append(f"  Excerpt: {d.excerpt}")
            sections.append("\n".join(doc_lines))

        # P7: Linked Issues
        if self.related_issues:
            iss_lines = ["### LINKED ISSUES:"]
            for iss in self.related_issues[:3]:
                iss_lines.append(f"- Issue #{iss.issue_number}: {iss.title} ({iss.relevance_explanation})")
            sections.append("\n".join(iss_lines))

        # P8: Contextual Observations
        observations = [m for m in self.relevant_memories if m.category == "OBSERVATION"]
        if observations:
            obs_lines = ["### CONTEXTUAL OBSERVATIONS (NON-AUTHORITATIVE):"]
            for o in observations[:4]:
                obs_lines.append(f"- Observation: {o.claim} (Scope: {o.scope})")
            sections.append("\n".join(obs_lines))

        # P9: PR Commit History (Evidence - Not Rules)
        if self.commits:
            commit_lines = ["### PR COMMITS & EVOLUTION (EVIDENCE ONLY):"]
            for c in self.commits[:4]:
                short_sha = c.sha[:7] if c.sha else "unknown"
                first_line = c.message.split("\n")[0] if c.message else "no message"
                commit_lines.append(f"- [{short_sha}] {first_line} (by @{c.author})")
            sections.append("\n".join(commit_lines))

        # P10: PR Conversation Highlights (Evidence - Not Rules)
        if self.conversation:
            conv_lines = ["### PR DISCUSSION & REVIEW HIGHLIGHTS (EVIDENCE ONLY):"]
            for cv in self.conversation[:3]:
                body_clean = cv.body.strip().replace("\n", " ")[:120] if cv.body else ""
                loc = f" at {cv.file_path}:{cv.line}" if cv.file_path and cv.line else ""
                conv_lines.append(f"- @{cv.author} ({cv.source_type}{loc}): {body_clean}")
            sections.append("\n".join(conv_lines))

        # P11: CI / Checks Status (Validation Evidence Only)
        if self.checks:
            check_lines = ["### CI / VALIDATION EVIDENCE:"]
            for ck in self.checks[:4]:
                status_str = f"status: {ck.status}" + (f", conclusion: {ck.conclusion}" if ck.conclusion else "")
                check_lines.append(f"- {ck.name} ({status_str})")
            sections.append("\n".join(check_lines))

        return "\n\n".join(sections)
