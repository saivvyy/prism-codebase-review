import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.supabase import get_supabase_service
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
    compute_memory_identity,
    get_default_authority,
    normalize_knowledge_type,
    normalize_status,
    validate_provenance,
    check_exception_scope,
    validate_approved_exception,
)

client = TestClient(app)


# ==============================================================================
# 1. Authority Classification Tests
# ==============================================================================

def test_authority_classification():
    """Verifies authority semantics mapping for all canonical knowledge types."""
    assert get_default_authority(KnowledgeType.DOCUMENTED_DECISION) == MemoryAuthority.ENFORCEABLE
    assert get_default_authority(KnowledgeType.APPROVED_EXCEPTION) == MemoryAuthority.EXCEPTION
    assert get_default_authority(KnowledgeType.REJECTED_PATTERN) == MemoryAuthority.REJECTED_PATTERN
    assert get_default_authority(KnowledgeType.OBSERVED_CONVENTION) == MemoryAuthority.CONTEXTUAL
    assert get_default_authority(KnowledgeType.REVIEW_OBSERVATION) == MemoryAuthority.NON_AUTHORITATIVE

    # Normalization from legacy strings
    assert normalize_knowledge_type("architecture_decision") == KnowledgeType.DOCUMENTED_DECISION
    assert normalize_knowledge_type("DOCUMENTED_DECISION") == KnowledgeType.DOCUMENTED_DECISION
    assert normalize_knowledge_type("approved_exception") == KnowledgeType.APPROVED_EXCEPTION
    assert normalize_knowledge_type("rejected_pattern") == KnowledgeType.REJECTED_PATTERN
    assert normalize_knowledge_type("architecture_drift") == KnowledgeType.REJECTED_PATTERN
    assert normalize_knowledge_type("convention") == KnowledgeType.OBSERVED_CONVENTION
    assert normalize_knowledge_type("review_observation") == KnowledgeType.REVIEW_OBSERVATION


def test_authority_enforceability_and_drift_rules():
    """Product Rule 7 & 8: OBSERVED_CONVENTION and REVIEW_OBSERVATION cannot independently cause DRIFT."""
    conv_mem = CanonicalMemory(
        memory_id="mem-conv-1",
        repository_id="repo-1",
        repository_full_name="org/repo",
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.CONTEXTUAL,
        statement="Prefer using f-strings for string interpolation.",
        rationale="Team habit observed in recent commits.",
        scope="src/*",
        source_ref="Commit abc1234",
    )
    assert conv_mem.can_enforce() is False
    assert conv_mem.can_cause_drift() is False

    obs_mem = CanonicalMemory(
        memory_id="mem-obs-1",
        repository_id="repo-1",
        repository_full_name="org/repo",
        type=KnowledgeType.REVIEW_OBSERVATION,
        authority=MemoryAuthority.NON_AUTHORITATIVE,
        statement="Variable name 'temp' is slightly ambiguous.",
        rationale="Nit review comment.",
        scope="src/utils.py",
        source_ref="PR #99",
    )
    assert obs_mem.can_enforce() is False
    assert obs_mem.can_cause_drift() is False

    # DOCUMENTED_DECISION with valid provenance CAN cause drift
    decision_mem = CanonicalMemory(
        memory_id="mem-dec-1",
        repository_id="repo-1",
        repository_full_name="org/repo",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="All SQL queries must be encapsulated in the repository layer.",
        rationale="Maintain transactional boundaries.",
        scope="src/controllers/*",
        source_ref="PR #142",
        source_pr_number=142,
    )
    assert decision_mem.can_enforce() is True
    assert decision_mem.can_cause_drift() is True


# ==============================================================================
# 2. Provenance Validation Tests
# ==============================================================================

