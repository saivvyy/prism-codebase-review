"""
Phase 8 — Context-Aware Fixes Tests.

Covers Section 17 checks A through T and all safety / correction gates:
- A. Finding with valid architectural precedent -> valid fix candidate.
- B. Fix uses actual repository structure (service layer insertion pattern).
- C. Fix references existing symbols.
- D. Fix respects approved exception (no fix generated).
- E. Non-authoritative memory cannot force a fix.
- F. Conflicting guidance blocks unsafe fix generation.
- G. Missing context produces NO_SAFE_FIX rather than hallucination.
- H. Candidate is repository isolated.
- I. Candidate is SHA isolated.
- J. Stale candidate cannot be applied.
- K. Unrelated files cannot be modified.
- L. Candidate requires explicit approval (READY_FOR_APPROVAL -> APPROVED).
- M. Unapproved candidate cannot apply.
- N. Successful apply is not automatically persisted as authoritative memory.
- O. Original finding can be re-evaluated after applying (RESOLVED / 0 new).
- P. Multiple findings remain correctly associated.
- Q. Multiple fixes remain isolated.
- R. Deterministic candidate metadata for same inputs.
- S. Patch validation failure blocks application.
- T. Real API route integration tests.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.schemas.context import (
    PRContext,
    PRIdentity,
    PRIntent,
    ChangeType,
    ChangedFileDetail,
    CodeDependencyGraph,
    DependencyGraphEdge,
    RelatedTestItem,
    ContextBudget,
)
from app.schemas.review import (
    ReviewAnalysisOutput,
    ReviewFindingSchema,
)
from app.schemas.walkthrough import (
    PRWalkthrough,
    ChangeGroup,
    ChangeGroupKind,
    ChangeGroupImpact,
    FileChangeEntry,
)
from app.schemas.fix import (
    FixCandidate,
    FixCandidateStatus,
    FixValidationStatus,
    FindingResolutionStatus,
)
from app.services.fix_engine import (
    FixCandidateEngine,
    get_fix_candidate_engine,
)
from app.services.supabase import SupabaseService


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

def _make_identity(pr_number: int = 42, repo_id: str = "repo-omega", head_sha: str = "commit_sha_888") -> PRIdentity:
    return PRIdentity(
        repository_id=repo_id,
        repository_full_name="org/prism",
        pr_number=pr_number,
        title="Refactor controller direct persistence",
        author="alice",
        head_sha=head_sha,
        changed_files_count=1,
        total_additions=10,
        total_deletions=5,
    )


def _make_intent() -> PRIntent:
    return PRIntent(
        summary="Refactor controller layer",
        goals=["Clean up direct db access"],
        likely_change_type=ChangeType.REFACTOR,
        affected_domain="controllers",
    )


def _make_context(
    repo_id: str = "repo-omega",
    pr_number: int = 42,
    head_sha: str = "commit_sha_888",
    file_path: str = "controllers/user_controller.py",
    content: str = "class UserController:\n    def get_user(self, db, user_id):\n        return db.query(User).get(user_id)\n",
) -> PRContext:
    identity = _make_identity(pr_number=pr_number, repo_id=repo_id, head_sha=head_sha)
    intent = _make_intent()

    changed_file = ChangedFileDetail(
        path=file_path,
        status="modified",
        additions=5,
        deletions=2,
        language="python",
        classes=["UserController"],
        functions=["get_user"],
        affected_symbols=["UserController", "get_user"],
    )

    dep_graph = CodeDependencyGraph(
        edges=[
            DependencyGraphEdge(
                source="UserController",
                target="UserService",
                relationship="imports",
            )
        ],
    )

    return PRContext(
        pr=identity,
        intent=intent,
        changed_files=[changed_file],
        dependencies=dep_graph,
        related_tests=[
            RelatedTestItem(
                test_file_path="tests/test_user_controller.py",
                target_symbol_or_file="UserController",
                is_in_pr_diff=False,
            )
        ],
        context_budget=ContextBudget(),
    )


def _make_review(
    repo_id: str = "repo-omega",
    pr_number: int = 42,
    verdict: str = "DRIFT",
    findings=None,
) -> ReviewAnalysisOutput:
    if findings is None:
        findings = [
            ReviewFindingSchema(
                file_path="controllers/user_controller.py",
                line_number=2,
                title="Controller directly accesses persistence",
                message="Controllers must not directly access database persistence.",
                suggested_fix="Delegate persistence query to UserService.",
                category="architecture_drift",
                decision="Controllers must use service layer",
                rationale="Separation of concerns",
                scope="controllers/*",
                source="PR #10",
                severity="warning",
            )
        ]

    return ReviewAnalysisOutput(
        summary="Architecture review found persistence drift.",
        drift_detected=(verdict == "DRIFT"),
        exception_approved=(verdict == "APPROVED_EXCEPTION"),
        verdict=verdict,
        findings=findings,
        commit_sha="commit_sha_888",
    )


def _make_walkthrough(repo_id: str = "repo-omega", pr_number: int = 42, head_sha: str = "commit_sha_888") -> PRWalkthrough:
    return PRWalkthrough(
        pr_number=pr_number,
        pr_title="Refactor controller direct persistence",
        repository_id=repo_id,
        head_sha=head_sha,
        intent_summary="Refactor controller layer",
        change_type="REFACTOR",
        groups=[
            ChangeGroup(
                id="group-1",
                kind=ChangeGroupKind.API,
                label="Controllers",
                description="Controller persistence updates",
                impact=ChangeGroupImpact.CONTAINED,
                files=[
                    FileChangeEntry(
                        path="controllers/user_controller.py",
                        status="modified",
                        additions=5,
                        deletions=2,
                        language="python",
                        affected_symbols=["UserController", "get_user"],
                    )
                ],
                symbols_changed=["UserController", "get_user"],
                related_findings_count=1,
                has_test_coverage=True,
                ordering=1,
            )
        ],
        steps=[],
        total_files_changed=1,
        total_additions=5,
        total_deletions=2,
        groups_count=1,
        has_schema_changes=False,
        has_api_changes=False,
        has_test_changes=False,
        has_config_changes=False,
        surfaced_finding_count=1,
        derived_from="Phase 5 & 6 structural evidence",
    )


# ---------------------------------------------------------------------------
# Test Cases A - T
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_finding_with_valid_precedent_generates_fix():
    """A. Finding with valid architectural precedent -> valid fix candidate."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 1
    fix = fixes[0]
    assert any("user_controller.py" in f for f in fix.affected_files)
    assert fix.status == FixCandidateStatus.READY_FOR_APPROVAL
    assert fix.validation_status == FixValidationStatus.PASSED
    assert fix.base_sha == context.pr.head_sha
    assert "UserController" in fix.affected_symbols or "get_user" in fix.affected_symbols
    assert fix.patch_diff != ""
    assert "diff --git" in fix.patch_diff


