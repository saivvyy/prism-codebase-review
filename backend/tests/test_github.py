from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    ConfigurationError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from app.main import app
from app.schemas.github import (
    GitHubFileDiff,
    GitHubPR,
    GitHubPRListResponse,
    GitHubRepo,
    PRDetailResponse,
)
from app.services.github import GitHubService, get_github_service
from app.services.supabase import get_supabase_service

client = TestClient(app)


# ---------------------------------------------------------------------------
# GitHub Service Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_service_unconfigured():
    service = GitHubService(token=None)
    assert not service.is_configured
    with pytest.raises(ConfigurationError):
        _ = service._get_headers()


@pytest.mark.asyncio
async def test_get_authenticated_user_success():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = {
        "login": "octocat",
        "id": 1,
        "name": "The Octocat",
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "html_url": "https://github.com/octocat",
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        user = await service.get_authenticated_user()

        assert user.login == "octocat"
        assert user.id == 1
        assert user.name == "The Octocat"


@pytest.mark.asyncio
async def test_get_repository_not_found():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.is_error = True
    mock_resp.text = "Not Found"

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubNotFoundError):
            await service.get_repository("nonexistent", "repo")


@pytest.mark.asyncio
async def test_rate_limit_error():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403
    mock_resp.is_error = True
    mock_resp.text = "API rate limit exceeded for user"

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubRateLimitError):
            await service.list_user_repositories()


@pytest.mark.asyncio
async def test_auth_error():
    service = GitHubService(token="invalid-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    mock_resp.is_error = True
    mock_resp.text = "Bad credentials"

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubAuthError):
            await service.get_authenticated_user()


@pytest.mark.asyncio
async def test_get_pr_files_diff():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.json.return_value = [
        {
            "filename": "app/controllers/user_controller.py",
            "status": "modified",
            "additions": 15,
            "deletions": 2,
            "changes": 17,
            "patch": "@@ -10,3 +10,15 @@\n+from app.database import db\n+db.query()",
        }
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        diffs = await service.get_pr_files("owner", "repo", 142)

        assert len(diffs) == 1
        assert diffs[0].filename == "app/controllers/user_controller.py"
        assert "db.query()" in diffs[0].patch


# ---------------------------------------------------------------------------
# API Routes Tests with Dependency Overrides
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_github():
    service = MagicMock(spec=GitHubService)
    service.is_configured = True
    return service


def test_connect_repository_bad_format():
    resp = client.post("/api/v1/repositories/connect", json={"full_name": "invalid-format"})
    assert resp.status_code == 400
    assert "owner/repo" in resp.json()["detail"]


def test_connect_repository_success(mock_github):
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=12345,
            full_name="octocat/Hello-World",
            name="Hello-World",
            owner="octocat",
            default_branch="main",
            private=False,
        )
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        resp = client.post("/api/v1/repositories/connect", json={"full_name": "octocat/Hello-World"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["full_name"] == "octocat/Hello-World"
        assert data["github_repo_id"] == 12345
        assert "id" in data
    finally:
        app.dependency_overrides.clear()


def test_list_repositories(mock_github):
    # Ensure at least one repository is connected
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=99999,
            full_name="org/test-repo",
            name="test-repo",
            owner="org",
            default_branch="main",
            private=False,
        )
    )
    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        client.post("/api/v1/repositories/connect", json={"full_name": "org/test-repo"})
        resp = client.get("/api/v1/repositories")
        assert resp.status_code == 200
        repos = resp.json()
        assert len(repos) >= 1
        assert any(r["full_name"] == "org/test-repo" for r in repos)
    finally:
        app.dependency_overrides.clear()


def test_list_repository_pulls_not_found():
    resp = client.get("/api/v1/repositories/non-existent-uuid/pulls")
    assert resp.status_code == 404


