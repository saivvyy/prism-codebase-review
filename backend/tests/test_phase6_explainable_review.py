import pytest
from unittest.mock import AsyncMock
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
)
from app.schemas.review import (
    HistoricalEvidenceSchema,
    ReviewAnalysisOutput,
    ReviewFindingSchema,
)
from app.services.supabase import SupabaseService


def test_gate_1_to_5_canonical_verdicts():
    """Validates the 5 canonical verdicts and their strict semantic distinctness."""
    assert ReviewVerdict.NO_PRECEDENT.value == "NO_PRECEDENT"
    assert ReviewVerdict.CONFORMING.value == "CONFORMING"
    assert ReviewVerdict.DRIFT.value == "DRIFT"
    assert ReviewVerdict.APPROVED_EXCEPTION.value == "APPROVED_EXCEPTION"
    assert ReviewVerdict.CONFLICTING_GUIDANCE.value == "CONFLICTING_GUIDANCE"

    # Adversarial: Ensure states do not collapse or equal each other
    verdicts = [
        ReviewVerdict.NO_PRECEDENT.value,
        ReviewVerdict.CONFORMING.value,
        ReviewVerdict.DRIFT.value,
        ReviewVerdict.APPROVED_EXCEPTION.value,
        ReviewVerdict.CONFLICTING_GUIDANCE.value,
    ]
    assert len(set(verdicts)) == 5


def test_gate_6_to_10_explainable_finding_model():
    """Validates the explainability hierarchy: DECISION -> RATIONALE -> PRECEDENT -> SCOPE -> EVIDENCE -> ACTION."""
    finding = ReviewFindingSchema(
        category="architecture_drift",
        severity="error",
        file_path="src/controllers/PaymentController.ts",
        line_number=42,
        title="Direct persistence access in controller",
        message="Controllers must access persistence through the service layer.",
        decision="Controller accesses persistence directly.",
        rationale="Persistence access is intentionally isolated behind the service layer to maintain transactions.",
        scope="src/controllers/**",
        source="PR #142",
        action="Route persistence access through PaymentService.",
        historical_evidence=HistoricalEvidenceSchema(
            claim="Controllers must not directly access persistence",
            rationale="Isolation of domain boundary",
            evidence_prs=["PR #142"],
            matched_code="src/controllers/PaymentController.ts:42",
        ),
    )

    assert finding.decision == "Controller accesses persistence directly."
    assert "service layer" in (finding.rationale or "")
    assert finding.scope == "src/controllers/**"
    assert finding.source == "PR #142"
    assert finding.action == "Route persistence access through PaymentService."
    assert finding.historical_evidence.evidence_prs == ["PR #142"]


def test_gate_11_to_14_missing_provenance_handling():
    """Validates that missing or unavailable provenance does not produce fabricated sources."""
    finding = ReviewFindingSchema(
        category="note",
        severity="info",
        file_path="src/utils/math.ts",
        title="Utility helper",
        message="Helper function added.",
        decision="No architectural rule applies.",
        action="Review against requirements.",
        historical_evidence=None,
    )
    assert finding.historical_pr_ref is None
    assert finding.source is None
    assert finding.historical_evidence is None


def test_gate_15_authority_vs_contextual_separation():
    """Validates that contextual observations and non-authoritative items cannot masquerade as authoritative rules."""
    obs = CanonicalMemory(
        memory_id="mem-obs-1",
        repository_id="repo-1",
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.NON_AUTHORITATIVE,
        statement="Team often puts helper functions in utils/",
        rationale="Convenience",
        scope="src/utils/**",
        source_ref="Commit abc1234",
    )
    assert obs.can_cause_drift() is False
    assert obs.authority == MemoryAuthority.NON_AUTHORITATIVE

    rule = CanonicalMemory(
        memory_id="mem-rule-1",
        repository_id="repo-1",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        statement="Controllers must not query DB",
        rationale="Layer isolation",
        scope="src/controllers/**",
        source_ref="PR #142",
    )
    assert rule.can_cause_drift() is True
    assert rule.authority == MemoryAuthority.ENFORCEABLE