@pytest.mark.asyncio
async def test_b_fix_uses_actual_repository_structure():
    """B. Fix uses actual repository structure (service layer pattern)."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 1
    fix = fixes[0]
    # Check that the proposed change cites service layer
    assert "service" in fix.proposed_change.lower() or "service" in fix.patch_diff.lower()
    assert fix.applicable_constraint is not None


@pytest.mark.asyncio
async def test_c_fix_references_existing_symbols():
    """C. Fix references existing symbols."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 1
    fix = fixes[0]
    # Affected symbols should come from actual context
    for sym in fix.affected_symbols:
        assert sym in ["UserController", "get_user"]


@pytest.mark.asyncio
async def test_d_fix_respects_approved_exception():
    """D. Fix respects approved exception (no fix proposed)."""
    engine = FixCandidateEngine()
    context = _make_context()
    # Review marked as APPROVED_EXCEPTION
    review = _make_review(
        verdict="APPROVED_EXCEPTION",
        findings=[
            ReviewFindingSchema(
                file_path="controllers/user_controller.py",
                line_number=2,
                title="Approved migration exception",
                message="Controllers accessing persistence during migration is allowed.",
                category="approved_exception",
                decision="Approved exception for migration scripts",
            )
        ],
    )
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 0  # No fix proposed for approved exception


@pytest.mark.asyncio
async def test_e_non_authoritative_memory_cannot_force_fix():
    """E. Non-authoritative memory cannot force a fix."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review(
        verdict="CONFORMING",
        findings=[
            ReviewFindingSchema(
                file_path="controllers/user_controller.py",
                line_number=2,
                title="Just an observation",
                message="Non-authoritative observation.",
                category="review_observation",
            )
        ],
    )
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    # Observations without drift / decision cannot produce an architectural fix
    assert len(fixes) == 0


@pytest.mark.asyncio
async def test_f_conflicting_guidance_blocks_unsafe_fix():
    """F. Conflicting guidance blocks unsafe fix generation."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review(verdict="CONFLICTING_GUIDANCE")
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    # When conflicting guidance is returned, fix generation is blocked
    assert len(fixes) == 0


