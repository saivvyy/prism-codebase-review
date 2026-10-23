import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.bootstrap import BootstrapSourceRequest, BootstrapCandidate
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
    compute_memory_identity,
    validate_provenance,
)
from app.schemas.github import GitHubPR, GitHubPRListResponse, PRDetailResponse, GitHubFileDiff
from app.schemas.review import ReviewAnalysisOutput, ReviewFindingSchema
from app.services.bootstrap import HistoricalBootstrapService, get_repo_bank_id
from app.services.code_analysis import get_code_analysis_service
from app.services.github import GitHubService
from app.services.groq import GroqService, get_groq_service
from app.services.memory import HindsightMemoryService
from app.services.review_engine import ReviewEngine
from app.services.supabase import SupabaseService, get_supabase_service

client = TestClient(app)


# ==============================================================================
# Helpers & Mock Fixtures
# ==============================================================================

def create_mock_github(files_dict=None, prs_list=None, comments_dict=None, reviews_dict=None):
    """Creates a mock GitHubService returning specified file contents and PRs."""
    gh = MagicMock(spec=GitHubService)
    files = files_dict or {}
    prs = prs_list or []
    comments = comments_dict or {}
    reviews = reviews_dict or {}

    async def _get_file_contents(owner, repo, path, ref=None):
        return files.get(path.lstrip("/"))

    async def _list_directory_contents(owner, repo, path="", ref=None):
        clean_path = path.strip("/")
        results = []
        for p in files.keys():
            if clean_path and p.startswith(clean_path + "/"):
                sub = p[len(clean_path) + 1:]
                if "/" not in sub:
                    results.append({"name": sub, "path": p, "type": "file"})
            elif not clean_path and "/" not in p:
                results.append({"name": p, "path": p, "type": "file"})
        return results

    async def _list_pull_requests(owner, repo, state="all", per_page=100, fetch_limit=500):
        sliced = prs[:fetch_limit]
        return GitHubPRListResponse(
            items=sliced,
            total_fetched=len(sliced),
            pages_fetched=1,
            per_page=per_page,
            fetch_limit=fetch_limit,
            complete=True,
            truncated=False,
            fetch_duration_ms=1.0,
            last_synced_at="2026-09-30T00:00:00Z",
        )

    async def _get_pr_comments(owner, repo, pull_number):
        return comments.get(pull_number, [])

    async def _get_pr_reviews(owner, repo, pull_number):
        return reviews.get(pull_number, [])

    gh.get_file_contents = AsyncMock(side_effect=_get_file_contents)
    gh.list_directory_contents = AsyncMock(side_effect=_list_directory_contents)
    gh.list_pull_requests = AsyncMock(side_effect=_list_pull_requests)
    gh.get_pr_comments = AsyncMock(side_effect=_get_pr_comments)
    gh.get_pr_reviews = AsyncMock(side_effect=_get_pr_reviews)
    return gh


# ==============================================================================
# Test A: Empty repository → NO_PRECEDENT
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_empty_repository(isolated_supabase: SupabaseService):
    """Test A: Empty repository has no documents/PRs, produces 0 candidates and NO_PRECEDENT review."""
    repo_full_name = "org/empty-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1001,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    mock_gh = create_mock_github(files_dict={}, prs_list=[])
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    assert summary.candidates_extracted == 0
    assert summary.candidates_retained == 0
    assert len(summary.memories) == 0

    # Reviewing against empty repo yields NO_PRECEDENT
    engine = ReviewEngine(
        github=mock_gh,
        code_analysis=get_code_analysis_service(),
        memory=mock_mem,
        groq=get_groq_service(),
        supabase=isolated_supabase,
    )
    pr_detail = PRDetailResponse(
        pr=GitHubPR(
            id=10, number=1, title="Add feature", author="alice", head_sha="sha1",
            base_branch="main", state="open", created_at="2026-09-30T00:00:00Z",
        ),
        files=[GitHubFileDiff(filename="src/app.py", status="added", additions=5, deletions=0, changes=5, patch="+ def foo(): pass")],
        commits=[], comments=[],
    )
    mock_gh.get_pr_details_full = AsyncMock(return_value=pr_detail)

    res = await engine.review_pull_request(repo_id=repo_id, pr_number=1)
    assert res.verdict == ReviewVerdict.NO_PRECEDENT.value
    assert res.drift_detected is False


