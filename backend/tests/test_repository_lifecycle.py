import uuid
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import get_optional_current_user
from app.schemas.auth import UserSessionProfile
from app.schemas.github import GitHubRepo
from app.services.bootstrap import get_repo_bank_id
from app.services.github import GitHubService, get_github_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
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
def mock_memory():
    svc = HindsightMemoryService(base_url=None, api_key=None, default_bank_id="test_bank")
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


# ==============================================================================
# Requirement 1: Disconnecting a repository removes it from the connected-repositories list
# ==============================================================================

def test_disconnect_removes_from_connected_list(mock_supabase, auth_user):
    """Req 1: Disconnecting a repository removes it from the connected-repositories list."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 12345,
            "full_name": "test-org/test-repo",
            "default_branch": "main",
            "webhook_active": True,
            "is_connected": True,
            "status": "active",
            "created_at": "2026-09-29T00:00:00Z",
            "updated_at": "2026-09-29T00:00:00Z",
        }

        # Verify listed before disconnect
        pre_res = client.get("/api/v1/repositories")
        assert pre_res.status_code == 200
        assert any(r["id"] == repo_id for r in pre_res.json())

        # Issue DELETE (disconnect)
        response = client.delete(f"/api/v1/repositories/{repo_id}")
        assert response.status_code == 204
        assert response.text == ""

        # Verify no longer listed in connected repositories
        post_res = client.get("/api/v1/repositories")
        assert post_res.status_code == 200
        assert not any(r["id"] == repo_id for r in post_res.json())

        # Verify subsequent refreshes also show it removed
        refresh_res = client.get("/api/v1/repositories")
        assert refresh_res.status_code == 200
        assert not any(r["id"] == repo_id for r in refresh_res.json())

        # Durable record remains stored as disconnected
        assert repo_id in mock_supabase._local_repos
        assert mock_supabase._local_repos[repo_id]["is_connected"] is False
        assert mock_supabase._local_repos[repo_id]["status"] == "disconnected"
        assert mock_supabase._local_repos[repo_id]["webhook_active"] is False
        assert mock_supabase._local_repos[repo_id]["disconnected_at"] is not None
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


# ==============================================================================
# Requirement 2: Disconnecting does not delete canonical engineering-memory records
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_does_not_delete_canonical_engineering_memories(mock_supabase, auth_user):
    """Req 2: Disconnecting a repository MUST NOT delete canonical engineering-memory records."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 99001,
            "full_name": "acme/payment-gateway",
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }

        # Create canonical engineering memories associated with this repository
        mem1 = await mock_supabase.create_engineering_memory(
            repository_id=repo_id,
            statement="All external payment webhooks must verify HMAC signatures before processing",
            type="DOCUMENTED_DECISION",
            authority="ARCHITECTURE_RULE",
            rationale="Prevents spoofed transaction fraud",
            scope="services/payments",
            source_type="team_agreement",
            confidence="supported",
        )
        mem2 = await mock_supabase.create_engineering_memory(
            repository_id=repo_id,
            statement="Never log raw credit card numbers or CVV in application logs",
            type="REJECTED_PATTERN",
            authority="TEAM_CONVENTION",
            rationale="PCI-DSS compliance requirement",
            scope="services/payments",
            source_type="pr_review",
            confidence="supported",
        )

        assert mem1["id"] in mock_supabase._local_engineering_memories
        assert mem2["id"] in mock_supabase._local_engineering_memories

        # Disconnect repository
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # INVARIANT: Engineering memories must remain completely intact
        assert mem1["id"] in mock_supabase._local_engineering_memories
        assert mem2["id"] in mock_supabase._local_engineering_memories
        stored1 = mock_supabase._local_engineering_memories[mem1["id"]]
        stored2 = mock_supabase._local_engineering_memories[mem2["id"]]
        assert stored1["repository_id"] == repo_id
        assert "HMAC signatures" in stored1["claim"]
        assert stored2["repository_id"] == repo_id
        assert "credit card numbers" in stored2["claim"]
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