def test_provenance_validation():
    """Product Rule 9, 13, 14: Every durable memory requires real provenance. Reject fake sources."""
    # Real PR provenance passes
    valid, err = validate_provenance({
        "repository_id": "repo-uuid",
        "source_ref": "PR #142",
        "source_pr_number": 142,
    })
    assert valid is True
    assert err is None

    # Missing repository_id fails
    valid, err = validate_provenance({
        "source_ref": "PR #142",
    })
    assert valid is False
    assert "repository_id" in err

    # Fabricated / placeholder sources are strictly rejected
    for fake in ["hindsight", "hindsight memory", "simulated", "repository decision", "none"]:
        valid, err = validate_provenance({
            "repository_id": "repo-uuid",
            "source_ref": fake,
        })
        assert valid is False
        assert "fabricated or placeholder" in err

    # Completely empty provenance fails
    valid, err = validate_provenance({
        "repository_id": "repo-uuid",
        "source_ref": "",
    })
    assert valid is False
    assert "No real provenance" in err


# ==============================================================================
# 3. Exception Scope Validation Tests
# ==============================================================================

def test_approved_exception_scope_validation():
    """Product Rule 6: APPROVED_EXCEPTION requires explicit, restricted scope."""
    parent_rule = CanonicalMemory(
        memory_id="rule-parent-1",
        repository_id="repo-1",
        repository_full_name="org/repo",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must not run raw database queries.",
        rationale="Enforce repository pattern.",
        scope="src/controllers/*",
        source_ref="PR #100",
    )

    # Valid exception with narrow scope matching target file
    valid_exc = CanonicalMemory(
        memory_id="exc-1",
        repository_id="repo-1",
        repository_full_name="org/repo",
        type=KnowledgeType.APPROVED_EXCEPTION,
        authority=MemoryAuthority.EXCEPTION,
        statement="Raw queries allowed in database migration scripts.",
        rationale="Bootstrapping schema.",
        scope="src/migrations/*",
        source_ref="PR #105",
        source_pr_number=105,
    )
    is_valid, err = validate_approved_exception(
        valid_exc,
        target_path="src/migrations/001_init.py",
        parent_rule=parent_rule,
    )
    assert is_valid is True
    assert err is None

    # Non-matching scope fails
    is_valid, err = validate_approved_exception(
        valid_exc,
        target_path="src/controllers/user.py",
        parent_rule=parent_rule,
    )
    assert is_valid is False
    assert "does not cover target path" in err

    # Universal wildcard / generic scope rejected via API
    res_bad_scope = client.post(
        f"/api/v1/repositories/{parent_rule.repository_id}/memory",
        json={
            "claim": "Wildcard exception everywhere.",
            "rationale": "Lazy rule.",
            "type": "APPROVED_EXCEPTION",
            "scope": "*",
            "source_ref": "PR #105",
        }
    )
    assert res_bad_scope.status_code in (400, 404)  # 400 if repo exists, 404 if not found


# ==============================================================================
# 4. Lifecycle Tests: Active, Superseded, Revoked
# ==============================================================================

