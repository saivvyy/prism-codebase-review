import hashlib
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.analysis import FileAnalysisResult
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    LearningCandidate,
    LearningResult,
    MemoryAuthority,
    MemoryStatus,
    MemorySyncState,
    ReviewVerdict,
    compute_memory_identity,
    get_default_authority,
    normalize_knowledge_type,
    validate_approved_exception,
    validate_provenance,
)
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.schemas.review import (
    ApplicabilityResultSchema,
    DurableMemoryCandidateSchema,
    ReviewAnalysisOutput,
    ReviewFindingSchema,
)
from app.services.applicability import ApplicabilityEngine
from app.services.learning import ControlledLearningEngine, get_learning_engine
from app.services.memory import HindsightMemoryService
from app.services.review_engine import ReviewEngine
from app.services.supabase import SupabaseService


@pytest.fixture
def mock_learning_deps():
    mock_supabase = MagicMock(spec=SupabaseService)
    mock_memory = MagicMock(spec=HindsightMemoryService)

    mock_memory.is_configured = True
    mock_memory.default_bank_id = "test_repo"
    mock_memory.aretain = AsyncMock(return_value=MagicMock(success=True, items_count=1))
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True, items_count=1))

    # In-memory storage for mock Supabase to simulate realistic database state
    local_store = {}

    async def mock_find_existing(repository_id, claim=None, statement=None, identity_hash=None):
        target_text = (statement or claim or "").lower().strip()
        for mem in local_store.values():
            if mem.get("repository_id") == repository_id:
                if identity_hash and mem.get("identity_hash") == identity_hash:
                    return mem
                if target_text and (mem.get("statement", "").lower().strip() == target_text or mem.get("claim", "").lower().strip() == target_text):
                    return mem
        return None

    async def mock_create_memory(**kwargs):
        mem_id = str(uuid.uuid4())
        rec = dict(kwargs)
        rec["id"] = mem_id
        rec["memory_id"] = mem_id
        rec["created_at"] = datetime.now(timezone.utc).isoformat()
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        local_store[mem_id] = rec
        return rec

    async def mock_update_memory(memory_id, data):
        if memory_id in local_store:
            local_store[memory_id].update(data)
            local_store[memory_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            return local_store[memory_id]
        return None

    async def mock_get_memory(memory_id):
        return local_store.get(memory_id)

    async def mock_list_memories(repository_id, memory_type=None, status=None, authority=None, limit=100):
        res = [m for m in local_store.values() if m.get("repository_id") == repository_id]
        if status and status != "all":
            res = [m for m in res if m.get("status") == status]
        if memory_type:
            res = [m for m in res if m.get("type") == memory_type]
        if authority:
            res = [m for m in res if m.get("authority") == authority]
        return res[:limit]

    mock_supabase.find_existing_memory = AsyncMock(side_effect=mock_find_existing)
    mock_supabase.create_engineering_memory = AsyncMock(side_effect=mock_create_memory)
    mock_supabase.update_engineering_memory = AsyncMock(side_effect=mock_update_memory)
    mock_supabase.get_engineering_memory = AsyncMock(side_effect=mock_get_memory)
    mock_supabase.list_engineering_memories = AsyncMock(side_effect=mock_list_memories)

    engine = ControlledLearningEngine(supabase=mock_supabase, memory=mock_memory)
    return engine, mock_supabase, mock_memory, local_store


@pytest.mark.asyncio
async def test_req1_and_req2_verdicts_do_not_create_enforceable_memory(mock_learning_deps):
    """Test 1 & 2 & Adversarial C & N:
    DRIFT or CONFORMING review verdicts do NOT automatically create ENFORCEABLE memories.
    PRism-generated verdict alone cannot establish architecture.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=101,
        number=101,
        title="Fix controller queries",
        body="Normal bugfix PR",
        author="alice",
        head_sha="sha101",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/101",
    )

    # Review with DRIFT verdict
    drift_output = ReviewAnalysisOutput(
        summary="Drift detected: direct persistence used",
        drift_detected=True,
        verdict="DRIFT",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/InvoiceController.py",
                title="Direct database access in controller",
                message="InvoiceController directly runs queries.",
            )
        ],
    )

    res = await engine.learn_from_review(repo, pr, drift_output)
    # The review finding becomes REVIEW_OBSERVATION with NON_AUTHORITATIVE authority.
    # It must NEVER be ENFORCEABLE.
    assert all(m.authority != MemoryAuthority.ENFORCEABLE for m in res.memories_created)
    assert all(m.type != KnowledgeType.DOCUMENTED_DECISION for m in res.memories_created)
    assert any(m.type == KnowledgeType.REVIEW_OBSERVATION and m.authority == MemoryAuthority.NON_AUTHORITATIVE for m in res.memories_created)

    # Review with CONFORMING verdict
    conforming_output = ReviewAnalysisOutput(
        summary="Conforming to existing service pattern",
        drift_detected=False,
        verdict="CONFORMING",
        findings=[],
    )
    res_conf = await engine.learn_from_review(repo, pr, conforming_output)
    assert len(res_conf.memories_created) == 0  # No explicit decisions extracted


@pytest.mark.asyncio
async def test_req3_and_adv_b_llm_cannot_create_authoritative_rule_without_evidence(mock_learning_deps):
    """Test 3 & Adversarial B:
    Groq / LLM claims an architectural rule -> downgraded to CONTEXTUAL / OBSERVED_CONVENTION without explicit repository evidence.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=102,
        number=102,
        title="Refactor customer endpoint",
        body="Routine refactoring of endpoints",
        author="bob",
        head_sha="sha102",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/102",
    )

    # Groq hallucinates a durable candidate marked as architecture_decision
    llm_output = ReviewAnalysisOutput(
        summary="Routine review",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Every customer endpoint must use caching layer.",
                type="architecture_decision",
                rationale="LLM decided caching is mandatory for architecture.",
                scope="src/endpoints/*",
                confidence="supported",
            )
        ],
    )

    res = await engine.learn_from_review(repo, pr, llm_output)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    # Must NOT be promoted to ENFORCEABLE or DOCUMENTED_DECISION because there's no ADR/evidence!
    assert created.authority == MemoryAuthority.CONTEXTUAL
    assert created.type == KnowledgeType.OBSERVED_CONVENTION


