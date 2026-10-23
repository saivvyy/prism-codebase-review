import uuid
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import get_optional_current_user
from app.schemas.auth import UserSessionProfile
from app.services.supabase import SupabaseService, SupabaseStateError, get_supabase_service

client = TestClient(app)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000000"
MOCK_USER = UserSessionProfile(
    id=MOCK_USER_ID,
    github_user_id=12345,
    login="octocat",
    name="Octocat Developer",
    avatar_url="https://github.com/octocat.png",
)

USER_B_ID = "11111111-1111-1111-1111-111111111111"
MOCK_USER_B = UserSessionProfile(
    id=USER_B_ID,
    github_user_id=67890,
    login="otheruser",
    name="Other User",
    avatar_url="https://github.com/otheruser.png",
)


@pytest.fixture
def mock_supabase():
    svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    return svc


@pytest.fixture
def auth_user():
    app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER
    yield MOCK_USER
    app.dependency_overrides.pop(get_optional_current_user, None)


def test_get_repositories_empty(mock_supabase, auth_user):
    """Scenario A: GET repositories when 0 connected repos returns [] with 200."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        response = client.get("/api/v1/repositories")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_repository_204_empty_body(mock_supabase, auth_user):
    """Requirement 1: A connected repository can be deleted (returns 204 with empty body)."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 12345,
            "full_name": "test-org/test-repo",
            "default_branch": "main",
            "webhook_active": False,
            "created_at": "2026-09-29T00:00:00Z",
            "updated_at": "2026-09-29T00:00:00Z",
        }

        # Issue DELETE
        response = client.delete(f"/api/v1/repositories/{repo_id}")
        assert response.status_code == 204
        assert response.text == ""  # Zero-length body
        assert "application/json" not in response.headers.get("content-type", "")

        # Verify it was removed
        assert repo_id not in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_repository_authoritative_listing_and_refresh(mock_supabase, auth_user):
    """Requirements 2 & 3: The repository no longer appears in authoritative listing, and refresh stays deleted."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 99999,
            "full_name": "test-org/to-delete",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Confirm it appears before deletion
        pre_res = client.get("/api/v1/repositories")
        assert pre_res.status_code == 200
        assert any(r["id"] == repo_id for r in pre_res.json())

        # Delete the repository
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # Listing immediately after deletion
        post_res = client.get("/api/v1/repositories")
        assert post_res.status_code == 200
        assert not any(r["id"] == repo_id for r in post_res.json())

        # Simulated page refresh (subsequent independent GET request)
        refresh_res = client.get("/api/v1/repositories")
        assert refresh_res.status_code == 200
        assert not any(r["id"] == repo_id for r in refresh_res.json())
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_database_failure_returns_error(mock_supabase, auth_user):
    """Requirement 4: Database failures produce an error rather than false success."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 55555,
            "full_name": "test-org/db-fail-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Mock delete_repository to raise an exception simulating database failure
        with patch.object(mock_supabase, "delete_repository", side_effect=SupabaseStateError("Connection timeout to PostgreSQL")):
            response = client.delete(f"/api/v1/repositories/{repo_id}")
            assert response.status_code == 500
            err_data = response.json()
            assert "detail" in err_data
            assert "Database failure while disconnecting repository" in err_data["detail"]

        # Ensure repo was NOT removed from database
        assert repo_id in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_unauthenticated_user_returns_401(mock_supabase):
    """Requirement 5: Unauthenticated users cannot delete repositories (HTTP 401)."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    # Ensure no authenticated user is in dependency overrides
    app.dependency_overrides.pop(get_optional_current_user, None)
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 44444,
            "full_name": "test-org/secure-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Unauthenticated request without session or token
        response = client.delete(f"/api/v1/repositories/{repo_id}")
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

        # Repo remains untouched
        assert repo_id in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_other_user_repository_returns_404(mock_supabase):
    """Requirement 6: One user cannot delete another user's repository."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        # User B owns this repo
        repo_b_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_b_id] = {
            "id": repo_b_id,
            "user_id": USER_B_ID,
            "github_repo_id": 88888,
            "full_name": "bob-org/bobs-private-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # User A attempts to delete User B's repository
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER
        res_a_del = client.delete(f"/api/v1/repositories/{repo_b_id}")
        assert res_a_del.status_code == 404
        assert f"Repository '{repo_b_id}' not found." in res_a_del.json()["detail"]

        # User B's repo remains intact
        assert repo_b_id in mock_supabase._local_repos

        # Now User B performs deletion, which succeeds
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER_B
        res_b_del = client.delete(f"/api/v1/repositories/{repo_b_id}")
        assert res_b_del.status_code == 204
        assert repo_b_id not in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_optional_current_user, None)


