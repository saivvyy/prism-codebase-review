import uuid
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from app.main import app
from app.services.supabase import get_supabase_service
from app.services.memory import get_hindsight_service, HindsightMemoryService
from app.services.review_engine import ReviewEngine
from app.services.code_analysis import get_code_analysis_service
from app.schemas.github import GitHubFileDiff, GitHubPR, PRDetailResponse
from app.schemas.review import (
    ReviewAnalysisOutput,
    ReviewFindingSchema,
    DurableMemoryCandidateSchema,
)
from app.core.exceptions import HindsightMemoryError

client = TestClient(app)


@pytest.mark.asyncio
async def test_review_with_durable_knowledge_retains():
    """A. Review with durable knowledge -> retain called."""
    supabase = get_supabase_service()
    mock_memory = MagicMock(spec=HindsightMemoryService)
    mock_memory.is_configured = True
    mock_memory.arecall = AsyncMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.aretain = AsyncMock(return_value=MagicMock(success=True, items_count=1))
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True, items_count=1))

    repo_name = f"test-org/repo-durable-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=9901, full_name=repo_name)

    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Review passed with durable convention guidance.",
        drift_detected=False,
        exception_approved=False,
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Treat memory as a past record and allow teams to configure their own guidance.",
                type="architecture_decision",
                rationale="Teams require autonomy to define repository architectural rules.",
                scope="Memory system",
                confidence="supported",
            )
        ],
    )

    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
        pr=GitHubPR(
            id=4922,
            number=4922,
            title="treat memory as a past record, and let teams add their own guidance",
            body="Architectural update to memory guidance.",
            state="open",
            html_url=f"https://github.com/{repo_name}/pull/4922",
            author="engineer",
            head_sha="sha4922",
            base_branch="main",
            head_branch="feature/memory-guidance",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[GitHubFileDiff(filename="src/memory.ts", status="modified", additions=10, deletions=2, patch="@@ -1,3 +1,5 @@")],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=get_code_analysis_service(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=supabase,
    )

    output = await engine.review_pull_request(repo_id=repo["id"], pr_number=4922)
    assert output is not None

    # Verify retain was called with correct bank and content
    expected_bank = repo_name.replace("/", "_").lower()
    mock_memory.retain.assert_called_once()
    call_kwargs = mock_memory.retain.call_args.kwargs
    assert call_kwargs["bank_id"] == expected_bank
    assert "Treat memory as a past record" in call_kwargs["content"]
    assert "architecture_decision" in call_kwargs["tags"]


@pytest.mark.asyncio
async def test_approved_review_with_durable_knowledge_retains():
    """B. Approved review with durable knowledge -> retain called."""
    supabase = get_supabase_service()
    mock_memory = MagicMock(spec=HindsightMemoryService)
    mock_memory.is_configured = True
    mock_memory.arecall = AsyncMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True, items_count=1))

    repo_name = f"test-org/repo-approved-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=9902, full_name=repo_name)

    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="PR approved. Adheres to architectural conventions.",
        drift_detected=False,
        exception_approved=False,
        confidence_score=0.98,
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="All public endpoints must specify response_model types.",
                type="architecture_decision",
                rationale="Ensures schema validation and API docs integrity.",
                scope="src/api/*",
                confidence="supported",
            )
        ],
    )

    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
        pr=GitHubPR(
            id=101,
            number=101,
            title="Add response_model to public routes",
            body="Convention enforcement",
            state="open",
            html_url=f"https://github.com/{repo_name}/pull/101",
            author="dev",
            head_sha="sha101",
            base_branch="main",
            head_branch="feature/routes",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[GitHubFileDiff(filename="src/api/routes.py", status="modified", additions=5, deletions=1, patch="@@ -1,2 +1,4 @@")],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=get_code_analysis_service(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=supabase,
    )

    output = await engine.review_pull_request(repo_id=repo["id"], pr_number=101)
    assert output.drift_detected is False
    # Retain must be called even though review was approved
    mock_memory.retain.assert_called_once()
    assert "response_model" in mock_memory.retain.call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_review_with_no_durable_knowledge_skips_retention():
    """C. Review with no durable knowledge -> no memory retained."""
    supabase = get_supabase_service()
    mock_memory = MagicMock(spec=HindsightMemoryService)
    mock_memory.is_configured = True
    mock_memory.arecall = AsyncMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.retain = MagicMock(return_value=MagicMock(success=True, items_count=1))

    repo_name = f"test-org/repo-nodurable-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=9903, full_name=repo_name)

    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Typo fix in documentation. No architectural modifications.",
        drift_detected=False,
        exception_approved=False,
        findings=[],
        durable_memory_candidates=[],  # No durable knowledge
    )

    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
        pr=GitHubPR(
            id=55,
            number=55,
            title="Fix typo in README.md",
            body="Fixed spelling of 'environment'",
            state="open",
            html_url=f"https://github.com/{repo_name}/pull/55",
            author="contributor",
            head_sha="sha55",
            base_branch="main",
            head_branch="patch-1",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[GitHubFileDiff(filename="README.md", status="modified", additions=1, deletions=1, patch="@@ -1 +1 @@")],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=get_code_analysis_service(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=supabase,
    )

    output = await engine.review_pull_request(repo_id=repo["id"], pr_number=55)
    assert output is not None
    # Verify retain was NEVER called
    mock_memory.retain.assert_not_called()


@pytest.mark.asyncio
async def test_retain_failure_raises_and_is_not_swallowed():
    """D. Retain failure -> visible failure, never silently ignored."""
    supabase = get_supabase_service()
    mock_memory = MagicMock(spec=HindsightMemoryService)
    mock_memory.is_configured = True
    mock_memory.arecall = AsyncMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.recall = MagicMock(return_value=MagicMock(items=[], prompt_context=""))
    mock_memory.retain = MagicMock(side_effect=HindsightMemoryError("Hindsight bank connection refused"))

    repo_name = f"test-org/repo-fail-{uuid.uuid4().hex[:6]}"
    repo = await supabase.connect_repository(github_repo_id=9904, full_name=repo_name)

    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Review completed with decision.",
        drift_detected=False,
        exception_approved=False,
        findings=[],
        durable_memory_candidates=[
            DurableMemoryCandidateSchema(
                claim="Mandatory transaction isolation level READ COMMITTED.",
                type="architecture_decision",
                rationale="Prevent dirty reads across financial ledger entries.",
                scope="src/ledger/*",
                confidence="supported",
            )
        ],
    )

    mock_github = MagicMock()
    mock_github.get_pr_details_full = AsyncMock(return_value=PRDetailResponse(
        pr=GitHubPR(
            id=77,
            number=77,
            title="Enforce isolation level on ledger",
            body="Config update",
            state="open",
            html_url=f"https://github.com/{repo_name}/pull/77",
            author="architect",
            head_sha="sha77",
            base_branch="main",
            head_branch="feature/ledger",
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
        ),
        files=[GitHubFileDiff(filename="src/ledger/db.py", status="modified", additions=2, deletions=0, patch="@@ -1 +1,3 @@")],
    ))

    engine = ReviewEngine(
        github=mock_github,
        code_analysis=get_code_analysis_service(),
        memory=mock_memory,
        groq=mock_groq,
        supabase=supabase,
    )

    with pytest.raises(HindsightMemoryError) as exc_info:
        await engine.review_pull_request(repo_id=repo["id"], pr_number=77)

    assert "Hindsight" in str(exc_info.value)