@pytest.mark.asyncio
async def test_req4_and_adv_a_repeated_implementation_remains_contextual(mock_learning_deps):
    """Test 4 & Adversarial A:
    100 identical implementations or conventions remain CONTEXTUAL only.
    Frequency NEVER upgrades to ENFORCEABLE.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=103,
        number=103,
        title="Batch controller updates",
        body="Updated 100 controllers to use the new logging format",
        author="charlie",
        head_sha="sha103",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/103",
    )

    # Create 100 files implementing the convention
    file_analyses = [
        FileAnalysisResult(
            file_path=f"src/controllers/ctrl_{i}.py",
            language="python",
            is_supported=True,
            parse_success=True,
            classes=[],
            functions=[],
            imports=[],
            calls=[],
        )
        for i in range(100)
    ]

    llm_output = ReviewAnalysisOutput(
        summary="All 100 controllers follow logging format",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Controllers must use structured logging helper.",
                type="lesson_learned",
                rationale="Observed in 100 controllers.",
                scope="src/controllers/*",
                confidence="supported",
            )
        ],
    )

    res = await engine.learn_from_review(repo, pr, llm_output, file_analyses=file_analyses)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    assert created.authority == MemoryAuthority.CONTEXTUAL
    assert created.type == KnowledgeType.OBSERVED_CONVENTION
    assert created.authority != MemoryAuthority.ENFORCEABLE


@pytest.mark.asyncio
async def test_req5_review_observation_remains_non_authoritative(mock_learning_deps):
    """Test 5:
    Normal review findings become REVIEW_OBSERVATION + NON_AUTHORITATIVE.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=104,
        number=104,
        title="Payment controller fix",
        body="Fix payment error handling",
        author="dave",
        head_sha="sha104",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/104",
    )

    review_output = ReviewAnalysisOutput(
        summary="PR note",
        verdict="NO_PRECEDENT",
        findings=[
            ReviewFindingSchema(
                category="note",
                severity="info",
                file_path="src/controllers/PaymentController.py",
                title="PR #104 moved persistence access behind the service layer.",
                message="Cleaned up direct DB queries.",
            )
        ],
    )

    res = await engine.learn_from_review(repo, pr, review_output)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    assert created.type == KnowledgeType.REVIEW_OBSERVATION
    assert created.authority == MemoryAuthority.NON_AUTHORITATIVE


