import uuid
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock
from app.main import app
from app.services.supabase import get_supabase_service
import app.services.memory as _memory_module
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.review_engine import ReviewEngine
from app.services.code_analysis import get_code_analysis_service
from app.services.groq import get_groq_service
from app.services.github import get_github_service
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema, HistoricalEvidenceSchema

client = TestClient(app)


@pytest.fixture(autouse=True)
def patch_memory_service(request):
    """Autouse fixture ensuring ephemeral in-memory Hindsight instance for zero live network calls."""
    if request.node.get_closest_marker("live_integration"):
        yield
        return

    ephemeral_mem = HindsightMemoryService(api_key=None)
    _saved_mem = _memory_module._hindsight_service
    _memory_module._hindsight_service = ephemeral_mem
    app.dependency_overrides[get_hindsight_service] = lambda: ephemeral_mem
    try:
        yield ephemeral_mem
    finally:
        _memory_module._hindsight_service = _saved_mem
        app.dependency_overrides.pop(get_hindsight_service, None)


@pytest.mark.asyncio
async def test_engineering_memory_e2e_lifecycle():
    """End-to-End test of the reconstructed Engineering Memory system:
    1. Repository A starts with empty memory.
    2. Record decision persists to Supabase and retains in Hindsight bank.
    3. Category filtering returns exact filtered results.
    4. Memory search retrieves relevant records.
    5. Deduplication prevents identical memory sprawl.
    6. Repository B is isolated and cannot see Repository A memories.
    7. Review execution recalls Repository A memory and emits historical evidence.
    """
    supabase = get_supabase_service()
    memory_svc = get_hindsight_service()

    # STEP 1: Repository A has no memory initially
    suffix_a = uuid.uuid4().hex[:6]
    repo_a_name = f"acme/billing-{suffix_a}"
    repo_a = await supabase.connect_repository(
        github_repo_id=10001,
        full_name=repo_a_name,
        default_branch="main",
    )
    repo_a_id = repo_a["id"]

    res_empty = client.get(f"/api/v1/repositories/{repo_a_id}/memory")
    assert res_empty.status_code == 200
    assert res_empty.json()["total_count"] == 0
    assert res_empty.json()["memories"] == []

    # STEP 2: Record an architectural decision with PR #142 evidence
    decision_payload = {
        "claim": "Controllers must not directly access persistence or run raw SQL queries.",
        "rationale": "Keep persistence behind the service layer to preserve transaction boundaries.",
        "type": "architecture_decision",
        "scope": "src/controllers/*",
        "source_ref": "PR #142",
        "source_url": f"https://github.com/{repo_a_name}/pull/142",
        "confidence": "supported",
    }
    create_res = client.post(f"/api/v1/repositories/{repo_a_id}/memory", json=decision_payload)
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["claim"] == decision_payload["claim"]
    assert created_data["type"] == "architecture_decision"
    assert created_data["repository_id"] == repo_a_id
    assert created_data["source_ref"] == "PR #142"
    assert created_data["confidence"] == "supported"
    memory_id = created_data["id"]

    # Verify memory exists in Supabase
    stored = await supabase.get_engineering_memory(memory_id)
    assert stored is not None
    assert stored["claim"] == decision_payload["claim"]

    # Verify retained in Hindsight bank scoped to repo_a
    bank_a = repo_a_name.replace("/", "_").lower()
    recall_a = memory_svc.recall("controllers persistence query", bank_id=bank_a)
    assert any(
        "controller" in item.text.lower()
        or "persistence" in item.text.lower()
        or "sql" in item.text.lower()
        for item in recall_a.items
    )

    # STEP 3: Record an approved exception
    exception_payload = {
        "claim": "Direct database queries are permitted inside database migration scripts.",
        "rationale": "Migrations run before application boot and require direct DDL/DML access.",
        "type": "approved_exception",
        "scope": "migrations/*",
        "source_ref": "PR #221",
        "source_url": f"https://github.com/{repo_a_name}/pull/221",
        "confidence": "supported",
    }
    create_exc_res = client.post(f"/api/v1/repositories/{repo_a_id}/memory", json=exception_payload)
    assert create_exc_res.status_code == 201

    # STEP 4: Category filtering works on backend
    res_all = client.get(f"/api/v1/repositories/{repo_a_id}/memory")
    assert res_all.status_code == 200
    assert res_all.json()["total_count"] == 2

    res_arch = client.get(f"/api/v1/repositories/{repo_a_id}/memory?type=architecture_decision")
    assert res_arch.status_code == 200
    assert res_arch.json()["total_count"] == 1
    assert res_arch.json()["memories"][0]["type"] == "architecture_decision"

    res_exc = client.get(f"/api/v1/repositories/{repo_a_id}/memory?type=approved_exception")
    assert res_exc.status_code == 200
    assert res_exc.json()["total_count"] == 1
    assert res_exc.json()["memories"][0]["type"] == "approved_exception"

    res_pat = client.get(f"/api/v1/repositories/{repo_a_id}/memory?type=rejected_pattern")
    assert res_pat.status_code == 200
    assert res_pat.json()["total_count"] == 0

    # STEP 5: Search queries the selected repository
    res_search = client.get(f"/api/v1/repositories/{repo_a_id}/memory/search?q=migration")
    assert res_search.status_code == 200
    assert res_search.json()["total_count"] >= 1
    assert any("migration" in m["claim"].lower() for m in res_search.json()["memories"])

    # STEP 6: Deduplication - recording the same decision does not create a duplicate
    duplicate_payload = {
        "claim": "Controllers must not directly access persistence or run raw SQL queries.",
        "rationale": "Updated rationale: preserve transaction boundaries and avoid connection leaks.",
        "type": "architecture_decision",
        "source_ref": "PR #176",
    }
    dup_res = client.post(f"/api/v1/repositories/{repo_a_id}/memory", json=duplicate_payload)
    assert dup_res.status_code in (200, 201)
    assert dup_res.json()["id"] == memory_id  # Same ID!
    # Evidence items should be merged
    assert "PR #142" in dup_res.json().get("evidence_items", [])
    assert "PR #176" in dup_res.json().get("evidence_items", [])

    # Total memories in Repo A should still be 2 (not 3)
    res_all_after_dup = client.get(f"/api/v1/repositories/{repo_a_id}/memory")
    assert res_all_after_dup.json()["total_count"] == 2

    # STEP 7: Repository Isolation - Create Repository B and verify no memory leak
    suffix_b = uuid.uuid4().hex[:6]
    repo_b_name = f"acme/analytics-{suffix_b}"
    repo_b = await supabase.connect_repository(
        github_repo_id=10002,
        full_name=repo_b_name,
        default_branch="main",
    )
    repo_b_id = repo_b["id"]

    # Repository B must have 0 memories
    res_b = client.get(f"/api/v1/repositories/{repo_b_id}/memory")
    assert res_b.status_code == 200
    assert res_b.json()["total_count"] == 0
    assert res_b.json()["memories"] == []

    # Searching Repo B for "persistence" must return 0
    res_b_search = client.get(f"/api/v1/repositories/{repo_b_id}/memory/search?q=persistence")
    assert res_b_search.status_code == 200
    assert res_b_search.json()["total_count"] == 0

    # Hindsight bank for Repo B must be empty
    bank_b = repo_b_name.replace("/", "_").lower()
    recall_b = memory_svc.recall("controllers persistence query", bank_id=bank_b)
    assert recall_b.results_count == 0

    # Back to Repository A: memory is still intact
    res_a_back = client.get(f"/api/v1/repositories/{repo_a_id}/memory")
    assert res_a_back.status_code == 200
    assert res_a_back.json()["total_count"] == 2

    # STEP 8: Review Execution recalls Repository A memory and emits Historical Evidence
    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=True,
        summary="Architecture drift: direct persistence access detected in controller.",
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/payment_controller.py",
                line_number=42,
                title="Controllers must not directly access persistence",
                message="Direct database query executed inside controller method.",
                suggested_fix="Delegate data access to PaymentService.",
                historical_pr_ref="PR #142",
                historical_evidence=HistoricalEvidenceSchema(
                    claim="Controllers must not directly access persistence or run raw SQL queries.",
                    rationale="Keep persistence behind the service layer to preserve transaction boundaries.",
                    evidence_prs=["PR #142", "PR #176"],
                    matched_code="db.execute('SELECT * FROM payments')",
                    reasoning="Repository previously established service layer boundary; direct SQL calls violate architectural precedent.",
                    exception_notes="PR #221 exception applies only to migration scripts.",
                ),
            )
        ],
        exception_approved=False,
    )

    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock()
    mock_github.get_pr_details_full.return_value = PRDetailResponse(
        pr=GitHubPR(
            id=300,
            number=300,
            title="Add fast payment status endpoint",
            body="Directly queries payment table for speed",
            state="open",
            html_url=f"https://github.com/{repo_a_name}/pull/300",
            author="dev",
            head_sha="sha300",
            base_branch="main",
            head_branch="feature/payment-status",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[
            GitHubFileDiff(
                filename="src/controllers/payment_controller.py",
                status="modified",
                additions=5,
                deletions=0,
                patch="@@ -40,3 +40,8 @@\n+    def get_status(self, id):\n+        return db.execute('SELECT * FROM payments WHERE id = %s', id)",
            )
        ],
    )

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=get_code_analysis_service(),
        memory=memory_svc,
        groq=mock_groq,
        supabase=supabase,
    )

    review_res = await engine.review_pull_request(
        repo_id=repo_a_id,
        pr_number=300,
    )

    # Verify review succeeded and preserved historical context
    assert review_res.drift_detected is True
    assert len(review_res.findings) == 1
    finding = review_res.findings[0]
    assert finding.historical_evidence is not None
    assert finding.historical_evidence.claim == "Controllers must not directly access persistence or run raw SQL queries."
    assert "PR #142" in finding.historical_evidence.evidence_prs
    assert finding.historical_evidence.exception_notes is not None

    # STEP 9: Dashboard review metrics & repository isolation
    # Repo A has 1 review (drift)
    res_reviews_a = client.get(f"/api/v1/reviews/recent?repo_id={repo_a_id}")
    assert res_reviews_a.status_code == 200
    reviews_a = res_reviews_a.json()
    assert len(reviews_a) == 1
    assert reviews_a[0]["drift_detected"] is True
    assert reviews_a[0]["verdict"] == "DRIFT"
    assert reviews_a[0]["pr_number"] == 300

    # Repo B has 0 reviews (strict repository isolation)
    res_reviews_b = client.get(f"/api/v1/reviews/recent?repo_id={repo_b_id}")
    assert res_reviews_b.status_code == 200
    assert len(res_reviews_b.json()) == 0

    # Run a conforming review on Repo A
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        drift_detected=False,
        summary="Architecture review passed. Conforms to repository architectural standards.",
        findings=[],
        exception_approved=False,
    )
    mock_github.get_pr_details_full.return_value = PRDetailResponse(
        pr=GitHubPR(
            id=301,
            number=301,
            title="Update controller conforming to service layer",
            body="Pure refactor with conforming interfaces",
            state="open",
            html_url=f"https://github.com/{repo_a_name}/pull/301",
            author="dev",
            head_sha="sha301",
            base_branch="main",
            head_branch="refactor/types",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[
            GitHubFileDiff(
                filename="src/controllers/payment_controller.py",
                status="modified",
                additions=2,
                deletions=2,
                patch="@@ -10,2 +10,2 @@\n-def pay(): pass\n+def pay() -> bool: return True",
            )
        ],
    )
    conforming_review = await engine.review_pull_request(repo_id=repo_a_id, pr_number=301)
    assert conforming_review.drift_detected is False
    assert conforming_review.verdict == "CONFORMING"

    # Verify updated dashboard reviews for Repo A
    res_reviews_a_after = client.get(f"/api/v1/reviews/recent?repo_id={repo_a_id}")
    assert res_reviews_a_after.status_code == 200
    reviews_a_after = res_reviews_a_after.json()
    assert len(reviews_a_after) == 2

    conforming_count = sum(1 for r in reviews_a_after if not r["drift_detected"] and r["status"] == "completed")
    drift_count = sum(1 for r in reviews_a_after if r["drift_detected"])
    assert conforming_count == 1
    assert drift_count == 1

    # Repo B review count is STILL 0
    res_reviews_b_after = client.get(f"/api/v1/reviews/recent?repo_id={repo_b_id}")
    assert len(res_reviews_b_after.json()) == 0

    # STEP 10: Memory Provenance Audit & Link Integrity
    # No memory can contain fabricated "Hindsight memory" as evidence
    res_mems = client.get(f"/api/v1/repositories/{repo_a_id}/memory")
    assert res_mems.status_code == 200
    mems = res_mems.json()["memories"]
    for m in mems:
        assert m["repository_id"] == repo_a_id
        for ev in m.get("evidence_items", []):
            assert "Hindsight memory" not in ev
            assert ev != "Hindsight"