def test_list_repository_pulls_success(mock_github):
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=777,
            full_name="org/prs-repo",
            name="prs-repo",
            owner="org",
            default_branch="main",
        )
    )
    mock_github.list_pull_requests = AsyncMock(
        return_value=[
            GitHubPR(
                id=1,
                number=142,
                title="Direct DB Access in Controller",
                state="open",
                author="dev-bob",
                head_sha="abc1234",
                base_branch="main",
            )
        ]
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        conn = client.post("/api/v1/repositories/connect", json={"full_name": "org/prs-repo"}).json()
        repo_id = conn["id"]

        resp = client.get(f"/api/v1/repositories/{repo_id}/pulls")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total_fetched"] == 1
        assert data["pages_fetched"] == 1
        assert data["per_page"] == 100
        assert data["fetch_limit"] == 500
        assert data["complete"] is True
        assert data["truncated"] is False
        prs = data["items"]
        assert len(prs) == 1
        assert prs[0]["number"] == 142
        assert prs[0]["author"] == "dev-bob"
    finally:
        app.dependency_overrides.clear()


def test_get_pull_request_detail_success(mock_github):
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=888,
            full_name="org/detail-repo",
            name="detail-repo",
            owner="org",
            default_branch="main",
        )
    )
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(
            pr=GitHubPR(
                id=2,
                number=142,
                title="Controllers directly access persistence",
                state="open",
                author="alice",
                head_sha="deadbeef",
                base_branch="main",
            ),
            files=[
                GitHubFileDiff(
                    filename="src/controllers/order.py",
                    status="modified",
                    additions=20,
                    deletions=5,
                    changes=25,
                    patch="@@ -1,5 +1,10 @@\n+from db import engine\n+engine.execute()",
                )
            ],
            commits=[],
            comments=[],
        )
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        conn = client.post("/api/v1/repositories/connect", json={"full_name": "org/detail-repo"}).json()
        repo_id = conn["id"]

        resp = client.get(f"/api/v1/repositories/{repo_id}/pulls/142")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pr"]["number"] == 142
        assert len(data["files"]) == 1
        assert data["files"][0]["filename"] == "src/controllers/order.py"
        assert "engine.execute()" in data["files"][0]["patch"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GitHub Pagination & Sanitization Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pull_requests_single_page():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.headers = {}
    mock_resp.json.return_value = [
        {
            "id": 101,
            "number": 1,
            "title": "Initial PR",
            "state": "open",
            "user": {"login": "alice"},
            "head": {"sha": "sha1"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/org/repo/pull/1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        prs = await service.list_pull_requests("org", "repo", per_page=100)
        assert len(prs) == 1
        assert prs[0].id == 101
        assert prs[0].number == 1
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_list_pull_requests_multi_page():
    service = GitHubService(token="fake-token")

    page1_items = [
        {
            "id": i,
            "number": i,
            "title": f"PR #{i}",
            "state": "open",
            "user": {"login": f"user{i}"},
            "head": {"sha": f"sha{i}"},
            "base": {"ref": "main"},
            "html_url": f"https://github.com/org/repo/pull/{i}",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        for i in range(1, 101)
    ]
    page2_items = [
        {
            "id": i,
            "number": i,
            "title": f"PR #{i}",
            "state": "open",
            "user": {"login": f"user{i}"},
            "head": {"sha": f"sha{i}"},
            "base": {"ref": "main"},
            "html_url": f"https://github.com/org/repo/pull/{i}",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        for i in range(101, 126)
    ]

    mock_resp1 = MagicMock(spec=httpx.Response)
    mock_resp1.status_code = 200
    mock_resp1.is_error = False
    mock_resp1.headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next"'}
    mock_resp1.json.return_value = page1_items

    mock_resp2 = MagicMock(spec=httpx.Response)
    mock_resp2.status_code = 200
    mock_resp2.is_error = False
    mock_resp2.headers = {}
    mock_resp2.json.return_value = page2_items

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_resp1, mock_resp2]
        prs = await service.list_pull_requests("org", "repo", per_page=100)
        assert len(prs) == 125
        assert prs[0].number == 1
        assert prs[99].number == 100
        assert prs[100].number == 101
        assert prs[124].number == 125
        assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_list_pull_requests_empty_final_page():
    service = GitHubService(token="fake-token")

    page1_items = [
        {
            "id": i,
            "number": i,
            "title": f"PR #{i}",
            "state": "open",
            "user": {"login": f"user{i}"},
            "head": {"sha": f"sha{i}"},
            "base": {"ref": "main"},
            "html_url": f"https://github.com/org/repo/pull/{i}",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        for i in range(1, 101)
    ]

    mock_resp1 = MagicMock(spec=httpx.Response)
    mock_resp1.status_code = 200
    mock_resp1.is_error = False
    mock_resp1.headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next"'}
    mock_resp1.json.return_value = page1_items

    mock_resp2 = MagicMock(spec=httpx.Response)
    mock_resp2.status_code = 200
    mock_resp2.is_error = False
    mock_resp2.headers = {}
    mock_resp2.json.return_value = []

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_resp1, mock_resp2]
        prs = await service.list_pull_requests("org", "repo", per_page=100)
        assert len(prs) == 100
        assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_list_pull_requests_duplicate_prevention():
    service = GitHubService(token="fake-token")

    # Item with id=100 appears in both pages (e.g. PR list shifted between calls)
    page1 = [
        {"id": 1, "number": 1, "title": "PR 1", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "m"}},
        {"id": 100, "number": 100, "title": "PR 100", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "m"}},
    ]
    page2 = [
        {"id": 100, "number": 100, "title": "PR 100", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "m"}},
        {"id": 101, "number": 101, "title": "PR 101", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "m"}},
    ]

    mock_resp1 = MagicMock(spec=httpx.Response)
    mock_resp1.status_code = 200
    mock_resp1.is_error = False
    mock_resp1.headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next"'}
    mock_resp1.json.return_value = page1

    mock_resp2 = MagicMock(spec=httpx.Response)
    mock_resp2.status_code = 200
    mock_resp2.is_error = False
    mock_resp2.headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="last"'}
    mock_resp2.json.return_value = page2

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_resp1, mock_resp2]
        prs = await service.list_pull_requests("org", "repo", per_page=2)
        assert len(prs) == 3
        assert [p.id for p in prs] == [1, 100, 101]


