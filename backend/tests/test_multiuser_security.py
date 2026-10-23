"""
Multi-User Security & Repository Ownership Isolation Tests.

Verifies strict authorization enforcement across repositories, pull requests,
reviews, and engineering memory:
- User A cannot access, read, list, modify, review, or delete User B's repositories.
- User B cannot access, read, list, modify, review, or delete User A's repositories.
- Direct API requests are validated with separate authenticated sessions.
"""
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.auth import UserSessionProfile
from app.schemas.github import GitHubPR, GitHubPRListResponse, GitHubRepo
from app.services.auth import get_auth_service
from app.services.supabase import get_supabase_service

client = TestClient(app)


@pytest.fixture
async def setup_two_users():
    """Sets up two authenticated users (User A and User B) with independent sessions."""
    auth_svc = get_auth_service()

    user_a = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.1001")),
        github_user_id=1001,
        login="alice",
        name="Alice Developer",
        avatar_url="https://github.com/alice.png",
    )
    user_b = UserSessionProfile(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "github.user.2002")),
        github_user_id=2002,
        login="bob",
        name="Bob Architect",
        avatar_url="https://github.com/bob.png",
    )

    token_a = await auth_svc.create_session(user=user_a, github_access_token="gho_alice_token")
    token_b = await auth_svc.create_session(user=user_b, github_access_token="gho_bob_token")

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    return {
        "user_a": user_a,
        "user_b": user_b,
        "token_a": token_a,
        "token_b": token_b,
        "headers_a": headers_a,
        "headers_b": headers_b,
    }


