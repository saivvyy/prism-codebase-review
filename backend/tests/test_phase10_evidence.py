import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.context import (
    CommitEvidence,
    ConversationEvidence,
    CheckEvidence,
    HistoricalPREvidence,
    PREvidenceSummary,
    PREvidenceResponse,
    PRContext,
    PRIdentity,
    PRIntent,
    ContextBudget,
    CodeDependencyGraph,
)
from app.schemas.github import GitHubPR, GitHubFileDiff, PRDetailResponse
from app.services.github import (
    GitHubService,
    MAX_COMMITS,
    MAX_CONVERSATION_ITEMS,
    MAX_REVIEW_COMMENTS,
    MAX_CHECKS,
    MAX_HISTORICAL_PRS,
)
from app.services.context_engine import ContextEngine, get_context_engine
from app.services.review_engine import ReviewEngine, get_review_engine
from app.services.supabase import get_supabase_service
from app.core.exceptions import GitHubNotFoundError, GitHubRateLimitError

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_pr_detail():
    pr = GitHubPR(
        id=501,
        number=10,
        title="feat: implement transaction boundary in payment service",
        state="open",
        author="alice",
        head_sha="head1010101010",
        base_sha="base1010101010",
        base_branch="main",
        body="Refactors payment processing to enforce strict transaction boundaries.",
    )
    files = [
        GitHubFileDiff(
            filename="app/services/payment_service.py",
            status="modified",
            additions=40,
            deletions=5,
            changes=45,
            patch="@@ -1,5 +1,10 @@\n+def execute_transaction():\n+    pass",
        ),
        GitHubFileDiff(
            filename="tests/test_payment_service.py",
            status="added",
            additions=30,
            deletions=0,
            changes=30,
            patch="@@ -0,0 +1,10 @@\n+def test_tx():\n+    pass",
        ),
    ]
    return PRDetailResponse(pr=pr, files=files, commits=[], comments=[])