# ==============================================================================
# Requirement 3: Disconnecting does not delete Hindsight banks or memory records
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_does_not_delete_hindsight_banks_or_records(mock_supabase, mock_memory, auth_user):
    """Req 3: Disconnecting a repository MUST NOT delete or reset Hindsight banks or memory records."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    app.dependency_overrides[get_hindsight_service] = lambda: mock_memory
    try:
        repo_id = str(uuid.uuid4())
        full_name = "acme/auth-service"
        bank_id = get_repo_bank_id(repo_id, full_name)

        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 99002,
            "full_name": full_name,
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }

        # Retain semantic engineering memories in Hindsight
        mock_memory.retain(
            content="OAuth tokens must be refreshed asynchronously before 5-minute expiry threshold",
            bank_id=bank_id,
            context="Token expiration handling in auth service",
            tags=["architecture_decision"],
        )
        mock_memory.retain(
            content="Do not store plain-text session tokens in localStorage",
            bank_id=bank_id,
            context="Web security convention",
            tags=["rejected_pattern"],
        )

        # Verify memories exist in Hindsight bank
        recall_pre = mock_memory.recall(query="token expiry threshold", bank_id=bank_id)
        assert recall_pre.results_count >= 1

        # Disconnect repository
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # INVARIANT: Hindsight bank and memory records remain untouched
        recall_post = mock_memory.recall(query="token expiry threshold", bank_id=bank_id)
        assert recall_post.results_count >= 1
        assert any("OAuth tokens must be refreshed" in it.text for it in recall_post.items)

        recall_pattern = mock_memory.recall(query="localStorage session tokens", bank_id=bank_id)
        assert recall_pattern.results_count >= 1
        assert any("localStorage" in it.text for it in recall_pattern.items)
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_hindsight_service, None)


# ==============================================================================
# Requirement 4: Existing PRs, reviews, findings, and evidence remain available
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_preserves_prs_reviews_findings_and_evidence(mock_supabase, auth_user):
    """Req 4: Disconnecting preserves PRs, reviews, findings, and their evidence according to retention policy."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 99003,
            "full_name": "acme/billing",
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }

        # Prepopulate PR
        pr = await mock_supabase.upsert_pull_request(
            repository_id=repo_id,
            pr_number=42,
            title="Refactor invoice generation",
            author="alice",
            head_sha="a1b2c3d4",
            base_branch="main",
            status="open",
        )

        # Prepopulate Review
        rev = await mock_supabase.create_review(
            pull_request_id=pr["id"],
            commit_sha="a1b2c3d4",
            summary="Review with architectural drift finding",
            drift_detected=True,
            verdict="DRIFT",
            status="completed",
        )

        # Prepopulate Review Findings with evidence snapshot
        findings = await mock_supabase.create_review_findings(
            review_id=rev["id"],
            findings=[
                {
                    "file_path": "billing/invoice.py",
                    "line_number": 88,
                    "category": "architecture_drift",
                    "title": "Bypassed transaction lock",
                    "message": "Direct write without distributed lock violates billing consistency rule",
                    "historical_evidence_snapshot": {"rule_id": "rule-42", "source_pr": 12},
                    "provenance_status": "verified",
                    "severity": "error",
                }
            ],
        )

        # Issue Disconnect
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # INVARIANT: PR, Review, and Findings records remain intact in database
        pr_key = f"{repo_id}_42"
        assert pr_key in mock_supabase._local_prs
        assert mock_supabase._local_prs[pr_key]["title"] == "Refactor invoice generation"

        assert rev["id"] in mock_supabase._local_reviews
        assert mock_supabase._local_reviews[rev["id"]]["verdict"] == "DRIFT"

        assert rev["id"] in mock_supabase._local_findings
        stored_findings = mock_supabase._local_findings[rev["id"]]
        assert len(stored_findings) == 1
        assert stored_findings[0]["title"] == "Bypassed transaction lock"
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


# ==============================================================================
# Requirement 5: Reconnecting the same repository restores access to existing memory and review history
# ==============================================================================

