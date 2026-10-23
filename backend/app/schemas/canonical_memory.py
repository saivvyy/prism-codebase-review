from datetime import datetime, timezone
from enum import Enum
import fnmatch
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, model_validator


class KnowledgeType(str, Enum):
    DOCUMENTED_DECISION = "DOCUMENTED_DECISION"
    APPROVED_EXCEPTION = "APPROVED_EXCEPTION"
    REJECTED_PATTERN = "REJECTED_PATTERN"
    OBSERVED_CONVENTION = "OBSERVED_CONVENTION"
    REVIEW_OBSERVATION = "REVIEW_OBSERVATION"


class MemoryAuthority(str, Enum):
    ENFORCEABLE = "ENFORCEABLE"
    EXCEPTION = "EXCEPTION"
    REJECTED_PATTERN = "REJECTED_PATTERN"
    CONTEXTUAL = "CONTEXTUAL"
    NON_AUTHORITATIVE = "NON_AUTHORITATIVE"


class MemoryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class MemorySyncState(str, Enum):
    SYNCED = "SYNCED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class ReviewVerdict(str, Enum):
    NO_PRECEDENT = "NO_PRECEDENT"
    CONFORMING = "CONFORMING"
    DRIFT = "DRIFT"
    APPROVED_EXCEPTION = "APPROVED_EXCEPTION"
    CONFLICTING_GUIDANCE = "CONFLICTING_GUIDANCE"


def normalize_knowledge_type(val: Union[str, KnowledgeType]) -> KnowledgeType:
    """Normalizes knowledge types including legacy formats."""
    if isinstance(val, KnowledgeType):
        return val
    cleaned = str(val or "").strip().upper()
    if cleaned in KnowledgeType.__members__:
        return KnowledgeType[cleaned]
    lowered = str(val or "").strip().lower()
    mapping = {
        "architecture_decision": KnowledgeType.DOCUMENTED_DECISION,
        "architectural_decision": KnowledgeType.DOCUMENTED_DECISION,
        "documented_decision": KnowledgeType.DOCUMENTED_DECISION,
        "approved_exception": KnowledgeType.APPROVED_EXCEPTION,
        "rejected_pattern": KnowledgeType.REJECTED_PATTERN,
        "architecture_drift": KnowledgeType.REJECTED_PATTERN,
        "observed_convention": KnowledgeType.OBSERVED_CONVENTION,
        "convention_violation": KnowledgeType.OBSERVED_CONVENTION,
        "convention": KnowledgeType.OBSERVED_CONVENTION,
        "lesson_learned": KnowledgeType.OBSERVED_CONVENTION,
        "review_decision": KnowledgeType.OBSERVED_CONVENTION,
        "review_observation": KnowledgeType.REVIEW_OBSERVATION,
        "note": KnowledgeType.REVIEW_OBSERVATION,
    }
    return mapping.get(lowered, KnowledgeType.OBSERVED_CONVENTION)


def get_default_authority(k_type: KnowledgeType) -> MemoryAuthority:
    """Returns authority level for a given knowledge type."""
    mapping = {
        KnowledgeType.DOCUMENTED_DECISION: MemoryAuthority.ENFORCEABLE,
        KnowledgeType.APPROVED_EXCEPTION: MemoryAuthority.EXCEPTION,
        KnowledgeType.REJECTED_PATTERN: MemoryAuthority.REJECTED_PATTERN,
        KnowledgeType.OBSERVED_CONVENTION: MemoryAuthority.CONTEXTUAL,
        KnowledgeType.REVIEW_OBSERVATION: MemoryAuthority.NON_AUTHORITATIVE,
    }
    return mapping.get(k_type, MemoryAuthority.NON_AUTHORITATIVE)


def normalize_status(val: Union[str, MemoryStatus]) -> MemoryStatus:
    """Normalizes memory status."""
    if isinstance(val, MemoryStatus):
        return val
    cleaned = str(val or "").strip().upper()
    if cleaned in MemoryStatus.__members__:
        return MemoryStatus[cleaned]
    lowered = str(val or "").strip().lower()
    if lowered in ("active", "enabled"):
        return MemoryStatus.ACTIVE
    if lowered in ("superseded", "replaced"):
        return MemoryStatus.SUPERSEDED
    if lowered in ("revoked", "deprecated", "deleted", "disabled"):
        return MemoryStatus.REVOKED
    return MemoryStatus.ACTIVE