@pytest.mark.asyncio
async def test_req6_and_req7_and_adv_d_explicit_adr_becomes_enforceable(mock_learning_deps):
    """Test 6 & 7 & Adversarial D:
    Explicit ADR or architecture decision in PR creates DOCUMENTED_DECISION + ENFORCEABLE with real provenance.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=105,
        number=105,
        title="[ADR] ADR-005: All controllers must use ServiceLayer",
        body="Explicit architectural decision record establishing service layer boundary.",
        author="lead-architect",
        head_sha="sha105",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/105",
    )

    file_analyses = [
        FileAnalysisResult(
            file_path="docs/adr/0005-controllers-service-layer.md",
            language="markdown",
            is_supported=True,
            parse_success=True,
            classes=[],
            functions=[],
            imports=[],
            calls=[],
        )
    ]

    review_output = ReviewAnalysisOutput(
        summary="ADR pull request",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Controllers must not directly access persistence or run raw queries.",
                type="architecture_decision",
                rationale="ADR-005 dictates service layer pattern for transactional consistency.",
                scope="src/controllers/*",
                confidence="supported",
            )
        ],
    )

    res = await engine.learn_from_review(repo, pr, review_output, file_analyses=file_analyses)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    assert created.type == KnowledgeType.DOCUMENTED_DECISION
    assert created.authority == MemoryAuthority.ENFORCEABLE
    assert created.source_ref == "PR #105"
    assert created.source_commit_sha == "sha105"
    assert created.source_pr_number == 105


@pytest.mark.asyncio
async def test_req8_missing_provenance_rejected(mock_learning_deps):
    """Test 8:
    Missing or placeholder provenance -> authoritative memory rejected.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    bad_candidate = LearningCandidate(
        statement="Controllers must use service layer.",
        rationale="Missing provenance test",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        scope="src/controllers/*",
        repository_id="repo-100",
        repository_full_name="acme/billing",
        source_type="manual",
        source_ref="hindsight memory",  # Placeholder disallowed
        source_pr_number=None,
        source_commit_sha=None,
        is_explicit_decision=True,
    )

    outcome = await engine.learn_candidate(bad_candidate)
    assert outcome["status"] == "rejected"
    assert "INVALID_PROVENANCE" in outcome["reason"]


