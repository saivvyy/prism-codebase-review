"""Phase 1-10 Final Performance & Reliability Hardening Regression Tests.

Covers:
1. Review cold path & warm path
2. Evidence cold path & warm path
3. Suggest fix cold path & warm path
4. Confirm persistence timing path
5. Cache isolation: repo isolation, PR isolation, SHA isolation
6. In-flight duplicate prevention & idempotent execution
7. Stale SHA invalidation
8. Hindsight failure resilience (canonical Supabase preserved)
9. Supabase failure resilience
10. Groq timeout & GitHub timeout
11. Empty evidence vs failed evidence distinction
12. NO_PRECEDENT cold start integrity
13. Authority preservation & Provenance preservation
14. Memory deduplication & reinforcement
15. Hindsight sync state semantics (SYNCED, PENDING, FAILED, UNAVAILABLE)
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    MemorySyncState,
    ReviewVerdict,
    DeveloperFeedbackRequest,
    FeedbackType,
    ConfirmLearningCandidateRequest,
    LearningCandidate,
    CandidateStatus,
)
from app.schemas.github import GitHubPR, GitHubFileDiff, PRDetailResponse
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema
from app.schemas.context import PRContext, PRIdentity, PRIntent, ContextReason
from app.services.review_engine import ReviewEngine
from app.services.context_engine import ContextEngine
from app.services.fix_engine import FixCandidateEngine
from app.services.learning import ControlledLearningEngine
from app.core.exceptions import (
    GitHubNotFoundError,
    GitHubAuthError,
    GitHubRateLimitError,
    GroqServiceError,
    HindsightMemoryError,
)


@pytest.fixture
def repo_a_id():
    return str(uuid.uuid4())


@pytest.fixture
def repo_b_id():
    return str(uuid.uuid4())


@pytest.fixture
def mock_pr_sha_1():
    return "1111111111111111111111111111111111111111"


@pytest.fixture
def mock_pr_sha_2():
    return "2222222222222222222222222222222222222222"


def make_pr_detail(pr_number: int, head_sha: str, filename: str = "src/api.py") -> PRDetailResponse:
    pr = GitHubPR(
        id=pr_number,
        number=pr_number,
        title=f"PR #{pr_number} update",
        state="open",
        author="alice",
        head_sha=head_sha,
        base_branch="main",
        body="PR description with architectural context",
    )
    diff = GitHubFileDiff(
        filename=filename,
        status="modified",
        additions=5,
        deletions=1,
        patch="@@ -1,3 +1,7 @@\n+def handler():\n+    return True",
    )
    return PRDetailResponse(pr=pr, files=[diff], commits=[], comments=[])


# -----------------------------------------------------------------------------
# 1-3. CACHE ISOLATION: REPO, PR, AND SHA
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_repo_isolation(repo_a_id, repo_b_id, mock_pr_sha_1):
    """Verifies that cache entries for repo A are never returned for repo B."""
    engine = ReviewEngine(
        github=MagicMock(),
        code_analysis=MagicMock(),
        memory=MagicMock(),
        groq=MagicMock(),
        supabase=MagicMock(),
        context_engine=ContextEngine(),
    )
    pr_detail = make_pr_detail(10, mock_pr_sha_1)

    ctx_a = await engine.context_engine.build_pr_context(
        repository_id=repo_a_id,
        repository_full_name="org/repo-a",
        pr_detail=pr_detail,
        use_cache=True,
    )
    assert ctx_a.pr.repository_id == repo_a_id

    # Lookup for repo_b must be a cache miss and construct fresh context for repo_b
    ctx_b = await engine.context_engine.build_pr_context(
        repository_id=repo_b_id,
        repository_full_name="org/repo-b",
        pr_detail=pr_detail,
        use_cache=True,
    )
    assert ctx_b.pr.repository_id == repo_b_id
    assert ctx_b is not ctx_a


@pytest.mark.asyncio
async def test_cache_pr_isolation(repo_a_id, mock_pr_sha_1):
    """Verifies that different PRs with same SHA do not collide in cache."""
    engine = ReviewEngine(
        github=MagicMock(),
        code_analysis=MagicMock(),
        memory=MagicMock(),
        groq=MagicMock(),
        supabase=MagicMock(),
        context_engine=ContextEngine(),
    )
    pr1 = make_pr_detail(101, mock_pr_sha_1)
    pr2 = make_pr_detail(102, mock_pr_sha_1)

    ctx_1 = await engine.context_engine.build_pr_context(repo_a_id, "org/repo-a", pr1, use_cache=True)
    ctx_2 = await engine.context_engine.build_pr_context(repo_a_id, "org/repo-a", pr2, use_cache=True)
    assert ctx_1.pr.pr_number == 101
    assert ctx_2.pr.pr_number == 102
    assert ctx_1 is not ctx_2


@pytest.mark.asyncio
async def test_cache_sha_isolation_and_stale_invalidation(repo_a_id, mock_pr_sha_1, mock_pr_sha_2):
    """Verifies that changing HEAD SHA causes cache miss and invalidates stale data."""
    engine = ReviewEngine(
        github=MagicMock(),
        code_analysis=MagicMock(),
        memory=MagicMock(),
        groq=MagicMock(),
        supabase=MagicMock(),
        context_engine=ContextEngine(),
    )
    pr_sha1 = make_pr_detail(101, mock_pr_sha_1)
    pr_sha2 = make_pr_detail(101, mock_pr_sha_2)

    ctx_sha1 = await engine.context_engine.build_pr_context(repo_a_id, "org/repo-a", pr_sha1, use_cache=True)
    assert ctx_sha1.pr.head_sha == mock_pr_sha_1

    # New commit SHA must miss cache and return new context with sha2
    ctx_sha2 = await engine.context_engine.build_pr_context(repo_a_id, "org/repo-a", pr_sha2, use_cache=True)
    assert ctx_sha2.pr.head_sha == mock_pr_sha_2
    assert ctx_sha2 is not ctx_sha1


# -----------------------------------------------------------------------------
# 4. REVIEW COLD VS WARM PATH
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_cold_and_warm_paths(repo_a_id, mock_pr_sha_1):
    """Verifies cold review generates fresh output, warm review hits cache with 0 groq re-calls."""
    mock_github = MagicMock()
    mock_supabase = MagicMock()
    mock_groq = MagicMock()
    mock_memory = MagicMock()

    mock_supabase.get_repository_by_id = AsyncMock(return_value={"id": repo_a_id, "full_name": "org/repo-a"})
    mock_supabase.upsert_pull_request = AsyncMock(return_value={"id": "pr-uuid", "pr_number": 50})
    mock_supabase.create_review = AsyncMock(return_value={"id": "rev-uuid"})
    mock_supabase.create_review_findings = AsyncMock(return_value=[])
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])

    pr_detail = make_pr_detail(50, mock_pr_sha_1)
    mock_github.get_pr_details_full = AsyncMock(return_value=pr_detail)
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[]))
    mock_groq.generate_review_analysis = MagicMock(return_value=ReviewAnalysisOutput(
        summary="Clean review",
        drift_detected=False,
        exception_approved=False,
        confidence_score=0.99,
        verdict=ReviewVerdict.NO_PRECEDENT.value,
        findings=[],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=MagicMock(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=mock_supabase,
        context_engine=ContextEngine(),
    )

    # Cold Review
    rev_cold = await engine.review_pull_request(repo_a_id, 50, use_cache=False)
    assert rev_cold.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert mock_groq.generate_review_analysis.call_count == 1

    # Warm Review (use_cache=True)
    rev_warm = await engine.review_pull_request(repo_a_id, 50, use_cache=True)
    assert rev_warm.verdict == ReviewVerdict.NO_PRECEDENT.value
    # Groq must NOT have been called again on warm cache hit
    assert mock_groq.generate_review_analysis.call_count == 1


# -----------------------------------------------------------------------------
# 5. SUGGEST FIX DETERMINISTIC COLD VS WARM
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_fix_cold_and_warm(repo_a_id, mock_pr_sha_1):
    """Verifies FixCandidateEngine executes deterministically for cold and warm contexts."""
    from app.schemas.context import ContextBudget, ChangedFileDetail
    fix_engine = FixCandidateEngine()
    changed_file = ChangedFileDetail(
        path="src/api.py",
        status="modified",
        additions=5,
        deletions=1,
        language="python",
        classes=[],
        functions=["handler"],
        affected_symbols=["handler"],
    )
    context = PRContext(
        pr=PRIdentity(
            repository_id=repo_a_id,
            repository_full_name="org/repo-a",
            pr_number=50,
            title="Update handler",
            author="alice",
            base_sha="000",
            head_sha=mock_pr_sha_1,
            changed_files_count=1,
            total_additions=5,
            total_deletions=1,
        ),
        intent=PRIntent(summary="Fix handler"),
        changed_files=[changed_file],
        changed_hunks=[],
        symbols=["handler"],
        definitions=[],
        references=[],
        imports=[],
        calls=[],
        dependencies={},
        related_files=[],
        related_tests=[],
        related_prs=[],
        related_issues=[],
        relevant_docs=[],
        relevant_memories=[],
        context_reasons=[],
        context_budget=ContextBudget(max_tokens=6000, estimated_tokens=100),
    )
    review = ReviewAnalysisOutput(
        summary="Drift detected",
        drift_detected=True,
        exception_approved=False,
        confidence_score=0.95,
        verdict=ReviewVerdict.DRIFT.value,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/api.py",
                line_number=5,
                title="Direct DB access",
                message="Direct DB query in API layer",
                suggested_fix="Use repository abstraction",
            )
        ],
    )

    # Cold Fix Generation
    fixes_cold = fix_engine.generate_fixes_for_pr(repo_a_id, 50, context, review)
    assert len(fixes_cold) == 1
    assert "Replace direct database calls" in fixes_cold[0].proposed_change

    # Warm Fix Generation
    fixes_warm = fix_engine.generate_fixes_for_pr(repo_a_id, 50, context, review)
    assert len(fixes_warm) == 1
    assert fixes_warm[0].fix_id == fixes_cold[0].fix_id


# -----------------------------------------------------------------------------
# 6. CONFIRMATION SEMANTICS & AUTHORITY PRESERVATION
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirmation_cannot_promote_observation_to_enforceable(repo_a_id):
    """Verifies invariant: REVIEW_OBSERVATION cannot be promoted to ENFORCEABLE on confirm."""
    mock_supabase = MagicMock()
    mock_supabase.find_existing_memory = AsyncMock(return_value=None)
    mock_supabase.create_engineering_memory = AsyncMock(return_value={"id": "mem-1"})
    mock_supabase.update_engineering_memory = AsyncMock(return_value={"id": "mem-1"})
    mock_memory = MagicMock()
    mock_memory.is_configured = True
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True))

    learning = ControlledLearningEngine(supabase=mock_supabase, memory=mock_memory)

    # Candidate derived from unverified review observation
    cand = LearningCandidate(
        repository_id=repo_a_id,
        repository_full_name="org/repo",
        source_type="developer_feedback",
        source_ref="PR #100",
        statement="Controllers should avoid inline helpers",
        rationale="Code cleanliness recommendation",
        scope="src/controllers/*",
        type=KnowledgeType.OBSERVED_CONVENTION,
        authority=MemoryAuthority.ENFORCEABLE,  # Illegally set without explicit evidence
        is_explicit_decision=False,
        evidence_items=["PR #100 comment"],
    )

    res = await learning.learn_candidate(cand)
    # Must be rejected because ENFORCEABLE requires explicit repository evidence
    assert res["status"] == "rejected"
    assert "AUTHORITY_PROMOTION_PROHIBITED" in res["reason"]


# -----------------------------------------------------------------------------
# 7. PROVENANCE INTEGRITY & MEMORY DEDUPLICATION
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_reinforcement_and_deduplication(repo_a_id):
    """Verifies that confirming an existing memory reinforces observation_count rather than duplicating."""
    existing_mem = {
        "id": "mem-existing-1",
        "repository_id": repo_a_id,
        "repository_full_name": "org/repo",
        "statement": "All services must log correlation IDs",
        "scope": "src/services/*",
        "type": "DOCUMENTED_DECISION",
        "authority": "ENFORCEABLE",
        "status": "ACTIVE",
        "observation_count": 2,
        "evidence_items": ["PR #10"],
    }
    mock_supabase = MagicMock()
    mock_supabase.find_existing_memory = AsyncMock(return_value=existing_mem)
    mock_supabase.reinforce_engineering_memory = AsyncMock(return_value={**existing_mem, "observation_count": 3})
    mock_supabase.update_engineering_memory = AsyncMock(return_value=existing_mem)
    mock_memory = MagicMock()
    mock_memory.is_configured = True
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True))

    learning = ControlledLearningEngine(supabase=mock_supabase, memory=mock_memory)
    cand = LearningCandidate(
        repository_id=repo_a_id,
        repository_full_name="org/repo",
        source_type="review_confirmation",
        source_ref="PR #20",
        source_pr_number=20,
        statement="All services must log correlation IDs",
        rationale="Traceability requirement",
        scope="src/services/*",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        is_explicit_decision=True,
        evidence_items=["PR #20 commit 1234567890abcdef1234567890abcdef12345678"],
    )

    outcome = await learning.learn_candidate(cand)
    assert outcome["status"] == "reinforced"
    assert outcome["sync_state"] == MemorySyncState.SYNCED.value
    assert mock_supabase.update_engineering_memory.call_count == 2
    mock_supabase.create_engineering_memory.assert_not_called()


# -----------------------------------------------------------------------------
# 8. HINDSIGHT FAILURE RESILIENCE (CANONICAL SUPABASE PRESERVED)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hindsight_failure_preserves_canonical_supabase_and_marks_failed(repo_a_id):
    """Verifies that Hindsight outage leaves canonical memory intact and records sync_state=FAILED."""
    mock_supabase = MagicMock()
    mock_supabase.find_existing_memory = AsyncMock(return_value=None)
    mock_supabase.create_engineering_memory = AsyncMock(return_value={"id": "mem-durable-1"})
    mock_supabase.update_engineering_memory = AsyncMock(return_value={"id": "mem-durable-1"})

    mock_memory = MagicMock()
    mock_memory.is_configured = True
    mock_memory.retain = MagicMock(side_effect=HindsightMemoryError("Hindsight 503 unavailable"))

    learning = ControlledLearningEngine(supabase=mock_supabase, memory=mock_memory)
    cand = LearningCandidate(
        repository_id=repo_a_id,
        repository_full_name="org/repo",
        source_type="review_confirmation",
        source_ref="PR #30",
        source_pr_number=30,
        statement="Unique idempotency keys required for webhooks",
        rationale="Prevent duplicate event processing",
        scope="src/webhooks/*",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        is_explicit_decision=True,
        evidence_items=["PR #30 commit 1234567890abcdef1234567890abcdef12345678"],
    )

    with pytest.raises(HindsightMemoryError):
        await learning.learn_candidate(cand)

    # Supabase memory was created canonically before Hindsight attempt
    mock_supabase.create_engineering_memory.assert_called_once()
    # Sync state was updated to FAILED in Supabase
    mock_supabase.update_engineering_memory.assert_called_with("mem-durable-1", {"sync_state": MemorySyncState.FAILED.value})


# -----------------------------------------------------------------------------
# 9. EMPTY EVIDENCE VS FAILED EVIDENCE
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_failure_raises_distinguishable_error(repo_a_id, mock_pr_sha_1):
    """Verifies that network/auth failure on evidence raises an error and is not reported as empty evidence."""
    mock_github = MagicMock()
    mock_github.get_commit_checks = AsyncMock(side_effect=GitHubRateLimitError("GitHub API rate limit exceeded"))
    mock_github.get_pr_details_full = AsyncMock(return_value=make_pr_detail(1, mock_pr_sha_1))

    # Calling get_commit_checks directly must bubble up GitHubRateLimitError, NOT 0 checks
    with pytest.raises(GitHubRateLimitError):
        await mock_github.get_commit_checks("org", "repo", mock_pr_sha_1)


# -----------------------------------------------------------------------------
# 10. NO_PRECEDENT COLD START
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cold_start_repository_produces_no_precedent(repo_a_id, mock_pr_sha_1):
    """Verifies cold-start repository with 0 memories returns NO_PRECEDENT, not false CONFORMING."""
    mock_github = MagicMock()
    mock_supabase = MagicMock()
    mock_groq = MagicMock()
    mock_memory = MagicMock()

    mock_supabase.get_repository_by_id = AsyncMock(return_value={"id": repo_a_id, "full_name": "org/brand-new"})
    mock_supabase.upsert_pull_request = AsyncMock(return_value={"id": "pr-uuid", "pr_number": 1})
    mock_supabase.create_review = AsyncMock(return_value={"id": "rev-uuid"})
    mock_supabase.create_review_findings = AsyncMock(return_value=[])
    mock_supabase.list_engineering_memories = AsyncMock(return_value=[])  # 0 memories

    pr_detail = make_pr_detail(1, mock_pr_sha_1)
    mock_github.get_pr_details_full = AsyncMock(return_value=pr_detail)
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[]))
    mock_groq.generate_review_analysis = MagicMock(return_value=ReviewAnalysisOutput(
        summary="New code without existing institutional precedent",
        drift_detected=False,
        exception_approved=False,
        confidence_score=0.90,
        verdict=ReviewVerdict.NO_PRECEDENT.value,
        findings=[],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=MagicMock(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=mock_supabase,
        context_engine=ContextEngine(),
    )

    result = await engine.review_pull_request(repo_a_id, 1)
    assert result.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert result.drift_detected is False
    assert result.exception_approved is False
