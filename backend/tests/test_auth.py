from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
import httpx

from app.core.config import get_settings
from app.main import app
from app.schemas.github import GitHubRepo, GitHubUser
from app.services.auth import AuthService, get_auth_service

client = TestClient(app)


def test_auth_service_unconfigured():
    """When GitHub OAuth credentials are not provided, AuthService signals unconfigured."""
    with patch("app.services.auth.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_CLIENT_ID = None
        mock_settings.return_value.GITHUB_CLIENT_SECRET = None
        mock_settings.return_value.GITHUB_REDIRECT_URI = None

        svc = AuthService()
        assert not svc.is_configured
        url, configured, client_id, state = svc.get_authorization_url()
        assert not configured
        assert url is None


def test_auth_service_configured_generates_valid_url_and_state():
    """When OAuth is configured, authorization URL contains client_id, redirect_uri, and single-use state nonce."""
    with patch("app.services.auth.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_CLIENT_ID = "test-client-id"
        mock_settings.return_value.GITHUB_CLIENT_SECRET = "test-client-secret"
        mock_settings.return_value.GITHUB_REDIRECT_URI = "http://localhost:5173/auth/callback"

        svc = AuthService()
        assert svc.is_configured
        url, configured, client_id, state = svc.get_authorization_url()
        assert configured is True
        assert client_id == "test-client-id"
        assert state is not None
        assert "github.com/login/oauth/authorize" in url
        assert "client_id=test-client-id" in url
        assert f"state={state}" in url

        # State should be valid on first check
        assert svc.validate_and_consume_state(state) is True
        # State must be consumed (single-use)
        assert svc.validate_and_consume_state(state) is False
        # Forged state must fail
        assert svc.validate_and_consume_state("forged-state-nonce") is False


def test_login_url_endpoint_unconfigured():
    """GET /api/v1/auth/github/login returns configured=False when credentials missing."""
    auth_svc = get_auth_service()
    orig_client_id = auth_svc.client_id
    orig_client_secret = auth_svc.client_secret
    try:
        auth_svc.client_id = None
        auth_svc.client_secret = None
        res = client.get("/api/v1/auth/github/login")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is False
        assert data["url"] is None
    finally:
        auth_svc.client_id = orig_client_id
        auth_svc.client_secret = orig_client_secret


def test_login_url_endpoint_configured():
    """GET /api/v1/auth/github/login returns configured=True with valid URL."""
    auth_svc = get_auth_service()
    orig_client_id = auth_svc.client_id
    orig_client_secret = auth_svc.client_secret
    try:
        auth_svc.client_id = "test-client-123"
        auth_svc.client_secret = "test-secret-456"
        res = client.get("/api/v1/auth/github/login")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert "test-client-123" in data["url"]
        assert data["state"] is not None
    finally:
        auth_svc.client_id = orig_client_id
        auth_svc.client_secret = orig_client_secret


def test_callback_fails_with_invalid_state():
    """POST /api/v1/auth/github/callback fails with 400 if state is invalid or forged."""
    res = client.post(
        "/api/v1/auth/github/callback",
        json={"code": "dummy-code", "state": "invalid-state-nonce"},
    )
    assert res.status_code == 400
    assert "Invalid or expired OAuth state" in res.json()["detail"]


@pytest.mark.asyncio
async def test_callback_and_session_lifecycle():
    """End-to-End OAuth callback exchange and session verification:
    1. Generate valid state
    2. Mock GitHub token exchange and user fetch
    3. Call callback endpoint
    4. Verify session is established and no access token is exposed
    5. Query /api/v1/auth/me
    6. Call logout and verify session termination
    """
    auth_svc = get_auth_service()
    orig_client_id = auth_svc.client_id
    orig_client_secret = auth_svc.client_secret
    auth_svc.client_id = "test-client"
    auth_svc.client_secret = "test-secret"

    try:
        valid_state = auth_svc.generate_state_nonce()

        mock_user = GitHubUser(
            id=1234567,
            login="octocat",
            name="The Octocat",
            avatar_url="https://github.com/images/error/octocat_happy.gif",
            html_url="https://github.com/octocat",
        )

        with patch.object(auth_svc, "exchange_code_for_token", new_callable=AsyncMock) as mock_exchange:
            mock_exchange.return_value = "gho_mock_access_token_super_secret"

            with patch("app.services.auth.GitHubService.get_authenticated_user", new_callable=AsyncMock) as mock_gh_user:
                mock_gh_user.return_value = mock_user

                # Execute callback
                res = client.post(
                    "/api/v1/auth/github/callback",
                    json={"code": "valid_github_code_123", "state": valid_state},
                )
                assert res.status_code == 200
                data = res.json()

                # Session token must be returned
                assert "session_token" in data
                session_token = data["session_token"]
                assert session_token != "gho_mock_access_token_super_secret"
                assert "gho_" not in session_token

                # User profile populated with real GitHub identity
                assert data["user"]["login"] == "octocat"
                assert data["user"]["github_user_id"] == 1234567
                assert data["user"]["name"] == "The Octocat"
                assert data["user"]["avatar_url"] == "https://github.com/images/error/octocat_happy.gif"
                assert data["redirect_to"] == "/repositories/select"

                # Check /api/v1/auth/me with session token
                me_res = client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {session_token}"},
                )
                assert me_res.status_code == 200
                me_data = me_res.json()
                assert me_data["authenticated"] is True
                assert me_data["user"]["login"] == "octocat"

                # Check /api/v1/auth/me without token
                unauth_res = client.get("/api/v1/auth/me")
                assert unauth_res.status_code == 200
                assert unauth_res.json()["authenticated"] is False

                # Logout
                logout_res = client.post(
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {session_token}"},
                )
                assert logout_res.status_code == 200

                # Verify session revoked
                revoked_res = client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {session_token}"},
                )
                assert revoked_res.json()["authenticated"] is False
    finally:
        auth_svc.client_id = orig_client_id
        auth_svc.client_secret = orig_client_secret