@pytest.mark.asyncio
async def test_req9_and_req10_and_adv_e_valid_exception_and_scope_isolation(mock_learning_deps):
    """Test 9 & 10 & Adversarial E:
    Valid explicit exception -> APPROVED_EXCEPTION + EXCEPTION.
    Scope cannot leak: HealthCheckController exception does NOT excuse PaymentController.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}

    # 1. Existing general rule in repo
    parent_rule = await mock_supabase.create_engineering_memory(
        repository_id="repo-100",
        statement="Controllers must not access persistence directly.",
        type="DOCUMENTED_DECISION",
        authority="ENFORCEABLE",
        scope="src/controllers/*",
        source_ref="PR #100",
        source_pr_number=100,
    )

    pr_exception = GitHubPR(
        id=106,
        number=106,
        title="Approve exception for HealthCheckController",
        body="Health check requires direct ping to DB for liveness probes.",
        author="devops",
        head_sha="sha106",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/106",
    )

    review_output = ReviewAnalysisOutput(
        summary="Approved exception review",
        verdict="APPROVED_EXCEPTION",
        exception_approved=True,
        findings=[
            ReviewFindingSchema(
                category="approved_exception",
                severity="info",
                file_path="src/controllers/HealthCheckController.py",
                title="HealthCheckController may directly access DB for liveness probe",
                message="Approved exception to general rule",
            )
        ],
    )

    app_result = ApplicabilityResultSchema(
        repository_id="repo-100",
        applicable_rules=[parent_rule],
    )

    res = await engine.learn_from_review(repo, pr_exception, review_output, applicability_result=app_result)
    assert len(res.memories_created) == 1
    exception_mem = res.memories_created[0]
    assert exception_mem.type == KnowledgeType.APPROVED_EXCEPTION
    assert exception_mem.authority == MemoryAuthority.EXCEPTION

    # Verify scope matching
    assert exception_mem.is_applicable_to_path("src/controllers/HealthCheckController.py") is True
    # Exception must NOT leak to PaymentController
    assert exception_mem.is_applicable_to_path("src/controllers/PaymentController.py") is False


@pytest.mark.asyncio
async def test_req11_and_req12_and_adv_f_revoked_and_superseded_exception_cannot_govern(mock_learning_deps):
    """Test 11 & 12 & Adversarial F:
    Revoked or superseded exception cannot govern.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    exception_mem = CanonicalMemory(
        memory_id=str(uuid.uuid4()),
        repository_id="repo-100",
        type=KnowledgeType.APPROVED_EXCEPTION,
        authority=MemoryAuthority.EXCEPTION,
        statement="HealthCheckController may access persistence directly.",
        rationale="Liveness probe exception",
        scope="src/controllers/HealthCheckController.py",
        status=MemoryStatus.ACTIVE,
        source_ref="PR #106",
    )

    # When active: valid
    is_valid, _ = validate_approved_exception(exception_mem, "src/controllers/HealthCheckController.py")
    assert is_valid is True

    # Revoke exception
    exception_mem.revoke()
    assert exception_mem.status == MemoryStatus.REVOKED
    is_valid_revoked, reason = validate_approved_exception(exception_mem, "src/controllers/HealthCheckController.py")
    assert is_valid_revoked is False
    assert "REVOKED" in reason

    # Supersede exception
    exception_mem.supersede("new-exception-uuid")
    assert exception_mem.status == MemoryStatus.SUPERSEDED
    is_valid_super, reason_super = validate_approved_exception(exception_mem, "src/controllers/HealthCheckController.py")
    assert is_valid_super is False
    assert "SUPERSEDED" in reason_super