# ==============================================================================
# Test B: ADR containing explicit architecture rule → DOCUMENTED_DECISION
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_adr_explicit_rule(isolated_supabase: SupabaseService):
    """Test B: ADR produces DOCUMENTED_DECISION with authoritative ENFORCEABLE authority and provenance."""
    repo_full_name = "org/adr-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1002,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_content = (
        "# ADR-0001: Enforce Repository Layer For Database Access\n\n"
        "Status: Accepted\n\n"
        "## Context\n"
        "Direct SQL access in presentation controllers leads to maintainability issues and connection leaks.\n\n"
        "## Decision\n"
        "All controllers must not access database directly; SQL queries must flow exclusively through the repository layer.\n\n"
        "Scope: *Controller*\n"
    )

    mock_gh = create_mock_github(
        files_dict={"docs/adr/0001-repository-layer.md": adr_content},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    assert summary.candidates_extracted >= 1
    assert summary.candidates_retained >= 1

    decision = next((m for m in summary.memories if m.type == KnowledgeType.DOCUMENTED_DECISION), None)
    assert decision is not None
    assert decision.authority == MemoryAuthority.ENFORCEABLE
    assert decision.status == MemoryStatus.ACTIVE
    assert "ADR-0001" in decision.source_ref
    assert decision.can_enforce() is True
    assert decision.can_cause_drift() is True

    # Provenance must be real and valid
    valid, err = validate_provenance(decision.model_dump())
    assert valid is True
    assert err is None


# ==============================================================================
# Test C: CONTRIBUTING guideline → valid documented knowledge
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_contributing_guideline(isolated_supabase: SupabaseService):
    """Test C: CONTRIBUTING.md guideline produces valid documented knowledge."""
    repo_full_name = "org/guide-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1003,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    guide_content = (
        "# Contributing Guidelines\n\n"
        "## Architecture and Coding Rules\n"
        "- All services must validate input schemas before invoking external APIs.\n"
        "- Prefer using typed DTOs over raw dictionary payloads.\n"
    )

    mock_gh = create_mock_github(
        files_dict={"CONTRIBUTING.md": guide_content},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    assert summary.candidates_retained >= 1
    enforceable = next((m for m in summary.memories if m.type == KnowledgeType.DOCUMENTED_DECISION), None)
    assert enforceable is not None
    assert enforceable.authority == MemoryAuthority.ENFORCEABLE
    assert "CONTRIBUTING.md" in enforceable.source_ref

    convention = next((m for m in summary.memories if m.type == KnowledgeType.OBSERVED_CONVENTION), None)
    assert convention is not None
    assert convention.authority == MemoryAuthority.CONTEXTUAL


# ==============================================================================
# Test D: Explicit approved exception → APPROVED_EXCEPTION
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_explicit_approved_exception(isolated_supabase: SupabaseService):
    """Test D: Explicit approved exception creates APPROVED_EXCEPTION with EXCEPTION authority."""
    repo_full_name = "org/exception-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1004,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_with_exception = (
        "# ADR-0005: Microservice Data Access Boundaries\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Controllers must not execute direct database queries.\n\n"
        "## Exceptions\n"
        "- HealthCheckController is permitted to directly ping the database for liveness checks.\n"
    )

    mock_gh = create_mock_github(
        files_dict={"docs/adr/0005-boundaries.md": adr_with_exception},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    exc = next((m for m in summary.memories if m.type == KnowledgeType.APPROVED_EXCEPTION), None)
    assert exc is not None
    assert exc.authority == MemoryAuthority.EXCEPTION
    assert exc.status == MemoryStatus.ACTIVE
    assert exc.is_approved_exception() is True
    assert "HealthCheckController" in exc.statement


# ==============================================================================
# Test E: Explicit rejected pattern → REJECTED_PATTERN
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_explicit_rejected_pattern(isolated_supabase: SupabaseService):
    """Test E: Explicit rejected pattern creates REJECTED_PATTERN with REJECTED_PATTERN authority."""
    repo_full_name = "org/rejected-pattern-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1005,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_with_rejected = (
        "# ADR-0008: State Management Pattern\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Use Redux Toolkit for centralized application state.\n\n"
        "## Rejected Patterns\n"
        "- Rejected: Global mutable singletons for state management are prohibited.\n"
    )

    mock_gh = create_mock_github(
        files_dict={"docs/adr/0008-state.md": adr_with_rejected},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    rej = next((m for m in summary.memories if m.type == KnowledgeType.REJECTED_PATTERN), None)
    assert rej is not None
    assert rej.authority == MemoryAuthority.REJECTED_PATTERN
    assert rej.can_cause_drift() is True
    assert "Global mutable singletons" in rej.statement


# ==============================================================================
# Test F: Repeated implementation behavior only → OBSERVED_CONVENTION, NOT authoritative
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_inferred_behavior_never_authoritative(isolated_supabase: SupabaseService):
    """Test F: Repeated implementation frequency creates OBSERVED_CONVENTION, NOT authoritative."""
    repo_full_name = "org/stats-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1006,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    mock_gh = create_mock_github(files_dict={}, prs_list=[])
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    inferred_conventions = [
        {
            "statement": "190 out of 200 files implement a service layer abstraction.",
            "rationale": "Static frequency analysis across current repository branch.",
            "scope": "*Controller*",
            "source_ref": "Repository codebase frequency analysis",
        }
    ]

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
        req=BootstrapSourceRequest(inferred_conventions=inferred_conventions),
    )

    assert summary.candidates_retained == 1
    mem = summary.memories[0]
    assert mem.type == KnowledgeType.OBSERVED_CONVENTION
    assert mem.authority == MemoryAuthority.CONTEXTUAL
    assert mem.can_enforce() is False
    assert mem.can_cause_drift() is False