def test_delete_repository_not_found(mock_supabase, auth_user):
    """DELETE non-existent repo returns 404 with JSON detail."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        non_existent_id = str(uuid.uuid4())
        response = client.delete(f"/api/v1/repositories/{non_existent_id}")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert f"Repository '{non_existent_id}' not found." in data["detail"]
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_repository_invalid_uuid(mock_supabase):
    """DELETE with malformed UUID returns 400."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        response = client.delete("/api/v1/repositories/not-a-valid-uuid")
        assert response.status_code == 400
        assert "Invalid repository ID format" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_repo_a_leaves_repo_b(mock_supabase, auth_user):
    """Deleting Repo A leaves Repo B completely intact and consistent."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_a_id = str(uuid.uuid4())
        repo_b_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_a_id] = {
            "id": repo_a_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 11111,
            "full_name": "org/repo-a",
            "default_branch": "main",
            "webhook_active": False,
        }
        mock_supabase._local_repos[repo_b_id] = {
            "id": repo_b_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 22222,
            "full_name": "org/repo-b",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Delete Repo A
        del_res = client.delete(f"/api/v1/repositories/{repo_a_id}")
        assert del_res.status_code == 204

        # Verify Repo A is gone and Repo B remains
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        repos = list_res.json()
        assert len(repos) == 1
        assert repos[0]["id"] == repo_b_id
        assert repos[0]["full_name"] == "org/repo-b"
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_final_repo_yields_empty_state(mock_supabase, auth_user):
    """Deleting the final repository returns DB to [] empty list."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 33333,
            "full_name": "org/final-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Delete the only repo
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # List is now empty
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        assert list_res.json() == []
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_shared_workspace_repo_allowed_for_workspace_member(mock_supabase, auth_user):
    """Shared default workspace repository can be deleted by workspace member."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        default_workspace_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace"))
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": default_workspace_uuid,
            "github_repo_id": 77777,
            "full_name": "org/shared-workspace-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # Member sees it in listing
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        assert any(r["id"] == repo_id for r in list_res.json())

        # Member deletes it
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # Disappears from member listing
        post_res = client.get("/api/v1/repositories")
        assert post_res.status_code == 200
        assert not any(r["id"] == repo_id for r in post_res.json())
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_cascades_dependent_data(mock_supabase, auth_user):
    """Requirement 9: Hard delete semantics with cascade cleans dependent PRs and memories."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 66666,
            "full_name": "org/cascade-repo",
            "default_branch": "main",
            "webhook_active": False,
        }
        # Prepopulate dependent PR and memory in local state
        pr_key = f"{repo_id}:101"
        mock_supabase._local_prs[pr_key] = {
            "id": str(uuid.uuid4()),
            "repository_id": repo_id,
            "pr_number": 101,
            "title": "Dependent PR",
        }
        mem_id = str(uuid.uuid4())
        mock_supabase._local_engineering_memories[mem_id] = {
            "id": mem_id,
            "repository_id": repo_id,
            "claim": "Architecture rule",
        }

        # Issue DELETE
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # Verify cascading removal
        assert repo_id not in mock_supabase._local_repos
        assert pr_key not in mock_supabase._local_prs
        assert mem_id not in mock_supabase._local_engineering_memories
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