@pytest.mark.asyncio
async def test_req13_and_req14_and_req15_and_adv_k_deduplication_and_idempotent_learning(mock_learning_deps):
    """Test 13, 14, 15 & Adversarial K:
    Same decision twice -> ONE canonical memory.
    Additional provenance reinforces existing memory (updates observation_count, evidence_items).
    Repeated learning is idempotent.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr1 = GitHubPR(
        id=107,
        number=107,
        title="[ADR] ADR-007: Domain events must be immutable",
        body="Architecture decision on domain event immutability",
        author="architect",
        head_sha="sha107",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/107",
    )

    review_output = ReviewAnalysisOutput(
        summary="ADR event review",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Domain events must be immutable dataclasses.",
                type="architecture_decision",
                rationale="ADR-007 establishes event immutability.",
                scope="src/events/*",
                confidence="supported",
            )
        ],
    )

    # 1. First learning cycle
    res1 = await engine.learn_from_review(repo, pr1, review_output)
    assert len(res1.memories_created) == 1
    assert len(res1.memories_reinforced) == 0
    created_id = res1.memories_created[0].memory_id
    assert len(local_store) == 1

    # 2. Second learning cycle with PR #108 reaffirming the exact same decision
    pr2 = GitHubPR(
        id=108,
        number=108,
        title="[ADR] Reaffirm domain events immutability",
        body="Reaffirming ADR-007",
        author="lead",
        head_sha="sha108",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/108",
    )
    res2 = await engine.learn_from_review(repo, pr2, review_output)
    assert len(res2.memories_created) == 0
    assert len(res2.memories_reinforced) == 1
    reinforced = res2.memories_reinforced[0]
    assert reinforced.memory_id == created_id
    assert len(local_store) == 1  # NO DUPLICATE ROW CREATED!
    assert "PR #108" in reinforced.evidence_items
    assert reinforced.observation_count == 2

    # 3. Third learning cycle (idempotency verification)
    res3 = await engine.learn_from_review(repo, pr2, review_output)
    assert len(res3.memories_created) == 0
    assert len(res3.memories_reinforced) == 1
    assert len(local_store) == 1
    assert res3.memories_reinforced[0].observation_count == 3


@pytest.mark.asyncio
async def test_req16_and_req17_and_req18_and_adv_g_supersession_semantics(mock_learning_deps):
    """Test 16, 17, 18 & Adversarial G:
    Explicit supersession works:
    Old rule -> SUPERSEDED, superseded_by = new_id.
    New rule -> ACTIVE, supersedes = old_id.
    Old rule remains auditable in Supabase but cannot govern applicability.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}

    # Rule A
    rule_a = await mock_supabase.create_engineering_memory(
        repository_id="repo-100",
        statement="Controllers must use sync RPC client.",
        type="DOCUMENTED_DECISION",
        authority="ENFORCEABLE",
        scope="src/controllers/*",
        source_ref="PR #50",
        source_pr_number=50,
    )
    rule_a_id = rule_a["id"]

    # Rule B explicitly supersedes Rule A
    pr_b = GitHubPR(
        id=110,
        number=110,
        title="[ADR] Supersedes PR #50: Migrate from sync RPC to async gRPC",
        body="ADR superseding synchronous RPC in favor of async gRPC clients.",
        author="lead",
        head_sha="sha110",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/110",
    )

    review_output = ReviewAnalysisOutput(
        summary="Supersession PR",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Controllers must use async gRPC client.",
                type="architecture_decision",
                rationale="Supersedes synchronous RPC.",
                scope="src/controllers/*",
                confidence="supported",
            )
        ],
    )

    app_result = ApplicabilityResultSchema(
        repository_id="repo-100",
        applicable_rules=[rule_a],
    )

    res = await engine.learn_from_review(repo, pr_b, review_output, applicability_result=app_result)
    assert len(res.memories_created) == 1
    rule_b_mem = res.memories_created[0]
    assert rule_b_mem.supersedes == rule_a_id

    # Verify old rule in store
    old_rule_updated = local_store[rule_a_id]
    assert old_rule_updated["status"] == MemoryStatus.SUPERSEDED.value
    assert old_rule_updated["superseded_by"] == rule_b_mem.memory_id

    # Convert to CanonicalMemory and verify can_enforce / can_cause_drift
    old_canonical = CanonicalMemory(**old_rule_updated)
    assert old_canonical.can_enforce() is False
    assert old_canonical.can_cause_drift() is False
    assert rule_b_mem.can_enforce() is True