# ==============================================================================
# Test G: Same rule appearing in multiple sources → deduplicated/reinforced
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_deduplication_and_reinforcement(isolated_supabase: SupabaseService):
    """Test G: Same rule across multiple files is deduplicated and reinforces evidence."""
    repo_full_name = "org/dedup-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1007,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_content = (
        "# ADR-0002: Service Layer Requirement\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Controllers must not execute database queries directly.\n"
    )
    guide_content = (
        "# Guidelines\n\n"
        "## Architecture\n"
        "- Controllers must not execute database queries directly.\n"
    )

    mock_gh = create_mock_github(
        files_dict={
            "docs/adr/0002-service.md": adr_content,
            "docs/guidelines.md": guide_content,
        },
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    # Candidates extracted = 2, but only 1 retained, 1 deduplicated
    assert summary.candidates_extracted >= 2
    assert summary.candidates_retained == 1
    assert summary.candidates_deduplicated >= 1

    stored_memories = await isolated_supabase.list_engineering_memories(repo_id)
    assert len(stored_memories) == 1
    # Evidence items should contain both or reinforced
    ev = stored_memories[0].get("evidence_items") or []
    assert len(ev) >= 1


# ==============================================================================
# Test H: Conflicting authoritative documents → conflict represented
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_conflicting_authoritative_documents(isolated_supabase: SupabaseService):
    """Test H: Conflicting authoritative documents are both preserved without silent resolution."""
    repo_full_name = "org/conflict-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1008,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    doc_a = (
        "# Architecture Rule A\n\n"
        "## Rules\n"
        "- Controllers must not access database persistence directly.\n"
    )
    doc_b = (
        "# Architecture Rule B\n\n"
        "## Rules\n"
        "- Controllers must access database persistence directly for low latency.\n"
    )

    mock_gh = create_mock_github(
        files_dict={
            "docs/architecture.md": doc_a,
            "docs/design.md": doc_b,
        },
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    # Both documents must be retained (neither silently discarded)
    assert summary.candidates_retained == 2
    assert summary.conflicts_detected >= 1

    stored = await isolated_supabase.list_engineering_memories(repo_id)
    assert len(stored) == 2


# ==============================================================================
# Test I: Superseded decision → old decision no longer applicable
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_superseded_decision(isolated_supabase: SupabaseService):
    """Test I: Superseded decision transitions old memory to SUPERSEDED status."""
    repo_full_name = "org/supersede-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1009,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    # First store an initial ADR-0001
    await isolated_supabase.create_engineering_memory(
        repository_id=repo_id,
        statement="Use Monolithic Logger singleton for all application logging.",
        source_ref="ADR-0001",
        status="ACTIVE",
        type="DOCUMENTED_DECISION",
    )

    # Bootstrap with ADR-0002 which explicitly supersedes ADR-0001
    adr2 = (
        "# ADR-0002: Structured Structured Logging With OpenTelemetry\n\n"
        "Status: Accepted (supersedes ADR-0001)\n\n"
        "## Decision\n"
        "All components must use OpenTelemetry structured logging instead of Monolithic Logger.\n"
    )

    mock_gh = create_mock_github(
        files_dict={"docs/adr/0002-opentelemetry.md": adr2},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    assert summary.candidates_superseded >= 1

    all_memories = await isolated_supabase.list_engineering_memories(repo_id, status="all")
    adr1_mem = next((m for m in all_memories if m.get("source_ref") == "ADR-0001"), None)
    assert adr1_mem is not None
    assert adr1_mem["status"] == MemoryStatus.SUPERSEDED.value

    # Superseded memory cannot cause drift
    cm1 = CanonicalMemory(**adr1_mem)
    assert cm1.can_cause_drift() is False


# ==============================================================================
# Test J: Revoked decision → old decision no longer applicable
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_revoked_decision(isolated_supabase: SupabaseService):
    """Test J: Deprecated/revoked ADR is saved with REVOKED status and cannot enforce."""
    repo_full_name = "org/revoked-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1010,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    revoked_adr = (
        "# ADR-0003: XML Configuration Files\n\n"
        "Status: Deprecated\n\n"
        "## Decision\n"
        "All modules must specify dependencies in XML config format.\n"
    )

    mock_gh = create_mock_github(
        files_dict={"docs/adr/0003-xml.md": revoked_adr},
        prs_list=[],
    )
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    mem = summary.memories[0]
    assert mem.status == MemoryStatus.REVOKED
    assert mem.can_enforce() is False
    assert mem.can_cause_drift() is False


# ==============================================================================
# Test K: Missing/invalid provenance → candidate rejected
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_invalid_provenance_rejected(isolated_supabase: SupabaseService):
    """Test K: Missing/fabricated provenance candidates are rejected."""
    repo_full_name = "org/invalid-prov-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1011,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    mock_gh = create_mock_github(files_dict={}, prs_list=[])
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    # Fake source_ref "hindsight" or empty
    inferred = [
        {
            "statement": "All endpoints must use JWT tokens.",
            "source_ref": "hindsight",  # Fabricated source
        }
    ]

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
        req=BootstrapSourceRequest(inferred_conventions=inferred),
    )

    assert summary.candidates_rejected == 1
    assert summary.candidates_retained == 0
    assert len(summary.memories) == 0