@pytest.mark.asyncio
async def test_g_missing_context_produces_no_safe_fix():
    """G. Missing context produces NO_SAFE_FIX rather than hallucination."""
    engine = FixCandidateEngine()
    # File not present in changed_files
    context = _make_context()
    context.changed_files.clear()

    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 0


@pytest.mark.asyncio
async def test_h_candidate_is_repository_isolated():
    """H. Candidate is repository isolated."""
    engine = FixCandidateEngine()
    context = _make_context(repo_id="repo-1")
    review = _make_review(repo_id="repo-1")
    walkthrough = _make_walkthrough(repo_id="repo-1")

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 1
    candidate = fixes[0]

    # Attempt validation against wrong repo_id
    wrong_context = _make_context(repo_id="repo-2")
    validated = engine.validate_fix(candidate, wrong_context, review)
    assert validated.validation_status == FixValidationStatus.FAILED
    assert any(c.check_id == "repo_identity" and c.status == FixValidationStatus.FAILED for c in validated.validation_checks)


@pytest.mark.asyncio
async def test_i_candidate_is_sha_isolated():
    """I. Candidate is SHA isolated."""
    engine = FixCandidateEngine()
    context = _make_context(head_sha="sha_alpha")
    review = _make_review()
    walkthrough = _make_walkthrough(head_sha="sha_alpha")

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]
    assert candidate.base_sha == "sha_alpha"

    # Context with different head_sha
    changed_sha_context = _make_context(head_sha="sha_beta")
    validated = engine.validate_fix(candidate, changed_sha_context, review)
    assert validated.validation_status == FixValidationStatus.FAILED
    assert any(c.check_id == "head_sha_match" and c.status == FixValidationStatus.FAILED for c in validated.validation_checks)


@pytest.mark.asyncio
async def test_j_stale_candidate_cannot_be_applied():
    """J. Stale candidate cannot be applied."""
    engine = FixCandidateEngine()
    context = _make_context(head_sha="sha_initial")
    review = _make_review()
    walkthrough = _make_walkthrough(head_sha="sha_initial")

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]

    # Approve candidate
    approved = engine.approve_fix(candidate, approver="alice")
    assert approved.status == FixCandidateStatus.APPROVED

    # HEAD moves to new SHA
    stale_context = _make_context(head_sha="sha_updated")
    with pytest.raises(ValueError) as excinfo:
        engine.apply_fix(approved, stale_context, review)
    assert "Fix is outdated because the repository changed after this suggestion was generated" in str(excinfo.value)


@pytest.mark.asyncio
async def test_k_unrelated_files_cannot_be_modified():
    """K. Unrelated files cannot be modified."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]

    # Inject unauthorized file into candidate
    candidate.affected_files.append("unrelated/secret_config.env")
    validated = engine.validate_fix(candidate, context, review)
    assert validated.validation_status == FixValidationStatus.FAILED
    assert any(c.check_id == "unrelated_files" and c.status == FixValidationStatus.FAILED for c in validated.validation_checks)


@pytest.mark.asyncio
async def test_l_candidate_requires_explicit_approval():
    """L. Candidate requires explicit approval."""
    engine = FixCandidateEngine()
    candidate = FixCandidate(
        finding_id="finding-1",
        repository_id="repo-1",
        pr_number=1,
        base_sha="sha1",
        title="Test Fix",
        explanation="Test",
        affected_files=["file1.py"],
        affected_symbols=["test_sym"],
        proposed_change="Test change",
        rationale="Test rationale",
        patch_diff="--- a\n+++ b",
        status=FixCandidateStatus.READY_FOR_APPROVAL,
        validation_status=FixValidationStatus.PASSED,
    )

    approved = engine.approve_fix(candidate, approver="alice")
    assert approved.status == FixCandidateStatus.APPROVED
    assert approved.is_approved is True
    assert approved.approved_by == "alice"
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_m_unapproved_candidate_cannot_apply():
    """M. Unapproved candidate cannot apply."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]
    assert candidate.status == FixCandidateStatus.READY_FOR_APPROVAL
    assert candidate.is_approved is False

    # Attempt to apply directly without approval
    with pytest.raises(ValueError) as excinfo:
        engine.apply_fix(candidate, context, review)
    assert "Candidate is not approved for application" in str(excinfo.value)