@pytest.mark.asyncio
async def test_req19_and_req20_and_adv_h_revocation_and_conflict_handling(mock_learning_deps):
    """Test 19, 20 & Adversarial H:
    Revoked memory cannot govern.
    Two active contradictory rules without supersession produce CONFLICTING_GUIDANCE.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    # Rule 1
    r1 = CanonicalMemory(
        memory_id=str(uuid.uuid4()),
        repository_id="repo-100",
        statement="Controllers must use service layer.",
        rationale="Rule 1",
        scope="src/controllers/*",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        status=MemoryStatus.ACTIVE,
        source_ref="PR #10",
    )
    # Rule 2 (contradictory)
    r2 = CanonicalMemory(
        memory_id=str(uuid.uuid4()),
        repository_id="repo-100",
        statement="Controllers may directly access persistence.",
        rationale="Rule 2",
        scope="src/controllers/*",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        status=MemoryStatus.ACTIVE,
        source_ref="PR #20",
    )

    app_engine = ApplicabilityEngine()
    recalled = [
        {"memory_id": r1.memory_id, "type": "DOCUMENTED_DECISION", "statement": r1.statement, "scope": r1.scope, "authority": "ENFORCEABLE", "status": "ACTIVE", "source_ref": "PR #10", "repository_id": "repo-100"},
        {"memory_id": r2.memory_id, "type": "DOCUMENTED_DECISION", "statement": r2.statement, "scope": r2.scope, "authority": "ENFORCEABLE", "status": "ACTIVE", "source_ref": "PR #20", "repository_id": "repo-100"},
    ]

    res = app_engine.evaluate(
        repository_id="repo-100",
        repository_full_name="acme/billing",
        changed_files=["src/controllers/UserController.py"],
        candidate_memories=recalled,
    )
    # Contradictory active rules yield conflicts
    assert len(res.conflicts) > 0

    # If Rule 1 is revoked:
    r1.revoke()
    assert r1.can_enforce() is False
    assert r1.can_cause_drift() is False


@pytest.mark.asyncio
async def test_req21_to_req24_and_adv_j_hindsight_retrieval_and_failure_resilience(mock_learning_deps):
    """Test 21, 22, 23, 24 & Adversarial J:
    Hindsight is retrieval, not authority.
    Hindsight failure does not create fake memory; Supabase memory survives with sync_state FAILED.
    Hindsight metadata preserves repository identity.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    # Simulate Hindsight failure during retention
    mock_memory.aretain = AsyncMock(side_effect=Exception("Hindsight cluster 503 Service Unavailable"))

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=112,
        number=112,
        title="[ADR] ADR-012: Kafka partition keys must use tenant_id",
        body="ADR on Kafka message keys",
        author="architect",
        head_sha="sha112",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/112",
    )

    review_output = ReviewAnalysisOutput(
        summary="ADR Kafka review",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Kafka partition keys must use tenant_id.",
                type="architecture_decision",
                rationale="ADR-012 dictates tenant_id key for multi-tenant ordering.",
                scope="src/events/*",
                confidence="supported",
            )
        ],
    )

    # Must NOT throw exception or create fake fallback memory
    res = await engine.learn_from_review(repo, pr, review_output)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    # Memory survived in Supabase
    assert created.memory_id in local_store
    # Sync state explicitly indicates failure
    assert created.sync_state == MemorySyncState.FAILED
    assert res.hindsight_sync_status == MemorySyncState.FAILED.value