# ==============================================================================
# Test L: Repository isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_repository_isolation(isolated_supabase: SupabaseService):
    """Test L: Repository A bootstrap never creates memory visible to Repository B."""
    repo_a = await isolated_supabase.connect_repository(github_repo_id=1012, full_name="org/repo-a")
    repo_b = await isolated_supabase.connect_repository(github_repo_id=1013, full_name="org/repo-b")

    adr_content = (
        "# ADR-0001: Private Architecture Rule For Repo A\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Repo A strictly requires GraphQL mutations.\n"
    )

    mock_gh_a = create_mock_github(files_dict={"docs/adr/0001-graphql.md": adr_content})
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh_a,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary_a = await svc.bootstrap_repository(
        repository_id=repo_a["id"],
        repository_full_name=repo_a["full_name"],
    )
    assert summary_a.candidates_retained == 1

    # Verify Repo B has zero memories
    memories_b = await isolated_supabase.list_engineering_memories(repo_b["id"])
    assert len(memories_b) == 0


# ==============================================================================
# Test M: Bootstrap is idempotent
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_idempotent_execution(isolated_supabase: SupabaseService):
    """Test M: Rerunning bootstrap twice against the same repository does not duplicate knowledge."""
    repo_full_name = "org/idempotent-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1014,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_content = (
        "# ADR-0001: Strict Schema Validation\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "All requests must be validated with Pydantic v2 schemas.\n"
    )

    mock_gh = create_mock_github(files_dict={"docs/adr/0001-schema.md": adr_content})
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    # First run
    run1 = await svc.bootstrap_repository(repository_id=repo_id, repository_full_name=repo_full_name)
    assert run1.candidates_retained == 1

    # Second run
    run2 = await svc.bootstrap_repository(repository_id=repo_id, repository_full_name=repo_full_name)
    assert run2.candidates_retained == 0
    assert run2.candidates_deduplicated == 1

    # Total in DB is still exactly 1
    total = await isolated_supabase.list_engineering_memories(repo_id)
    assert len(total) == 1


