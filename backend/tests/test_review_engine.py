from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    GitHubNotFoundError,
    GroqServiceError,
    HindsightMemoryError,
)
from app.main import app
from app.schemas.analysis import ClassDefinition, FileAnalysisResult, FunctionCall, FunctionDefinition
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.schemas.memory import MemoryItemSchema, RecallMemoryResponse, RetainMemoryResponse
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema
from app.services.code_analysis import CodeAnalysisService
from app.services.github import GitHubService
from app.services.groq import GroqService
from app.services.memory import HindsightMemoryService
from app.services.review_engine import ReviewEngine, get_review_engine
from app.services.supabase import SupabaseService, get_supabase_service

client = TestClient(app)


@pytest.fixture
def mock_engine_deps():
    mock_github = MagicMock(spec=GitHubService)
    mock_code_analysis = MagicMock(spec=CodeAnalysisService)
    mock_memory = MagicMock(spec=HindsightMemoryService)
    mock_groq = MagicMock(spec=GroqService)
    mock_supabase = MagicMock(spec=SupabaseService)

    mock_memory.is_configured = True
    mock_memory.default_bank_id = "engineering_memory"
    mock_supabase.is_configured = False  # Use local mock mode

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=mock_code_analysis,
        memory=mock_memory,
        groq=mock_groq,
        supabase=mock_supabase,
    )
    return engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase


