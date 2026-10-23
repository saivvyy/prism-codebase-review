import uuid
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.exceptions import GitHubNotFoundError
from app.services.github import GitHubService, get_github_service
from app.services.supabase import SupabaseService, get_supabase_service
from app.schemas.github import GitHubPR, GitHubPRListResponse, PRDetailResponse

client = TestClient(app)

@pytest.mark.asyncio
async def test_github_service_list_prs_pagination_deduplicates():
    """Verify GitHubService deduplicates items with same PR number across pagination."""
    service = GitHubService()

    raw_page_1 = [
        {
            "id": 1001,
            "number": 5000,
            "title": "Fix memory leak",
            "state": "closed",
            "html_url": "https://github.com/foo/bar/pull/5000",
            "user": {"login": "dev1", "avatar_url": ""},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "head": {"sha": "sha123", "ref": "feat"},
            "base": {"ref": "main"}
        },
        # Duplicate of #5000 with different id
        {
            "id": 1002,
            "number": 5000,
            "title": "Fix memory leak duplicate",
            "state": "closed",
            "html_url": "https://github.com/foo/bar/pull/5000",
            "user": {"login": "dev1", "avatar_url": ""},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "head": {"sha": "sha123", "ref": "feat"},
            "base": {"ref": "main"}
        },
        {
            "id": 1003,
            "number": 5001,
            "title": "Add telemetry",
            "state": "closed",
            "html_url": "https://github.com/foo/bar/pull/5001",
            "user": {"login": "dev2", "avatar_url": ""},
            "created_at": "2026-01-03T00:00:00Z",
            "updated_at": "2026-01-04T00:00:00Z",
            "head": {"sha": "sha456", "ref": "telemetry"},
            "base": {"ref": "main"}
        }
    ]

    resp_p1 = httpx.Response(200, json=raw_page_1)
    resp_empty = httpx.Response(200, json=[])

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [resp_p1, resp_empty]
        result = await service.list_pull_requests(
            owner="foo",
            repo="bar",
            state="closed",
            per_page=100,
            fetch_limit=100
        )
        assert len(result.items) == 2
        numbers = [p.number for p in result.items]
        assert numbers == [5000, 5001]
        assert result.total_fetched == 2