# ==============================================================================
# Test N: Hindsight unavailable → no fake memory
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_hindsight_unavailable(isolated_supabase: SupabaseService):
    """Test N: When Hindsight is unavailable, failure is exposed and no fake memory is created."""
    repo_full_name = "org/hindsight-unavail-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1015,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_content = (
        "# ADR-0001: Async IO Standard\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Network calls must use httpx AsyncClient.\n"
    )

    mock_gh = create_mock_github(files_dict={"docs/adr/0001-async.md": adr_content})
    # Unconfigured Hindsight
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
    )

    assert summary.hindsight_status == "unavailable"
    assert summary.hindsight_retained == 0
    assert "not configured" in summary.hindsight_error.lower()


# ==============================================================================
# Test O: Bounded historical processing
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_bounded_historical_processing(isolated_supabase: SupabaseService):
    """Test O: Historical PR processing enforces strict bounds and does not scan hundreds of PRs."""
    repo_full_name = "org/bounded-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1016,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    # Provide 100 historical PRs
    hundred_prs = [
        GitHubPR(
            id=i, number=i, title=f"PR #{i}", author="dev", head_sha=f"sha{i}",
            base_branch="main", state="merged", created_at="2026-09-30T00:00:00Z",
        )
        for i in range(1, 101)
    ]

    mock_gh = create_mock_github(files_dict={}, prs_list=hundred_prs)
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    req = BootstrapSourceRequest(include_prs=True, max_prs=15)
    summary = await svc.bootstrap_repository(
        repository_id=repo_id,
        repository_full_name=repo_full_name,
        req=req,
    )

    # list_pull_requests must have been called with fetch_limit <= 15
    mock_gh.list_pull_requests.assert_called_once()
    call_kwargs = mock_gh.list_pull_requests.call_args.kwargs
    assert call_kwargs.get("fetch_limit") == 15


# ==============================================================================
# Test P: Bootstrap-created memory can later be recalled by review engine
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_memory_recalled_by_review_engine(isolated_supabase: SupabaseService):
    """Test P: Memory created via bootstrap is recalled by ReviewEngine and enforces DRIFT/CONFORMING."""
    repo_full_name = "org/recall-integration-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1017,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_content = (
        "# ADR-0010: Database Access In Controllers\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Controllers must not execute database queries directly.\n\n"
        "Scope: *Controller*\n"
    )

    mock_gh = create_mock_github(files_dict={"docs/adr/0010-db.md": adr_content})
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    # 1. Bootstrap repository
    bootstrap_res = await svc.bootstrap_repository(repo_id, repo_full_name)
    assert bootstrap_res.candidates_retained >= 1

    # 2. Execute ReviewEngine with a PR modifying PaymentController.ts
    violating_diff = (
        "@@ -1,4 +1,7 @@\n"
        "+ import db from '../db'\n"
        "  export class PaymentController {\n"
        "+   pay() { db.query('SELECT 1'); }\n"
        "  }\n"
    )
    pr_violating = PRDetailResponse(
        pr=GitHubPR(
            id=501, number=50, title="Add direct query in PaymentController",
            author="dev", head_sha="sha50", base_branch="main", state="open",
            created_at="2026-09-30T00:00:00Z",
        ),
        files=[GitHubFileDiff(
            filename="src/controllers/PaymentController.ts",
            status="modified", additions=3, deletions=0, changes=3,
            patch=violating_diff,
        )],
        commits=[], comments=[],
    )
    mock_gh.get_pr_details_full = AsyncMock(return_value=pr_violating)

    mock_groq = MagicMock(spec=GroqService)
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Architecture Drift Detected: direct database query in controller.",
        drift_detected=True,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="warning",
                file_path="src/controllers/PaymentController.ts",
                title="Direct DB query in PaymentController",
                message="Controllers must not execute database queries directly per ADR-0010.",
                historical_pr_ref="ADR-0010",
            )
        ],
    )

    engine = ReviewEngine(
        github=mock_gh,
        code_analysis=get_code_analysis_service(),
        memory=mock_mem,
        groq=mock_groq,
        supabase=isolated_supabase,
    )

    review_res = await engine.review_pull_request(repo_id=repo_id, pr_number=50)

    # The bootstrapped memory was fetched, matched scope, and caught the violation!
    assert review_res.verdict == ReviewVerdict.DRIFT.value
    assert review_res.drift_detected is True
    assert any("ADR-0010" in (f.historical_pr_ref or f.message) for f in review_res.findings)