@pytest.mark.asyncio
async def test_reconnect_restores_access_to_memory_and_review_history(mock_supabase, auth_user):
    """Req 5: Reconnecting the same repository restores access to its existing memory and review history."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase

    fake_gh_repo = GitHubRepo(
        id=99005,
        full_name="acme/identity-service",
        name="identity-service",
        owner="acme",
        default_branch="main",
    )
    mock_gh_service = AsyncMock(spec=GitHubService)
    mock_gh_service.get_repository.return_value = fake_gh_repo
    app.dependency_overrides[get_github_service] = lambda: mock_gh_service

    try:
        # Step 1: Connect repository
        connect_res = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "acme/identity-service", "github_repo_id": 99005},
        )
        assert connect_res.status_code == 201
        repo_data = connect_res.json()
        canonical_repo_id = repo_data["id"]

        # Step 2: Add canonical memory and PR review history
        mem = await mock_supabase.create_engineering_memory(
            repository_id=canonical_repo_id,
            statement="JWT signatures must use RS256 with key rotation",
            type="DOCUMENTED_DECISION",
            authority="ARCHITECTURE_RULE",
            scope="auth/jwt",
        )
        pr = await mock_supabase.upsert_pull_request(
            repository_id=canonical_repo_id,
            pr_number=10,
            title="Update token verification",
            author="bob",
            head_sha="beef01",
        )
        rev = await mock_supabase.create_review(
            pull_request_id=pr["id"],
            commit_sha="beef01",
            summary="Clean token verification review",
            verdict="CONFORMING",
            status="completed",
        )

        # Step 3: Disconnect repository
        disc_res = client.delete(f"/api/v1/repositories/{canonical_repo_id}")
        assert disc_res.status_code == 204

        # While disconnected, active repository query returns 404
        get_disc = client.get(f"/api/v1/repositories/{canonical_repo_id}")
        assert get_disc.status_code == 404

        # Step 4: Reconnect the same repository
        reconnect_res = client.post(
            "/api/v1/repositories/connect",
            json={"full_name": "acme/identity-service", "github_repo_id": 99005},
        )
        assert reconnect_res.status_code in (200, 201)
        reconnected_data = reconnect_res.json()

        # INVARIANT: Must restore the EXACT SAME canonical repository identity
        assert reconnected_data["id"] == canonical_repo_id
        assert reconnected_data["full_name"] == "acme/identity-service"

        # Step 5: Verify repository is active again
        active_res = client.get(f"/api/v1/repositories/{canonical_repo_id}")
        assert active_res.status_code == 200

        # Step 6: Verify memory is immediately accessible again
        mem_res = client.get(f"/api/v1/repositories/{canonical_repo_id}/canonical-memory")
        assert mem_res.status_code == 200
        mem_data = mem_res.json()
        assert mem_data["total_count"] >= 1
        assert any("RS256" in m["statement"] for m in mem_data["memories"])

        # Step 7: Verify PR review history is accessible again
        rev_history = await mock_supabase.list_reviews_for_pr(canonical_repo_id, 10)
        assert len(rev_history) >= 1
        assert rev_history[0]["commit_sha"] == "beef01"
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


# ==============================================================================
# Requirement 6: Reconnection does not create duplicate identities or banks
# ==============================================================================

@pytest.mark.asyncio
async def test_reconnect_does_not_create_duplicate_identities_or_banks(mock_supabase, auth_user):
    """Req 6: Reconnecting a repository does not create duplicate repository identities or duplicate memory banks."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase

    fake_gh_repo = GitHubRepo(
        id=99006,
        full_name="acme/dedup-repo",
        name="dedup-repo",
        owner="acme",
        default_branch="main",
    )
    mock_gh_service = AsyncMock(spec=GitHubService)
    mock_gh_service.get_repository.return_value = fake_gh_repo
    app.dependency_overrides[get_github_service] = lambda: mock_gh_service

    try:
        # Initial Connect
        res1 = client.post("/api/v1/repositories/connect", json={"full_name": "acme/dedup-repo", "github_repo_id": 99006})
        id_1 = res1.json()["id"]
        bank_1 = get_repo_bank_id(id_1, "acme/dedup-repo")

        # Disconnect
        client.delete(f"/api/v1/repositories/{id_1}")

        # Reconnect
        res2 = client.post("/api/v1/repositories/connect", json={"full_name": "acme/dedup-repo", "github_repo_id": 99006})
        id_2 = res2.json()["id"]
        bank_2 = get_repo_bank_id(id_2, "acme/dedup-repo")

        # INVARIANT: Exact same UUID and exact same bank ID
        assert id_1 == id_2
        assert bank_1 == bank_2
        assert bank_1 == "acme_dedup-repo"

        # Assert no duplicate rows in repository store
        matching = [
            r for r in mock_supabase._local_repos.values()
            if r.get("github_repo_id") == 99006 and r.get("user_id") == MOCK_USER_ID
        ]
        assert len(matching) == 1
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