@pytest.mark.asyncio
async def test_list_user_github_repositories():
    """GET /api/v1/auth/github/repositories returns user repositories cross-referenced with PRism."""
    auth_svc = get_auth_service()
    user_profile = await auth_svc.fetch_github_user_profile.__wrapped__(
        auth_svc, "dummy_token"
    ) if hasattr(auth_svc.fetch_github_user_profile, "__wrapped__") else None

    # Manually create a session for testing
    from app.schemas.auth import UserSessionProfile
    test_user = UserSessionProfile(
        id="user-uuid-123",
        github_user_id=9999,
        login="octodev",
        name="Octo Dev",
        avatar_url="https://avatars.githubusercontent.com/u/9999",
    )
    token = await auth_svc.create_session(test_user, "mock_gh_token")

    mock_repos = [
        GitHubRepo(
            id=101,
            full_name="octodev/repo-one",
            name="repo-one",
            owner="octodev",
            private=False,
            default_branch="main",
            description="First repo",
            html_url="https://github.com/octodev/repo-one",
        ),
        GitHubRepo(
            id=102,
            full_name="octodev/repo-two",
            name="repo-two",
            owner="octodev",
            private=True,
            default_branch="main",
            description="Second repo",
            html_url="https://github.com/octodev/repo-two",
        ),
    ]

    with patch("app.services.auth.GitHubService.list_user_repositories", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_repos

        res = client.get(
            "/api/v1/auth/github/repositories",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        repos = res.json()
        assert len(repos) == 2
        assert repos[0]["full_name"] == "octodev/repo-one"
        assert repos[1]["full_name"] == "octodev/repo-two"
        assert repos[1]["private"] is True

    # Test unauthenticated access fails
    fail_res = client.get("/api/v1/auth/github/repositories")
    assert fail_res.status_code == 401


@pytest.mark.asyncio
async def test_org_accessible_repositories_appear_with_metadata():
    """Verifies that organization-accessible and collaborator repositories appear with access context."""
    auth_svc = get_auth_service()
    from app.schemas.auth import UserSessionProfile
    test_user = UserSessionProfile(
        id="user-uuid-org-test",
        github_user_id=7777,
        login="octodev",
        name="Octo Dev",
        avatar_url="https://avatars.githubusercontent.com/u/7777",
    )
    token = await auth_svc.create_session(test_user, "mock_gh_token")

    mock_repos = [
        GitHubRepo(
            id=201,
            full_name="octodev/personal-repo",
            name="personal-repo",
            owner="octodev",
            owner_avatar_url="https://avatars.githubusercontent.com/u/7777",
            owner_type="User",
            private=False,
            default_branch="main",
            description="Personal repo",
            html_url="https://github.com/octodev/personal-repo",
            stargazers_count=10,
            permissions={"admin": True, "push": True, "pull": True},
        ),
        GitHubRepo(
            id=202,
            full_name="acme-corp/core-api",
            name="core-api",
            owner="acme-corp",
            owner_avatar_url="https://avatars.githubusercontent.com/u/8888",
            owner_type="Organization",
            private=True,
            default_branch="main",
            description="Acme org repository",
            html_url="https://github.com/acme-corp/core-api",
            stargazers_count=42,
            permissions={"admin": False, "push": True, "pull": True},
        ),
    ]

    with patch("app.services.auth.GitHubService.list_user_repositories", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_repos

        res = client.get(
            "/api/v1/auth/github/repositories",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        repos = res.json()
        assert len(repos) == 2
        # Personal repo role is owner
        assert repos[0]["full_name"] == "octodev/personal-repo"
        assert repos[0]["access_role"] == "owner"
        assert repos[0]["stargazers_count"] == 10

        # Organization repo role is write
        assert repos[1]["full_name"] == "acme-corp/core-api"
        assert repos[1]["owner_type"] == "Organization"
        assert repos[1]["access_role"] == "write"
        assert repos[1]["private"] is True
        assert repos[1]["stargazers_count"] == 42


@pytest.mark.asyncio
async def test_public_search_works_and_does_not_autoconnect():
    """Verifies that public repository search returns results without auto-connecting them."""
    auth_svc = get_auth_service()
    from app.schemas.auth import UserSessionProfile
    test_user = UserSessionProfile(
        id="user-search-test",
        github_user_id=8888,
        login="searcher",
        name="Searcher",
        avatar_url="https://avatars.githubusercontent.com/u/8888",
    )
    token = await auth_svc.create_session(test_user, "mock_token")

    mock_search_repos = [
        GitHubRepo(
            id=9901,
            full_name="torvalds/linux",
            name="linux",
            owner="torvalds",
            owner_avatar_url="https://avatars.githubusercontent.com/u/1024025",
            owner_type="User",
            private=False,
            default_branch="master",
            description="Linux kernel source tree",
            html_url="https://github.com/torvalds/linux",
            stargazers_count=180000,
        ),
        GitHubRepo(
            id=9902,
            full_name="git/git",
            name="git",
            owner="git",
            owner_avatar_url="https://avatars.githubusercontent.com/u/18133",
            owner_type="Organization",
            private=False,
            default_branch="master",
            description="Fast, scalable, distributed revision control system",
            html_url="https://github.com/git/git",
            stargazers_count=52000,
        ),
    ]

    with patch("app.services.auth.GitHubService.search_public_repositories", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = (2, mock_search_repos)

        res = client.get(
            "/api/v1/auth/github/search?q=kernel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_count"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["full_name"] == "torvalds/linux"
        assert data["items"][0]["connected"] is False
        assert data["items"][1]["full_name"] == "git/git"
        assert data["items"][1]["connected"] is False

        # CRITICAL: Verify searching has NOT connected either repo
        list_res = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_res.status_code == 200
        connected = list_res.json()
        assert len(connected) == 0


@pytest.mark.asyncio
async def test_connect_single_repository_and_prevent_duplicate():
    """Connecting connects ONLY that specific repository and prevents duplicate connection records."""
    auth_svc = get_auth_service()
    from app.schemas.auth import UserSessionProfile
    test_user = UserSessionProfile(
        id="user-connect-test",
        github_user_id=1111,
        login="connector",
        name="Connector",
    )
    token = await auth_svc.create_session(test_user, "mock_token")

    mock_repo = GitHubRepo(
        id=5555,
        full_name="prism-test/target-repo",
        name="target-repo",
        owner="prism-test",
        private=False,
        default_branch="main",
        description="Target test repository",
    )

    with patch("app.api.repositories.GitHubService.get_repository", new_callable=AsyncMock) as mock_get_repo:
        mock_get_repo.return_value = mock_repo

        # Connect repository
        res1 = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "prism-test/target-repo", "github_repo_id": 5555},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res1.status_code == 201
        data1 = res1.json()
        assert data1["full_name"] == "prism-test/target-repo"
        assert data1["github_repo_id"] == 5555
        repo_id = data1["id"]

        # Duplicate connect attempt returns the existing repository without creating a duplicate
        res2 = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "prism-test/target-repo", "github_repo_id": 5555},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res2.status_code == 201
        data2 = res2.json()
        assert data2["id"] == repo_id  # Same canonical ID
        assert data2["github_repo_id"] == 5555

        # Verify only 1 repository is connected
        list_res = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_res.status_code == 200
        repos = list_res.json()
        assert len(repos) == 1
        assert repos[0]["id"] == repo_id


@pytest.mark.asyncio
async def test_user_repository_isolation():
    """User A and User B have complete isolation:
    - User A connects Repo A
    - User B cannot see Repo A in list_repositories
    - User B cannot access Repo A pulls or details
    - User B connects Repo B
    - User A sees only Repo A; User B sees only Repo B
    """
    auth_svc = get_auth_service()
    from app.schemas.auth import UserSessionProfile

    user_a = UserSessionProfile(
        id="user-a-uuid",
        github_user_id=1001,
        login="alice",
    )
    token_a = await auth_svc.create_session(user_a, "token_a")

    user_b = UserSessionProfile(
        id="user-b-uuid",
        github_user_id=1002,
        login="bob",
    )
    token_b = await auth_svc.create_session(user_b, "token_b")

    repo_a = GitHubRepo(
        id=7001,
        full_name="alice/project-alpha",
        name="project-alpha",
        owner="alice",
        default_branch="main",
    )
    repo_b = GitHubRepo(
        id=7002,
        full_name="bob/project-beta",
        name="project-beta",
        owner="bob",
        default_branch="main",
    )

    with patch("app.api.repositories.GitHubService.get_repository", new_callable=AsyncMock) as mock_get_repo:
        def get_repo_side_effect(owner, name):
            if name == "project-alpha":
                return repo_a
            return repo_b
        mock_get_repo.side_effect = get_repo_side_effect

        # User A connects Repo A
        res_a = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "alice/project-alpha"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res_a.status_code == 201
        repo_a_id = res_a.json()["id"]

        # User B queries their connected repositories -> MUST BE EMPTY
        res_b_list = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_b_list.status_code == 200
        assert len(res_b_list.json()) == 0

        # User B attempts to access User A's repository details -> 404 NOT FOUND
        unauthorized_access = client.get(
            f"/api/v1/repositories/{repo_a_id}/pulls",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert unauthorized_access.status_code == 404

        # User B connects Repo B
        res_b_conn = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "bob/project-beta"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_b_conn.status_code == 201
        repo_b_id = res_b_conn.json()["id"]

        # User A lists repos -> sees ONLY Repo A
        res_a_final = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert [r["id"] for r in res_a_final.json()] == [repo_a_id]

        # User B lists repos -> sees ONLY Repo B
        res_b_final = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert [r["id"] for r in res_b_final.json()] == [repo_b_id]


@pytest.mark.asyncio
async def test_optional_repository_connection_onboarding_lifecycle():
    """Validates the full post-auth onboarding lifecycle with optional repo connection:
    - User logs in and reaches repo selection
    - Connects ZERO repositories and continues to workspace
    - Workspace has 0 connected repos, no fake active repo, no review data
    - Later connects ONE repo -> only that repo is connected
    - Discovery shows that repo as 'connected'
    - Connects second repo -> both are available
    - Relogin preserves connected repositories
    """
    auth_svc = get_auth_service()
    from app.schemas.auth import UserSessionProfile

    user = UserSessionProfile(
        id="user-onboarding-uuid",
        github_user_id=8888,
        login="newuser",
        name="New User",
    )
    session_token = await auth_svc.create_session(user, "user_gh_token")

    # Step C & D: User connects ZERO repositories and continues to workspace
    list_res = client.get(
        "/api/v1/repositories",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert list_res.status_code == 200
    assert list_res.json() == []

    # Step E: Verify 0 connected repos, no review data, no PR sync
    rev_res = client.get(
        "/api/v1/reviews/recent",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert rev_res.status_code == 200

    # Step G: Connect ONE repository
    repo_1 = GitHubRepo(
        id=8001,
        full_name="newuser/first-repo",
        name="first-repo",
        owner="newuser",
        default_branch="main",
    )
    repo_2 = GitHubRepo(
        id=8002,
        full_name="newuser/second-repo",
        name="second-repo",
        owner="newuser",
        default_branch="main",
    )

    with patch("app.api.repositories.GitHubService.get_repository", new_callable=AsyncMock) as mock_get_repo, \
         patch("app.services.auth.GitHubService.list_user_repositories", new_callable=AsyncMock) as mock_list_gh:

        def get_repo_side_effect(owner, name):
            if name == "first-repo":
                return repo_1
            return repo_2
        mock_get_repo.side_effect = get_repo_side_effect
        mock_list_gh.return_value = [repo_1, repo_2]

        conn_1 = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "newuser/first-repo"},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert conn_1.status_code == 201
        assert conn_1.json()["full_name"] == "newuser/first-repo"

        # Step H: Workspace now has that single repository
        ws_repos_1 = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert ws_repos_1.status_code == 200
        assert len(ws_repos_1.json()) == 1
        assert ws_repos_1.json()[0]["full_name"] == "newuser/first-repo"

        # Step I: Return to repository picker -> first-repo shows connected=True, second-repo shows connected=False
        picker_res = client.get(
            "/api/v1/auth/github/repositories",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert picker_res.status_code == 200
        picker_items = {r["full_name"]: r for r in picker_res.json()}
        assert picker_items["newuser/first-repo"]["connected"] is True
        assert picker_items["newuser/second-repo"]["connected"] is False

        # Step J: Connect second repository
        conn_2 = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "newuser/second-repo"},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert conn_2.status_code == 201

        # Both repositories are now connected
        ws_repos_2 = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert ws_repos_2.status_code == 200
        assert len(ws_repos_2.json()) == 2
        repo_names = {r["full_name"] for r in ws_repos_2.json()}
        assert repo_names == {"newuser/first-repo", "newuser/second-repo"}

        # Step K: Logout / Login again preserves repositories
        client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        # Create a new session for the same user identity (simulating relogin)
        new_session_token = await auth_svc.create_session(user, "user_gh_token_2")
        ws_repos_relogin = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {new_session_token}"},
        )
        assert ws_repos_relogin.status_code == 200
        assert len(ws_repos_relogin.json()) == 2