def normalize_claim_or_statement(text: str) -> str:
    """Strips formatting noise, prefixes, and normalizes statement string."""
    cleaned = text.strip()
    cleaned = re.sub(r"^In\s+(?:the\s+)?[\w\-]+/[\w\-]+(?:\s+repository)?,\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Rule:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Type:[^\n]+\n+Claim:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Claim:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Statement:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def compute_memory_identity(
    repository_id: str,
    statement: str,
    scope: Optional[str] = None,
    knowledge_type: Optional[Union[str, KnowledgeType]] = None,
) -> str:
    """Computes a deterministic identity hash for deduplication within a repository.
    Includes repository_id, normalized statement, normalized scope, and optional knowledge type.
    """
    norm_statement = normalize_claim_or_statement(statement).lower()
    norm_scope = (scope or "").strip().lower()
    if knowledge_type:
        k_type_str = normalize_knowledge_type(knowledge_type).value
        content = f"{repository_id.strip()}::{norm_statement}::{norm_scope}::{k_type_str}"
    else:
        content = f"{repository_id.strip()}::{norm_statement}::{norm_scope}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_provenance(mem_dict: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validates that a memory possesses real, structured provenance.
    Returns (is_valid, failure_reason).
    """
    repo_id = mem_dict.get("repository_id")
    if not repo_id or str(repo_id).strip() == "":
        return False, "Missing repository_id"

    source_ref = str(mem_dict.get("source_ref") or "").strip()
    pr_number = mem_dict.get("source_pr_number") or mem_dict.get("pr_number")
    source_url = str(mem_dict.get("source_url") or "").strip()
    source_commit = str(mem_dict.get("source_commit_sha") or "").strip()

    # Disallowed fake/placeholder provenance patterns
    fake_sources = {
        "hindsight",
        "hindsight memory",
        "repository decision",
        "simulated",
        "none",
        "undefined",
        "null",
        "fake",
        "unknown",
        "dummy",
        "test",
        "placeholder",
        "n/a",
        "na",
    }
    if source_ref.lower() in fake_sources:
        return False, f"Source reference '{source_ref}' is fabricated or placeholder"

    if source_commit and source_commit.lower() in fake_sources:
        return False, f"Commit SHA '{source_commit}' is fabricated or placeholder"

    if source_url and source_url.lower() in fake_sources:
        return False, f"Source URL '{source_url}' is fabricated or placeholder"

    # Only non-placeholder values qualify as real provenance
    has_valid_ref = bool(source_ref and source_ref.lower() not in fake_sources)
    has_valid_pr = bool(pr_number is not None and str(pr_number).isdigit() and int(pr_number) > 0)
    has_valid_commit = bool(source_commit and source_commit.lower() not in fake_sources)
    has_valid_url = bool(source_url and source_url.lower() not in fake_sources)

    if not (has_valid_ref or has_valid_pr or has_valid_commit or has_valid_url):
        return False, "No real provenance (source_ref, source_pr_number, source_commit_sha, or source_url) provided"

    return True, None


class CanonicalMemory(BaseModel):
    """Canonical domain model for durable engineering memory with structured provenance."""
    memory_id: str = Field(..., description="Unique UUID for this memory")
    repository_id: str = Field(..., description="Repository UUID")
    repository_full_name: str = Field(default="unknown/repo", description="e.g. 'owner/repo'")
    type: KnowledgeType = Field(..., description="Canonical knowledge classification")
    authority: MemoryAuthority = Field(..., description="Enforceable semantics")
    statement: str = Field(..., min_length=5, description="Clear architectural rule or decision statement")
    rationale: str = Field(..., description="Documented reason or justification")
    scope: str = Field(..., description="Path pattern, component, or architectural layer boundary")
    status: MemoryStatus = Field(default=MemoryStatus.ACTIVE, description="Lifecycle status")
    source_type: str = Field(default="manual", description="e.g. 'pr_review', 'manual', 'team_agreement'")
    source_ref: str = Field(..., description="Traceable source reference (e.g. 'PR #142')")
    source_url: Optional[str] = Field(default=None, description="Direct URL to source PR or document")
    source_pr_number: Optional[int] = Field(default=None, description="GitHub PR number if applicable")
    source_commit_sha: Optional[str] = Field(default=None, description="Commit SHA if applicable")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    identity_hash: Optional[str] = Field(default=None, description="Deterministic identity hash for deduplication")
    supersedes: Optional[str] = Field(default=None, description="UUID of previous memory superseded by this one")
    superseded_by: Optional[str] = Field(default=None, description="UUID of new memory that supersedes this one")
    valid_from: Optional[str] = Field(default=None, description="ISO timestamp from which memory is valid")
    valid_until: Optional[str] = Field(default=None, description="ISO timestamp until which memory is valid (expiry)")
    sync_state: MemorySyncState = Field(default=MemorySyncState.SYNCED, description="Hindsight sync state: SYNCED, UNAVAILABLE, FAILED")
    observation_count: int = Field(default=1, description="Number of times this knowledge was observed or reinforced")
    first_observed_at: Optional[str] = Field(default=None, description="ISO timestamp of first observation")
    last_observed_at: Optional[str] = Field(default=None, description="ISO timestamp of latest observation or reinforcement")

    # Compatibility fields / aliases for existing consumers
    id: Optional[str] = None
    claim: Optional[str] = None
    pr_number: Optional[int] = None
    confidence: str = "supported"
    evidence_items: List[str] = Field(default_factory=list)
    hindsight_document_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def populate_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        # Normalize statement/claim
        statement = values.get("statement") or values.get("claim") or ""
        values["statement"] = normalize_claim_or_statement(statement)
        values["claim"] = values["statement"]

        # Normalize memory_id/id
        mem_id = values.get("memory_id") or values.get("id") or ""
        values["memory_id"] = str(mem_id)
        values["id"] = values["memory_id"]

        # Normalize knowledge type & authority
        k_type = normalize_knowledge_type(values.get("type", "DOCUMENTED_DECISION"))
        values["type"] = k_type
        if not values.get("authority"):
            values["authority"] = get_default_authority(k_type)
        elif isinstance(values["authority"], str):
            auth_str = values["authority"].strip().upper()
            values["authority"] = MemoryAuthority[auth_str] if auth_str in MemoryAuthority.__members__ else get_default_authority(k_type)

        # Normalize status
        values["status"] = normalize_status(values.get("status", "ACTIVE"))

        # Normalize PR number
        pr_num = values.get("source_pr_number") or values.get("pr_number")
        if pr_num is not None:
            try:
                values["source_pr_number"] = int(pr_num)
                values["pr_number"] = int(pr_num)
            except (ValueError, TypeError):
                pass

        # Ensure source_ref
        if not values.get("source_ref"):
            pr_num = values.get("source_pr_number") or values.get("pr_number")
            values["source_ref"] = f"PR #{pr_num}" if pr_num else "manual"

        # Ensure evidence_items
        evidence = list(values.get("evidence_items") or [])
        source_ref = values.get("source_ref")
        if source_ref and source_ref not in evidence and "hindsight" not in source_ref.lower():
            evidence.append(source_ref)
        values["evidence_items"] = evidence

        # Ensure scope, rationale, and repository_full_name
        if not values.get("repository_full_name"):
            values["repository_full_name"] = str(values.get("repository_id") or "unknown/repo")
        if not values.get("scope"):
            values["scope"] = "Repository architecture"
        if not values.get("rationale"):
            values["rationale"] = "Documented engineering knowledge precedent."

        # Compute deterministic identity hash if missing
        repo_id = values.get("repository_id")
        if not values.get("identity_hash") and repo_id and values.get("statement"):
            values["identity_hash"] = compute_memory_identity(
                repository_id=str(repo_id),
                statement=values["statement"],
                scope=values.get("scope"),
                knowledge_type=values.get("type"),
            )

        # Normalize sync_state
        raw_sync = values.get("sync_state") or "SYNCED"
        if isinstance(raw_sync, MemorySyncState):
            values["sync_state"] = raw_sync
        else:
            cleaned_sync = str(raw_sync).strip().upper()
            values["sync_state"] = MemorySyncState[cleaned_sync] if cleaned_sync in MemorySyncState.__members__ else MemorySyncState.SYNCED

        # Normalize observation count and observation timestamps
        values["observation_count"] = int(values.get("observation_count") or 1)
        if not values.get("first_observed_at"):
            values["first_observed_at"] = values.get("created_at") or datetime.now(timezone.utc).isoformat()
        if not values.get("last_observed_at"):
            values["last_observed_at"] = values.get("updated_at") or values["first_observed_at"]

        return values

    def is_applicable_to_path(self, file_path: str, structural_symbols: Optional[List[str]] = None) -> bool:
        """Determines whether this memory's scope covers the given file path or structural symbols."""
        if not self.scope or self.scope in ("Repository architecture", "*", "all", "repository-wide", "global"):
            # If the statement specifically calls out a specific file path, ensure it matches
            full_text = f"{self.statement} {self.rationale or ''}"
            file_mentions = re.findall(r"[\w\-\./]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|c|h|rb|php|cs|swift|kt|yaml|yml|json|md)", full_text)
            if file_mentions:
                # If explicit file paths are named in the memory statement, it is only applicable
                # if the target file matches one of the mentioned files or directories
                if any(fm in file_path or file_path in fm for fm in file_mentions):
                    return True
                # If structural symbols match, permit
                if structural_symbols:
                    for sym in structural_symbols:
                        if any(fm in sym or sym in fm for fm in file_mentions):
                            return True
                return False
            return True
        scope_pattern = self.scope.strip()
        # Handle comma or space separated scopes
        sub_scopes = [s.strip() for s in re.split(r"[,;]\s*", scope_pattern) if s.strip()]
        for pattern in sub_scopes:
            if fnmatch.fnmatch(file_path, pattern):
                return True
            if pattern.endswith("/*") and file_path.startswith(pattern[:-2]):
                return True
            if pattern.lower() in file_path.lower():
                return True
            # Also check structural symbols (class names, function names, calls)
            if structural_symbols:
                for sym in structural_symbols:
                    if fnmatch.fnmatch(sym, pattern) or pattern.lower() in sym.lower() or sym.lower() in pattern.lower():
                        return True
        return False

    def is_temporally_valid(self, at_time: Optional[Union[datetime, str]] = None) -> Tuple[bool, Optional[str]]:
        """Validates whether this memory is active at the specified timestamp."""
        if not self.valid_from and not self.valid_until:
            return True, None

        target_dt: datetime
        if at_time is None:
            target_dt = datetime.now(timezone.utc)
        elif isinstance(at_time, str):
            try:
                target_dt = datetime.fromisoformat(at_time.replace("Z", "+00:00"))
            except Exception:
                target_dt = datetime.now(timezone.utc)
        else:
            target_dt = at_time

        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)

        if self.valid_from:
            try:
                from_dt = datetime.fromisoformat(self.valid_from.replace("Z", "+00:00"))
                if from_dt.tzinfo is None:
                    from_dt = from_dt.replace(tzinfo=timezone.utc)
                if target_dt < from_dt:
                    return False, f"Guidance is not yet valid (valid_from: {self.valid_from})"
            except Exception:
                pass

        if self.valid_until:
            try:
                until_dt = datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
                if until_dt.tzinfo is None:
                    until_dt = until_dt.replace(tzinfo=timezone.utc)
                if target_dt > until_dt:
                    return False, f"Guidance has expired (valid_until: {self.valid_until})"
            except Exception:
                pass

        return True, None

    def can_enforce(self) -> bool:
        """Returns True if this memory is active and carries enforceable authority."""
        return self.status == MemoryStatus.ACTIVE and self.authority in (
            MemoryAuthority.ENFORCEABLE,
            MemoryAuthority.REJECTED_PATTERN,
        )

    def can_cause_drift(self) -> bool:
        """Only ACTIVE memories with ENFORCEABLE or REJECTED_PATTERN authority can cause DRIFT.
        OBSERVED_CONVENTION and REVIEW_OBSERVATION cannot independently cause DRIFT.
        Requires valid structured provenance.
        """
        if self.status != MemoryStatus.ACTIVE:
            return False
        if self.authority not in (MemoryAuthority.ENFORCEABLE, MemoryAuthority.REJECTED_PATTERN):
            return False
        valid_prov, _ = validate_provenance(self.model_dump())
        return valid_prov

    def is_approved_exception(self) -> bool:
        """Returns True if this memory represents an active approved exception."""
        return self.status == MemoryStatus.ACTIVE and self.authority == MemoryAuthority.EXCEPTION

    def supersede(self, new_memory_id: str) -> None:
        """Transitions this memory to SUPERSEDED status."""
        self.status = MemoryStatus.SUPERSEDED
        self.superseded_by = new_memory_id
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def revoke(self) -> None:
        """Transitions this memory to REVOKED status."""
        self.status = MemoryStatus.REVOKED
        self.updated_at = datetime.now(timezone.utc).isoformat()


