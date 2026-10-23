import uuid
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.services.code_analysis import get_code_analysis_service
from app.services.github import get_github_service
from app.services.groq import get_groq_service
from app.services.memory import get_hindsight_service
from app.services.review_engine import ReviewEngine
from app.services.supabase import get_supabase_service

client = TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: Real Environment Configuration Verification
# ---------------------------------------------------------------------------
def test_environment_configuration_probe():
    """Verifies that all 4 service configurations are probed safely without exposing secrets."""
    response = client.get("/api/v1/settings/status")
    assert response.status_code == 200
    data = response.json()

    assert "services" in data
    services = data["services"]

    # Verify all 4 required services are inspected
    assert "github" in services
    assert "supabase" in services
    assert "hindsight" in services
    assert "groq" in services
    assert "tree_sitter" in services

    # Verify no secret values are leaked
    response_str = response.text.lower()
    assert "password" not in response_str
    assert "secret" not in response_str
    assert "token" not in response_str or "token_active" in response_str or "public" in response_str

    print("Configuration verification passed: All services safely probed.")


# ---------------------------------------------------------------------------
# Test 2: Live Public GitHub Repository Fetch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_live_public_github_fetch():
    """Verifies that the GitHub service can communicate with api.github.com for public repositories."""
    gh = get_github_service()
    repo = await gh.get_repository("octocat", "Hello-World")
    assert repo.full_name == "octocat/Hello-World"
    assert repo.default_branch in ("master", "main")
    print("Live GitHub public repo fetch passed:", repo.full_name)


