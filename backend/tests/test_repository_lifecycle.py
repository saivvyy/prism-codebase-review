import uuid
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.supabase import SupabaseService, get_supabase_service

client = TestClient(app)


@pytest.fixture
def mock_supabase():
    svc = SupabaseService(supabase_url=None, supabase_key=None, persist_local=False)
    return svc


def test_get_repositories_empty(mock_supabase):
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


def test_delete_repository_204_empty_body(mock_supabase):
    """Scenario D: DELETE returns 204 with completely empty body."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        # Prepopulate one repo
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": "00000000-0000-0000-0000-000000000000",
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


def test_delete_repository_not_found(mock_supabase):
    """Scenario F: DELETE non-existent repo returns 404 with JSON detail."""
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


def test_delete_repo_a_leaves_repo_b(mock_supabase):
    """Scenario G: Deleting Repo A leaves Repo B completely intact."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
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


def test_delete_final_repo_yields_empty_state(mock_supabase):
    """Scenario H: Deleting the final repository returns DB to [] empty list."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": "00000000-0000-0000-0000-000000000000",
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