@pytest.mark.asyncio
async def test_demo_scenario_architecture_drift(mock_engine_deps):
    """Core Demo: Controllers must not directly access persistence. Previously rejected in PR #142."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = mock_engine_deps

    # 1. State setup
    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-uuid-142", "pr_number": 142}
    )
    mock_supabase.create_review = AsyncMock(
        return_value={"id": "review-uuid-1"}
    )
    mock_supabase.create_review_findings = AsyncMock(return_value=[])
    # Canonical memory: active authoritative rule that backs the DRIFT verdict.
    # Without this durable rule, LLM output alone cannot cause DRIFT (Product Rule 8).
    mock_supabase.list_engineering_memories = AsyncMock(
        return_value=[
            {
                "memory_id": "mem-rule-ctrl-1",
                "repository_id": "repo-uuid-1",
                "repository_full_name": "org/core-api",
                "type": "DOCUMENTED_DECISION",
                "authority": "ENFORCEABLE",
                "statement": "Controllers must not directly access persistence; all DB queries must go through the repository layer.",
                "rationale": "Architectural boundary rule rejected in PR #142.",
                "scope": "src/controllers/*",
                "source_ref": "PR #142",
                "source_pr_number": 142,
                "status": "ACTIVE",
            }
        ]
    )
    mock_supabase.create_engineering_memory = AsyncMock(return_value={"id": "mem-new-1"})

    # 2. GitHub PR with direct persistence access
    pr = GitHubPR(
        id=142,
        number=142,
        title="Direct database query in UserController",
        state="open",
        author="developer",
        head_sha="sha142",
        base_branch="main",
    )
    diff = GitHubFileDiff(
        filename="src/controllers/user_controller.py",
        status="modified",
        additions=10,
        deletions=2,
        patch="@@ -5,3 +5,8 @@\n+from app.db import database\n+database.execute('SELECT 1')",
    )
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(pr=pr, files=[diff], commits=[], comments=[])
    )

    # 3. Tree-sitter extracts class and function calls
    mock_code_analysis.analyze_diff.return_value = FileAnalysisResult(
        file_path="src/controllers/user_controller.py",
        language="python",
        is_supported=True,
        parse_success=True,
        classes=[ClassDefinition(name="UserController", start_line=5, end_line=15)],
        functions=[FunctionDefinition(name="get_users", class_name="UserController", start_line=6, end_line=14)],
        calls=[FunctionCall(target="database.execute", enclosing_class="UserController", line_number=8)],
        summary="Python: 1 class (UserController), 1 call (database.execute)",
    )

    # 4. Hindsight retrieves historical rejection from PR #142
    historical_fact = "Historical Rule: Controllers must not directly access persistence. Previously rejected in PR #142."
    mock_memory.recall.return_value = RecallMemoryResponse(
        bank_id="org_core-api",
        query="UserController | database.execute",
        results_count=1,
        items=[MemoryItemSchema(text=historical_fact, score=0.95)],
        prompt_context=historical_fact,
    )
    mock_memory.retain.return_value = RetainMemoryResponse(success=True, bank_id="org_core-api", items_count=1)

    # 5. Groq detects architecture drift
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Architecture Drift Detected: UserController directly queries persistence.",
        drift_detected=True,
        exception_approved=False,
        confidence_score=0.98,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="error",
                file_path="src/controllers/user_controller.py",
                line_number=8,
                title="Direct database query violates architecture standard",
                message="This pattern was previously rejected in PR #142 because persistence queries belong in the repository layer.",
                historical_pr_ref="PR #142",
                suggested_fix="Inject UserRepository instead of directly invoking database.execute()",
            )
        ],
    )

    # Execute review
    result = await engine.review_pull_request("repo-uuid-1", 142)

    # Assertions
    assert result.drift_detected is True
    assert result.exception_approved is False
    assert len(result.findings) == 1
    assert result.findings[0].category == "architecture_drift"
    assert result.findings[0].historical_pr_ref == "PR #142"
    assert "PR #142" in result.findings[0].message

    # Verify Hindsight recalled memory
    mock_memory.recall.assert_called_once()

    # Verify Review was persisted in Supabase
    mock_supabase.create_review.assert_called_once()
    mock_supabase.create_review_findings.assert_called_once()

    # Verify Memory Learning: retained decision into Hindsight
    mock_memory.retain.assert_called_once()


@pytest.mark.asyncio
async def test_demo_scenario_approved_exception(mock_engine_deps):
    """Engine recognizes approved exceptions and avoids false violations."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = mock_engine_deps

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-uuid-200", "pr_number": 200}
    )
    mock_supabase.create_review = AsyncMock(return_value={"id": "review-uuid-2"})
    mock_supabase.create_review_findings = AsyncMock(return_value=[])
    # Canonical memory: active general rule + active approved exception.
    # APPROVED_EXCEPTION requires both a general rule and a valid scoped exception (Product Rule 6).
    mock_supabase.list_engineering_memories = AsyncMock(
        return_value=[
            {
                "memory_id": "mem-rule-ctrl-2",
                "repository_id": "repo-uuid-1",
                "repository_full_name": "org/core-api",
                "type": "DOCUMENTED_DECISION",
                "authority": "ENFORCEABLE",
                "statement": "Controllers must not directly access persistence layer.",
                "rationale": "Architectural boundary rule.",
                "scope": "src/*",
                "source_ref": "PR #88",
                "source_pr_number": 88,
                "status": "ACTIVE",
            },
            {
                "memory_id": "mem-exc-legacy-1",
                "repository_id": "repo-uuid-1",
                "repository_full_name": "org/core-api",
                "type": "APPROVED_EXCEPTION",
                "authority": "EXCEPTION",
                "statement": "LegacyDataMigrationController is approved to perform direct DB queries for migration bootstrapping.",
                "rationale": "Bootstrapping schema migrations require direct DB access; approved in PR #88.",
                "scope": "LegacyDataMigrationController*",
                "source_ref": "PR #88",
                "source_pr_number": 88,
                "status": "ACTIVE",
            },
        ]
    )
    mock_supabase.create_engineering_memory = AsyncMock(return_value={"id": "mem-new-2"})

    pr = GitHubPR(
        id=200,
        number=200,
        title="Hotfix for LegacyDataMigrationController",
        state="open",
        author="dev",
        head_sha="sha200",
        base_branch="main",
    )
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(pr=pr, files=[], commits=[], comments=[])
    )
    mock_code_analysis.analyze_diff.return_value = FileAnalysisResult(
        file_path="LegacyDataMigrationController.py",
        language="python",
        is_supported=True,
        parse_success=True,
    )

    # Hindsight contains approved exception
    exception_fact = "Approved Exception: PR #88 granted approved exception for direct DB queries in LegacyDataMigrationController."
    mock_memory.recall.return_value = RecallMemoryResponse(
        bank_id="org_core-api",
        query="migration",
        results_count=1,
        items=[MemoryItemSchema(text=exception_fact, score=0.92)],
        prompt_context=exception_fact,
    )

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Checking legacy controller queries.",
        drift_detected=True,
        exception_approved=False,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="warning",
                file_path="LegacyDataMigrationController.py",
                title="Direct DB query",
                message="Queries persistence directly.",
            )
        ],
    )

    result = await engine.review_pull_request("repo-uuid-1", 200)

    # Engine re-evaluates finding against recalled approved exception
    assert result.exception_approved is True
    assert result.findings[0].category == "approved_exception"
    assert result.findings[0].severity == "info"
    assert "Approved exception" in result.findings[0].message