# ==============================================================================
# Requirement 7: Multi-user isolation for disconnected repository
# ==============================================================================

@pytest.mark.asyncio
async def test_multi_user_isolation_for_disconnected_repo(mock_supabase):
    """Req 7: A different user cannot access another user's disconnected repository or its private data."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase

    USER_A_ID = "22222222-2222-2222-2222-222222222222"
    MOCK_USER_A = UserSessionProfile(
        id=USER_A_ID,
        github_user_id=12345,
        login="usera",
        name="User A Developer",
        avatar_url="https://github.com/usera.png",
    )

    repo_a_id = str(uuid.uuid4())
    mock_supabase._local_repos[repo_a_id] = {
        "id": repo_a_id,
        "user_id": USER_A_ID,  # Belongs to User A
        "github_repo_id": 77001,
        "full_name": "usera/private-vault",
        "default_branch": "main",
        "is_connected": True,
        "status": "active",
    }
    # Add memory belonging to User A's repo
    mem = await mock_supabase.create_engineering_memory(
        repository_id=repo_a_id,
        statement="User A confidential architecture rule",
        type="DOCUMENTED_DECISION",
    )

    try:
        # User A disconnects repo A
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER_A
        disc_res = client.delete(f"/api/v1/repositories/{repo_a_id}")
        assert disc_res.status_code == 204

        # Now authenticate as User B
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER_B

        # User B attempts to access User A's disconnected repo
        get_res = client.get(f"/api/v1/repositories/{repo_a_id}")
        assert get_res.status_code == 404

        # User B attempts to access User A's memories
        mem_res = client.get(f"/api/v1/repositories/{repo_a_id}/canonical-memory")
        assert mem_res.status_code == 404

        # User B attempts to delete/disconnect User A's repo
        del_res = client.delete(f"/api/v1/repositories/{repo_a_id}")
        assert del_res.status_code == 404

        # User B listing must not contain User A's repo
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        assert not any(r["id"] == repo_a_id for r in list_res.json())
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_optional_current_user, None)


# ==============================================================================
# Requirement 8: Failed disconnect does not report success or leave partial state
# ==============================================================================

def test_failed_disconnect_atomicity_and_error_handling(mock_supabase, auth_user):
    """Req 8: A failed disconnect does not report success or leave partially inconsistent state."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 55555,
            "full_name": "test-org/db-fail-repo",
            "default_branch": "main",
            "webhook_active": True,
            "is_connected": True,
            "status": "active",
        }

        # Mock disconnect_repository to raise a database failure
        with patch.object(mock_supabase, "disconnect_repository", side_effect=SupabaseStateError("Connection timeout to PostgreSQL")):
            response = client.delete(f"/api/v1/repositories/{repo_id}")
            assert response.status_code == 500
            err_data = response.json()
            assert "detail" in err_data
            assert "Database failure while disconnecting repository" in err_data["detail"]

        # Ensure repository remains active and untouched
        repo = mock_supabase._local_repos[repo_id]
        assert repo["is_connected"] is True
        assert repo["status"] == "active"
        assert repo["webhook_active"] is True
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