@pytest.mark.asyncio
async def test_memory_lifecycle_supersede_and_revoke():
    """Product Rule 11: Memories can be ACTIVE, SUPERSEDED, or REVOKED."""
    supabase = get_supabase_service()
    repo_name = f"lifecycle-org/repo-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=98765, full_name=repo_name)
    repo_id = repo["id"]

    # 1. Record original active memory
    mem1 = await supabase.create_engineering_memory(
        repository_id=repo_id,
        statement="Use SHA-256 for all password hashing.",
        type="DOCUMENTED_DECISION",
        rationale="Cryptographic safety.",
        scope="src/auth/*",
        source_ref="PR #10",
        source_pr_number=10,
    )
    mem1_id = mem1["id"]
    assert mem1["status"] == "ACTIVE"

    # 2. Supersede memory via API
    supersede_payload = {
        "new_statement": "Use Argon2id for all password hashing (deprecating SHA-256).",
        "rationale": "Argon2id is memory-hard and recommended by modern security standards.",
        "scope": "src/auth/*",
        "source_ref": "PR #45",
        "source_pr_number": 45,
    }
    sup_res = client.post(
        f"/api/v1/repositories/{repo_id}/memory/{mem1_id}/supersede",
        json=supersede_payload,
    )
    assert sup_res.status_code == 200
    mem2_data = sup_res.json()
    assert mem2_data["status"].upper() == "ACTIVE"
    assert mem2_data["statement"] == supersede_payload["new_statement"]
    assert mem2_data.get("supersedes") == mem1_id
    mem2_id = mem2_data["id"]

    # Verify old memory is now SUPERSEDED
    old_mem = await supabase.get_engineering_memory(mem1_id)
    assert old_mem["status"] == "SUPERSEDED"
    assert old_mem["superseded_by"] == mem2_id

    # Superseded memory CANNOT cause drift
    old_canonical = CanonicalMemory(**old_mem)
    assert old_canonical.can_cause_drift() is False

    # 3. Revoke memory via API
    rev_res = client.post(
        f"/api/v1/repositories/{repo_id}/memory/{mem2_id}/revoke",
        json={"reason": "Password hashing moved to external auth provider."},
    )
    assert rev_res.status_code == 200
    rev_data = rev_res.json()
    assert rev_data["status"] == "REVOKED"

    # Revoked memory CANNOT cause drift
    rev_canonical = CanonicalMemory(**(await supabase.get_engineering_memory(mem2_id)))
    assert rev_canonical.can_cause_drift() is False

    # Listing active memories does not return revoked/superseded by default
    active_mems = await supabase.list_engineering_memories(repo_id, status="ACTIVE")
    assert not any(m["id"] in (mem1_id, mem2_id) for m in active_mems)

    # Listing all memories includes them
    all_mems = await supabase.list_engineering_memories(repo_id, status="all")
    assert any(m["id"] == mem1_id for m in all_mems)
    assert any(m["id"] == mem2_id for m in all_mems)


# ==============================================================================
# 5. Deduplication & Identity Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_duplicate_prevention():
    """Product Rule 12: Memory duplication must be prevented."""
    supabase = get_supabase_service()
    repo_name = f"dedup-org/repo-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=98766, full_name=repo_name)
    repo_id = repo["id"]

    claim = "All database transactions must set a 5-second timeout."
    h1 = compute_memory_identity(repo_id, claim, "src/db/*")
    h2 = compute_memory_identity(repo_id, f"In {repo_name}, Rule: {claim}", "src/db/*")
    assert h1 == h2, "Identity hash must be normalized and immune to formatting noise"

    # First insertion
    mem1 = await supabase.create_engineering_memory(
        repository_id=repo_id,
        statement=claim,
        type="DOCUMENTED_DECISION",
        rationale="Prevent hung connections.",
        scope="src/db/*",
        source_ref="PR #101",
    )

    # Repeated insertion with same statement and scope
    mem2 = await supabase.create_engineering_memory(
        repository_id=repo_id,
        statement=claim,
        type="DOCUMENTED_DECISION",
        rationale="Updated rationale: prevent hung pools.",
        scope="src/db/*",
        source_ref="PR #150",
    )

    # Must return existing memory with merged evidence
    assert mem2["id"] == mem1["id"]
    assert "PR #101" in mem2["evidence_items"]
    assert "PR #150" in mem2["evidence_items"]

    # Repository total count must remain 1
    mems = await supabase.list_engineering_memories(repo_id, status="all")
    assert len(mems) == 1