@pytest.mark.asyncio
async def test_review_no_relevant_memory(mock_engine_deps):
    """Clean review when no historical rejection exists."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = mock_engine_deps

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-uuid-300", "pr_number": 300}
    )
    mock_supabase.create_review = AsyncMock(return_value={"id": "review-uuid-3"})
    mock_supabase.create_review_findings = AsyncMock(return_value=[])

    pr = GitHubPR(
        id=300,
        number=300,
        title="Add documentation",
        state="open",
        author="dev",
        head_sha="sha300",
        base_branch="main",
    )
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(pr=pr, files=[], commits=[], comments=[])
    )
    mock_memory.recall.return_value = RecallMemoryResponse(
        bank_id="org_core-api",
        query="docs",
        results_count=0,
        items=[],
        prompt_context="",
    )
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Clean documentation changes.",
        drift_detected=False,
        exception_approved=False,
        confidence_score=1.0,
        findings=[],
    )

    result = await engine.review_pull_request("repo-uuid-1", 300)

    assert result.drift_detected is False
    assert result.exception_approved is False
    assert len(result.findings) == 0


@pytest.mark.asyncio
async def test_review_github_failure(mock_engine_deps):
    """GitHub failure raises clean GitHubNotFoundError."""
    engine, mock_github, _, _, _, mock_supabase = mock_engine_deps

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_github.get_pr_details_full = AsyncMock(
        side_effect=GitHubNotFoundError("PR 999 not found on GitHub")
    )

    with pytest.raises(GitHubNotFoundError):
        await engine.review_pull_request("repo-uuid-1", 999)


@pytest.mark.asyncio
async def test_review_hindsight_failure_degrades_gracefully(mock_engine_deps):
    """Hindsight memory outage does not crash the review pipeline."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = mock_engine_deps

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-uuid-400", "pr_number": 400}
    )
    mock_supabase.create_review = AsyncMock(return_value={"id": "review-uuid-4"})
    mock_supabase.create_review_findings = AsyncMock(return_value=[])

    pr = GitHubPR(id=400, number=400, title="Feature", state="open", author="dev", head_sha="sha", base_branch="main")
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(pr=pr, files=[], commits=[], comments=[])
    )
    # Hindsight fails
    mock_memory.recall.side_effect = HindsightMemoryError("Hindsight API timeout")

    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Review completed without memory.",
        drift_detected=False,
        findings=[],
    )

    # Must complete review without throwing
    result = await engine.review_pull_request("repo-uuid-1", 400)
    assert result.summary == "Review completed without memory."


@pytest.mark.asyncio
async def test_review_groq_failure(mock_engine_deps):
    """Groq service failure raises GroqServiceError."""
    engine, mock_github, mock_code_analysis, mock_memory, mock_groq, mock_supabase = mock_engine_deps

    mock_supabase.get_repository_by_id = AsyncMock(
        return_value={"id": "repo-uuid-1", "full_name": "org/core-api"}
    )
    mock_supabase.upsert_pull_request = AsyncMock(
        return_value={"id": "pr-uuid-500", "pr_number": 500}
    )
    pr = GitHubPR(id=500, number=500, title="Feature", state="open", author="dev", head_sha="sha", base_branch="main")
    mock_github.get_pr_details_full = AsyncMock(
        return_value=PRDetailResponse(pr=pr, files=[], commits=[], comments=[])
    )
    mock_memory.recall.return_value = RecallMemoryResponse(
        bank_id="bank", query="q", results_count=0, items=[]
    )
    mock_groq.generate_review_analysis.side_effect = GroqServiceError("Groq service unavailable")

    with pytest.raises(GroqServiceError):
        await engine.review_pull_request("repo-uuid-1", 500)


def test_trigger_review_api_endpoint():
    """Test POST /api/v1/reviews/{repo_id}/{pr_number} API endpoint."""
    mock_supabase = get_supabase_service()
    mock_supabase._local_repos["test-repo-id"] = {
        "id": "test-repo-id",
        "full_name": "test-owner/test-repo",
        "default_branch": "main",
        "github_repo_id": 99999,
        "user_id": "00000000-0000-0000-0000-000000000000",
    }
    mock_engine = MagicMock(spec=ReviewEngine)
    mock_engine.review_pull_request = AsyncMock(
        return_value=ReviewAnalysisOutput(
            summary="API triggered review finished.",
            drift_detected=False,
            exception_approved=False,
            confidence_score=1.0,
            findings=[],
        )
    )

    app.dependency_overrides[get_review_engine] = lambda: mock_engine
    try:
        resp = client.post("/api/v1/reviews/test-repo-id/142")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "API triggered review finished."
        assert data["drift_detected"] is False
    finally:
        app.dependency_overrides.clear()