@pytest.mark.asyncio
async def test_n_successful_apply_is_not_authoritative_memory():
    """N. Successful apply is not automatically persisted as authoritative memory."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]
    approved = engine.approve_fix(candidate, approver="alice")

    with patch.object(SupabaseService, "create_engineering_memory", MagicMock()) as mock_create_mem:
        applied = engine.apply_fix(approved, context, review)
        assert applied.status == FixCandidateStatus.APPLIED
        # Learning boundary: Must NEVER automatically create authoritative memory in Phase 8
        mock_create_mem.assert_not_called()


@pytest.mark.asyncio
async def test_o_original_finding_re_evaluated_after_applying():
    """O. Original finding can be re-evaluated after applying."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]
    approved = engine.approve_fix(candidate, approver="alice")

    applied = engine.apply_fix(approved, context, review)
    assert applied.status == FixCandidateStatus.APPLIED
    assert applied.post_apply_validation is not None
    assert applied.post_apply_validation.original_finding_status == FindingResolutionStatus.RESOLVED
    assert applied.post_apply_validation.validation_status == FixValidationStatus.PASSED
    assert applied.post_apply_validation.new_findings_count == 0


@pytest.mark.asyncio
async def test_p_multiple_findings_remain_correctly_associated():
    """P. Multiple findings remain correctly associated."""
    engine = FixCandidateEngine()
    context = _make_context()
    context.changed_files.append(
        ChangedFileDetail(
            path="controllers/item_controller.py",
            status="modified",
            additions=3,
            deletions=1,
            language="python",
            classes=["ItemController"],
            affected_symbols=["ItemController"],
        )
    )

    review = _make_review(
        findings=[
            ReviewFindingSchema(
                file_path="controllers/user_controller.py",
                title="User finding",
                message="Direct user persistence",
                category="architecture_drift",
                decision="No direct user persistence",
            ),
            ReviewFindingSchema(
                file_path="controllers/item_controller.py",
                title="Item finding",
                message="Direct item persistence",
                category="architecture_drift",
                decision="No direct item persistence",
            ),
        ]
    )
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    assert len(fixes) == 2
    affected = {f.affected_files[0] for f in fixes}
    assert "controllers/user_controller.py" in affected
    assert "controllers/item_controller.py" in affected


@pytest.mark.asyncio
async def test_q_multiple_fixes_remain_isolated():
    """Q. Multiple fixes remain isolated."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    fix_a = fixes[0]

    # Create a second alternative fix manually
    fix_b = fix_a.model_copy(update={"id": "fix-alt-2", "title": "Alternative Fix"})

    # Reject fix A
    rejected_a = engine.reject_fix(fix_a, reason="Prefer manual fix")
    assert rejected_a.status == FixCandidateStatus.REJECTED
    # Fix B is unaffected
    assert fix_b.status == FixCandidateStatus.READY_FOR_APPROVAL


@pytest.mark.asyncio
async def test_r_deterministic_candidate_metadata():
    """R. Deterministic candidate metadata for same inputs."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes1 = engine.generate_fixes_for_pr(context, review, walkthrough)
    fixes2 = engine.generate_fixes_for_pr(context, review, walkthrough)

    assert len(fixes1) == len(fixes2) == 1
    assert fixes1[0].title == fixes2[0].title
    assert fixes1[0].affected_files == fixes2[0].affected_files
    assert fixes1[0].proposed_change == fixes2[0].proposed_change
    assert fixes1[0].patch_diff == fixes2[0].patch_diff