def test_api_list_prs_deduplicates_by_pr_number():
    """Verify endpoint deduplicates PR list if downstream returns duplicate PR numbers."""
    repo_id = str(uuid.uuid4())
    fake_repo = {
        "id": repo_id,
        "owner": "vectorize-io",
        "name": "hindsight",
        "full_name": "vectorize-io/hindsight",
        "default_branch": "main",
        "is_private": False,
        "installation_id": 123
    }
    
    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.get_repository_by_id = AsyncMock(return_value=fake_repo)
    
    mock_pr = GitHubPR(
        id=101,
        number=5000,
        title="Deduplicated PR",
        state="closed",
        author="user1",
        head_sha="sha1",
        base_branch="main"
    )
    
    mock_github = MagicMock(spec=GitHubService)
    mock_github.list_pull_requests = AsyncMock(
        return_value=GitHubPRListResponse(
            items=[mock_pr, mock_pr, mock_pr],
            total_fetched=3,
            pages_fetched=1,
            per_page=100,
            fetch_limit=500,
            complete=True,
            truncated=False,
            fetch_duration_ms=10.0
        )
    )

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls?state=closed")
        assert res.status_code == 200
        data = res.json()
        assert data["total_fetched"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["number"] == 5000
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


def test_api_get_pr_detail_not_found_returns_precise_error():
    """Verify missing PR returns 404 with exact detail and not generic crash."""
    repo_id = str(uuid.uuid4())
    fake_repo = {
        "id": repo_id,
        "owner": "vectorize-io",
        "name": "hindsight",
        "full_name": "vectorize-io/hindsight",
        "default_branch": "main",
        "is_private": False,
        "installation_id": 123
    }

    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.get_repository_by_id = AsyncMock(return_value=fake_repo)

    mock_github = MagicMock(spec=GitHubService)
    mock_github.is_configured = False
    mock_github.get_pr_details_full = AsyncMock(side_effect=GitHubNotFoundError("PR 99999 not found"))

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls/99999")
        assert res.status_code == 404
        assert "not found on GitHub" in res.json()["detail"]
        assert "99999" in res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


def test_api_list_prs_keeps_distinct_prs_with_same_title():
    """Verify different PR numbers with identical titles are NOT collapsed."""
    repo_id = str(uuid.uuid4())
    fake_repo = {
        "id": repo_id,
        "owner": "vectorize-io",
        "name": "hindsight",
        "full_name": "vectorize-io/hindsight",
        "default_branch": "main",
        "is_private": False,
        "installation_id": 123
    }
    
    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.get_repository_by_id = AsyncMock(return_value=fake_repo)
    
    pr_a = GitHubPR(
        id=101,
        number=101,
        title="Bump dependencies",
        state="closed",
        author="bot",
        head_sha="sha1",
        base_branch="main"
    )
    pr_b = GitHubPR(
        id=102,
        number=102,
        title="Bump dependencies",
        state="closed",
        author="bot",
        head_sha="sha2",
        base_branch="main"
    )
    
    mock_github = MagicMock(spec=GitHubService)
    mock_github.list_pull_requests = AsyncMock(
        return_value=GitHubPRListResponse(
            items=[pr_a, pr_b],
            total_fetched=2,
            pages_fetched=1,
            per_page=100,
            fetch_limit=500,
            complete=True,
            truncated=False,
            fetch_duration_ms=10.0
        )
    )

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls?state=closed")
        assert res.status_code == 200
        data = res.json()
        assert data["total_fetched"] == 2
        numbers = [p["number"] for p in data["items"]]
        assert numbers == [101, 102]
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


@pytest.mark.asyncio
async def test_supabase_service_unauthenticated_repository_resolution():
    """Verify get_repository_by_id resolves workspace repository for unauth callers but denies private repos."""
    svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    repo_id = str(uuid.uuid4())
    default_workspace_user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace"))

    # Store repo owned by system default workspace user
    svc._local_repos[repo_id] = {
        "id": repo_id,
        "user_id": default_workspace_user_id,
        "github_repo_id": 99999,
        "full_name": "vectorize-io/hindsight",
        "default_branch": "main",
        "webhook_active": False,
    }

    # Unauthenticated resolution (e.g. direct review URL visit / refresh) must resolve workspace repo
    repo = await svc.get_repository_by_id(repo_id, user_id=None)
    assert repo is not None
    assert repo["id"] == repo_id
    assert repo["full_name"] == "vectorize-io/hindsight"

    # Authenticated user also has access to shared workspace repo
    any_user_id = str(uuid.uuid4())
    repo_shared = await svc.get_repository_by_id(repo_id, user_id=any_user_id)
    assert repo_shared is not None
    assert repo_shared["id"] == repo_id

    # Unauthenticated caller attempting to access another user's private repo must be blocked
    private_repo_id = str(uuid.uuid4())
    private_user_id = str(uuid.uuid4())
    svc._local_repos[private_repo_id] = {
        "id": private_repo_id,
        "user_id": private_user_id,
        "github_repo_id": 88888,
        "full_name": "private-org/secret-repo",
        "default_branch": "main",
        "webhook_active": False,
    }
    repo_private_unauth = await svc.get_repository_by_id(private_repo_id, user_id=None)
    assert repo_private_unauth is None

    # Authenticated resolution with wrong user_id must NOT resolve private repo (multi-tenant security)
    other_user_id = str(uuid.uuid4())
    repo_other = await svc.get_repository_by_id(private_repo_id, user_id=other_user_id)
    assert repo_other is None


def test_format_error_cause_timeout_and_empty():
    """Verify _format_error_cause formats timeouts and never leaves trailing colons or blank strings."""
    from app.services.github import _format_error_cause
    import httpx

    # ReadTimeout stringifies to empty string in httpx
    timeout_err = httpx.ReadTimeout("")
    assert str(timeout_err) == ""
    cause = _format_error_cause(timeout_err)
    assert cause == "Request timed out (ReadTimeout)"
    assert not cause.endswith(":")

    # Generic TimeoutException
    generic_timeout = httpx.TimeoutException("")
    assert _format_error_cause(generic_timeout) == "Request timed out"

    # Network connection error
    conn_err = httpx.ConnectError("Connection refused:")
    cause_conn = _format_error_cause(conn_err)
    assert not cause_conn.endswith(":")
    assert "Network connection failed" in cause_conn

    # Empty exception
    empty_err = Exception("")
    assert _format_error_cause(empty_err) == "Exception"


@pytest.mark.asyncio
async def test_github_service_pr_details_in_flight_deduplication():
    """Verify concurrent requests for the same PR share a single in-flight execution and cache."""
    import asyncio
    from app.services.github import GitHubService
    from app.schemas.github import GitHubPR, PRDetailResponse

    service = GitHubService(token="test_token")
    # Clear cache
    service._pr_details_in_flight.clear()
    service._pr_details_cache.clear()

    call_count = 0

    async def mock_get_pr(owner, repo, pr):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        return GitHubPR(
            id=pr,
            number=pr,
            title=f"Test PR #{pr}",
            state="open",
            author="tester",
            head_sha="sha999",
            base_branch="main"
        )

    with patch.object(service, "get_pull_request", side_effect=mock_get_pr), \
         patch.object(service, "get_pr_files", new_callable=AsyncMock, return_value=[]), \
         patch.object(service, "get_pr_commits", new_callable=AsyncMock, return_value=[]), \
         patch.object(service, "get_pr_comments", new_callable=AsyncMock, return_value=[]):

        # Fire 5 concurrent requests for PR 5120
        results = await asyncio.gather(*[
            service.get_pr_details_full("vectorize-io", "hindsight", 5120)
            for _ in range(5)
        ])

        # All 5 callers get the exact same PR detail response
        assert len(results) == 5
        for r in results:
            assert r.pr.number == 5120
            assert r.pr.title == "Test PR #5120"

        # GitHub get_pull_request was only called ONCE due to in-flight deduplication!
        assert call_count == 1

        # Subsequent call hits the 60s cache immediately without calling get_pull_request again
        cached_result = await service.get_pr_details_full("vectorize-io", "hindsight", 5120)
        assert cached_result.pr.number == 5120
        assert call_count == 1


def test_api_handle_service_error_no_trailing_colon():
    """Verify handle_service_error strips trailing colons and returns informative details."""
    from app.api.repositories import handle_service_error
    from app.core.exceptions import GitHubError
    from fastapi import HTTPException

    err = GitHubError("Failed to fetch PR #5120 for 'vectorize-io/hindsight':")
    with pytest.raises(HTTPException) as exc_info:
        handle_service_error(err, "fetching PR detail")

    assert exc_info.value.status_code == 502
    assert not exc_info.value.detail.endswith(":")
    assert exc_info.value.detail == "Failed to fetch PR #5120 for 'vectorize-io/hindsight'"


def test_api_detail_endpoint_resolves_pr_cold():
    """Verify GET /repositories/{repo_id}/pulls/{pr_number} resolves PR independently of list state."""
    repo_id = str(uuid.uuid4())
    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={
            "id": repo_id,
            "full_name": "vectorize-io/hindsight",
            "github_repo_id": 1086419061,
            "user_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace")),
        }
    )
    mock_supabase.upsert_pull_request = AsyncMock()

    mock_pr = GitHubPR(
        id=5120,
        number=5120,
        title="chore: bump dependencies",
        state="open",
        author="cdbartholomew",
        head_sha="sha5120",
        base_branch="main"
    )
    mock_detail = PRDetailResponse(
        pr=mock_pr,
        files=[],
        commits=[],
        comments=[]
    )

    mock_github = MagicMock(spec=GitHubService)
    mock_github.get_pr_details_full = AsyncMock(return_value=mock_detail)
    mock_github.is_configured = True

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls/5120")
        assert res.status_code == 200
        data = res.json()
        assert data["pr"]["number"] == 5120
        assert data["pr"]["state"] == "open"
        assert data["pr"]["author"] == "cdbartholomew"
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


def test_api_detail_endpoint_404_not_found():
    """Verify GET /repositories/{repo_id}/pulls/{pr_number} returns 404 when PR not found."""
    repo_id = str(uuid.uuid4())
    mock_supabase = MagicMock(spec=SupabaseService)
    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={
            "id": repo_id,
            "full_name": "vectorize-io/hindsight",
            "github_repo_id": 1086419061,
            "user_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace")),
        }
    )

    mock_github = MagicMock(spec=GitHubService)
    mock_github.get_pr_details_full = AsyncMock(side_effect=GitHubNotFoundError("PR 99999 not found"))
    mock_github.is_configured = True

    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        res = client.get(f"/api/v1/repositories/{repo_id}/pulls/99999")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