# ---------------------------------------------------------------------------
# 1-4: Commits Retrieval, Pagination, Bounds, Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commit_evidence_retrieval_and_mapping():
    service = GitHubService(token="fake-token")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = [
        {
            "sha": "1234567890abcdef1234567890abcdef12345678",
            "commit": {
                "message": "fix: isolate db session",
                "author": {"name": "alice", "email": "alice@example.com", "date": "2026-10-01T10:00:00Z"},
                "committer": {"name": "alice", "email": "alice@example.com", "date": "2026-10-01T10:05:00Z"},
            },
            "author": {"login": "alice"},
            "stats": {"additions": 15, "deletions": 2, "total": 17},
            "files": [{"filename": "app/services/payment_service.py"}],
            "html_url": "https://github.com/org/repo/commit/1234567",
        }
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        commits, truncated = await service.get_pr_commits_evidence("org", "repo", 10, max_commits=50)

        assert len(commits) == 1
        assert not truncated
        c = commits[0]
        assert c.sha == "1234567890abcdef1234567890abcdef12345678"
        assert c.short_sha == "1234567"
        assert c.author == "alice"
        assert c.additions == 15
        assert c.deletions == 2
        assert "app/services/payment_service.py" in c.changed_files


@pytest.mark.asyncio
async def test_commit_evidence_bounds_and_truncation():
    service = GitHubService(token="fake-token")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = [
        {
            "sha": f"sha{i:03d}0000000000000000000000000000000000",
            "commit": {
                "message": f"commit {i}",
                "author": {"name": "alice", "date": f"2026-10-01T{i:02d}:00:00Z"},
            },
            "author": {"login": "alice"},
        }
        for i in range(15)
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        commits, truncated = await service.get_pr_commits_evidence("org", "repo", 10, max_commits=10)

        assert len(commits) == 10
        assert truncated is True


@pytest.mark.asyncio
async def test_commit_evidence_deduplication():
    service = GitHubService(token="fake-token")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    duplicate_item = {
        "sha": "dup1234567890abcdef1234567890abcdef12345",
        "commit": {
            "message": "fix: commit",
            "author": {"name": "alice", "date": "2026-10-01T10:00:00Z"},
        },
        "author": {"login": "alice"},
    }
    mock_resp.json.return_value = [duplicate_item, duplicate_item]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        commits, _ = await service.get_pr_commits_evidence("org", "repo", 10, max_commits=50)

        assert len(commits) == 1
        assert commits[0].sha == "dup1234567890abcdef1234567890abcdef12345"


# ---------------------------------------------------------------------------
# 5-9: Conversation Retrieval, Review Comments, Deduplication, Bounds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conversation_evidence_retrieval_and_deduplication():
    service = GitHubService(token="fake-token")

    async def mock_get_side_effect(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.is_error = False
        if "issues/10/comments" in str(url):
            resp.json.return_value = [
                {
                    "id": 1001,
                    "user": {"login": "bob"},
                    "body": "Does this handle rollback properly?",
                    "created_at": "2026-10-01T11:00:00Z",
                    "html_url": "https://github.com/org/repo/issues/10#issuecomment-1001",
                }
            ]
        elif "pulls/10/comments" in str(url):
            resp.json.return_value = [
                {
                    "id": 2001,
                    "user": {"login": "carol"},
                    "body": "Need a session flush here.",
                    "created_at": "2026-10-01T12:00:00Z",
                    "path": "app/services/payment_service.py",
                    "line": 4,
                    "side": "RIGHT",
                    "commit_id": "head1010101010",
                    "html_url": "https://github.com/org/repo/pull/10#discussion_r2001",
                }
            ]
        elif "pulls/10/reviews" in str(url):
            resp.json.return_value = [
                {
                    "id": 3001,
                    "user": {"login": "dave"},
                    "body": "LGTM from architectural perspective.",
                    "state": "APPROVED",
                    "submitted_at": "2026-10-01T13:00:00Z",
                    "html_url": "https://github.com/org/repo/pull/10#pullrequestreview-3001",
                }
            ]
        else:
            resp.json.return_value = []
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get_side_effect):
        items, truncated = await service.get_pr_conversation_evidence("org", "repo", 10, max_items=100)

        assert len(items) == 3
        assert not truncated
        assert items[0].id == "issue_comment-1001"
        assert items[0].author == "bob"
        assert items[1].id == "review_comment-2001"
        assert items[1].file_path == "app/services/payment_service.py"
        assert items[1].line == 4
        assert items[2].id == "review-3001"
        assert items[2].author == "dave"


@pytest.mark.asyncio
async def test_conversation_evidence_bounds_truncation():
    service = GitHubService(token="fake-token")

    async def mock_get_side_effect(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.is_error = False
        if "issues/10/comments" in str(url):
            resp.json.return_value = [
                {
                    "id": i,
                    "user": {"login": f"user{i}"},
                    "body": f"comment {i}",
                    "created_at": f"2026-10-01T{i%24:02d}:00:00Z",
                }
                for i in range(15)
            ]
        else:
            resp.json.return_value = []
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get_side_effect):
        items, truncated = await service.get_pr_conversation_evidence("org", "repo", 10, max_items=5)
        assert len(items) == 5
        assert truncated is True


# ---------------------------------------------------------------------------
# 10-14: Checks / CI Evidence (Pass, Failure, Pending, None, Pagination)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checks_evidence_status_and_conclusion_states():
    service = GitHubService(token="fake-token")

    async def mock_get_side_effect(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.is_error = False
        if "check-runs" in str(url):
            resp.json.return_value = {
                "check_runs": [
                    {
                        "id": 901,
                        "name": "lint",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://ci/lint",
                        "app": {"name": "GitHub Actions"},
                    },
                    {
                        "id": 902,
                        "name": "unit-tests",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://ci/tests",
                        "app": {"name": "GitHub Actions"},
                    },
                    {
                        "id": 903,
                        "name": "e2e-tests",
                        "status": "in_progress",
                        "conclusion": None,
                        "html_url": "https://ci/e2e",
                        "app": {"name": "GitHub Actions"},
                    },
                ]
            }
        elif "status" in str(url):
            resp.json.return_value = {
                "statuses": [
                    {
                        "id": 801,
                        "context": "coverage/codecov",
                        "state": "success",
                        "target_url": "https://codecov.io",
                    }
                ]
            }
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get_side_effect):
        checks, truncated = await service.get_pr_checks_evidence("org", "repo", "head1010101010", max_checks=10)

        assert len(checks) == 4
        assert not truncated
        
        lint_chk = next(c for c in checks if c.name == "lint")
        assert lint_chk.status == "completed"
        assert lint_chk.conclusion == "success"

        fail_chk = next(c for c in checks if c.name == "unit-tests")
        assert fail_chk.status == "completed"
        assert fail_chk.conclusion == "failure"

        pend_chk = next(c for c in checks if c.name == "e2e-tests")
        assert pend_chk.status == "in_progress"
        assert pend_chk.conclusion is None


@pytest.mark.asyncio
async def test_checks_evidence_empty():
    service = GitHubService(token="fake-token")

    async def mock_get_side_effect(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.is_error = False
        if "check-runs" in str(url):
            resp.json.return_value = {"check_runs": []}
        else:
            resp.json.return_value = {"statuses": []}
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get_side_effect):
        checks, truncated = await service.get_pr_checks_evidence("org", "repo", "head1010101010", max_checks=10)
        assert checks == []
        assert truncated is False


# ---------------------------------------------------------------------------
# 15-17: Historical PR Retrieval, Relevance Ranking & Rationales, Bounds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_historical_prs_evidence_ranking_and_bounds():
    service = GitHubService(token="fake-token")

    async def mock_get_side_effect(url, params=None, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.is_error = False
        resp.headers = {}
        url_str = str(url)
        if "/pulls/5/files" in url_str:
            resp.json.return_value = [
                {"filename": "app/services/payment_service.py"},
                {"filename": "app/models/payment.py"},
            ]
        elif "/pulls/3/files" in url_str:
            resp.json.return_value = [
                {"filename": "package.json"},
            ]
        elif "/pulls" in url_str:
            resp.json.return_value = [
                {
                    "id": 105,
                    "number": 5,
                    "title": "refactor: payment service architecture",
                    "state": "closed",
                    "merged_at": "2026-09-15T00:00:00Z",
                    "html_url": "https://github.com/org/repo/pull/5",
                },
                {
                    "id": 103,
                    "number": 3,
                    "title": "chore: bump dependencies in package.json",
                    "state": "closed",
                    "merged_at": "2026-09-01T00:00:00Z",
                    "html_url": "https://github.com/org/repo/pull/3",
                },
                {
                    "id": 110,
                    "number": 10,
                    "title": "feat: implement transaction boundary",
                    "state": "open",
                    "html_url": "https://github.com/org/repo/pull/10",
                },
            ]
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get_side_effect):
        hist_prs, truncated = await service.get_historical_prs_evidence(
            "org",
            "repo",
            current_pr_number=10,
            changed_files=["app/services/payment_service.py", "tests/test_payment_service.py"],
            symbols=["PaymentService", "execute_transaction"],
            max_prs=5,
        )

        assert len(hist_prs) == 1
        assert not truncated
        assert hist_prs[0].pr_number == 5
        assert hist_prs[0].is_merged is True
        assert "app/services/payment_service.py" in hist_prs[0].relevant_files
        assert "payment" in hist_prs[0].relevance_reason.lower()


# ---------------------------------------------------------------------------
# 18-22: Determinism, Repo Isolation, PR Isolation, SHA Isolation, Stale SHA
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sha_cache_isolation_and_stale_invalidation(sample_pr_detail):
    mock_gh = MagicMock(spec=GitHubService)
    mock_gh.get_pr_commits_evidence = AsyncMock(return_value=([], False))
    mock_gh.get_pr_conversation_evidence = AsyncMock(return_value=([], False))
    mock_gh.get_pr_checks_evidence = AsyncMock(return_value=([], False))
    mock_gh.get_historical_prs_evidence = AsyncMock(return_value=([], False))

    engine = ContextEngine(github=mock_gh, supabase=MagicMock(), memory=MagicMock())
    engine.clear_cache()

    ctx1 = await engine.build_pr_context("repo-abc", "org/repo", sample_pr_detail, use_cache=True)
    assert ctx1.pr.head_sha == "head1010101010"

    cached = engine.get_cached_context("repo-abc", 10, "head1010101010")
    assert cached is ctx1

    assert engine.get_cached_context("repo-abc", 10, "head-new-sha") is None
    assert engine.get_cached_context("repo-xyz", 10, "head1010101010") is None
    assert engine.get_cached_context("repo-abc", 99, "head1010101010") is None


# ---------------------------------------------------------------------------
# 23-26: GitHub API Error Handling & Graceful Degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_failure_graceful_degradation(sample_pr_detail):
    mock_github = MagicMock(spec=GitHubService)
    mock_github.get_pr_commits_evidence = AsyncMock(side_effect=GitHubRateLimitError("Rate limit exceeded"))
    mock_github.get_pr_conversation_evidence = AsyncMock(side_effect=GitHubNotFoundError("PR not found"))
    mock_github.get_pr_checks_evidence = AsyncMock(side_effect=Exception("Network error"))
    mock_github.get_historical_prs_evidence = AsyncMock(side_effect=Exception("Timeout"))

    engine = ContextEngine(github=mock_github, supabase=MagicMock(), memory=MagicMock())
    context = await engine.build_pr_context("repo-fail", "org/fail-repo", sample_pr_detail, use_cache=False)

    assert context.commits == []
    assert context.conversation == []
    assert context.checks == []
    assert context.historical_prs == []
    assert context.evidence_summary.total_commits == 0
    assert context.evidence_summary.total_checks == 0


# ---------------------------------------------------------------------------
# 27-30: Authority Escalation Boundaries & Full API Verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_cannot_produce_architectural_authority():
    check = CheckEvidence(
        id="chk_1",
        name="build",
        status="completed",
        conclusion="success",
    )
    assert not hasattr(check, "authority")
    assert not hasattr(check, "status_override")

    conv = ConversationEvidence(
        id="conv_1",
        source_type="issue_comment",
        author="lead_dev",
        body="We must always use transaction wrappers for payment.",
        created_at="2026-10-01T10:00:00Z",
    )
    assert not hasattr(conv, "is_enforceable")
    assert not hasattr(conv, "is_canonical")


@pytest.mark.asyncio
async def test_get_pr_evidence_endpoint_success(sample_pr_detail):
    mock_supabase = MagicMock()
    mock_supabase.get_repository_by_id = AsyncMock(return_value={
        "id": "repo-phase10",
        "full_name": "org/repo-phase10",
        "owner_id": "test-user-id",
    })

    mock_github = MagicMock(spec=GitHubService)
    mock_github.get_pr_details_full = AsyncMock(return_value=sample_pr_detail)
    mock_github.get_pr_commits_evidence = AsyncMock(return_value=([
        CommitEvidence(
            sha="c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1",
            short_sha="c1c1c1c",
            message="feat: payment tx",
            author="alice",
            changed_files=["app/services/payment_service.py"],
        )
    ], False))
    mock_github.get_pr_conversation_evidence = AsyncMock(return_value=([], False))
    mock_github.get_pr_checks_evidence = AsyncMock(return_value=([], False))
    mock_github.get_historical_prs_evidence = AsyncMock(return_value=([], False))

    context_engine = ContextEngine(github=mock_github, supabase=mock_supabase, memory=MagicMock())
    review_engine = ReviewEngine(github=mock_github, supabase=mock_supabase, memory=MagicMock())

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_context_engine] = lambda: context_engine
    app.dependency_overrides[get_review_engine] = lambda: review_engine

    try:
        resp = client.get("/api/v1/reviews/repo-phase10/10/evidence")
        assert resp.status_code == 200
        data = resp.json()
        assert data["repository_id"] == "repo-phase10"
        assert data["pr_number"] == 10
        assert data["head_sha"] == "head1010101010"
        assert len(data["commits"]) == 1
        assert data["commits"][0]["message"] == "feat: payment tx"
        assert data["summary"]["total_commits"] == 1
    finally:
        app.dependency_overrides.clear()