@pytest.mark.asyncio
async def test_s_patch_validation_failure_blocks_application():
    """S. Patch validation failure blocks application."""
    engine = FixCandidateEngine()
    context = _make_context()
    review = _make_review()
    walkthrough = _make_walkthrough()

    fixes = engine.generate_fixes_for_pr(context, review, walkthrough)
    candidate = fixes[0]

    # Corrupt patch
    candidate.patch_diff = "MALFORMED PATCH CANNOT APPLY"
    validated = engine.validate_fix(candidate, context, review)
    assert validated.validation_status == FixValidationStatus.FAILED

    with pytest.raises(ValueError) as excinfo:
        engine.approve_fix(validated, approver="alice")
    assert "Only validated candidates can be approved" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T. FastAPI Route Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t_real_api_routes_integration():
    """T. Real API route integration tests for fix lifecycle endpoints."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    repo_id = "test-repo-p8"
    pr_number = 101
    head_sha = "sha-p8-head"

    mock_ctx = _make_context(repo_id=repo_id, pr_number=pr_number, head_sha=head_sha)
    mock_rev = _make_review(repo_id=repo_id, pr_number=pr_number)
    mock_wt = _make_walkthrough(repo_id=repo_id, pr_number=pr_number, head_sha=head_sha)

    from unittest.mock import AsyncMock
    from app.services.supabase import get_supabase_service
    from app.services.context_engine import get_context_engine
    from app.services.review_engine import get_review_engine
    from app.schemas.github import GitHubPR, PRDetailResponse

    mock_supabase = MagicMock()
    mock_supabase.get_repository_by_id = AsyncMock(return_value={
        "id": repo_id,
        "full_name": "org/prism",
        "default_branch": "main",
    })
    mock_supabase.get_latest_review_for_pr = AsyncMock(return_value={
        "summary": "Persistence drift found.",
        "drift_detected": True,
        "exception_approved": False,
        "verdict": "DRIFT",
        "findings": [
            {
                "file_path": "controllers/user_controller.py",
                "line_number": 2,
                "title": "Controller directly accesses persistence",
                "message": "Controllers must not directly access persistence.",
                "category": "architecture_drift",
                "decision": "Controllers must use service layer",
                "severity": "warning",
            }
        ],
    })
    mock_supabase.save_fix_candidate = AsyncMock()
    mock_supabase.list_fix_candidates_for_pr = AsyncMock(return_value=[])
    mock_supabase.get_fix_candidate = AsyncMock()
    mock_supabase.update_fix_candidate = AsyncMock()

    mock_ctx_engine = MagicMock()
    mock_ctx_engine.build_pr_context = AsyncMock(return_value=mock_ctx)

    mock_rev_engine = MagicMock()
    mock_rev_engine.github = MagicMock()
    mock_rev_engine.github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
        pr=GitHubPR(
            id=101,
            number=pr_number,
            title="Refactor controller direct persistence",
            body="Description",
            author="alice",
            head_sha=head_sha,
            base_sha="sha-base",
            base_branch="main",
            state="open",
        ),
        files=[],
    ))

    # Maintain local memory for mock supabase fix storage
    stored_fixes = {}

    async def _mock_save(f_dict):
        stored_fixes[f_dict["fix_id"]] = f_dict
        return f_dict

    async def _mock_get(f_id):
        return stored_fixes.get(f_id)

    async def _mock_list(r_id, pr_num):
        return list(stored_fixes.values())

    async def _mock_update(f_id, updates):
        if f_id in stored_fixes:
            stored_fixes[f_id].update(updates)
            return stored_fixes[f_id]
        return None

    mock_supabase.save_fix_candidate.side_effect = _mock_save
    mock_supabase.get_fix_candidate.side_effect = _mock_get
    mock_supabase.list_fix_candidates_for_pr.side_effect = _mock_list
    mock_supabase.update_fix_candidate.side_effect = _mock_update

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_context_engine] = lambda: mock_ctx_engine
    app.dependency_overrides[get_review_engine] = lambda: mock_rev_engine

    try:
        # 1. POST /fixes/generate
        gen_resp = client.post(
            f"/api/v1/reviews/{repo_id}/{pr_number}/fixes/generate",
            json={},
        )
        assert gen_resp.status_code == 200, gen_resp.text
        gen_data = gen_resp.json()
        assert "fixes" in gen_data
        fixes_list = gen_data["fixes"]
        assert len(fixes_list) >= 1
        fix_id = fixes_list[0]["fix_id"]

        # 2. GET /fixes
        list_resp = client.get(f"/api/v1/reviews/{repo_id}/{pr_number}/fixes")
        assert list_resp.status_code == 200
        assert any(f["fix_id"] == fix_id for f in list_resp.json()["fixes"])

        # 3. POST /fixes/{fix_id}/validate
        val_resp = client.post(f"/api/v1/reviews/{repo_id}/{pr_number}/fixes/{fix_id}/validate")
        assert val_resp.status_code == 200
        assert val_resp.json()["validation_status"] == "PASSED"

        # 4. POST /fixes/{fix_id}/approve
        appr_resp = client.post(f"/api/v1/reviews/{repo_id}/{pr_number}/fixes/{fix_id}/approve")
        assert appr_resp.status_code == 200
        assert appr_resp.json()["status"] == "APPROVED"

        # 5. POST /fixes/{fix_id}/apply
        apply_resp = client.post(f"/api/v1/reviews/{repo_id}/{pr_number}/fixes/{fix_id}/apply")
        assert apply_resp.status_code == 200
        assert apply_resp.json()["status"] == "APPLIED"
        assert apply_resp.json()["post_apply"]["finding_status"] == "RESOLVED"

        # 6. POST /fixes/{fix_id}/reject
        rej_resp = client.post(f"/api/v1/reviews/{repo_id}/{pr_number}/fixes/{fix_id}/reject")
        assert rej_resp.status_code == 200
        assert rej_resp.json()["status"] == "REJECTED"
    finally:
        app.dependency_overrides.clear()