# ==============================================================================
# Test Q: HTTP API Endpoints for Bootstrap
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_http_api_endpoints():
    """Test Q: POST /repositories/{repo_id}/bootstrap and memory alias."""
    supabase = get_supabase_service()
    repo = await supabase.connect_repository(
        github_repo_id=1018,
        full_name="org/http-bootstrap-repo",
    )
    repo_id = repo["id"]

    adr_content = (
        "# ADR-0001: Standard Result Envelopes\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "All API responses must wrap response payloads in a Standard Result Envelope.\n"
    )

    mock_gh = create_mock_github(files_dict={"docs/adr/0001-envelope.md": adr_content})
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    from app.services.github import get_github_service
    from app.services.bootstrap import get_bootstrap_service
    app.dependency_overrides[get_github_service] = lambda: mock_gh
    app.dependency_overrides[get_bootstrap_service] = lambda: HistoricalBootstrapService(
        github=mock_gh,
        supabase=supabase,
        memory=mock_mem,
    )

    try:
        # 1. POST /repositories/{repo_id}/bootstrap
        res1 = client.post(f"/api/v1/repositories/{repo_id}/bootstrap", json={})
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["candidates_retained"] >= 1
        assert data1["repository_id"] == repo_id

        # 2. Duplicate run via /memory/bootstrap alias → deduplicated
        res2 = client.post(f"/api/v1/repositories/{repo_id}/memory/bootstrap", json={})
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["candidates_retained"] == 0
        assert data2["candidates_deduplicated"] >= 1
    finally:
        app.dependency_overrides.pop(get_github_service, None)
        app.dependency_overrides.pop(get_bootstrap_service, None)


# ==============================================================================
# Test R: Approved Exception Wrong Scope → General Rule Enforced (DRIFT)
# ==============================================================================

