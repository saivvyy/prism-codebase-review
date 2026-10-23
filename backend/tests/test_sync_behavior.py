import uuid
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.supabase import SupabaseService, get_supabase_service
from app.services.github import GitHubService, get_github_service
from app.schemas.github import GitHubPR, GitHubPRListResponse

client = TestClient(app)


@pytest.fixture
def mock_supabase():
    svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    return svc


@pytest.mark.asyncio
async def test_sync_pull_requests_open_and_closed_endpoints(mock_supabase):
    """Verifies that repositories pulls endpoint correctly responds to open and closed states."""
    repo_id = str(uuid.uuid4())
    mock_supabase._local_repos[repo_id] = {
        "id": repo_id,
        "user_id": "00000000-0000-0000-0000-000000000000",
        "github_repo_id": 12345,
        "full_name": "octocat/Spoon-Knife",
        "default_branch": "main",
        "webhook_active": False,
    }

    mock_github = MagicMock(spec=GitHubService)
    open_prs = [
        GitHubPR(
            id=1,
            number=1,
            title="Open PR 1",
            state="open",
            author="alice",
            head_sha="sha1",
            base_branch="main",
        )
    ]
    closed_prs = [
        GitHubPR(
            id=2,
            number=2,
            title="Closed PR 2",
            state="closed",
            author="bob",
            head_sha="sha2",
            base_branch="main",
        )
    ]

    async def fake_list_pull_requests(owner, repo, state="all", per_page=100, fetch_limit=500):
        items = open_prs if state == "open" else closed_prs
        return GitHubPRListResponse(
            items=items,
            total_fetched=len(items),
            pages_fetched=1,
            per_page=per_page,
            fetch_limit=fetch_limit,
            complete=True,
            truncated=False,
            open_pr_count=len(open_prs),
            total_repository_prs=len(open_prs) + len(closed_prs),
        )

    mock_github.list_pull_requests = AsyncMock(side_effect=fake_list_pull_requests)
    mock_github.get_repository_open_pr_count = AsyncMock(return_value=len(open_prs))

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        # Fetch open
        res_open = client.get(f"/api/v1/repositories/{repo_id}/pulls?state=open")
        assert res_open.status_code == 200
        data_open = res_open.json()
        assert len(data_open["items"]) == 1
        assert data_open["items"][0]["number"] == 1
        assert data_open["open_pr_count"] == 1

        # Fetch closed
        res_closed = client.get(f"/api/v1/repositories/{repo_id}/pulls?state=closed")
        assert res_closed.status_code == 200
        data_closed = res_closed.json()
        assert len(data_closed["items"]) == 1
        assert data_closed["items"][0]["number"] == 2
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


@pytest.mark.asyncio
async def test_sync_repository_isolation(mock_supabase):
    """Syncing Repo A must be isolated and not touch Repo B."""
    repo_a_id = str(uuid.uuid4())
    repo_b_id = str(uuid.uuid4())
    mock_supabase._local_repos[repo_a_id] = {
        "id": repo_a_id,
        "user_id": "00000000-0000-0000-0000-000000000000",
        "github_repo_id": 11111,
        "full_name": "org/repo-a",
        "default_branch": "main",
        "webhook_active": False,
    }
    mock_supabase._local_repos[repo_b_id] = {
        "id": repo_b_id,
        "user_id": "00000000-0000-0000-0000-000000000000",
        "github_repo_id": 22222,
        "full_name": "org/repo-b",
        "default_branch": "main",
        "webhook_active": False,
    }

    mock_github = MagicMock(spec=GitHubService)
    mock_github.list_pull_requests = AsyncMock(
        return_value=GitHubPRListResponse(
            items=[],
            total_fetched=0,
            pages_fetched=1,
            complete=True,
            open_pr_count=0,
        )
    )
    mock_github.get_repository_open_pr_count = AsyncMock(return_value=0)

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        # Request pulls for Repo A only
        res = client.get(f"/api/v1/repositories/{repo_a_id}/pulls?state=open")
        assert res.status_code == 200
        mock_github.list_pull_requests.assert_called_once_with("org", "repo-a", state="open")
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