@pytest.mark.asyncio
async def test_list_pull_requests_110_prs_complete():
    service = GitHubService(token="fake-token")

    page1_items = [
        {"id": i, "number": i, "title": f"PR #{i}", "state": "open", "user": {"login": f"u{i}"}, "head": {"sha": f"s{i}"}, "base": {"ref": "main"}}
        for i in range(1, 101)
    ]
    page2_items = [
        {"id": i, "number": i, "title": f"PR #{i}", "state": "open", "user": {"login": f"u{i}"}, "head": {"sha": f"s{i}"}, "base": {"ref": "main"}}
        for i in range(101, 111)
    ]

    resp1 = MagicMock(spec=httpx.Response, status_code=200, is_error=False)
    resp1.headers = {"Link": '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next"'}
    resp1.json.return_value = page1_items

    resp2 = MagicMock(spec=httpx.Response, status_code=200, is_error=False)
    resp2.headers = {}
    resp2.json.return_value = page2_items

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [resp1, resp2]
        res = await service.list_pull_requests("org", "repo", per_page=100)

        assert res.total_fetched == 110
        assert res.pages_fetched == 2
        assert res.per_page == 100
        assert res.fetch_limit == 500
        assert res.complete is True
        assert res.truncated is False
        assert len(res.items) == 110


