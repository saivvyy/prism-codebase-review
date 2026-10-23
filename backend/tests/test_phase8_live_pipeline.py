import uuid
from unittest.mock import AsyncMock
import pytest
from app.core.config import get_settings
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.services.code_analysis import get_code_analysis_service
from app.services.github import get_github_service
from app.services.groq import get_groq_service
from app.services.memory import get_hindsight_service
from app.services.review_engine import ReviewEngine
from app.services.supabase import get_supabase_service


@pytest.mark.asyncio
async def test_phase8_live_architecture_violation():
    """Validates real pipeline with an architecture violation and historical precedent recall."""
    settings = get_settings()
    github = get_github_service()
    code_analysis = get_code_analysis_service()
    memory = get_hindsight_service()
    groq = get_groq_service()
    supabase = get_supabase_service()

    suffix = uuid.uuid4().hex[:6]
    repo_full_name = f"org/payments-p8-{suffix}"
    bank_id = repo_full_name.replace("/", "_").lower()

    # 1. Retain historical architectural decision in Hindsight
    hist_decision = (
        "PR #142 Architectural Decision: Controllers must not directly access persistence or execute database queries. "
        "All data access must flow through dedicated domain services to preserve layer boundaries."
    )
    retain_res = await memory.aretain(
        content=hist_decision,
        bank_id=bank_id,
        context="PR #142 Review Decision",
        document_id=f"doc-phase8-pr142-{suffix}",
        tags=["architecture_drift", "rejected_pattern", "controller"],
    )
    assert retain_res.success is True

    # 2. Connect repository
    repo_record = await supabase.connect_repository(
        github_repo_id=9876543,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    # 3. Create PR #250 introducing direct DB query in Controller
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
            id=250001,
            number=pr_number,
            title="Add fast payment processing endpoint",
            author="contributor-dev",
            head_sha="sha250payment",
            base_branch="main",
            base_sha="shabase001",
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

        review_result = await engine.review_pull_request(
            repo_id=repo_id,
            pr_number=pr_number,
        )

        assert review_result.drift_detected is True
        drift_finding = next((f for f in review_result.findings if f.category == "architecture_drift"), None)
        assert drift_finding is not None
        assert "142" in (drift_finding.historical_pr_ref or "")
    finally:
        github.get_pr_details_full = original_get_pr


@pytest.mark.asyncio
async def test_phase8_live_approved_exception():
    """Validates real pipeline with an approved exception avoiding false positive violations."""
    github = get_github_service()
    code_analysis = get_code_analysis_service()
    memory = get_hindsight_service()
    groq = get_groq_service()
    supabase = get_supabase_service()

    suffix = uuid.uuid4().hex[:6]
    repo_full_name = f"org/diagnostics-p8-{suffix}"
    bank_id = repo_full_name.replace("/", "_").lower()

    # 1. Retain approved exception in Hindsight
    exception_decision = (
        "Approved Exception in PR #88: HealthCheckController and DiagnosticsController are permitted to directly "
        "ping the database for sub-millisecond liveness probes without domain service overhead."
    )
    retain_res = await memory.aretain(
        content=exception_decision,
        bank_id=bank_id,
        context="PR #88 Approved Exception",
        document_id=f"doc-phase8-pr88-{suffix}",
        tags=["approved_exception", "controller"],
    )
    assert retain_res.success is True

    repo_record = await supabase.connect_repository(
        github_repo_id=9876544,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    pr_number_exp = 95
    diff_patch_exp = (
        "@@ -1,5 +1,10 @@\n"
        "+ import { Database } from '../db/connection'\n"
        "  export class HealthCheckController {\n"
        "+   async checkLiveness(req, res) {\n"
        "+     const status = await Database.query('SELECT 1')\n"
        "+     return res.json({ healthy: true })\n"
        "+   }\n"
        "  }\n"
    )

    pr_detail_exp = PRDetailResponse(
        pr=GitHubPR(
            id=950001,
            number=pr_number_exp,
            title="Implement health check liveness controller exception",
            author="platform-engineer",
            head_sha="sha95health",
            base_branch="main",
            base_sha="shabase002",
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
                patch=diff_patch_exp,
            )
        ],
        commits=[],
        comments=[],
    )

    original_get_pr = github.get_pr_details_full
    github.get_pr_details_full = AsyncMock(return_value=pr_detail_exp)

    try:
        engine = ReviewEngine(
            github=github,
            code_analysis=code_analysis,
            memory=memory,
            groq=groq,
            supabase=supabase,
        )

        review_result_exp = await engine.review_pull_request(
            repo_id=repo_id,
            pr_number=pr_number_exp,
        )

        assert review_result_exp.drift_detected is False
        assert review_result_exp.exception_approved is True
        error_findings = [f for f in review_result_exp.findings if f.severity == "error"]
        assert len(error_findings) == 0
    finally:
        github.get_pr_details_full = original_get_pr