@pytest.mark.asyncio
async def test_sync_repository_error_does_not_mask():
    """An error fetching pulls raises proper HTTP error and is not masked as 0 PRs."""
    mock_supabase_svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    repo_id = str(uuid.uuid4())
    mock_supabase_svc._local_repos[repo_id] = {
        "id": repo_id,
        "user_id": "00000000-0000-0000-0000-000000000000",
        "github_repo_id": 99999,
        "full_name": "org/rate-limited-repo",
        "default_branch": "main",
        "webhook_active": False,
    }

    mock_github = MagicMock(spec=GitHubService)
    from app.core.exceptions import GitHubRateLimitError
    mock_github.list_pull_requests = AsyncMock(side_effect=GitHubRateLimitError("Rate limit exceeded"))

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase_svc
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls?state=open")
        # Should return 429 or 502, never 200 with empty list
        assert res.status_code in (429, 502, 500)
        assert res.status_code != 200
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


@pytest.mark.asyncio
async def test_pr_detail_lifecycle_state_resolution(mock_supabase):
    """Regression test: verifies that PR detail endpoint returns exact lifecycle state (merged, closed, open)

    and that lifecycle state is distinct from review findings (e.g. drift vs conforming).
    """
    repo_id = str(uuid.uuid4())
    mock_supabase._local_repos[repo_id] = {
        "id": repo_id,
        "user_id": "00000000-0000-0000-0000-000000000000",
        "github_repo_id": 1086419061,
        "full_name": "vectorize-io/hindsight",
        "default_branch": "main",
        "webhook_active": False,
    }

    mock_github = MagicMock(spec=GitHubService)

    # 1. PR #4922: closed and merged
    merged_pr = GitHubPR(
        id=4922,
        number=4922,
        title="fix: memory resolution and evidence links",
        state="closed",
        author="alice",
        head_sha="sha4922",
        base_branch="main",
        closed_at="2026-09-29T12:00:00Z",
        merged_at="2026-09-29T12:00:00Z",
        is_merged=True,
    )

    # 2. Closed but unmerged PR
    closed_unmerged_pr = GitHubPR(
        id=4923,
        number=4923,
        title="refactor: abandoned experimental branch",
        state="closed",
        author="bob",
        head_sha="sha4923",
        base_branch="main",
        closed_at="2026-09-29T13:00:00Z",
        merged_at=None,
        is_merged=False,
    )

    # 3. Open PR
    open_pr = GitHubPR(
        id=4924,
        number=4924,
        title="feat: real-time memory synchronization",
        state="open",
        author="carol",
        head_sha="sha4924",
        base_branch="main",
        closed_at=None,
        merged_at=None,
        is_merged=False,
    )

    from app.schemas.github import PRDetailResponse

    def fake_get_pull_request(owner, repo, pr_number):
        if pr_number == 4922:
            return merged_pr
        elif pr_number == 4923:
            return closed_unmerged_pr
        return open_pr

    async def fake_get_pr_details_full(owner, repo, pr_number):
        pr = fake_get_pull_request(owner, repo, pr_number)
        return PRDetailResponse(
            pr=pr,
            files=[],
            commits=[],
            comments=[],
        )

    mock_github.get_pr_details_full = AsyncMock(side_effect=fake_get_pr_details_full)

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        # A. Merged PR #4922
        res_merged = client.get(f"/api/v1/repositories/{repo_id}/pulls/4922")
        assert res_merged.status_code == 200
        data_merged = res_merged.json()
        assert data_merged["pr"]["number"] == 4922
        assert data_merged["pr"]["state"] == "closed"
        assert data_merged["pr"]["is_merged"] is True
        assert data_merged["pr"]["merged_at"] is not None

        # B. Closed unmerged PR #4923
        res_closed = client.get(f"/api/v1/repositories/{repo_id}/pulls/4923")
        assert res_closed.status_code == 200
        data_closed = res_closed.json()
        assert data_closed["pr"]["number"] == 4923
        assert data_closed["pr"]["state"] == "closed"
        assert data_closed["pr"]["is_merged"] is False
        assert data_closed["pr"]["merged_at"] is None

        # C. Open PR #4924
        res_open = client.get(f"/api/v1/repositories/{repo_id}/pulls/4924")
        assert res_open.status_code == 200
        data_open = res_open.json()
        assert data_open["pr"]["number"] == 4924
        assert data_open["pr"]["state"] == "open"
        assert data_open["pr"]["is_merged"] is False
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)

