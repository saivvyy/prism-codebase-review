import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.exceptions import GitHubAuthError
from app.main import app
from app.schemas.auth import UserSessionProfile
from app.schemas.github import GitHubPR, GitHubPRListResponse, PRDetailResponse
from app.services.auth import get_auth_service
from app.services.github import GitHubService, get_github_service
from app.services.supabase import get_supabase_service


@pytest.fixture
def mock_pr_item():
    return GitHubPR(
        id=5555,
        number=42,
        title="Security Hardened Feature",
        state="open",
        author="alice",
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
        head_sha="sha-head-42",
        base_branch="main",
    )


@pytest.fixture
async def security_context(mock_pr_item):
    """Sets up isolated mock users, tokens, and repositories for testing PRism security boundaries."""
    client = TestClient(app)

    supabase = get_supabase_service()
    supabase.supabase_url = ""
    supabase.supabase_key = ""
    supabase._local_repos = {}
    supabase._local_prs = {}
    supabase._local_reviews = {}
    supabase._local_findings = {}

    auth_service = get_auth_service()
    auth_service._sessions.clear()

    # User A
    user_a = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.1001")),
        github_user_id=1001,
        login="alice",
        name="Alice Developer",
        avatar_url="https://github.com/alice.png",
    )
    token_a = await auth_service.create_session(user=user_a, github_access_token="gho_ALICE_VALID_TOKEN_123")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # User B
    user_b = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.2002")),
        github_user_id=2002,
        login="bob",
        name="Bob Architect",
        avatar_url="https://github.com/bob.png",
    )
    token_b = await auth_service.create_session(user=user_b, github_access_token="gho_BOB_VALID_TOKEN_456")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User C (Unauthorized user with their own valid OAuth token)
    user_c = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.3003")),
        github_user_id=3003,
        login="charlie",
        name="Charlie Hacker",
        avatar_url="https://github.com/charlie.png",
    )
    token_c = await auth_service.create_session(user=user_c, github_access_token="gho_CHARLIE_VALID_TOKEN_789")
    headers_c = {"Authorization": f"Bearer {token_c}"}

    # User D (Unauthorized user with an EXPIRED OAuth token)
    user_d = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.4004")),
        github_user_id=4004,
        login="dave",
        name="Dave Expired",
        avatar_url="https://github.com/dave.png",
    )
    token_d = await auth_service.create_session(user=user_d, github_access_token="gho_DAVE_EXPIRED_TOKEN_000")
    headers_d = {"Authorization": f"Bearer {token_d}"}

    # Connect Repo A for User A
    repo_a = await supabase.connect_repository(
        github_repo_id=11111,
        full_name="alice-org/repo-a",
        default_branch="main",
        user_id=user_a.id,
    )
    repo_a_id = repo_a["id"]

    # Connect Repo B for User B
    repo_b = await supabase.connect_repository(
        github_repo_id=22222,
        full_name="bob-org/repo-b",
        default_branch="main",
        user_id=user_b.id,
    )
    repo_b_id = repo_b["id"]

    system_gh = MagicMock(spec=GitHubService)
    system_gh.is_configured = True
    system_gh.list_pull_requests = AsyncMock(
        return_value=GitHubPRListResponse(
            items=[mock_pr_item],
            total_fetched=1,
            pages_fetched=1,
            per_page=100,
            fetch_limit=500,
            complete=True,
            truncated=False,
            fetch_duration_ms=10.0,
            last_synced_at="2026-10-01T00:00:00Z",
            open_pr_count=1,
            total_repository_prs=1,
        )
    )
    system_gh.get_repository_open_pr_count = AsyncMock(return_value=1)
    system_gh.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(
            pr=mock_pr_item,
            files=[],
            commits=[],
            comments=[],
        )
    )

    app.dependency_overrides[get_github_service] = lambda: system_gh

    yield {
        "client": client,
        "supabase": supabase,
        "auth_service": auth_service,
        "system_gh": system_gh,
        "user_a": user_a,
        "token_a": token_a,
        "headers_a": headers_a,
        "repo_a_id": repo_a_id,
        "user_b": user_b,
        "token_b": token_b,
        "headers_b": headers_b,
        "repo_b_id": repo_b_id,
        "user_c": user_c,
        "token_c": token_c,
        "headers_c": headers_c,
        "user_d": user_d,
        "token_d": token_d,
        "headers_d": headers_d,
        "mock_pr_item": mock_pr_item,
    }

    app.dependency_overrides.clear()