@pytest.mark.asyncio
async def test_multiuser_repository_and_memory_isolation(setup_two_users):
    """Verifies that User A and User B cannot access each other's repositories, PRs, reviews, or memories."""
    context = setup_two_users
    headers_a = context["headers_a"]
    headers_b = context["headers_b"]
    user_a = context["user_a"]
    user_b = context["user_b"]
    supabase = get_supabase_service()

    # 1. Connect Repo A for User A
    repo_a = await supabase.connect_repository(
        github_repo_id=11111,
        full_name="alice-org/repo-a",
        default_branch="main",
        user_id=user_a.id,
    )
    repo_a_id = repo_a["id"]

    # 2. Connect Repo B for User B
    repo_b = await supabase.connect_repository(
        github_repo_id=22222,
        full_name="bob-org/repo-b",
        default_branch="main",
        user_id=user_b.id,
    )
    repo_b_id = repo_b["id"]

    # Add PRs and reviews
    pr_a = await supabase.upsert_pull_request(
        repository_id=repo_a_id,
        pr_number=1,
        title="Alice Feature 1",
        author="alice",
        head_sha="sha_a1",
        base_branch="main",
    )
    pr_b = await supabase.upsert_pull_request(
        repository_id=repo_b_id,
        pr_number=2,
        title="Bob Feature 2",
        author="bob",
        head_sha="sha_b2",
        base_branch="main",
    )

    await supabase.create_review(
        pull_request_id=pr_a["id"],
        commit_sha="sha_a1",
        summary="Looks good",
        drift_detected=False,
    )
    await supabase.create_review(
        pull_request_id=pr_b["id"],
        commit_sha="sha_b2",
        summary="Drift detected",
        drift_detected=True,
    )

    mem_a = await supabase.create_engineering_memory(
        repository_id=repo_a_id,
        claim="Alice memory rule",
        type="architecture_decision",
        rationale="Alice rationale",
        scope="Frontend",
    )
    mem_b = await supabase.create_engineering_memory(
        repository_id=repo_b_id,
        claim="Bob memory rule",
        type="rejected_pattern",
        rationale="Bob rationale",
        scope="Backend",
    )

    # --------------------------------------------------------------------------
    # Test 1: User A lists repositories -> sees only Repo A, NOT Repo B
    # --------------------------------------------------------------------------
    res_a_repos = client.get("/api/v1/repositories", headers=headers_a)
    assert res_a_repos.status_code == 200
    repo_ids_for_a = [r["id"] for r in res_a_repos.json()]
    assert repo_a_id in repo_ids_for_a
    assert repo_b_id not in repo_ids_for_a

    # User B lists repositories -> sees only Repo B, NOT Repo A
    res_b_repos = client.get("/api/v1/repositories", headers=headers_b)
    assert res_b_repos.status_code == 200
    repo_ids_for_b = [r["id"] for r in res_b_repos.json()]
    assert repo_b_id in repo_ids_for_b
    assert repo_a_id not in repo_ids_for_b

    # --------------------------------------------------------------------------
    # Test 2: User A can read Repo A, but CANNOT read Repo B
    # --------------------------------------------------------------------------
    res_a_get_a = client.get(f"/api/v1/repositories/{repo_a_id}", headers=headers_a)
    assert res_a_get_a.status_code == 200
    assert res_a_get_a.json()["id"] == repo_a_id

    res_a_get_b = client.get(f"/api/v1/repositories/{repo_b_id}", headers=headers_a)
    assert res_a_get_b.status_code == 404

    # User B can read Repo B, but CANNOT read Repo A
    res_b_get_b = client.get(f"/api/v1/repositories/{repo_b_id}", headers=headers_b)
    assert res_b_get_b.status_code == 200
    assert res_b_get_b.json()["id"] == repo_b_id

    res_b_get_a = client.get(f"/api/v1/repositories/{repo_a_id}", headers=headers_b)
    assert res_b_get_a.status_code == 404

    # --------------------------------------------------------------------------
    # Test 3: User A CANNOT access Repo B pulls
    # --------------------------------------------------------------------------
    res_a_pulls_b = client.get(f"/api/v1/repositories/{repo_b_id}/pulls", headers=headers_a)
    assert res_a_pulls_b.status_code == 404

    res_b_pulls_a = client.get(f"/api/v1/repositories/{repo_a_id}/pulls", headers=headers_b)
    assert res_b_pulls_a.status_code == 404

    # --------------------------------------------------------------------------
    # Test 4: User A CANNOT read Repo B reviews
    # --------------------------------------------------------------------------
    res_a_review_b = client.get(f"/api/v1/reviews/{repo_b_id}/2", headers=headers_a)
    assert res_a_review_b.status_code == 404

    res_a_recent_scoped_b = client.get(f"/api/v1/reviews/recent?repo_id={repo_b_id}", headers=headers_a)
    assert res_a_recent_scoped_b.status_code == 404

    # User A recent reviews without repo_id returns only User A reviews
    res_a_recent = client.get("/api/v1/reviews/recent", headers=headers_a)
    assert res_a_recent.status_code == 200
    reviews_for_a = res_a_recent.json()
    assert all(r.get("repository_id") == repo_a_id for r in reviews_for_a)

    # User B CANNOT read Repo A reviews
    res_b_review_a = client.get(f"/api/v1/reviews/{repo_a_id}/1", headers=headers_b)
    assert res_b_review_a.status_code == 404

    # --------------------------------------------------------------------------
    # Test 5: User A CANNOT access Repo B memories
    # --------------------------------------------------------------------------
    res_a_mem_list_b = client.get(f"/api/v1/repositories/{repo_b_id}/memory", headers=headers_a)
    assert res_a_mem_list_b.status_code == 404

    res_a_mem_search_b = client.get(f"/api/v1/repositories/{repo_b_id}/memory/search?q=rule", headers=headers_a)
    assert res_a_mem_search_b.status_code == 404

    res_a_mem_get_b = client.get(f"/api/v1/repositories/{repo_b_id}/memory/{mem_b['id']}", headers=headers_a)
    assert res_a_mem_get_b.status_code == 404

    res_a_mem_del_b = client.delete(f"/api/v1/repositories/{repo_b_id}/memory/{mem_b['id']}", headers=headers_a)
    assert res_a_mem_del_b.status_code == 404

    # User A CANNOT record memory into Repo B
    res_a_mem_post_b = client.post(
        f"/api/v1/repositories/{repo_b_id}/memory",
        headers=headers_a,
        json={"claim": "Malicious rule", "type": "architecture_decision"},
    )
    assert res_a_mem_post_b.status_code == 404

    # User A CANNOT recall Repo B memory
    res_a_recall_b = client.get(f"/api/v1/memory/recall?repo_id={repo_b_id}", headers=headers_a)
    assert res_a_recall_b.status_code == 404

    # User B CANNOT access Repo A memory
    res_b_mem_list_a = client.get(f"/api/v1/repositories/{repo_a_id}/memory", headers=headers_b)
    assert res_b_mem_list_a.status_code == 404

    # --------------------------------------------------------------------------
    # Test 6: User A CANNOT delete Repo B
    # --------------------------------------------------------------------------
    res_a_delete_b = client.delete(f"/api/v1/repositories/{repo_b_id}", headers=headers_a)
    assert res_a_delete_b.status_code == 404

    # Verify Repo B still exists for User B
    res_b_check_b = client.get(f"/api/v1/repositories/{repo_b_id}", headers=headers_b)
    assert res_b_check_b.status_code == 200

    # User B can delete Repo B
    res_b_delete_b = client.delete(f"/api/v1/repositories/{repo_b_id}", headers=headers_b)
    assert res_b_delete_b.status_code == 204