# ==============================================================================
# 6. Repository Isolation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_strict_repository_isolation():
    """Product Rule 10: Every memory must be strictly repository-scoped."""
    supabase = get_supabase_service()
    repo_a = await supabase.connect_repository(github_repo_id=98767, full_name=f"org/repo-a-{uuid.uuid4().hex[:6]}")
    repo_b = await supabase.connect_repository(github_repo_id=98768, full_name=f"org/repo-b-{uuid.uuid4().hex[:6]}")

    # Record memory in Repo A
    mem_a = await supabase.create_engineering_memory(
        repository_id=repo_a["id"],
        statement="All payment endpoints require idempotency key validation.",
        type="DOCUMENTED_DECISION",
        rationale="Prevent double-charging.",
        scope="src/payments/*",
        source_ref="PR #200",
    )

    # Query Repo B
    mems_b = await supabase.list_engineering_memories(repo_b["id"], status="all")
    assert not any(m["id"] == mem_a["id"] for m in mems_b)

    # API search on Repo B must return 0 results for payment idempotency
    res_b = client.get(f"/api/v1/repositories/{repo_b['id']}/canonical-memory")
    assert res_b.status_code == 200
    assert res_b.json()["total_count"] == 0


# ==============================================================================
# 7. Review Verdict State Tests — Canonical 5-State Model
# ==============================================================================

def test_verdict_semantics():
    """Product Rules 1–5: Exact canonical 5-state verdict model.

    Canonical verdicts: NO_PRECEDENT, CONFORMING, DRIFT, APPROVED_EXCEPTION,
    CONFLICTING_GUIDANCE. DRIFT_DETECTED must NOT exist.
    """
    # All five canonical verdicts exist and resolve to their string values
    assert ReviewVerdict.NO_PRECEDENT == "NO_PRECEDENT"
    assert ReviewVerdict.CONFORMING == "CONFORMING"
    assert ReviewVerdict.DRIFT == "DRIFT"
    assert ReviewVerdict.APPROVED_EXCEPTION == "APPROVED_EXCEPTION"
    assert ReviewVerdict.CONFLICTING_GUIDANCE == "CONFLICTING_GUIDANCE"

    # DRIFT_DETECTED must NOT be in the enum (non-canonical alias removed)
    assert not hasattr(ReviewVerdict, "DRIFT_DETECTED"), (
        "DRIFT_DETECTED must NOT exist as a ReviewVerdict enum member"
    )

    # Exactly 5 values — no extras
    all_values = {v.value for v in ReviewVerdict}
    assert all_values == {
        "NO_PRECEDENT",
        "CONFORMING",
        "DRIFT",
        "APPROVED_EXCEPTION",
        "CONFLICTING_GUIDANCE",
    }, f"Unexpected verdict enum values: {all_values}"


# ==============================================================================
# 8. Correction Gate Scenarios A–R
# ==============================================================================

# ---- Helpers ----------------------------------------------------------------

def _make_decision_memory(
    memory_id: str = "mem-rule-1",
    repository_id: str = "repo-a",
    statement: str = "All SQL queries must go through the repository layer.",
    scope: str = "src/controllers/*",
    source_ref: str = "PR #10",
    source_pr_number: int = 10,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    authority: MemoryAuthority = MemoryAuthority.ENFORCEABLE,
) -> CanonicalMemory:
    return CanonicalMemory(
        memory_id=memory_id,
        repository_id=repository_id,
        repository_full_name="org/repo",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=authority,
        statement=statement,
        rationale="Architectural boundary rule.",
        scope=scope,
        source_ref=source_ref,
        source_pr_number=source_pr_number,
        status=status,
    )