# ==============================================================================
# Requirement 9: Repeated disconnect/reconnect operations are idempotent
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_and_reconnect_idempotency(mock_supabase, auth_user):
    """Req 9: Repeated disconnect and reconnect operations are idempotent."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase

    fake_gh_repo = GitHubRepo(
        id=99009,
        full_name="acme/idempotent-repo",
        name="idempotent-repo",
        owner="acme",
        default_branch="main",
    )
    mock_gh_service = AsyncMock(spec=GitHubService)
    mock_gh_service.get_repository.return_value = fake_gh_repo
    app.dependency_overrides[get_github_service] = lambda: mock_gh_service

    try:
        # Connect initially
        res1 = client.post("/api/v1/repositories/connect", json={"full_name": "acme/idempotent-repo", "github_repo_id": 99009})
        assert res1.status_code == 201
        repo_id = res1.json()["id"]

        # Connect again (idempotent connect)
        res_dup_connect = client.post("/api/v1/repositories/connect", json={"full_name": "acme/idempotent-repo", "github_repo_id": 99009})
        assert res_dup_connect.status_code in (200, 201)
        assert res_dup_connect.json()["id"] == repo_id

        # Disconnect 1st time
        del1 = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del1.status_code == 204

        # Disconnect 2nd time (idempotent disconnect)
        del2 = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del2.status_code == 204

        # Disconnect 3rd time
        del3 = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del3.status_code == 204

        # Reconnect after multiple disconnects
        res_reconnect = client.post("/api/v1/repositories/connect", json={"full_name": "acme/idempotent-repo", "github_repo_id": 99009})
        assert res_reconnect.status_code in (200, 201)
        assert res_reconnect.json()["id"] == repo_id
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)
        app.dependency_overrides.pop(get_github_service, None)


# ==============================================================================
# Requirement 10: Permanent-deletion operation cannot be triggered accidentally
# ==============================================================================

@pytest.mark.asyncio
async def test_disconnect_endpoint_cannot_trigger_permanent_purge(mock_supabase, auth_user):
    """Req 10: Any separate permanent-deletion operation cannot be triggered accidentally through the disconnect endpoint."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 99010,
            "full_name": "acme/safety-test",
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }
        # Add memory
        mem = await mock_supabase.create_engineering_memory(
            repository_id=repo_id,
            statement="Permanent purge guard rule",
            type="DOCUMENTED_DECISION",
        )

        # Standard disconnect endpoint MUST NOT purge the repository row or memory
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # Repository row and memory STILL EXIST
        assert repo_id in mock_supabase._local_repos
        assert mem["id"] in mock_supabase._local_engineering_memories

        # Attempting permanent purge without explicit confirmation raises ValueError
        with pytest.raises(ValueError, match="requires explicit confirm=True"):
            await mock_supabase.purge_repository_permanently(repo_id, user_id=MOCK_USER_ID, confirm=False)

        # Still preserved
        assert repo_id in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


# ==============================================================================
# Baseline Authorization & Edge Case Regression Tests
# ==============================================================================