@pytest.mark.asyncio
async def test_bootstrap_exception_wrong_scope_yields_drift(isolated_supabase: SupabaseService):
    """Test R: When an approved exception exists for HealthCheckController, a PR modifying
    PaymentController cannot claim exception and triggers DRIFT under the general rule."""
    repo_full_name = "org/scope-exception-repo"
    repo_record = await isolated_supabase.connect_repository(
        github_repo_id=1019,
        full_name=repo_full_name,
        default_branch="main",
    )
    repo_id = repo_record["id"]

    adr_with_scoped_exception = (
        "# ADR-0020: Service Boundaries\n\n"
        "Status: Accepted\n\n"
        "## Decision\n"
        "Controllers must not execute database queries directly.\n\n"
        "Scope: *Controller*\n\n"
        "## Exceptions\n"
        "- HealthCheckController is permitted to directly ping the database for liveness checks.\n"
    )

    mock_gh = create_mock_github(files_dict={"docs/adr/0020-boundary.md": adr_with_scoped_exception})
    mock_mem = HindsightMemoryService(base_url=None, api_key=None)

    svc = HistoricalBootstrapService(
        github=mock_gh,
        supabase=isolated_supabase,
        memory=mock_mem,
    )

    # 1. Bootstrap repository: stores general rule (*Controller*) and approved exception (HealthCheckController)
    summary = await svc.bootstrap_repository(repo_id, repo_full_name)
    assert summary.candidates_retained >= 2

    # Verify both general rule and exception exist
    all_memories = await isolated_supabase.list_engineering_memories(repo_id, status="all")
    general_rule = next((m for m in all_memories if m.get("type") == "DOCUMENTED_DECISION"), None)
    exc_rule = next((m for m in all_memories if m.get("type") == "APPROVED_EXCEPTION"), None)
    assert general_rule is not None
    assert exc_rule is not None
    # Adjust exception scope specifically to HealthCheck
    await isolated_supabase.update_engineering_memory(
        exc_rule["id"],
        {"scope": "*HealthCheck*"},
    )

    # 2. Case 1: PR touches HealthCheckController.ts -> APPROVED_EXCEPTION verdict
    hc_diff = (
        "@@ -1,3 +1,5 @@\n"
        "+ import db from '../db'\n"
        "  export class HealthCheckController {\n"
        "+   ping() { db.query('SELECT 1'); }\n"
        "  }\n"
    )
    pr_hc = PRDetailResponse(
        pr=GitHubPR(
            id=51, number=51, title="HealthCheck db ping",
            author="dev", head_sha="sha51", base_branch="main", state="open",
            created_at="2026-09-30T00:00:00Z",
        ),
        files=[GitHubFileDiff(
            filename="src/controllers/HealthCheckController.ts",
            status="modified", additions=2, deletions=0, changes=2,
            patch=hc_diff,
        )],
        commits=[], comments=[],
    )
    mock_gh.get_pr_details_full = AsyncMock(return_value=pr_hc)

    mock_groq = MagicMock(spec=GroqService)
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Architecture Drift Detected: direct database query in controller.",
        drift_detected=True,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="warning",
                file_path="src/controllers/HealthCheckController.ts",
                title="Direct DB query in controller",
                message="Controllers must not execute database queries directly per ADR-0020.",
                historical_pr_ref="ADR-0020",
            )
        ],
    )

    engine = ReviewEngine(
        github=mock_gh,
        code_analysis=get_code_analysis_service(),
        memory=mock_mem,
        groq=mock_groq,
        supabase=isolated_supabase,
    )

    res_hc = await engine.review_pull_request(repo_id=repo_id, pr_number=51)
    assert res_hc.verdict == ReviewVerdict.APPROVED_EXCEPTION.value
    assert res_hc.exception_approved is True
    assert res_hc.drift_detected is False

    # 3. Case 2: PR touches PaymentController.ts -> WRONG SCOPE -> DRIFT verdict (NOT APPROVED_EXCEPTION)
    payment_diff = (
        "@@ -1,3 +1,5 @@\n"
        "+ import db from '../db'\n"
        "  export class PaymentController {\n"
        "+   pay() { db.query('SELECT 1'); }\n"
        "  }\n"
    )
    pr_payment = PRDetailResponse(
        pr=GitHubPR(
            id=52, number=52, title="Payment db query",
            author="dev", head_sha="sha52", base_branch="main", state="open",
            created_at="2026-09-30T00:00:00Z",
        ),
        files=[GitHubFileDiff(
            filename="src/controllers/PaymentController.ts",
            status="modified", additions=2, deletions=0, changes=2,
            patch=payment_diff,
        )],
        commits=[], comments=[],
    )
    mock_gh.get_pr_details_full = AsyncMock(return_value=pr_payment)
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Architecture Drift Detected: direct database query in controller.",
        drift_detected=True,
        findings=[
            ReviewFindingSchema(
                category="architecture_drift",
                severity="warning",
                file_path="src/controllers/PaymentController.ts",
                title="Direct DB query in controller",
                message="Controllers must not execute database queries directly per ADR-0020.",
                historical_pr_ref="ADR-0020",
            )
        ],
    )

    res_payment = await engine.review_pull_request(repo_id=repo_id, pr_number=52)
    assert res_payment.verdict == ReviewVerdict.DRIFT.value
    assert res_payment.drift_detected is True
    assert res_payment.exception_approved is False