def is_valid_exception_scope(scope: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Determines whether a supplied scope is a valid narrow/bounded exception scope.
    Rejects universal, root, or unbounded scopes such as '*', '/', '**', '.', './', '/*', 'all', 'global'.
    """
    if not scope or not str(scope).strip():
        return False, "Scope cannot be empty or whitespace"

    clean = str(scope).strip().replace("\\", "/")
    lower = clean.lower()

    # Disallowed universal / repository-wide scopes
    universal_scopes = {
        "*",
        "/*",
        "**",
        "/**",
        ".",
        "./",
        "./*",
        ".\\",
        "/",
        "all",
        "global",
        "repository",
        "entire repository",
        "repository architecture",
        "repo",
    }
    if lower in universal_scopes:
        return False, f"Scope '{scope}' represents a global/repository-wide scope and cannot be used for an exception"

    stripped = lower.lstrip("./").lstrip("/")
    if stripped in {"", "*", "**", "*.*", "all"}:
        return False, f"Scope '{scope}' resolves to root wildcard and cannot be used for an exception"

    if clean.startswith("/*") or clean.startswith("/**"):
        return False, f"Scope '{scope}' represents an unbounded root wildcard"

    return True, None


def detect_architectural_conflict(
    candidate_stmt: str,
    candidate_scope: str,
    active_stmt: str,
    active_scope: str,
) -> bool:
    """Determines deterministically whether candidate guidance contradicts active guidance.
    Identifies:
    - Scope overlap vs disjointness
    - Subject / domain match vs separation
    - Target / concept match
    - Opposing polarity / conflicting modal language
    """
    c_stmt = (candidate_stmt or "").strip().lower()
    a_stmt = (active_stmt or "").strip().lower()

    if not c_stmt or not a_stmt:
        return False

    # 1. Scope overlap check
    c_sc = (candidate_scope or "").strip().lower().replace("\\", "/").rstrip("/*")
    a_sc = (active_scope or "").strip().lower().replace("\\", "/").rstrip("/*")

    universal = {"*", "", "repository architecture", "global", "all"}
    c_is_univ = c_sc in universal
    a_is_univ = a_sc in universal

    if not c_is_univ and not a_is_univ:
        if c_sc != a_sc and not c_sc.startswith(a_sc) and not a_sc.startswith(c_sc):
            scope_keywords = ["controller", "service", "migration", "test", "model", "resolver", "route"]
            c_domain = [k for k in scope_keywords if k in c_sc]
            a_domain = [k for k in scope_keywords if k in a_sc]
            if c_domain and a_domain and set(c_domain).isdisjoint(set(a_domain)):
                return False

    # Check if subjects within the statements are explicitly disjoint
    stmt_domains = ["controller", "migration", "test", "service", "resolver", "middleware"]
    c_stmt_dom = [d for d in stmt_domains if d in c_stmt]
    a_stmt_dom = [d for d in stmt_domains if d in a_stmt]
    if c_stmt_dom and a_stmt_dom and set(c_stmt_dom).isdisjoint(set(a_stmt_dom)):
        return False

    # 2. Extract shared concept / target
    concept_clusters = [
        {"db", "database", "persistence", "sql", "datastore", "query db", "direct db", "direct database", "direct persistence"},
        {"service", "service layer", "services"},
        {"tls 1.2", "tls 1.3", "tls"},
        {"authorization", "auth header", "cookie", "session"},
    ]

    shared_concept = False
    for cluster in concept_clusters:
        c_matches = any(k in c_stmt for k in cluster)
        a_matches = any(k in a_stmt for k in cluster)
        if c_matches and a_matches:
            shared_concept = True
            break

    ignore_tokens = {
        "must", "not", "never", "should", "shall", "cannot", "can", "may",
        "always", "use", "avoid", "allow", "allowed", "prohibited", "forbidden",
        "disallowed", "the", "a", "an", "in", "to", "for", "with", "is", "are",
        "be", "all", "each", "every", "directly", "direct"
    }
    c_tokens = set(c_stmt.replace(".", " ").replace(",", " ").split()) - ignore_tokens
    a_tokens = set(a_stmt.replace(".", " ").replace(",", " ").split()) - ignore_tokens
    subject_tokens = {
        "controllers", "controller", "handlers", "handler", "resolvers",
        "resolver", "routes", "route", "endpoints", "endpoint", "classes",
        "methods", "functions", "files", "scripts", "components"
    }
    c_target_tokens = c_tokens - subject_tokens
    a_target_tokens = a_tokens - subject_tokens

    persistence_words = {"db", "database", "persistence", "sql", "datastore", "storage"}
    service_words = {"service", "services", "service layer"}

    c_has_persist = any(w in c_stmt for w in persistence_words)
    a_has_persist = any(w in a_stmt for w in persistence_words)
    c_has_service = any(w in c_stmt for w in service_words)
    a_has_service = any(w in a_stmt for w in service_words)

    # Complementary rules: One prescribes service layer, other proscribes direct persistence
    if (c_has_service and not c_has_persist and a_has_persist and not a_has_service):
        return False
    if (a_has_service and not a_has_persist and c_has_persist and not c_has_service):
        return False

    if not shared_concept and len(c_target_tokens.intersection(a_target_tokens)) >= 1:
        shared_concept = True

    if not shared_concept and not (c_has_persist and a_has_persist):
        return False

    # 3. Direct access specific check
    c_direct = ("direct" in c_stmt or "directly" in c_stmt)
    a_direct = ("direct" in a_stmt or "directly" in a_stmt)

    # 4. Modality / Polarity Analysis
    neg_indicators = [
        "must not", "must never", "shall not", "should not", "may not",
        "cannot", "prohibited", "forbidden", "disallowed", "avoid",
        "never", "not allow", "not permitted", "do not"
    ]
    pos_indicators = [
        "must", "shall", "should", "required", "allowed", "permitted",
        "may", "can", "always", "allow"
    ]

    c_is_neg = any(p in c_stmt for p in neg_indicators)
    a_is_neg = any(p in a_stmt for p in neg_indicators)

    if c_is_neg != a_is_neg:
        if (c_direct and a_direct) or (c_direct and any(k in a_stmt for k in ["query", "access", "call", "invoke"])) or (a_direct and any(k in c_stmt for k in ["query", "access", "call", "invoke"])):
            return True

        if c_has_persist and a_has_persist:
            return True

        if len(c_target_tokens.intersection(a_target_tokens)) >= 1:
            return True

    # 5. Check direct opposite pairs
    opposites = [
        ("must use", "must not use"),
        ("must use", "avoid"),
        ("required", "forbidden"),
        ("prohibited", "allowed"),
        ("disallowed", "allowed"),
        ("forbidden", "permitted"),
        ("never", "always"),
        ("not allow", "allow"),
    ]
    for pos, neg in opposites:
        if (pos in c_stmt and neg in a_stmt) or (neg in c_stmt and pos in a_stmt):
            return True

    return False


def check_exception_scope(exception_mem: Union[CanonicalMemory, Dict[str, Any]], target_path: str) -> bool:
    """Checks whether an approved exception memory has valid scope covering target_path."""
    if isinstance(exception_mem, CanonicalMemory):
        mem_obj = exception_mem
    else:
        try:
            mem_obj = CanonicalMemory(**exception_mem)
        except Exception:
            return False

    if mem_obj.status != MemoryStatus.ACTIVE:
        return False
    if mem_obj.authority != MemoryAuthority.EXCEPTION:
        return False

    return mem_obj.is_applicable_to_path(target_path)


def validate_approved_exception(
    exception_mem: Union[CanonicalMemory, Dict[str, Any]],
    target_path: Optional[str] = None,
    parent_rule: Optional[Union[CanonicalMemory, Dict[str, Any]]] = None,
) -> Tuple[bool, Optional[str]]:
    """Validates that an item is a valid APPROVED_EXCEPTION per product rules:
    - Explicit exception type and authority
    - Active status
    - Valid provenance
    - Matching scope (when target_path provided)
    - Valid narrow scope (not global/universal)
    - Valid parent/general rule when applicable
    """
    if isinstance(exception_mem, CanonicalMemory):
        mem_obj = exception_mem
    else:
        try:
            mem_obj = CanonicalMemory(**exception_mem)
        except Exception as e:
            return False, f"Invalid exception memory model: {e}"

    if mem_obj.type != KnowledgeType.APPROVED_EXCEPTION:
        return False, f"Memory type is '{mem_obj.type}', expected 'APPROVED_EXCEPTION'"

    if mem_obj.authority != MemoryAuthority.EXCEPTION:
        return False, f"Memory authority is '{mem_obj.authority}', expected 'EXCEPTION'"

    if mem_obj.status != MemoryStatus.ACTIVE:
        return False, f"Exception memory status is '{mem_obj.status}', expected 'ACTIVE'"

    is_prov_valid, prov_reason = validate_provenance(mem_obj.model_dump())
    if not is_prov_valid:
        return False, f"Invalid provenance for exception: {prov_reason}"

    is_scope_valid, scope_err = is_valid_exception_scope(mem_obj.scope)
    if not is_scope_valid:
        return False, f"Invalid exception scope: {scope_err}"

    if target_path and not mem_obj.is_applicable_to_path(target_path):
        return False, f"Exception scope '{mem_obj.scope}' does not cover target path '{target_path}'"

    if parent_rule:
        p_obj = parent_rule if isinstance(parent_rule, CanonicalMemory) else CanonicalMemory(**parent_rule)
        if not p_obj.can_enforce():
            return False, f"Parent rule '{p_obj.memory_id}' is not an active enforceable rule"

    return True, None


class RecordCanonicalMemoryRequest(BaseModel):
    statement: str = Field(..., min_length=5, description="Clear architectural rule or decision statement")
    type: KnowledgeType = Field(default=KnowledgeType.DOCUMENTED_DECISION)
    authority: Optional[MemoryAuthority] = None
    rationale: str = Field(..., min_length=3, description="Documented reason or justification")
    scope: str = Field(default="Repository architecture", description="Component, layer, or file path scope")
    source_type: str = Field(default="manual")
    source_ref: str = Field(..., min_length=2, description="Real source reference (e.g. 'PR #142', 'ADR-004')")
    source_url: Optional[str] = None
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None
    supersedes: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class SupersedeMemoryRequest(BaseModel):
    new_statement: str = Field(..., min_length=5)
    rationale: str = Field(..., min_length=3)
    scope: Optional[str] = None
    source_ref: str = Field(..., min_length=2)
    source_url: Optional[str] = None
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None


class RevokeMemoryRequest(BaseModel):
    reason: Optional[str] = Field(default=None, description="Reason for revoking this memory")


class ListCanonicalMemoriesResponse(BaseModel):
    repository_id: str
    repository_full_name: Optional[str] = None
    memories: List[CanonicalMemory]
    total_count: int


class FeedbackType(str, Enum):
    """Semantic developer feedback types defined in Phase 9."""
    CORRECT = "CORRECT"
    NOT_A_VIOLATION = "NOT_A_VIOLATION"
    APPROVED_EXCEPTION = "APPROVED_EXCEPTION"
    ARCHITECTURE_CHANGED = "ARCHITECTURE_CHANGED"
    REMEMBER_THIS_DECISION = "REMEMBER_THIS_DECISION"


class CandidateStatus(str, Enum):
    """Lifecycle states of a learning candidate through the controlled pipeline."""
    DRAFT = "DRAFT"
    PENDING_VALIDATION = "PENDING_VALIDATION"
    VALIDATED = "VALIDATED"
    CONFLICTING = "CONFLICTING"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    PERSISTED = "PERSISTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class LearningCandidate(BaseModel):
    """Auditable, typed learning candidate model for Phase 9 controlled learning."""
    candidate_id: str = Field(default_factory=lambda: str(hashlib.sha256(str(datetime.now(timezone.utc).timestamp()).encode()).hexdigest()[:16]), description="Unique identifier for learning candidate")
    repository_id: str = Field(..., description="Target repository ID (must be strictly scoped)")
    repository_full_name: str = Field(..., description="e.g. 'owner/repo'")
    pr_number: Optional[int] = Field(default=None, description="Source PR number")
    review_id: Optional[str] = Field(default=None, description="Associated review ID")
    commit_sha: Optional[str] = Field(default=None, description="Verified commit SHA")
    feedback_type: Optional[FeedbackType] = Field(default=None, description="Type of feedback that initiated this candidate")
    statement: str = Field(..., min_length=5, description="Clear architectural rule or decision statement")
    rationale: str = Field(..., min_length=3, description="Documented reason or justification")
    proposed_memory_type: KnowledgeType = Field(default=KnowledgeType.OBSERVED_CONVENTION, description="Proposed canonical knowledge classification")
    proposed_authority: MemoryAuthority = Field(default=MemoryAuthority.NON_AUTHORITATIVE, description="Proposed enforceable semantics")
    scope: str = Field(..., description="Path pattern, component, or architectural layer boundary")
    evidence_refs: List[str] = Field(default_factory=list, description="Extracted structural evidence references")
    source_refs: List[str] = Field(default_factory=list, description="Traceable provenance references")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Structured provenance details (PR, SHA, author, URL)")
    conflicting_memory_ids: List[str] = Field(default_factory=list, description="IDs of competing or conflicting active memories")
    supersedes_memory_ids: List[str] = Field(default_factory=list, description="IDs of memories that this candidate explicitly supersedes")
    status: CandidateStatus = Field(default=CandidateStatus.DRAFT, description="Current lifecycle status")
    validation_reasons: List[str] = Field(default_factory=list, description="Log of validation checks or failure reasons")
    persisted_memory_id: Optional[str] = Field(default=None, description="ID of canonical memory created upon persistence")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Backward compatibility fields
    source_type: str = Field(default="pr_review", description="e.g. 'pr_review', 'adr', 'contributing'")
    source_ref: Optional[str] = Field(default=None, description="Traceable source reference (e.g. 'PR #142')")
    source_url: Optional[str] = None
    source_pr_number: Optional[int] = None
    source_commit_sha: Optional[str] = None
    confidence: str = Field(default="supported")
    evidence_items: List[str] = Field(default_factory=list)
    supersedes: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    is_explicit_decision: bool = False
    observation_count: int = 1
    raw_type: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def sync_compat_fields(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        # Normalize statement
        if "statement" in values:
            values["statement"] = normalize_claim_or_statement(values["statement"])

        # Sync candidate_id
        if not values.get("candidate_id"):
            values["candidate_id"] = str(uuid.uuid4()) if "uuid" in globals() else hashlib.sha256(str(datetime.now(timezone.utc).timestamp()).encode()).hexdigest()[:16]

        # Sync types
        if "type" in values and "proposed_memory_type" not in values:
            values["proposed_memory_type"] = normalize_knowledge_type(values["type"])
        elif "proposed_memory_type" in values:
            values["proposed_memory_type"] = normalize_knowledge_type(values["proposed_memory_type"])

        # Sync authority
        if "authority" in values and "proposed_authority" not in values:
            auth_val = values["authority"]
            values["proposed_authority"] = MemoryAuthority[auth_val] if isinstance(auth_val, str) and auth_val in MemoryAuthority.__members__ else auth_val
        elif "proposed_authority" in values and isinstance(values["proposed_authority"], str):
            pauth = values["proposed_authority"].strip().upper()
            values["proposed_authority"] = MemoryAuthority[pauth] if pauth in MemoryAuthority.__members__ else MemoryAuthority.NON_AUTHORITATIVE

        # Sync PR number and SHA
        pr_num = values.get("pr_number") or values.get("source_pr_number")
        if pr_num is not None:
            try:
                values["pr_number"] = int(pr_num)
                values["source_pr_number"] = int(pr_num)
            except (ValueError, TypeError):
                pass

        sha = values.get("commit_sha") or values.get("source_commit_sha")
        if sha:
            values["commit_sha"] = sha
            values["source_commit_sha"] = sha

        # Sync source_ref
        if not values.get("source_ref") and values.get("pr_number"):
            values["source_ref"] = f"PR #{values['pr_number']}"

        # Sync evidence
        evidence = list(values.get("evidence_refs") or values.get("evidence_items") or [])
        if values.get("source_ref") and values["source_ref"] not in evidence:
            evidence.append(values["source_ref"])
        values["evidence_refs"] = evidence
        values["evidence_items"] = evidence

        # Sync supersedes
        if values.get("supersedes") and not values.get("supersedes_memory_ids"):
            values["supersedes_memory_ids"] = [values["supersedes"]]
        elif values.get("supersedes_memory_ids") and not values.get("supersedes"):
            values["supersedes"] = values["supersedes_memory_ids"][0]

        return values

    @property
    def type(self) -> KnowledgeType:
        return self.proposed_memory_type

    @property
    def authority(self) -> MemoryAuthority:
        return self.proposed_authority


class DeveloperFeedbackRequest(BaseModel):
    """Developer feedback payload submitted via PRism review workspace."""
    feedback_type: FeedbackType = Field(..., description="CORRECT, NOT_A_VIOLATION, APPROVED_EXCEPTION, ARCHITECTURE_CHANGED, REMEMBER_THIS_DECISION")
    finding_id: Optional[str] = Field(default=None, description="ID of the review finding being acted upon")
    finding_title: Optional[str] = Field(default=None, description="Title of the review finding")
    statement: Optional[str] = Field(default=None, description="Custom or refined rule statement")
    rationale: Optional[str] = Field(default=None, description="Explanation or justification")
    scope: Optional[str] = Field(default=None, description="Target scope pattern or component")
    parent_rule_id: Optional[str] = Field(default=None, description="Parent general rule ID when proposing an exception")
    supersedes_rule_id: Optional[str] = Field(default=None, description="Rule being superseded when architecture changed")
    commit_sha: Optional[str] = Field(default=None, description="Verified commit SHA")
    file_path: Optional[str] = Field(default=None, description="File path if tied to specific file")


class ConfirmLearningCandidateRequest(BaseModel):
    """Developer confirmation payload to persist candidate to canonical memory."""
    confirmed_statement: Optional[str] = Field(default=None, description="Optional developer-edited statement")
    confirmed_rationale: Optional[str] = Field(default=None, description="Optional developer-edited rationale")
    confirmed_scope: Optional[str] = Field(default=None, description="Optional developer-edited scope")
    supersedes_rule_id: Optional[str] = Field(default=None, description="Explicit supersession target confirmed by developer")


class RejectLearningCandidateRequest(BaseModel):
    """Developer rejection of a learning candidate."""
    reason: Optional[str] = Field(default="Rejected by developer in review workspace", description="Reason for rejection")


class LearningCandidateListResponse(BaseModel):
    repository_id: str
    pr_number: int
    candidates: List[LearningCandidate]
    total_count: int


class LearningResult(BaseModel):
    repository_id: str
    repository_full_name: str
    pr_number: int
    candidates_extracted: int
    memories_created: List[CanonicalMemory] = Field(default_factory=list)
    memories_reinforced: List[CanonicalMemory] = Field(default_factory=list)
    memories_superseded: List[CanonicalMemory] = Field(default_factory=list)
    memories_rejected: List[Dict[str, Any]] = Field(default_factory=list)
    hindsight_sync_status: str = Field(default="SYNCED", description="SYNCED, UNAVAILABLE, or FAILED")