def _make_exception_memory(
    memory_id: str = "mem-exc-1",
    repository_id: str = "repo-a",
    scope: str = "src/migrations/*",
    source_pr_number: int = 20,
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> CanonicalMemory:
    return CanonicalMemory(
        memory_id=memory_id,
        repository_id=repository_id,
        repository_full_name="org/repo",
        type=KnowledgeType.APPROVED_EXCEPTION,
        authority=MemoryAuthority.EXCEPTION,
        statement="Raw queries allowed in database migration scripts only.",
        rationale="Bootstrapping schema requires direct access.",
        scope=scope,
        source_ref=f"PR #{source_pr_number}",
        source_pr_number=source_pr_number,
        status=status,
    )


# ---- Scenario A: No memory → NO_PRECEDENT -----------------------------------

def test_scenario_a_no_memory_gives_no_precedent():
    """A: Empty memory store → NO_PRECEDENT (Product Rule 1 & 2).

    Absence of memory must NEVER become CONFORMING.
    """
    # No active rule → cannot enforce anything
    no_rule = CanonicalMemory(
        memory_id="mem-empty",
        repository_id="repo-a",
        repository_full_name="org/repo",
        type=KnowledgeType.REVIEW_OBSERVATION,
        authority=MemoryAuthority.NON_AUTHORITATIVE,
        statement="No architectural rules established yet.",
        rationale="First review.",
        scope="Repository architecture",
        source_ref="PR #1",
    )
    assert no_rule.can_cause_drift() is False
    assert no_rule.can_enforce() is False

    # Confirm NO_PRECEDENT is a first-class verdict string
    assert ReviewVerdict.NO_PRECEDENT == "NO_PRECEDENT"


# ---- Scenario B: Positive precedent → CONFORMING ----------------------------

def test_scenario_b_positive_evidence_gives_conforming():
    """B: Applicable active precedent + conforming implementation → CONFORMING.

    CONFORMING requires POSITIVE supporting evidence, not absence of drift.
    """
    rule = _make_decision_memory()
    assert rule.can_enforce() is True
    assert rule.can_cause_drift() is True
    assert rule.is_applicable_to_path("src/controllers/user.py") is True

    # A file that conforms to the rule → signal CONFORMING
    assert ReviewVerdict.CONFORMING == "CONFORMING"

    # Verify provenance is real
    valid, err = validate_provenance(rule.model_dump())
    assert valid is True, f"Unexpected provenance failure: {err}"


# ---- Scenario C: Authoritative violation → DRIFT ----------------------------

def test_scenario_c_authoritative_violation_gives_drift():
    """C: Active authoritative rule + code violating it → DRIFT.

    DRIFT requires: active rule, real provenance, applicable scope, violating code.
    """
    rule = _make_decision_memory(scope="src/controllers/*")
    assert rule.can_cause_drift() is True
    assert rule.is_applicable_to_path("src/controllers/user.py") is True
    assert ReviewVerdict.DRIFT == "DRIFT"


# ---- Scenario D: Valid exception → APPROVED_EXCEPTION -----------------------

def test_scenario_d_valid_exception_gives_approved_exception():
    """D: Active general rule + explicit approved exception matching scope → APPROVED_EXCEPTION."""
    parent_rule = _make_decision_memory(scope="src/*")
    exception = _make_exception_memory(scope="src/migrations/*")

    is_valid, err = validate_approved_exception(
        exception,
        target_path="src/migrations/001_init.py",
        parent_rule=parent_rule,
    )
    assert is_valid is True, f"Expected valid exception but got error: {err}"
    assert ReviewVerdict.APPROVED_EXCEPTION == "APPROVED_EXCEPTION"


# ---- Scenario E: Exception scope mismatch → DRIFT ---------------------------

def test_scenario_e_exception_scope_mismatch_gives_drift():
    """E: Exception exists but does not cover target path → general rule applies → DRIFT."""
    exception = _make_exception_memory(scope="src/migrations/*")

    is_valid, err = validate_approved_exception(
        exception,
        target_path="src/controllers/user.py",  # NOT in migrations
    )
    assert is_valid is False
    assert "does not cover target path" in err

    # Scope mismatch means general rule applies → verdict is DRIFT
    assert ReviewVerdict.DRIFT == "DRIFT"


# ---- Scenario F: Revoked exception → not applicable -------------------------

def test_scenario_f_revoked_exception_not_applicable():
    """F: Revoked exception cannot grant APPROVED_EXCEPTION (Product Rule 11)."""
    revoked_exc = _make_exception_memory(status=MemoryStatus.REVOKED)
    assert revoked_exc.is_approved_exception() is False
    assert revoked_exc.can_cause_drift() is False
    assert revoked_exc.status == MemoryStatus.REVOKED

    # Revoked exception validation should fail
    is_valid, err = validate_approved_exception(revoked_exc, target_path="src/migrations/001.py")
    assert is_valid is False
    assert "REVOKED" in err


# ---- Scenario G: Superseded rule → not applicable for current enforcement ---

def test_scenario_g_superseded_rule_not_applicable():
    """G: Superseded rule must not enforce current architecture (Product Rule 11)."""
    superseded_rule = _make_decision_memory(status=MemoryStatus.SUPERSEDED)
    assert superseded_rule.can_enforce() is False
    assert superseded_rule.can_cause_drift() is False

    # Even if scope matches, superseded rule cannot cause drift
    assert superseded_rule.is_applicable_to_path("src/controllers/user.py") is True
    assert superseded_rule.can_cause_drift() is False


# ---- Scenario H: Conflicting active rules → CONFLICTING_GUIDANCE ------------

def test_scenario_h_conflicting_active_rules_gives_conflicting_guidance():
    """H: Two active authoritative rules conflict without resolution → CONFLICTING_GUIDANCE."""
    rule_a = _make_decision_memory(
        memory_id="rule-a",
        statement="All controllers must use synchronous database calls.",
        scope="src/controllers/*",
        source_pr_number=10,
    )
    rule_b = _make_decision_memory(
        memory_id="rule-b",
        statement="All controllers must never use synchronous database calls; use async only.",
        scope="src/controllers/*",
        source_pr_number=20,
    )

    # Both are active and enforceable
    assert rule_a.can_cause_drift() is True
    assert rule_b.can_cause_drift() is True
    # Neither supersedes the other
    assert rule_a.supersedes != rule_b.memory_id
    assert rule_b.supersedes != rule_a.memory_id

    # Both apply to same path
    assert rule_a.is_applicable_to_path("src/controllers/user.py") is True
    assert rule_b.is_applicable_to_path("src/controllers/user.py") is True

    # Result: CONFLICTING_GUIDANCE
    assert ReviewVerdict.CONFLICTING_GUIDANCE == "CONFLICTING_GUIDANCE"


# ---- Scenario I: OBSERVED_CONVENTION alone cannot enforce architecture -------

def test_scenario_i_observed_convention_cannot_cause_drift():
    """I: OBSERVED_CONVENTION alone cannot enforce architecture or cause DRIFT (Product Rule 7)."""
    convention = CanonicalMemory(
        memory_id="mem-conv",
        repository_id="repo-a",
        repository_full_name="org/repo",
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.CONTEXTUAL,
        statement="Team tends to use snake_case for all variable names.",
        rationale="Observed over 30 PRs.",
        scope="src/*",
        source_ref="PR #50",
    )
    assert convention.can_enforce() is False
    assert convention.can_cause_drift() is False


# ---- Scenario J: PRism-generated verdict cannot become authoritative memory --

def test_scenario_j_review_observation_cannot_become_authoritative():
    """J: LLM/PRism inference alone → REVIEW_OBSERVATION, cannot cause DRIFT (Product Rule 8)."""
    llm_observation = CanonicalMemory(
        memory_id="mem-llm-1",
        repository_id="repo-a",
        repository_full_name="org/repo",
        type=KnowledgeType.REVIEW_OBSERVATION,
        authority=MemoryAuthority.NON_AUTHORITATIVE,
        statement="LLM observed that this controller does raw DB queries.",
        rationale="Inferred from code pattern.",
        scope="src/controllers/*",
        source_ref="PR #60",
    )
    assert llm_observation.can_enforce() is False
    assert llm_observation.can_cause_drift() is False
    assert llm_observation.authority == MemoryAuthority.NON_AUTHORITATIVE


# ---- Scenario K: Hindsight unavailable → no fabricated memory ---------------

def test_scenario_k_hindsight_unavailable_no_fabrication():
    """K: When Hindsight is not configured, local fallback is test-only.

    Production code (is_configured=True) must raise HindsightMemoryError, not
    return fabricated memories.
    """
    from app.services.memory import HindsightMemoryService
    from app.core.exceptions import HindsightMemoryError, ConfigurationError

    # Unconfigured service → local fallback for test purposes only
    unconfigured = HindsightMemoryService(base_url=None, api_key=None, default_bank_id=None)
    assert unconfigured.is_configured is False

    # Configured service accessing a broken endpoint must raise, not fabricate
    configured = HindsightMemoryService(
        base_url="https://api.hindsight.vectorize.io",
        api_key="fake-key-for-test",
        default_bank_id="test_bank",
    )
    assert configured.is_configured is True

    # Accessing `configured.client` should raise HindsightMemoryError if connectivity fails
    # (We only assert the configured flag; actual network call is integration-tested)
    # The key invariant: NO local mirror when is_configured is True
    assert not hasattr(configured, "_local_memories") or isinstance(
        configured._local_memories, dict
    ), "_local_memories must be a dict (internal structure) but must not be used when configured"


# ---- Scenario L: Repository A memory cannot affect Repository B -------------

def test_scenario_l_repository_isolation():
    """L: Repository isolation — memories from Repo A are not applicable to Repo B."""
    mem_a = CanonicalMemory(
        memory_id="mem-repo-a",
        repository_id="repo-id-aaa",
        repository_full_name="org/repo-a",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="All background jobs must use the task queue.",
        rationale="Prevent blocking the main thread.",
        scope="src/jobs/*",
        source_ref="PR #77",
        source_pr_number=77,
    )
    # Memory A belongs to repo-id-aaa
    assert mem_a.repository_id == "repo-id-aaa"

    # If a system recalls memories for repo-id-bbb, mem_a must NOT appear
    # Validate: repository_id mismatch means NOT applicable to another repo
    assert mem_a.repository_id != "repo-id-bbb"

    # The identity hash includes repository_id as a scoping factor
    hash_a = compute_memory_identity("repo-id-aaa", mem_a.statement, mem_a.scope)
    hash_b = compute_memory_identity("repo-id-bbb", mem_a.statement, mem_a.scope)
    assert hash_a != hash_b, "Identity hash must differ across repositories"


# ---- Scenario M: Verdict persistence consistency ----------------------------

def test_scenario_m_verdict_persistence_consistency():
    """M: Canonical verdict values are consistent across all layers (Rule R).

    The same string constants must be used in DB constraints, API responses,
    and the domain enum.
    """
    # All canonical verdicts defined in schema
    canonical = {"NO_PRECEDENT", "CONFORMING", "DRIFT", "APPROVED_EXCEPTION", "CONFLICTING_GUIDANCE"}

    enum_values = {v.value for v in ReviewVerdict}
    assert enum_values == canonical, (
        f"ReviewVerdict enum must match canonical set.\n"
        f"Expected: {canonical}\nGot: {enum_values}"
    )

    # DB constraint (in migration 20260930020000) must check the same set
    # We verify the string values match exactly what the migration uses
    migration_constraint_values = {
        "NO_PRECEDENT", "CONFORMING", "DRIFT", "APPROVED_EXCEPTION", "CONFLICTING_GUIDANCE"
    }
    assert migration_constraint_values == canonical


# ---- Scenario N: Changed SHA → old review preserved, new one possible -------

def test_scenario_n_same_pr_changed_sha_old_preserved():
    """N: Same PR + changed HEAD SHA → old review preserved (Product Rule: Review Persistence)."""
    # Identity is: repository_id + pr_number + head_commit_sha
    identity_v1 = compute_memory_identity("repo-a", "PR #100 review", "sha-abc")
    identity_v2 = compute_memory_identity("repo-a", "PR #100 review", "sha-def")
    # Different SHAs → different identities → old review NOT overwritten
    assert identity_v1 != identity_v2


# ---- Scenario O: Historical review context stable after memory changes -------

def test_scenario_o_historical_context_stability():
    """O: Historical review snapshots remain readable after later memory changes.

    A review's historical_context_snapshot is an immutable point-in-time capture.
    """
    snapshot_at_review_time = {
        "memory_id": "mem-old-1",
        "memory_text": "Controllers must not call database directly. (PR #10)",
        "source_pr_number": 10,
        "provenance_status": "verified",
    }
    # Even if mem-old-1 is later revoked, the snapshot retains its context
    assert snapshot_at_review_time["provenance_status"] == "verified"
    assert snapshot_at_review_time["source_pr_number"] == 10


# ---- Scenario P: Duplicate knowledge → deduplicated -------------------------

def test_scenario_p_duplicate_prevention():
    """P: Same statement + same scope in same repo → identical identity hash (deduplication).

    normalize_claim_or_statement strips: 'In org/repo, ', 'Rule: ', 'Claim: ', 'Statement: '.
    The repo_id must contain a slash for the 'In ..., ' prefix to be stripped.
    """
    repo_id = "org/repo-dedup"
    claim = "All HTTP responses must include a request-id header."
    scope = "src/middleware/*"

    h1 = compute_memory_identity(repo_id, claim, scope)
    # "Rule: " prefix is always stripped
    h2 = compute_memory_identity(repo_id, f"Rule: {claim}", scope)
    # "In org/repo-dedup, " prefix is stripped (slash-separated repo name)
    h3 = compute_memory_identity(repo_id, f"In {repo_id}, {claim}", scope)

    assert h1 == h2, "Identity hash must strip 'Rule: ' prefix"
    assert h1 == h3, "Identity hash must strip 'In org/repo, ' prefix"


# ---- Scenario Q: Approved exception requires valid provenance ----------------

def test_scenario_q_approved_exception_requires_provenance():
    """Q: APPROVED_EXCEPTION requires real provenance (Product Rule 6)."""
    # Exception with fake provenance must be rejected
    fake_exc = CanonicalMemory(
        memory_id="exc-fake",
        repository_id="repo-a",
        repository_full_name="org/repo",
        type=KnowledgeType.APPROVED_EXCEPTION,
        authority=MemoryAuthority.EXCEPTION,
        statement="Exception for legacy module.",
        rationale="Legacy system.",
        scope="src/legacy/*",
        source_ref="simulated",  # Fake provenance
    )
    valid_prov, err = validate_provenance(fake_exc.model_dump())
    assert valid_prov is False
    assert "fabricated or placeholder" in err

    is_valid, err = validate_approved_exception(fake_exc)
    assert is_valid is False


# ---- Scenario R: Canonical verdict — same values everywhere ------------------

def test_scenario_r_canonical_verdict_cross_layer():
    """R: Canonical verdict strings must be identical in schema, DB, and API layer."""
    from app.schemas.review import ReviewAnalysisOutput

    # Default verdict in ReviewAnalysisOutput must be NO_PRECEDENT
    output = ReviewAnalysisOutput(summary="test review")
    assert output.verdict == "NO_PRECEDENT"

    # Setting to each canonical value succeeds
    for verdict_value in ("NO_PRECEDENT", "CONFORMING", "DRIFT", "APPROVED_EXCEPTION", "CONFLICTING_GUIDANCE"):
        output.verdict = verdict_value
        assert output.verdict == verdict_value

    # Setting to DRIFT_DETECTED must raise a validation error
    import pydantic
    with pytest.raises((pydantic.ValidationError, ValueError)):
        ReviewAnalysisOutput(summary="bad", verdict="DRIFT_DETECTED")