@pytest.mark.asyncio
async def test_list_pull_requests_500_plus_truncated():
    service = GitHubService(token="fake-token")

    pages = []
    for page_idx in range(5):
        page_items = [
            {"id": page_idx * 100 + i, "number": page_idx * 100 + i, "title": f"PR #{page_idx * 100 + i}", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "main"}}
            for i in range(1, 101)
        ]
        resp = MagicMock(spec=httpx.Response, status_code=200, is_error=False)
        resp.headers = {"Link": f'<https://api.github.com/repos/org/repo/pulls?page={page_idx + 2}>; rel="next"'}
        resp.json.return_value = page_items
        pages.append(resp)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = pages
        res = await service.list_pull_requests("org", "repo", per_page=100)

        assert res.total_fetched == 500
        assert res.pages_fetched == 5
        assert res.per_page == 100
        assert res.fetch_limit == 500
        assert res.complete is False
        assert res.truncated is True
        assert len(res.items) == 500
        assert mock_get.call_count == 5


@pytest.mark.asyncio
async def test_list_pull_requests_small_repo():
    service = GitHubService(token="fake-token")

    items = [
        {"id": i, "number": i, "title": f"PR #{i}", "state": "open", "user": {"login": "u"}, "head": {"sha": "s"}, "base": {"ref": "main"}}
        for i in range(1, 16)
    ]
    resp = MagicMock(spec=httpx.Response, status_code=200, is_error=False)
    resp.headers = {}
    resp.json.return_value = items

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = resp
        res = await service.list_pull_requests("org", "repo", per_page=100)

        assert res.total_fetched == 15
        assert res.pages_fetched == 1
        assert res.complete is True
        assert res.truncated is False
        assert len(res.items) == 15
        assert mock_get.call_count == 1



@pytest.mark.asyncio
async def test_list_pull_requests_rate_limit():
    service = GitHubService(token="fake-token")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403
    mock_resp.is_error = True
    mock_resp.text = '{"message": "API rate limit exceeded for user"}'
    mock_resp.headers = {"x-ratelimit-remaining": "0"}
    mock_resp.json.return_value = {"message": "API rate limit exceeded"}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        with pytest.raises(GitHubRateLimitError):
            await service.list_pull_requests("org", "repo")


def test_connect_repository_url_format_sanitization(mock_github):
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=999,
            full_name="ronaksarda/microsoft-hindsight",
            name="microsoft-hindsight",
            owner="ronaksarda",
            default_branch="main",
        )
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        # Accepts owner/repo
        res1 = client.post("/api/v1/repositories/connect", json={"full_name": "ronaksarda/microsoft-hindsight"})
        assert res1.status_code == 201
        assert res1.json()["full_name"] == "ronaksarda/microsoft-hindsight"

        # Accepts full URL format gracefully as well
        res2 = client.post("/api/v1/repositories/connect", json={"full_name": "https://github.com/ronaksarda/microsoft-hindsight"})
        assert res2.status_code == 201
        assert res2.json()["full_name"] == "ronaksarda/microsoft-hindsight"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_pull_requests_open_closed_merged_categorization():
    service = GitHubService(token="fake-token")

    raw_items = [
        {
            "id": 1,
            "number": 101,
            "title": "Open feature PR",
            "state": "open",
            "merged_at": None,
            "closed_at": None,
            "user": {"login": "alice"},
            "head": {"sha": "sha_open"},
            "base": {"ref": "main", "sha": "base_sha1"},
            "html_url": "https://github.com/org/repo/pull/101",
        },
        {
            "id": 2,
            "number": 102,
            "title": "Merged PR into main",
            "state": "closed",
            "merged_at": "2026-09-20T10:00:00Z",
            "closed_at": "2026-09-20T10:00:00Z",
            "user": {"login": "bob"},
            "head": {"sha": "sha_merged"},
            "base": {"ref": "main", "sha": "base_sha2"},
            "html_url": "https://github.com/org/repo/pull/102",
        },
        {
            "id": 3,
            "number": 103,
            "title": "Closed without merge PR",
            "state": "closed",
            "merged_at": None,
            "closed_at": "2026-09-21T11:00:00Z",
            "user": {"login": "carol"},
            "head": {"sha": "sha_closed"},
            "base": {"ref": "main", "sha": "base_sha3"},
            "html_url": "https://github.com/org/repo/pull/103",
        },
    ]

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.is_error = False
    mock_resp.headers = {}
    mock_resp.json.return_value = raw_items

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        prs = await service.list_pull_requests("org", "repo", state="all")

        assert len(prs) == 3
        # Check Open PR
        assert prs[0].number == 101
        assert prs[0].state == "open"
        assert prs[0].is_merged is False

        # Check Merged PR
        assert prs[1].number == 102
        assert prs[1].state == "merged"
        assert prs[1].is_merged is True
        assert prs[1].merged_at == "2026-09-20T10:00:00Z"

        # Check Closed PR
        assert prs[2].number == 103
        assert prs[2].state == "closed"
        assert prs[2].is_merged is False
        assert prs[2].closed_at == "2026-09-21T11:00:00Z"