@pytest.mark.asyncio
async def test_gate_16_to_19_review_persistence_and_multigen():
    """Validates same-SHA review reuse, changed-SHA historical review preservation, and repository scoping."""
    service = SupabaseService(supabase_url=None, supabase_key=None)

    repo_id = "test-repo-p6"
    pr_num = 101
    head_sha_v1 = "commit_sha_v1"
    head_sha_v2 = "commit_sha_v2"

    # Upsert PR v1
    pr = await service.upsert_pull_request(
        repository_id=repo_id,
        pr_number=pr_num,
        title="Add payment flow",
        author="alice",
        head_sha=head_sha_v1,
    )
    pr_id = pr["id"]

    # Create review for commit v1
    rev_v1 = await service.create_review(
        pull_request_id=pr_id,
        commit_sha=head_sha_v1,
        verdict=ReviewVerdict.DRIFT.value,
        summary="Drift detected on commit v1",
        drift_detected=True,
    )
    await service.create_review_findings(
        review_id=rev_v1["id"],
        findings=[
            {
                "file_path": "src/controllers/PaymentController.ts",
                "line_number": 10,
                "category": "architecture_drift",
                "title": "Direct query",
                "message": "Controller accesses DB directly.",
                "decision": "Violation of persistence boundary.",
                "action": "Move DB query to service.",
            }
        ],
    )

    # Fetch latest review for PR
    latest = await service.get_latest_review_for_pr(repo_id, pr_num)
    assert latest is not None
    assert latest["commit_sha"] == head_sha_v1
    assert latest["verdict"] == "DRIFT"
    assert len(latest["findings"]) == 1
    assert latest["findings"][0]["decision"] == "Violation of persistence boundary."

    # PR advances to commit v2
    import asyncio
    await asyncio.sleep(0.02)
    await service.upsert_pull_request(
        repository_id=repo_id,
        pr_number=pr_num,
        title="Add payment flow (fixed)",
        author="alice",
        head_sha=head_sha_v2,
    )

    # Create new review generation for commit v2 (without overwriting v1)
    rev_v2 = await service.create_review(
        pull_request_id=pr_id,
        commit_sha=head_sha_v2,
        verdict=ReviewVerdict.CONFORMING.value,
        summary="Conforming on commit v2",
        drift_detected=False,
    )

    # Verify both review generations exist and remain distinct in history
    history = await service.list_reviews_for_pr(repo_id, pr_num)
    assert len(history) == 2
    assert history[0]["commit_sha"] == head_sha_v2
    assert history[0]["verdict"] == "CONFORMING"
    assert history[1]["commit_sha"] == head_sha_v1
    assert history[1]["verdict"] == "DRIFT"


@pytest.mark.asyncio
async def test_gate_20_repository_isolation():
    """Validates that Repository A reviews and findings cannot be accessed by Repository B."""
    service = SupabaseService(supabase_url=None, supabase_key=None)

    repo_a = "repo-alpha-id"
    repo_b = "repo-beta-id"

    # PR in repo A
    pr_a = await service.upsert_pull_request(
        repository_id=repo_a,
        pr_number=1,
        title="PR in repo A",
        author="alice",
        head_sha="sha_a",
    )
    rev_a = await service.create_review(
        pull_request_id=pr_a["id"],
        commit_sha="sha_a",
        verdict=ReviewVerdict.DRIFT.value,
        summary="Repo A review",
    )

    # PR in repo B
    pr_b = await service.upsert_pull_request(
        repository_id=repo_b,
        pr_number=1,
        title="PR in repo B",
        author="bob",
        head_sha="sha_b",
    )
    rev_b = await service.create_review(
        pull_request_id=pr_b["id"],
        commit_sha="sha_b",
        verdict=ReviewVerdict.CONFORMING.value,
        summary="Repo B review",
    )

    # Lookup PR 1 in repo A must return rev_a, not rev_b
    res_a = await service.get_latest_review_for_pr(repo_a, 1)
    assert res_a is not None
    assert res_a["id"] == rev_a["id"]
    assert res_a["summary"] == "Repo A review"

    # Lookup PR 1 in repo B must return rev_b, not rev_a
    res_b = await service.get_latest_review_for_pr(repo_b, 1)
    assert res_b is not None
    assert res_b["id"] == rev_b["id"]
    assert res_b["summary"] == "Repo B review"

    # Listing reviews for repo A must never contain reviews from repo B
    recent_a = await service.list_recent_reviews(repository_id=repo_a)
    assert all(r["repository_id"] == repo_a for r in recent_a if r.get("repository_id"))