# ---------------------------------------------------------------------------
# Test 3: Core Demo Scenario - Architecture Drift Detected with Historical PR Precedent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_engineering_memory_demo_architecture_drift():
    """Tests the full Engineering Memory pipeline:
    GitHub repo -> Changed code -> Tree-sitter -> Hindsight memory recall -> Review reasoning -> Supabase persistence.
    Verifies that architectural drift is flagged citing past precedent (PR #142).
    """
    supabase = get_supabase_service()
    memory = get_hindsight_service()
    code_analysis = get_code_analysis_service()
    groq = get_groq_service()
    github = get_github_service()

    suffix = uuid.uuid4().hex[:6]
    repo_full_name = f"org/payments-{suffix}"
    bank_id = repo_full_name.replace("/", "_").lower()

    # Step 1: Retain historical architectural decision in Hindsight memory
    # Historical PR #142 rejected controllers accessing persistence directly
    historical_knowledge = (
        "PR #142 Architectural Decision: Controllers must not directly access persistence or execute database queries. "
        "All data access must flow through dedicated domain services to preserve layer boundaries."
    )
    retain_res = memory.retain(
        content=historical_knowledge,
        bank_id=bank_id,
        context="PR #142 Review Decision",
        document_id=f"pr-142-{suffix}",
        tags=["architecture_drift", "rejected_pattern", "controller"],
    )
    assert retain_res.success is True

    # Step 2: Connect repository in Supabase state
    repo_record = await supabase.connect_repository(
        github_repo_id=987654,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    # Step 3: Setup new PR (#250) violating the historical decision
    # Controller directly imports and calls database
    pr_number = 250
    diff_patch = (
        "@@ -1,5 +1,12 @@\n"
        "+ import { Database } from '../db/connection'\n"
        "  export class PaymentController {\n"
        "+   async processPayment(req, res) {\n"
        "+     const record = await Database.query('SELECT * FROM accounts WHERE id = ?', req.body.id)\n"
        "+     return res.json(record)\n"
        "+   }\n"
        "  }\n"
    )

    pr_detail = PRDetailResponse(
        pr=GitHubPR(
            id=25001,
            number=pr_number,
            title="Add fast payment processing endpoint",
            author="contributor-dev",
            head_sha="abc123def456",
            base_branch="main",
            base_sha="base000111",
            state="open",
            created_at="2026-09-28T12:00:00Z",
            updated_at="2026-09-28T12:00:00Z",
        ),
        files=[
            GitHubFileDiff(
                filename="src/controllers/PaymentController.ts",
                status="modified",
                additions=7,
                deletions=0,
                changes=7,
                patch=diff_patch,
            )
        ],
        commits=[],
        comments=[],
    )

    # Mock GitHub's full PR details returning our test PR
    original_get_pr = github.get_pr_details_full
    github.get_pr_details_full = AsyncMock(return_value=pr_detail)

    try:
        # Step 4: Execute ReviewEngine
        engine = ReviewEngine(
            github=github,
            code_analysis=code_analysis,
            memory=memory,
            groq=groq,
            supabase=supabase,
        )

        review_output = await engine.review_pull_request(repo_id, pr_number)

        # Step 5: Verify Architecture Drift Detection
        assert review_output.drift_detected is True
        assert review_output.exception_approved is False
        assert len(review_output.findings) > 0

        # Step 6: Verify Historical Evidence (PR #142) was retrieved and cited
        finding = review_output.findings[0]
        assert finding.category in ("architecture_drift", "rejected_pattern")
        assert finding.historical_pr_ref == "PR #142"
        assert (
            "PR #142" in finding.message
            or "PR #142" in review_output.summary
            or finding.historical_pr_ref == "PR #142"
        )
        assert finding.suggested_fix is not None

        # Step 7: Verify Supabase Persistence
        persisted_review = await supabase.get_latest_review_for_pr(repo_id, pr_number)
        assert persisted_review is not None
        assert persisted_review["drift_detected"] is True
        assert len(persisted_review["findings"]) > 0
        assert persisted_review["findings"][0]["historical_pr_ref"] == "PR #142"

        # Step 8: Verify API Endpoint returns the review for UI presentation
        api_res = client.get(f"/api/v1/reviews/{repo_id}/{pr_number}")
        assert api_res.status_code == 200
        review_data = api_res.json()
        assert review_data["drift_detected"] is True
        assert review_data["findings"][0]["historical_pr_ref"] == "PR #142"

        # Step 9: Verify Recent Reviews overview endpoint
        recent_res = client.get("/api/v1/reviews/recent")
        assert recent_res.status_code == 200
        assert any(r["pr_number"] == pr_number for r in recent_res.json())

        # Step 10: Verify Phase 7 Walkthrough Endpoint returns the semantic walkthrough for the reviewed PR
        walkthrough_res = client.get(f"/api/v1/reviews/{repo_id}/{pr_number}/walkthrough")
        assert walkthrough_res.status_code == 200
        walkthrough_data = walkthrough_res.json()
        assert walkthrough_data["pr_number"] == pr_number
        assert walkthrough_data["repository_id"] == repo_id
        assert walkthrough_data["head_sha"] == "abc123def456"
        assert len(walkthrough_data["groups"]) > 0
        assert walkthrough_data["surfaced_finding_count"] > 0
        assert any(
            f["historical_pr_ref"] == "PR #142"
            for g in walkthrough_data["groups"]
            for f in g["findings"]
        )

        print("Engineering Memory Demo test passed: Architecture drift detected with PR #142 citation and walkthrough verified.")
    finally:
        github.get_pr_details_full = original_get_pr


# ---------------------------------------------------------------------------
# Test 4: Approved Exception Scenario - No False Violation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_engineering_memory_approved_exception():
    """Tests that an approved architectural exception retrieved from memory avoids a false positive violation."""
    supabase = get_supabase_service()
    memory = get_hindsight_service()
    code_analysis = get_code_analysis_service()
    groq = get_groq_service()
    github = get_github_service()

    suffix = uuid.uuid4().hex[:6]
    repo_full_name = f"org/diagnostics-{suffix}"
    bank_id = repo_full_name.replace("/", "_").lower()

    # Step 1: Retain an approved exception in Hindsight memory
    approved_knowledge = (
        "Approved Exception in PR #88: HealthCheckController and DiagnosticsController are permitted to directly "
        "ping the database for sub-millisecond liveness probes without domain service overhead."
    )
    memory.retain(
        content=approved_knowledge,
        bank_id=bank_id,
        context="PR #88 Approved Exception",
        document_id=f"pr-88-{suffix}",
        tags=["approved_exception", "controller"],
    )

    # Step 2: Connect repo in Supabase
    repo_record = await supabase.connect_repository(
        github_repo_id=112233,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    # Step 3: Setup PR (#95) implementing the approved exception
    pr_number = 95
    diff_patch = (
        "@@ -1,5 +1,10 @@\n"
        "+ import { Database } from '../db/connection'\n"
        "  export class HealthCheckController {\n"
        "+   async checkLiveness(req, res) {\n"
        "+     const status = await Database.query('SELECT 1')\n"
        "+     return res.json({ healthy: true })\n"
        "+   }\n"
        "  }\n"
    )

    pr_detail = PRDetailResponse(
        pr=GitHubPR(
            id=95001,
            number=pr_number,
            title="Implement health check liveness controller exception",
            author="platform-engineer",
            head_sha="fff999aaa111",
            base_branch="main",
            base_sha="base999888",
            state="open",
            created_at="2026-09-28T14:00:00Z",
            updated_at="2026-09-28T14:00:00Z",
        ),
        files=[
            GitHubFileDiff(
                filename="src/controllers/HealthCheckController.ts",
                status="modified",
                additions=5,
                deletions=0,
                changes=5,
                patch=diff_patch,
            )
        ],
        commits=[],
        comments=[],
    )

    original_get_pr = github.get_pr_details_full
    github.get_pr_details_full = AsyncMock(return_value=pr_detail)

    try:
        engine = ReviewEngine(
            github=github,
            code_analysis=code_analysis,
            memory=memory,
            groq=groq,
            supabase=supabase,
        )

        review_output = await engine.review_pull_request(repo_id, pr_number)

        # Step 4: Verify exception approved without producing an error violation
        assert review_output.exception_approved is True
        assert review_output.drift_detected is False
        assert all(f.severity != "error" for f in review_output.findings)

        # Step 5: Verify persisted review
        persisted_review = await supabase.get_latest_review_for_pr(repo_id, pr_number)
        assert persisted_review is not None
        assert persisted_review["exception_approved"] is True
        assert persisted_review["drift_detected"] is False

        print("Approved exception scenario passed: No false positive violation produced.")
    finally:
        github.get_pr_details_full = original_get_pr