def test_delete_unauthenticated_user_returns_401(mock_supabase):
    """Unauthenticated users cannot disconnect repositories (HTTP 401)."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
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

        response = client.delete(f"/api/v1/repositories/{repo_id}")
        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]
        assert repo_id in mock_supabase._local_repos
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_other_user_repository_returns_404(mock_supabase):
    """One user cannot disconnect another user's repository."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_b_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_b_id] = {
            "id": repo_b_id,
            "user_id": USER_B_ID,
            "github_repo_id": 88888,
            "full_name": "bob-org/bobs-private-repo",
            "default_branch": "main",
            "webhook_active": False,
        }

        # User A attempts to disconnect User B's repository
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER
        res_a_del = client.delete(f"/api/v1/repositories/{repo_b_id}")
        assert res_a_del.status_code == 404
        assert f"Repository '{repo_b_id}' not found." in res_a_del.json()["detail"]

        # User B's repo remains intact and active
        assert repo_b_id in mock_supabase._local_repos

        # Now User B performs disconnect, which succeeds
        app.dependency_overrides[get_optional_current_user] = lambda: MOCK_USER_B
        res_b_del = client.delete(f"/api/v1/repositories/{repo_b_id}")
        assert res_b_del.status_code == 204
        assert mock_supabase._local_repos[repo_b_id]["is_connected"] is False
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
    """Disconnecting Repo A leaves Repo B completely intact and connected."""
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
            "is_connected": True,
            "status": "active",
        }
        mock_supabase._local_repos[repo_b_id] = {
            "id": repo_b_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 22222,
            "full_name": "org/repo-b",
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }

        # Disconnect Repo A
        del_res = client.delete(f"/api/v1/repositories/{repo_a_id}")
        assert del_res.status_code == 204

        # Verify Repo A is disconnected and Repo B remains connected
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        repos = list_res.json()
        assert len(repos) == 1
        assert repos[0]["id"] == repo_b_id
        assert repos[0]["full_name"] == "org/repo-b"
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_final_repo_yields_empty_state(mock_supabase, auth_user):
    """Disconnecting the final connected repository yields [] empty active list."""
    app.dependency_overrides[get_supabase_service] = lambda: mock_supabase
    try:
        repo_id = str(uuid.uuid4())
        mock_supabase._local_repos[repo_id] = {
            "id": repo_id,
            "user_id": MOCK_USER_ID,
            "github_repo_id": 33333,
            "full_name": "org/final-repo",
            "default_branch": "main",
            "is_connected": True,
            "status": "active",
        }

        # Disconnect the only repo
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # List is now empty
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        assert list_res.json() == []
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


def test_delete_shared_workspace_repo_allowed_for_workspace_member(mock_supabase, auth_user):
    """Shared default workspace repository can be disconnected by workspace member."""
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
            "is_connected": True,
            "status": "active",
        }

        # Member sees it in listing
        list_res = client.get("/api/v1/repositories")
        assert list_res.status_code == 200
        assert any(r["id"] == repo_id for r in list_res.json())

        # Member disconnects it
        del_res = client.delete(f"/api/v1/repositories/{repo_id}")
        assert del_res.status_code == 204

        # Disappears from member active listing
        post_res = client.get("/api/v1/repositories")
        assert post_res.status_code == 200
        assert not any(r["id"] == repo_id for r in post_res.json())
    finally:
        app.dependency_overrides.pop(get_supabase_service, None)


@pytest.mark.asyncio
async def test_disconnect_fails_safely_when_migration_missing(auth_user):
    """When Supabase is configured but lifecycle columns are missing, disconnect fails safely."""
    configured_svc = SupabaseService(
        supabase_url="https://mock.supabase.co",
        supabase_key="mock_key",
        persist_local=False,
    )
    repo_id = str(uuid.uuid4())
    fake_repo = {
        "id": repo_id,
        "user_id": MOCK_USER_ID,
        "github_repo_id": 88888,
        "full_name": "org/unmigrated-repo",
        "default_branch": "main",
        "webhook_active": True,
    }

    # Mock get_repository_by_id and _check_lifecycle_columns
    with patch.object(configured_svc, "get_repository_by_id", return_value=fake_repo), \
         patch.object(configured_svc, "_check_lifecycle_columns", return_value=False):
        
        # Calling disconnect_repository directly raises SupabaseStateError
        with pytest.raises(SupabaseStateError) as exc_info:
            await configured_svc.disconnect_repository(repo_id, user_id=MOCK_USER_ID)
        
        assert "20261023000000_repository_disconnection_lifecycle.sql" in str(exc_info.value)
        assert "is_connected" in str(exc_info.value)

        # Calling via API returns 500 and reports missing migration
        app.dependency_overrides[get_supabase_service] = lambda: configured_svc
        try:
            res = client.delete(f"/api/v1/repositories/{repo_id}")
            assert res.status_code == 500
            assert "20261023000000_repository_disconnection_lifecycle.sql" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_supabase_service, None)