def test_list_repository_pulls_retrieves_both_open_and_closed(mock_github):
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=778,
            full_name="org/all-prs-repo",
            name="all-prs-repo",
            owner="org",
            default_branch="main",
        )
    )
    mock_github.list_pull_requests = AsyncMock(
        return_value=[
            GitHubPR(
                id=1,
                number=201,
                title="Active open PR",
                state="open",
                author="dev-alice",
                head_sha="sha1",
                base_branch="main",
                is_merged=False,
            ),
            GitHubPR(
                id=2,
                number=202,
                title="Completed merged PR",
                state="merged",
                author="dev-bob",
                head_sha="sha2",
                base_branch="main",
                merged_at="2026-09-25T12:00:00Z",
                is_merged=True,
            ),
            GitHubPR(
                id=3,
                number=203,
                title="Rejected closed PR",
                state="closed",
                author="dev-charlie",
                head_sha="sha3",
                base_branch="main",
                closed_at="2026-09-26T15:00:00Z",
                is_merged=False,
            ),
        ]
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        conn = client.post("/api/v1/repositories/connect", json={"full_name": "org/all-prs-repo"}).json()
        repo_id = conn["id"]

        resp = client.get(f"/api/v1/repositories/{repo_id}/pulls")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_fetched"] == 3
        prs = data["items"]
        assert len(prs) == 3

        states = [p["state"] for p in prs]
        assert "open" in states
        assert "merged" in states
        assert "closed" in states

        # Verify state parameter was defaulted to 'all'
        mock_github.list_pull_requests.assert_called_with("org", "all-prs-repo", state="all")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supabase_upsert_pull_requests_batch():
    from app.services.supabase import SupabaseService
    svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    prs_sample = [
        {"number": 1, "title": "PR 1", "author": "alice", "head_sha": "sha1", "base_branch": "main", "state": "open"},
        {"number": 2, "title": "PR 2", "author": "bob", "head_sha": "sha2", "base_branch": "main", "state": "merged"},
    ]
    # Verify non-blocking batch upsert succeeds even with local fallback or unconfigured RLS
    await svc.upsert_pull_requests_batch("test-repo-uuid", prs_sample)
    local_prs = await svc.list_pull_requests("test-repo-uuid")
    assert len(local_prs) == 2
    assert {p["pr_number"] for p in local_prs} == {1, 2}


@pytest.mark.asyncio
async def test_open_count_not_equal_to_total_count():
    """Proves: open count != total count (e.g. 100 open != 3600 total),
    and get_repository_open_pr_count specifically queries GET /repos/{owner}/{repo}/pulls?state=open&per_page=1
    using Link pagination metadata rather than state=all or total count.
    """
    service = GitHubService(token="fake-token")

    # Simulate 100 open PRs Link header (rel="last" page=100) with per_page=1
    open_resp = MagicMock(spec=httpx.Response)
    open_resp.status_code = 200
    open_resp.is_error = False
    open_resp.headers = {
        "link": '<https://api.github.com/repositories/123/pulls?state=open&per_page=1&page=2>; rel="next", <https://api.github.com/repositories/123/pulls?state=open&per_page=1&page=100>; rel="last"'
    }
    open_resp.json.return_value = [{"id": 1, "number": 100}]

    # Simulate 3600 total PRs Link header (rel="last" page=3600) with per_page=1
    total_resp = MagicMock(spec=httpx.Response)
    total_resp.status_code = 200
    total_resp.is_error = False
    total_resp.headers = {
        "link": '<https://api.github.com/repositories/123/pulls?state=all&per_page=1&page=2>; rel="next", <https://api.github.com/repositories/123/pulls?state=all&per_page=1&page=3600>; rel="last"'
    }
    total_resp.json.return_value = [{"id": 1, "number": 3600}]

    # 1. Fetch Open PR count
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = open_resp
        open_count = await service.get_repository_open_pr_count("org", "sample-repo")

        # Verify specifically queries state=open & per_page=1
        mock_get.assert_called_once_with(
            "https://api.github.com/repos/org/sample-repo/pulls",
            headers=service._get_headers(require_auth=False),
            params={"state": "open", "per_page": 1},
            timeout=10.0,
        )

    # 2. Fetch Total PR count
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        # Search API returns 3600 total
        search_resp = MagicMock(spec=httpx.Response)
        search_resp.status_code = 200
        search_resp.json.return_value = {"total_count": 3600}
        mock_get.return_value = search_resp
        total_count = await service.get_repository_total_pr_count("org", "sample-repo")

    # 3. Assert open count != total count
    assert open_count == 100
    assert total_count == 3600
    assert open_count != total_count, f"Expected open count ({open_count}) != total count ({total_count})"


def test_repository_header_count_endpoint_uses_open_count(mock_github):
    """Proves: The header PR count metadata endpoint (/pr-count and /open-pr-count)
    uses the actual OPEN count (100) instead of total count (3600).
    """
    mock_github.get_repository = AsyncMock(
        return_value=GitHubRepo(
            id=54321,
            full_name="org/header-repo",
            name="header-repo",
            owner="org",
            default_branch="main",
        )
    )
    mock_github.get_repository_open_pr_count = AsyncMock(return_value=100)
    mock_github.get_repository_total_pr_count = AsyncMock(return_value=3600)
    mock_github.list_pull_requests = AsyncMock(
        return_value=GitHubPRListResponse(
            items=[],
            total_fetched=0,
            pages_fetched=1,
            per_page=100,
            fetch_limit=500,
            complete=True,
            truncated=False,
            fetch_duration_ms=0.0,
        )
    )

    app.dependency_overrides[get_github_service] = lambda: mock_github
    try:
        # Connect repo
        conn = client.post("/api/v1/repositories/connect", json={"full_name": "org/header-repo"}).json()
        repo_id = conn["id"]

        # Call header metadata count endpoint
        resp = client.get(f"/api/v1/repositories/{repo_id}/pr-count")
        assert resp.status_code == 200
        data = resp.json()

        # Proves header count uses OPEN count (100) and NOT total count (3600)
        assert data["pr_count"] == 100
        assert data["pr_count"] != 3600
        assert data["open_pr_count"] == 100
        mock_github.get_repository_open_pr_count.assert_called_with("org", "header-repo")

        # Also test dedicated open-pr-count endpoint
        resp_open = client.get(f"/api/v1/repositories/{repo_id}/open-pr-count")
        assert resp_open.status_code == 200
        data_open = resp_open.json()
        assert data_open["open_pr_count"] == 100
        assert data_open["open_pr_count"] != 3600

        # And verify pulls endpoint returns open_pr_count as 100
        pulls_resp = client.get(f"/api/v1/repositories/{repo_id}/pulls")
        assert pulls_resp.status_code == 200
        pulls_data = pulls_resp.json()
        assert pulls_data["open_pr_count"] == 100
        assert pulls_data["open_pr_count"] != 3600
    finally:
        app.dependency_overrides.clear()