# ==============================================================================
# Security Invariant 1: Authorized user + valid OAuth token -> succeeds
# ==============================================================================
@pytest.mark.asyncio
async def test_1_authorized_user_valid_oauth_token_succeeds(security_context):
    s = security_context
    client = s["client"]

    # Mock user-scoped GitHub service call
    with patch("app.api.repositories.GitHubService") as MockGHService:
        user_gh_instance = MagicMock()
        user_gh_instance.list_pull_requests = AsyncMock(
            return_value=[s["mock_pr_item"]]
        )
        user_gh_instance.get_repository_open_pr_count = AsyncMock(return_value=1)
        MockGHService.return_value = user_gh_instance

        resp = client.get(
            f"/api/v1/repositories/{s['repo_a_id']}/pulls?state=open",
            headers=s["headers_a"],
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["number"] == 42
    # Proves user token was used
    MockGHService.assert_called_with(token="gho_ALICE_VALID_TOKEN_123")


# ==============================================================================
# Security Invariant 2: Authorized user + expired OAuth token + system token -> succeeds
# ==============================================================================
@pytest.mark.asyncio
async def test_2_authorized_user_expired_oauth_token_system_fallback_succeeds(security_context):
    s = security_context
    client = s["client"]

    with patch("app.api.repositories.GitHubService") as MockGHService:
        user_gh_instance = MagicMock()
        # User token is expired -> raises GitHubAuthError
        user_gh_instance.list_pull_requests = AsyncMock(
            side_effect=GitHubAuthError("Bad credentials")
        )
        MockGHService.return_value = user_gh_instance

        resp = client.get(
            f"/api/v1/repositories/{s['repo_a_id']}/pulls?state=open",
            headers=s["headers_a"],
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["number"] == 42
    # System fallback was invoked because user was already authorized for repo A
    s["system_gh"].list_pull_requests.assert_awaited_once_with(
        "alice-org", "repo-a", state="open"
    )


# ==============================================================================
# Security Invariant 3: Unauthorized user + valid OAuth token -> denied
# ==============================================================================
@pytest.mark.asyncio
async def test_3_unauthorized_user_valid_oauth_token_denied(security_context):
    s = security_context
    client = s["client"]

    # Charlie (User C) has a valid GitHub OAuth token, but has NOT connected Repo B
    with patch("app.api.repositories.GitHubService") as MockGHService:
        resp = client.get(
            f"/api/v1/repositories/{s['repo_b_id']}/pulls?state=open",
            headers=s["headers_c"],
        )
        # GitHubService must never be instantiated for an unauthorized repo
        assert MockGHService.call_count == 0

    assert resp.status_code == 404
    assert "not found in connected repositories" in resp.json()["detail"].lower()
    # System GitHub token was never called
    assert s["system_gh"].list_pull_requests.call_count == 0


# ==============================================================================
# Security Invariant 4: Unauthorized user + expired OAuth token + system token -> STILL denied
# ==============================================================================
@pytest.mark.asyncio
async def test_4_unauthorized_user_expired_oauth_token_system_token_still_denied(security_context):
    s = security_context
    client = s["client"]

    # Dave (User D) has an expired token, is unauthorized for Repo B, and backend has system token
    with patch("app.api.repositories.GitHubService") as MockGHService:
        resp = client.get(
            f"/api/v1/repositories/{s['repo_b_id']}/pulls?state=open",
            headers=s["headers_d"],
        )
        assert MockGHService.call_count == 0

    assert resp.status_code == 404
    assert s["system_gh"].list_pull_requests.call_count == 0


# ==============================================================================
# Security Invariant 5: User A cannot access User B's connected repository using system token
# ==============================================================================
@pytest.mark.asyncio
async def test_5_user_a_cannot_access_user_b_repo_using_system_token(security_context):
    s = security_context
    client = s["client"]

    # User A tries pulls
    resp_pulls = client.get(
        f"/api/v1/repositories/{s['repo_b_id']}/pulls",
        headers=s["headers_a"],
    )
    assert resp_pulls.status_code == 404

    # User A tries PR detail
    resp_detail = client.get(
        f"/api/v1/repositories/{s['repo_b_id']}/pulls/42",
        headers=s["headers_a"],
    )
    assert resp_detail.status_code == 404

    # User A tries pr-count
    resp_count = client.get(
        f"/api/v1/repositories/{s['repo_b_id']}/pr-count",
        headers=s["headers_a"],
    )
    assert resp_count.status_code == 404

    # User A tries open-pr-count
    resp_opencount = client.get(
        f"/api/v1/repositories/{s['repo_b_id']}/open-pr-count",
        headers=s["headers_a"],
    )
    assert resp_opencount.status_code == 404

    # System token was never touched for repo B
    assert s["system_gh"].list_pull_requests.call_count == 0
    assert s["system_gh"].get_pr_details_full.call_count == 0


# ==============================================================================
# Security Invariant 6: Guessing a repo_id does not grant access
# ==============================================================================
@pytest.mark.asyncio
async def test_6_guessing_repo_id_does_not_grant_access(security_context):
    s = security_context
    client = s["client"]

    random_id = str(uuid.uuid4())

    # Authenticated user guessing random_id
    resp_auth = client.get(
        f"/api/v1/repositories/{random_id}/pulls",
        headers=s["headers_a"],
    )
    assert resp_auth.status_code == 404

    # Unauthenticated user guessing random_id
    resp_unauth = client.get(f"/api/v1/repositories/{random_id}/pulls")
    assert resp_unauth.status_code == 404

    # Unauthenticated user guessing User B's real repo_id
    resp_unauth_real = client.get(f"/api/v1/repositories/{s['repo_b_id']}/pulls")
    assert resp_unauth_real.status_code == 404

    # System token was never called
    assert s["system_gh"].list_pull_requests.call_count == 0


# ==============================================================================
# Security Invariant 7: System token is never exposed to frontend
# ==============================================================================
@pytest.mark.asyncio
async def test_7_system_token_never_exposed_to_frontend(security_context):
    s = security_context
    client = s["client"]

    # Even under fallback execution, response body and headers must never leak system token
    with patch("app.api.repositories.GitHubService") as MockGHService:
        user_gh_instance = MagicMock()
        user_gh_instance.list_pull_requests = AsyncMock(
            side_effect=GitHubAuthError("Expired OAuth token")
        )
        MockGHService.return_value = user_gh_instance

        resp = client.get(
            f"/api/v1/repositories/{s['repo_a_id']}/pulls?state=open",
            headers=s["headers_a"],
        )

    assert resp.status_code == 200
    body_text = resp.text
    header_text = str(dict(resp.headers))

    # Verify settings token is not exposed
    settings = get_settings()
    if settings.GITHUB_TOKEN:
        assert settings.GITHUB_TOKEN not in body_text
        assert settings.GITHUB_TOKEN not in header_text

    assert "ghp_" not in body_text
    assert "github_token" not in body_text.lower()


# ==============================================================================
# Security Invariant 8: GitHub token is never returned in API responses
# ==============================================================================
@pytest.mark.asyncio
async def test_8_github_token_never_returned_in_api_responses(security_context):
    s = security_context
    client = s["client"]

    # 1. PR list endpoint
    resp1 = client.get(f"/api/v1/repositories/{s['repo_a_id']}/pulls", headers=s["headers_a"])
    assert "gho_ALICE_VALID_TOKEN_123" not in resp1.text
    assert "gho_" not in resp1.text

    # 2. Repository detail endpoint
    resp2 = client.get(f"/api/v1/repositories/{s['repo_a_id']}", headers=s["headers_a"])
    assert "gho_" not in resp2.text

    # 3. Auth user profile endpoint
    resp3 = client.get("/api/v1/auth/me", headers=s["headers_a"])
    assert "gho_" not in resp3.text
    assert "token" not in resp3.json() or resp3.json().get("token") is None


# ==============================================================================
# Security Invariant 9: Repository authorization happens before GitHub fallback
# ==============================================================================
@pytest.mark.asyncio
async def test_9_repository_authorization_happens_before_github_fallback(security_context):
    s = security_context
    client = s["client"]

    # When authorization fails, GitHubService is NEVER called and fallback is NEVER triggered
    with patch("app.api.repositories.GitHubService") as MockGHService:
        resp = client.get(
            f"/api/v1/repositories/{s['repo_b_id']}/pulls",
            headers=s["headers_a"],
        )
        assert MockGHService.call_count == 0

    assert resp.status_code == 404
    assert s["system_gh"].list_pull_requests.call_count == 0


# ==============================================================================
# Security Invariant 10: GitHub 401 is distinguishable from PRism authorization failure
# ==============================================================================
@pytest.mark.asyncio
async def test_10_github_401_distinguishable_from_prism_authorization_failure(security_context):
    s = security_context
    client = s["client"]

    # Scenario A: PRism authorization failure (User A accessing User B's repo)
    resp_prism_fail = client.get(
        f"/api/v1/repositories/{s['repo_b_id']}/pulls",
        headers=s["headers_a"],
    )
    # MUST be 404 Not Found (repository access denied)
    assert resp_prism_fail.status_code == 404
    assert "not found" in resp_prism_fail.json()["detail"].lower()

    # Scenario B: User IS authorized in PRism for Repo A, but their GitHub token is expired
    # AND system token fallback is unavailable/disabled
    s["system_gh"].is_configured = False  # system fallback disabled

    with patch("app.api.repositories.GitHubService") as MockGHService:
        user_gh_instance = MagicMock()
        user_gh_instance.list_pull_requests = AsyncMock(
            side_effect=GitHubAuthError("Bad credentials - token expired")
        )
        MockGHService.return_value = user_gh_instance

        resp_gh_fail = client.get(
            f"/api/v1/repositories/{s['repo_a_id']}/pulls",
            headers=s["headers_a"],
        )

    # MUST be 401 Unauthorized (GitHub authentication failure)
    assert resp_gh_fail.status_code == 401
    assert "bad credentials" in resp_gh_fail.json()["detail"].lower()


# ==============================================================================
# Security Invariant 11: connect_repository NEVER uses system token to discover repos
# ==============================================================================
@pytest.mark.asyncio
async def test_11_connect_repository_never_uses_system_token_to_discover_repos(security_context):
    s = security_context
    client = s["client"]

    # User attempts to connect a private repository using an expired/unauthorized OAuth token
    with patch("app.api.repositories.GitHubService") as MockGHService:
        user_gh_instance = MagicMock()
        user_gh_instance.get_repository = AsyncMock(
            side_effect=GitHubAuthError("Bad credentials")
        )
        MockGHService.return_value = user_gh_instance

        resp = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "secret-corp/private-asset"},
            headers=s["headers_a"],
        )

    # Must be rejected with 401, NOT discovered via system token
    assert resp.status_code == 401
    assert s["system_gh"].get_repository.call_count == 0