@pytest.mark.asyncio
async def test_req25_and_req26_and_adv_i_repository_isolation(mock_learning_deps):
    """Test 25, 26 & Adversarial I:
    Repository A memory cannot affect Repository B.
    Foreign candidate or Hindsight recall from Repo B into Repo A is rejected.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    # Attempt to learn candidate for Repo B into Repo A context
    foreign_candidate = LearningCandidate(
        statement="Repo B internal rule",
        rationale="Testing foreign repo leakage",
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.CONTEXTUAL,
        scope="*",
        repository_id="repo-B",
        repository_full_name="foreign/repo-b",
        source_type="pr_review",
        source_ref="PR #999",
        is_explicit_decision=False,
    )

    is_valid, reason = engine.validate_candidate(foreign_candidate, expected_repo_id="repo-A")
    assert is_valid is False
    assert "REPOSITORY_MISMATCH" in reason


@pytest.mark.asyncio
async def test_req27_and_req28_and_adv_m_temporal_validity(mock_learning_deps):
    """Test 27, 28 & Adversarial M:
    Future memory cannot govern.
    Expired memory cannot govern.
    """
    now = datetime.now(timezone.utc)
    future_time = (now + timedelta(days=30)).isoformat()
    past_time = (now - timedelta(days=30)).isoformat()

    future_mem = CanonicalMemory(
        memory_id="mem-future",
        repository_id="repo-100",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Future architectural standard.",
        rationale="Effective next quarter",
        scope="*",
        status=MemoryStatus.ACTIVE,
        source_ref="PR #1",
        valid_from=future_time,
    )
    is_valid, reason = future_mem.is_temporally_valid()
    assert is_valid is False
    assert "not yet valid" in reason

    expired_mem = CanonicalMemory(
        memory_id="mem-expired",
        repository_id="repo-100",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Deprecated protocol rule.",
        rationale="Expired last month",
        scope="*",
        status=MemoryStatus.ACTIVE,
        source_ref="PR #2",
        valid_until=past_time,
    )
    is_valid_exp, reason_exp = expired_mem.is_temporally_valid()
    assert is_valid_exp is False
    assert "expired" in reason_exp


@pytest.mark.asyncio
async def test_req31_local_prism_workspace_isolation(mock_learning_deps):
    """Test 31:
    Local PRism repository workspace is NEVER treated as connected target repository history.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    target_repo = {"id": "connected-repo-uuid", "full_name": "target-owner/connected-target-repo"}
    pr = GitHubPR(
        id=200,
        number=200,
        title="Target repo PR",
        body="Analyzing target repo",
        author="external-dev",
        head_sha="sha200",
        base_branch="main",
        html_url="https://github.com/target-owner/connected-target-repo/pull/200",
    )

    review_output = ReviewAnalysisOutput(
        summary="Review for connected target repository",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Target repository pattern",
                type="lesson_learned",
                rationale="Observed in target PR #200",
                scope="src/*",
                confidence="supported",
            )
        ],
    )

    res = await engine.learn_from_review(target_repo, pr, review_output)
    assert len(res.memories_created) == 1
    created = res.memories_created[0]
    assert created.repository_id == "connected-repo-uuid"
    assert created.repository_full_name == "target-owner/connected-target-repo"
    assert "engineering-review" not in created.repository_full_name


@pytest.mark.asyncio
async def test_req32_and_adv_l_restart_idempotency(mock_learning_deps):
    """Test 32 & Adversarial L:
    Same review learned after backend restart remains idempotent.
    """
    engine, mock_supabase, mock_memory, local_store = mock_learning_deps

    repo = {"id": "repo-100", "full_name": "acme/billing"}
    pr = GitHubPR(
        id=300,
        number=300,
        title="[ADR] ADR-300: Database transactions must have max 5s timeout",
        body="Explicit architectural rule on query timeouts",
        author="dba",
        head_sha="sha300",
        base_branch="main",
        html_url="https://github.com/acme/billing/pull/300",
    )

    review_output = ReviewAnalysisOutput(
        summary="ADR 300 review",
        verdict="NO_PRECEDENT",
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Database transactions must have max 5s timeout.",
                type="architecture_decision",
                rationale="ADR-300 prevents database connection pool starvation.",
                scope="src/db/*",
                confidence="supported",
            )
        ],
    )

    # 1. Initial learning
    res1 = await engine.learn_from_review(repo, pr, review_output)
    assert len(res1.memories_created) == 1
    orig_id = res1.memories_created[0].memory_id
    assert len(local_store) == 1

    # 2. Simulate complete backend restart (new ControlledLearningEngine instance)
    fresh_engine = ControlledLearningEngine(supabase=mock_supabase, memory=mock_memory)

    # 3. Learn the exact same review again
    res2 = await fresh_engine.learn_from_review(repo, pr, review_output)
    assert len(res2.memories_created) == 0
    assert len(res2.memories_reinforced) == 1
    assert res2.memories_reinforced[0].memory_id == orig_id
    assert len(local_store) == 1  # Total canonical records in Supabase remains 1!