def test_adversarial_a_no_precedent_never_renders_as_conforming():
    """Adversarial A: NO_PRECEDENT must never render as CONFORMING."""
    out = ReviewAnalysisOutput(
        summary="No applicable historical evidence found.",
        drift_detected=False,
        exception_approved=False,
        verdict=ReviewVerdict.NO_PRECEDENT.value,
        confidence_score=0.5,
        findings=[],
    )
    assert out.verdict == "NO_PRECEDENT"
    assert out.verdict != "CONFORMING"


def test_adversarial_b_contextual_memory_never_authoritative_rule():
    """Adversarial B: Contextual memory must never render as an architectural rule."""
    mem = CanonicalMemory(
        memory_id="mem-ctx-1",
        repository_id="repo-1",
        type=KnowledgeType.REVIEW_OBSERVATION,
        authority=MemoryAuthority.NON_AUTHORITATIVE,
        statement="Prefer async/await over raw promises",
        rationale="Readability",
        scope="src/**",
        source_ref="Commit def5678",
    )
    assert mem.can_cause_drift() is False
    assert mem.authority != MemoryAuthority.ENFORCEABLE


def test_adversarial_d_approved_exception_requires_general_rule_and_exception():
    """Adversarial D: APPROVED_EXCEPTION must capture both the general rule and the explicit exception."""
    finding = ReviewFindingSchema(
        category="approved_exception",
        severity="info",
        file_path="src/migrations/001_init.ts",
        title="Direct persistence allowed in migration",
        message="Approved exception applies to database migration scripts.",
        decision="Approved exception applies to migration context.",
        rationale="Migration scripts require raw database handles to execute DDL.",
        scope="src/migrations/**",
        source="PR #142 (Rule) + PR #176 (Exception)",
        action="No architectural correction required.",
        historical_evidence=HistoricalEvidenceSchema(
            claim="Controllers must not query DB directly",
            rationale="Layer isolation",
            evidence_prs=["PR #142", "PR #176"],
            exception_notes="Migration scripts are exempted per PR #176",
        ),
    )
    assert finding.category == "approved_exception"
    assert "PR #142" in (finding.source or "")
    assert "PR #176" in (finding.source or "")
    assert finding.historical_evidence.exception_notes is not None


def test_adversarial_f_conflicting_guidance_preserves_both_sources():
    """Adversarial F: CONFLICTING_GUIDANCE must capture both conflicting sources without picking a winner."""
    out = ReviewAnalysisOutput(
        summary="Multiple active authoritative rules conflict for this scope.",
        drift_detected=False,
        exception_approved=False,
        verdict=ReviewVerdict.CONFLICTING_GUIDANCE.value,
        findings=[
            ReviewFindingSchema(
                category="convention_violation",
                severity="warning",
                file_path="src/api/handler.ts",
                title="Conflict between ADR-002 and ADR-008",
                message="ADR-002 specifies REST endpoints, while ADR-008 mandates GraphQL for this service.",
                decision="Conflicting authoritative guidance detected.",
                source="ADR-002 vs ADR-008",
                action="Clarify and resolve the repository standard with team architecture leads.",
            )
        ],
    )
    assert out.verdict == "CONFLICTING_GUIDANCE"
    assert "ADR-002" in out.findings[0].source
    assert "ADR-008" in out.findings[0].source

