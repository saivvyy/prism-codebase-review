"""Tests for Phase 3 Recall + Deterministic Applicability Engine.

Verifies scenarios A through Z:
A. No recalled memory -> NO_PRECEDENT
B. Recalled memory from another repository -> ignored
C. OBSERVED_CONVENTION -> cannot cause DRIFT
D. REVIEW_OBSERVATION -> cannot cause DRIFT
E. Active enforceable rule + actual violation -> DRIFT
F. Active enforceable rule + positive compliance evidence -> CONFORMING
G. Active general rule + valid scoped exception -> APPROVED_EXCEPTION
H. Exception scope mismatch -> DRIFT
I. Revoked rule -> cannot enforce
J. Superseded rule -> cannot enforce
K. Expired rule -> cannot enforce
L. Future rule -> cannot enforce
M. Conflicting active authoritative rules -> CONFLICTING_GUIDANCE
N. Explicit supersession -> conflict resolved correctly
O. Hindsight similarity cannot override authority
P. Hindsight unavailable -> no fake memory
Q. Irrelevant recalled memories -> filtered
R. Cross-repository recall isolation
S. Multiple equivalent supporting memories -> deduplicated/combined
T. Structural Tree-sitter evidence correctly affects scope
U. No applicable precedent -> NO_PRECEDENT
V. Historical implementation frequency only -> cannot cause DRIFT
W. Bootstrap-created ADR memory -> recallable and applicable through ReviewEngine
X. Bootstrap-created exception -> correct exception semantics
Y. Recalled memory with invalid provenance -> cannot enforce
Z. Conflicting memory from another repository -> ignored, not CONFLICTING_GUIDANCE
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
    compute_memory_identity,
)
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.schemas.memory import MemoryItemSchema, RecallMemoryResponse
from app.schemas.review import (
    ReviewAnalysisOutput,
    ReviewFindingSchema,
    HistoricalEvidenceSchema,
)
from app.services.applicability import ApplicabilityEngine
from app.services.code_analysis import CodeAnalysisService
from app.services.review_engine import ReviewEngine


@pytest.fixture
def base_repo_id():
    return str(uuid.uuid4())


@pytest.fixture
def base_engine_deps(base_repo_id):
    mock_github = MagicMock()
    mock_code_analysis = MagicMock()
    mock_memory = MagicMock()
    mock_groq = MagicMock()
    mock_supabase = MagicMock()

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": base_repo_id, "full_name": "acme/payments"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-db-uuid", "pr_number": 101}
    )
    mock_supabase.create_review = AsyncMock(
        return_value={"id": "review-uuid-1"}
    )
    mock_supabase.create_review_findings = AsyncMock(return_value=[])
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])

    mock_code_analysis.analyze_file = AsyncMock(return_value=MagicMock(classes=[], functions=[], function_calls=[], imports=[]))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=mock_code_analysis,
        memory=mock_memory,
        groq=mock_groq,
        supabase=mock_supabase,
    )
    return engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase


def build_pr_fixture(pr_number: int = 101, files=None):
    if files is None:
        files = [
            GitHubFileDiff(
                filename="src/controllers/payment_controller.py",
                status="modified",
                additions=10,
                deletions=2,
                patch="@@ -10,2 +10,10 @@\n+db.execute('SELECT 1')",
            )
        ]
    return PRDetailResponse(
        pr=GitHubPR(
            id=pr_number,
            number=pr_number,
            title="Update payment controller logic",
            body="Enhance transaction handling",
            state="open",
            html_url=f"https://github.com/acme/payments/pull/{pr_number}",
            author="developer",
            head_sha="head123",
            base_branch="main",
            head_branch="feature/payments",
            created_at="2026-09-30T12:00:00Z",
            updated_at="2026-09-30T12:00:00Z",
        ),
        files=files,
    )


# A & U. No recalled memory / No applicable precedent -> NO_PRECEDENT
@pytest.mark.asyncio
async def test_scenario_a_and_u_no_recalled_memory(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="Clean PR with no architecture concerns.",
        findings=[],
        verdict=ReviewVerdict.NO_PRECEDENT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert review.drift_detected is False
    assert review.exception_approved is False


# B & R. Recalled memory from another repository -> strictly ignored
@pytest.mark.asyncio
async def test_scenario_b_and_r_cross_repo_isolation(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    foreign_repo_id = str(uuid.uuid4())
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    # Foreign memory recalled
    foreign_item = MemoryItemSchema(
        id="foreign-mem-1",
        text="Controllers must not call database directly.",
        score=0.99,
        type="observation",
        metadata={
            "repository_id": foreign_repo_id,
            "category": "DOCUMENTED_DECISION",
            "scope": "*Controller*",
            "pr_number": 42,
        },
    )
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=1, items=[foreign_item])
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="No applicable local precedent.",
        findings=[],
        verdict=ReviewVerdict.NO_PRECEDENT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.NO_PRECEDENT.value
    # Ensure foreign memory was marked rejected under repository_mismatch
    assert review.applicability_result is not None
    rejected_reasons = [r["rejection_reason"] for r in review.applicability_result.rejected_memories]
    assert "repository_mismatch" in rejected_reasons


# C & V. OBSERVED_CONVENTION / Historical frequency -> cannot cause DRIFT
@pytest.mark.asyncio
async def test_scenario_c_and_v_observed_convention_cannot_cause_drift(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    obs_mem = {
        "memory_id": "mem-obs-1",
        "repository_id": base_repo_id,
        "type": "OBSERVED_CONVENTION",
        "authority": "CONTEXTUAL",
        "statement": "Most controllers historically instantiate services directly.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #55",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[obs_mem])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    # Groq hallucinates drift based solely on observation
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Drift detected against convention.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="warning",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="Convention deviation",
                message="Did not instantiate service directly.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    # Must NOT be DRIFT because OBSERVED_CONVENTION cannot cause drift
    assert review.verdict != ReviewVerdict.DRIFT.value
    assert review.drift_detected is False


# D. REVIEW_OBSERVATION -> cannot cause DRIFT
@pytest.mark.asyncio
async def test_scenario_d_review_observation_cannot_cause_drift(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    rev_mem = {
        "memory_id": "mem-rev-1",
        "repository_id": base_repo_id,
        "type": "REVIEW_OBSERVATION",
        "authority": "NON_AUTHORITATIVE",
        "statement": "Reviewer noted that controllers should consider logging inputs.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #72",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rev_mem])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Drift detected against reviewer observation.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="Missing logging",
                message="Review observation not followed.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict != ReviewVerdict.DRIFT.value
    assert review.drift_detected is False


# E. Active enforceable rule + actual violation -> DRIFT
@pytest.mark.asyncio
async def test_scenario_e_enforceable_rule_violation_is_drift(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    rule = {
        "memory_id": "rule-1",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must not directly access database or persistence.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #142",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rule])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct persistence access detected in payment_controller.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="Controllers must not directly access persistence",
                message="Direct SQL call detected.",
                historical_pr_ref="PR #142",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.DRIFT.value
    assert review.drift_detected is True
    assert review.exception_approved is False


# F. Active enforceable rule + positive compliance evidence -> CONFORMING
@pytest.mark.asyncio
async def test_scenario_f_enforceable_rule_positive_compliance_is_conforming(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    rule = {
        "memory_id": "rule-1",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must delegate all operations to PaymentService.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #142",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rule])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="PR conforms with architectural guidelines by delegating to PaymentService.",
        findings=[],
        verdict=ReviewVerdict.CONFORMING.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.CONFORMING.value
    assert review.drift_detected is False


# G. Active general rule + valid scoped exception -> APPROVED_EXCEPTION
@pytest.mark.asyncio
async def test_scenario_g_active_rule_with_valid_scoped_exception(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/controllers/health_check_controller.py",
            status="modified",
            additions=5,
            deletions=0,
            patch="@@ -1,3 +1,8 @@\n+db.ping()",
        )
    ]))

    rule = {
        "memory_id": "rule-1",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must not directly access persistence.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #142",
    }
    exc = {
        "memory_id": "exc-1",
        "repository_id": base_repo_id,
        "type": "APPROVED_EXCEPTION",
        "authority": "EXCEPTION",
        "statement": "HealthCheckController is permitted to ping database directly for liveness probe.",
        "scope": "src/controllers/health_check_controller.py",
        "status": "ACTIVE",
        "source_ref": "PR #205",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rule, exc])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct db ping in health check controller.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/health_check_controller.py",
                line_number=2,
                title="Direct database ping",
                message="Health check directly pings db.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.APPROVED_EXCEPTION.value
    assert review.exception_approved is True
    assert review.drift_detected is False


# H. Exception scope mismatch -> DRIFT
@pytest.mark.asyncio
async def test_scenario_h_exception_scope_mismatch_remains_drift(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/controllers/payment_controller.py",
            status="modified",
            additions=5,
            deletions=0,
            patch="@@ -1,3 +1,8 @@\n+db.execute('SELECT 1')",
        )
    ]))

    rule = {
        "memory_id": "rule-1",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must not directly access persistence.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #142",
    }
    exc = {
        "memory_id": "exc-1",
        "repository_id": base_repo_id,
        "type": "APPROVED_EXCEPTION",
        "authority": "EXCEPTION",
        "statement": "HealthCheckController is permitted to ping database directly.",
        "scope": "src/controllers/health_check_controller.py",
        "status": "ACTIVE",
        "source_ref": "PR #205",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rule, exc])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct db execute in payment controller.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=2,
                title="Direct database access",
                message="Direct persistence query executed inside PaymentController.",
                historical_pr_ref="PR #142",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.DRIFT.value
    assert review.drift_detected is True
    assert review.exception_approved is False


# I & J. Revoked and Superseded rules cannot enforce
@pytest.mark.asyncio
async def test_scenario_i_and_j_revoked_and_superseded_rules_cannot_enforce(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    revoked_rule = {
        "memory_id": "rule-revoked",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must use XML-RPC format.",
        "scope": "src/controllers/*",
        "status": "REVOKED",
        "source_ref": "PR #10",
    }
    superseded_rule = {
        "memory_id": "rule-old",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must return SOAP envelopes.",
        "scope": "src/controllers/*",
        "status": "SUPERSEDED",
        "source_ref": "PR #20",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[revoked_rule, superseded_rule])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="PR does not return SOAP envelopes.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="SOAP format violation",
                message="Violated old superseded rule.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert review.drift_detected is False


# K & L. Temporal validity (expired rule & future rule cannot enforce)
def test_scenario_k_and_l_temporal_validity(base_repo_id):
    app_engine = ApplicabilityEngine()
    now_time = "2026-09-30T12:00:00Z"

    expired_mem = CanonicalMemory(
        memory_id="mem-expired",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Temporary requirement during Q1 2025.",
        scope="*",
        source_ref="PR #88",
        status=MemoryStatus.ACTIVE,
        valid_until="2025-03-31T23:59:59Z",
    )

    future_mem = CanonicalMemory(
        memory_id="mem-future",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Requirement taking effect in 2027.",
        scope="*",
        source_ref="PR #99",
        status=MemoryStatus.ACTIVE,
        valid_from="2027-01-01T00:00:00Z",
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[expired_mem, future_mem],
        review_time=now_time,
    )

    assert len(result.applicable_rules) == 0
    rejection_reasons = {r["rejection_reason"] for r in result.rejected_memories}
    assert "expired_guidance" in rejection_reasons
    assert "future_guidance" in rejection_reasons


# M & Z. Conflicting active authoritative rules -> CONFLICTING_GUIDANCE (foreign repo ignored)
@pytest.mark.asyncio
async def test_scenario_m_and_z_conflicting_guidance(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())

    rule_1 = {
        "memory_id": "rule-direct",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must directly access database via raw SQL for high throughput.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #100",
    }
    rule_2 = {
        "memory_id": "rule-service",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must never directly access database and must use services.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #101",
    }
    foreign_conflict = {
        "memory_id": "foreign-conflict",
        "repository_id": str(uuid.uuid4()),
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must use GraphQL only.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #999",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[rule_1, rule_2, foreign_conflict])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="Analysis",
        findings=[],
        verdict=ReviewVerdict.NO_PRECEDENT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.CONFLICTING_GUIDANCE.value
    assert len(review.applicability_result.conflicts) > 0


# N. Explicit supersession resolves conflict
def test_scenario_n_explicit_supersession_resolves_conflict(base_repo_id):
    app_engine = ApplicabilityEngine()

    rule_old = CanonicalMemory(
        memory_id="rule-old",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must use REST endpoints.",
        scope="src/controllers/*",
        source_ref="PR #10",
        status=MemoryStatus.ACTIVE,
        superseded_by="rule-new",
    )
    rule_new = CanonicalMemory(
        memory_id="rule-new",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must use gRPC streaming.",
        scope="src/controllers/*",
        source_ref="PR #20",
        status=MemoryStatus.ACTIVE,
        supersedes="rule-old",
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[rule_old, rule_new],
    )

    assert len(result.conflicts) == 0
    assert len(result.applicable_rules) == 1
    assert result.applicable_rules[0]["memory_id"] == "rule-new"


# O. Hindsight similarity cannot override authority
def test_scenario_o_hindsight_similarity_cannot_override_authority(base_repo_id):
    app_engine = ApplicabilityEngine()

    high_similarity_obs = CanonicalMemory(
        memory_id="mem-obs",
        repository_id=base_repo_id,
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.CONTEXTUAL,
        statement="Controllers frequently call persistence directly in legacy code.",
        scope="src/controllers/*",
        source_ref="PR #5",
        status=MemoryStatus.ACTIVE,
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[high_similarity_obs],
    )

    assert len(result.applicable_rules) == 0
    assert len(result.contextual_memories) == 1


# P. Hindsight unavailable -> no fake memory, hindsight_status="unavailable"
@pytest.mark.asyncio
async def test_scenario_p_hindsight_unavailable_no_fake_memory(base_engine_deps, base_repo_id):
    from app.core.exceptions import HindsightMemoryError

    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())
    mock_memory.recall.side_effect = HindsightMemoryError("Hindsight API timeout")

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="PR clean.",
        findings=[],
        verdict=ReviewVerdict.NO_PRECEDENT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.applicability_result is not None
    assert review.applicability_result.hindsight_status == "unavailable"
    assert review.applicability_result.recalled_memories_count == 0


# Q. Irrelevant recalled memories filtered out
def test_scenario_q_irrelevant_memories_filtered(base_repo_id):
    app_engine = ApplicabilityEngine()

    unrelated_mem = CanonicalMemory(
        memory_id="mem-auth",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Auth tokens must be JWT with RS256 signature.",
        scope="src/auth/*",
        source_ref="PR #12",
        status=MemoryStatus.ACTIVE,
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[unrelated_mem],
    )

    assert len(result.applicable_rules) == 0
    assert any(r["rejection_reason"] == "scope_mismatch" for r in result.rejected_memories)


# S. Multiple equivalent supporting memories deduplicated
def test_scenario_s_multiple_equivalent_memories_deduplicated(base_repo_id):
    app_engine = ApplicabilityEngine()

    mem1 = CanonicalMemory(
        memory_id="mem-1",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must not directly access persistence.",
        scope="src/controllers/*",
        source_ref="PR #142",
        status=MemoryStatus.ACTIVE,
    )
    mem2 = CanonicalMemory(
        memory_id="mem-2",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must not directly access persistence.",
        scope="src/controllers/*",
        source_ref="PR #180",
        status=MemoryStatus.ACTIVE,
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[mem1, mem2],
    )

    assert len(result.applicable_rules) == 1
    assert "PR #142" in result.provenance_references or "PR #180" in result.provenance_references


# T. Structural Tree-sitter evidence correctly affects scope
def test_scenario_t_structural_evidence_affects_scope(base_repo_id):
    app_engine = ApplicabilityEngine()

    rule_by_class = CanonicalMemory(
        memory_id="rule-class",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="PaymentGateway classes must implement retry loops.",
        scope="PaymentGateway",
        source_ref="PR #77",
        status=MemoryStatus.ACTIVE,
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/external/stripe.py"],
        candidate_memories=[rule_by_class],
        structural_symbols={"src/external/stripe.py": ["PaymentGateway", "process_charge"]},
    )

    assert len(result.applicable_rules) == 1
    assert result.applicable_rules[0]["memory_id"] == "rule-class"


# W & X. Bootstrap-created ADR memory & exception
@pytest.mark.asyncio
async def test_scenario_w_and_x_bootstrap_adr_and_exception(base_engine_deps, base_repo_id):
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/migrations/001_initial.py",
            status="modified",
            additions=10,
            deletions=0,
            patch="@@ -1,2 +1,10 @@\n+db.execute('CREATE TABLE payments')",
        )
    ]))

    bootstrap_adr = {
        "memory_id": "adr-004",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Raw SQL queries must not be run directly.",
        "scope": "*",
        "status": "ACTIVE",
        "source_ref": "docs/adr/0004-database-access.md",
        "source_type": "adr",
    }
    bootstrap_exc = {
        "memory_id": "exc-adr-004",
        "repository_id": base_repo_id,
        "type": "APPROVED_EXCEPTION",
        "authority": "EXCEPTION",
        "statement": "Database migration scripts may run direct raw DDL/DML.",
        "scope": "src/migrations/*",
        "status": "ACTIVE",
        "source_ref": "docs/adr/0004-database-access.md",
        "source_type": "adr",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[bootstrap_adr, bootstrap_exc])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Raw DDL statement in migration file.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/migrations/001_initial.py",
                line_number=2,
                title="Direct SQL execution",
                message="CREATE TABLE executed directly.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.APPROVED_EXCEPTION.value
    assert review.exception_approved is True


# Y. Recalled memory with invalid/fake provenance cannot enforce
def test_scenario_y_invalid_provenance_cannot_enforce(base_repo_id):
    app_engine = ApplicabilityEngine()

    fake_prov_mem = CanonicalMemory(
        memory_id="mem-fake",
        repository_id=base_repo_id,
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="All services must use Redis caching.",
        scope="src/controllers/*",
        source_ref="hindsight memory",
        status=MemoryStatus.ACTIVE,
    )

    result = app_engine.evaluate(
        repository_id=base_repo_id,
        repository_full_name="acme/payments",
        changed_files=["src/controllers/payment_controller.py"],
        candidate_memories=[fake_prov_mem],
    )

    assert len(result.applicable_rules) == 0
    assert any(r["rejection_reason"] == "invalid_provenance" for r in result.rejected_memories)


# GATE 2 & 7: Comprehensive Approved Exception vs General Rule Matrix (Cases 1 - 5)
@pytest.mark.asyncio
async def test_gate_7_approved_exception_cases_1_to_5(base_engine_deps, base_repo_id):
    """Verifies all 5 cases of Gate 7:
    Case 1: HealthCheckController changed -> APPROVED_EXCEPTION
    Case 2: PaymentController changed -> DRIFT
    Case 3: Unrelated file changed -> NO_PRECEDENT
    Case 4: Exception revoked -> DRIFT on HealthCheckController
    Case 5: Exception superseded -> DRIFT on HealthCheckController
    """
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps

    gen_rule = {
        "memory_id": "rule-svc",
        "repository_id": base_repo_id,
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "statement": "Controllers must use service layer and never access persistence directly.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #100",
    }
    exc_active = {
        "memory_id": "exc-health",
        "repository_id": base_repo_id,
        "type": "APPROVED_EXCEPTION",
        "authority": "EXCEPTION",
        "statement": "HealthCheckController may directly access persistence.",
        "scope": "src/controllers/health_check_controller.py",
        "status": "ACTIVE",
        "source_ref": "PR #105",
    }
    exc_revoked = dict(exc_active, memory_id="exc-revoked", status="REVOKED")
    exc_superseded = dict(exc_active, memory_id="exc-superseded", status="SUPERSEDED")

    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    # Case 1: HealthCheckController changed + active exception -> APPROVED_EXCEPTION
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[gen_rule, exc_active])
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/controllers/health_check_controller.py",
            status="modified",
            additions=3,
            deletions=0,
            patch="@@ -1,2 +1,5 @@\n+db.ping()",
        )
    ]))
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct db ping in health check.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/health_check_controller.py",
                line_number=2,
                title="Direct DB ping",
                message="Direct persistence call.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )
    rev_c1 = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c1.verdict == ReviewVerdict.APPROVED_EXCEPTION.value
    assert rev_c1.exception_approved is True
    assert rev_c1.drift_detected is False

    # Case 2: PaymentController changed + active exception -> DRIFT (exception scope doesn't apply)
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/controllers/payment_controller.py",
            status="modified",
            additions=3,
            deletions=0,
            patch="@@ -1,2 +1,5 @@\n+db.execute('SELECT 1')",
        )
    ]))
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct db call in payment controller.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=2,
                title="Direct DB call",
                message="Direct persistence call.",
                historical_pr_ref="PR #100",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )
    rev_c2 = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c2.verdict == ReviewVerdict.DRIFT.value
    assert rev_c2.drift_detected is True
    assert rev_c2.exception_approved is False

    # Case 3: Unrelated file changed -> NO_PRECEDENT
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="docs/README.md",
            status="modified",
            additions=2,
            deletions=0,
            patch="@@ -1,2 +1,4 @@\n+# Docs update",
        )
    ]))
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="Docs update.",
        findings=[],
        verdict=ReviewVerdict.NO_PRECEDENT.value,
    )
    rev_c3 = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c3.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert rev_c3.drift_detected is False

    # Case 4: Exception revoked -> DRIFT on HealthCheckController
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[gen_rule, exc_revoked])
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture(files=[
        GitHubFileDiff(
            filename="src/controllers/health_check_controller.py",
            status="modified",
            additions=3,
            deletions=0,
            patch="@@ -1,2 +1,5 @@\n+db.ping()",
        )
    ]))
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Direct db ping in health check.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/health_check_controller.py",
                line_number=2,
                title="Direct DB ping",
                message="Direct persistence call.",
                historical_pr_ref="PR #100",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )
    rev_c4 = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c4.verdict == ReviewVerdict.DRIFT.value
    assert rev_c4.drift_detected is True

    # Case 5: Exception superseded -> DRIFT on HealthCheckController
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[gen_rule, exc_superseded])
    rev_c5 = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c5.verdict == ReviewVerdict.DRIFT.value
    assert rev_c5.drift_detected is True


# GATE 3: REJECTED_PATTERN Authority Separation
@pytest.mark.asyncio
async def test_gate_3_rejected_pattern_authority_separation(base_engine_deps, base_repo_id):
    """Verifies that REJECTED_PATTERN cannot cause DRIFT when authority is CONTEXTUAL or NON_AUTHORITATIVE,
    and only causes DRIFT when ENFORCEABLE or REJECTED_PATTERN authority is active.
    """
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    # A. REJECTED_PATTERN + NON_AUTHORITATIVE -> cannot cause DRIFT
    pat_non_auth = {
        "memory_id": "pat-1",
        "repository_id": base_repo_id,
        "type": "REJECTED_PATTERN",
        "authority": "NON_AUTHORITATIVE",
        "statement": "Passing kwargs in controller methods was discouraged in PR #40.",
        "scope": "src/controllers/*",
        "status": "ACTIVE",
        "source_ref": "PR #40",
    }
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[pat_non_auth])
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="kwargs passed in controller method.",
        findings=[
            ReviewFindingSchema(
                category="rejected_pattern",
                severity="warning",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="kwargs passed",
                message="Matches non-authoritative rejected pattern.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )
    rev_a = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_a.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert rev_a.drift_detected is False

    # B. REJECTED_PATTERN + CONTEXTUAL -> cannot cause DRIFT
    pat_ctx = dict(pat_non_auth, authority="CONTEXTUAL")
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[pat_ctx])
    rev_b = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_b.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert rev_b.drift_detected is False

    # C. REJECTED_PATTERN + ENFORCEABLE authority + violation -> causes DRIFT
    pat_enforceable = dict(pat_non_auth, authority="ENFORCEABLE")
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[pat_enforceable])
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="kwargs passed in controller method.",
        findings=[
            ReviewFindingSchema(
                category="rejected_pattern",
                severity="warning",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="kwargs passed",
                message="Matches enforceable rejected pattern.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )
    rev_c = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert rev_c.verdict == ReviewVerdict.DRIFT.value
    assert rev_c.drift_detected is True


# GATE 6: LLM Hallucinated Drift Without Memory -> NO_PRECEDENT
@pytest.mark.asyncio
async def test_gate_6_groq_hallucinated_drift_without_evidence_yields_no_precedent(base_engine_deps, base_repo_id):
    """When Groq returns drift_detected=True but there is no active enforceable rule, verdict MUST be NO_PRECEDENT."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="I think this code violates good architecture.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=10,
                title="Generic opinionated drift",
                message="Not backed by any repository precedent.",
            )
        ],
        verdict=ReviewVerdict.DRIFT.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert review.drift_detected is False


# GATE 5: "No Violation Detected" alone cannot produce CONFORMING
@pytest.mark.asyncio
async def test_gate_5_no_violation_alone_cannot_produce_conforming(base_engine_deps, base_repo_id):
    """A clean PR with no applicable rules must be NO_PRECEDENT, never CONFORMING."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = base_engine_deps
    mock_github.get_pr_details_full = AsyncMock(return_value=build_pr_fixture())
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])
    mock_memory.recall.return_value = RecallMemoryResponse(bank_id="acme_payments", query="test", results_count=0, items=[])

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="PR is clean, no violation found.",
        findings=[],
        verdict=ReviewVerdict.CONFORMING.value,
    )

    review = await engine.review_pull_request(repo_id=base_repo_id, pr_number=101)
    assert review.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert review.drift_detected is False