@pytest.mark.asyncio
async def test_retain_and_recall_same_bank_and_isolation():
    """E & F. Retain + recall against same bank and repository isolation."""
    memory_svc = get_hindsight_service()
    bank_1 = f"org_repo_alpha_{uuid.uuid4().hex[:6]}"
    bank_2 = f"org_repo_beta_{uuid.uuid4().hex[:6]}"

    # Retain memory into bank 1
    content_1 = "Architecture Decision: In repo Alpha, service classes must use dependency injection."
    await memory_svc.aretain(
        content=content_1,
        bank_id=bank_1,
        tags=["architecture_decision"],
    )

    # Recall bank 1
    recall_1 = await memory_svc.arecall(query="dependency injection service", bank_id=bank_1)
    assert recall_1.results_count >= 1
    assert any("dependency injection" in it.text.lower() for it in recall_1.items)

    # Recall bank 2 (must be empty -> isolation verified!)
    recall_2 = await memory_svc.arecall(query="dependency injection service", bank_id=bank_2)
    assert recall_2.results_count == 0


@pytest.mark.asyncio
async def test_memory_api_recall_endpoint():
    """H & I. Memory API endpoint GET /api/v1/memory/recall?repo_id=<id> returns actual memory."""
    supabase = get_supabase_service()
    repo_name = f"vectorize-io/hindsight"
    repo = await supabase.connect_repository(github_repo_id=9905, full_name=repo_name)
    repo_id = repo["id"]

    res = client.get(f"/api/v1/memory/recall?repo_id={repo_id}")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "bank_id" in data
    assert data["bank_id"] == "vectorize-io_hindsight"
    assert data["results_count"] >= 1
    # Verify items have the fields required by the frontend
    first_item = data["items"][0]
    assert "text" in first_item
    assert "metadata" in first_item
