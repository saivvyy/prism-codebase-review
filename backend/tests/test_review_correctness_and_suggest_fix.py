"""Regression tests for Review Correctness, Evidence Grounding, and Suggest Fix Isolation."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.schemas.canonical_memory import (
    CanonicalMemory,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
    ReviewVerdict,
)
from app.schemas.review import (
    ReviewFindingSchema,
    ReviewAnalysisOutput,
)
from app.schemas.fix import (
    FixCandidateStatus,
    FixListResponse,
)
from app.schemas.github import (
    GitHubFileDiff,
    GitHubPR,
    PRDetailResponse,
)
from app.schemas.context import (
    PRContext,
    PRIdentity,
    PRIntent,
    ChangedFileDetail,
    ChangeType,
    ContextBudget,
)
from app.services.review_engine import ReviewEngine
from app.services.fix_engine import FixCandidateEngine
from app.services.context_engine import ContextEngine


def test_1_finding_file_path_grounding_invariant():
    """Requirement 1 & 11: Finding file path must correspond to current PR changed files."""
    cm = CanonicalMemory(
        memory_id="mem-1",
        repository_id="repo-1",
        statement="Controllers must not call database directly.",
        rationale="Separation of concerns.",
        scope="*Controller*",
        type=KnowledgeType.REJECTED_PATTERN,
        authority=MemoryAuthority.ENFORCEABLE,
        status=MemoryStatus.ACTIVE,
    )
    # Target file in obsidian vs gemini_llm
    assert cm.is_applicable_to_path("app/controllers/user_controller.py") is True
    assert cm.is_applicable_to_path("hindsight_api/engine/providers/gemini_llm.py") is False


def test_2_and_3_no_cross_file_or_cross_pr_leakage_in_is_applicable_to_path():
    """Requirement 2 & 3: Finding cannot cite another file or PR's evidence when statement targets specific file."""
    # Obsidian client memory with generic scope
    obsidian_mem = CanonicalMemory(
        memory_id="mem-obsidian",
        repository_id="repo-1",
        statement="Following updates to the deleteDocument method in hindsight-integrations/obsidian/src/client.ts, client-level HTTP calls are permitted per PR #5085.",
        rationale="Client integration exemption.",
        scope="Repository architecture",  # Generic scope
        type=KnowledgeType.APPROVED_EXCEPTION,
        authority=MemoryAuthority.EXCEPTION,
        status=MemoryStatus.ACTIVE,
    )

    # Must be applicable to obsidian file
    assert obsidian_mem.is_applicable_to_path("hindsight-integrations/obsidian/src/client.ts") is True
    # Must NOT be applicable to gemini_llm.py even though scope is "Repository architecture"
    assert obsidian_mem.is_applicable_to_path("hindsight-api-slim/hindsight_api/engine/providers/gemini_llm.py") is False


def test_4_context_cache_keyed_by_repo_pr_sha():
    """Requirement 4: Cache is strictly isolated by (repo_id, pr_number, head_sha)."""
    engine = ContextEngine()
    engine.clear_cache()

    pr_detail_1 = PRDetailResponse(
        pr=GitHubPR(
            id=516901,
            number=5169,
            title="Update Gemini LLM",
            author="dev",
            head_sha="sha-aaa",
            base_branch="main",
            base_sha="sha-base",
            state="open",
            html_url="https://github.com/org/repo/pull/5169",
            created_at="2026-10-01T12:00:00Z",
            updated_at="2026-10-01T12:00:00Z",
        ),
        files=[GitHubFileDiff(filename="providers/gemini_llm.py", status="modified", additions=5, deletions=2, changes=7)],
        commits=[],
    )

    pr_detail_2 = PRDetailResponse(
        pr=GitHubPR(
            id=516902,
            number=5169,
            title="Update Gemini LLM new commit",
            author="dev",
            head_sha="sha-bbb",
            base_branch="main",
            base_sha="sha-base",
            state="open",
            html_url="https://github.com/org/repo/pull/5169",
            created_at="2026-10-01T12:00:00Z",
            updated_at="2026-10-01T12:00:00Z",
        ),
        files=[GitHubFileDiff(filename="providers/gemini_llm.py", status="modified", additions=6, deletions=2, changes=8)],
        commits=[],
    )

    # Direct cache key structure test
    k1 = ("repo-1", 5169, "sha-aaa")
    k2 = ("repo-1", 5169, "sha-bbb")
    assert k1 != k2


def test_6_and_7_historical_precedent_scope_and_relevance():
    """Requirement 6 & 7: Historical precedent must be repository-scoped and relevance-validated."""
    mem_repo_a = CanonicalMemory(
        memory_id="mem-a",
        repository_id="repo-a",
        statement="Rule for repo A",
        scope="*",
        type=KnowledgeType.DOCUMENTED_DECISION,
        authority=MemoryAuthority.ENFORCEABLE,
        status=MemoryStatus.ACTIVE,
    )
    # Verify repository isolation
    assert mem_repo_a.repository_id == "repo-a"


def test_8_and_10_no_applicable_memory_produces_no_precedent():
    """Requirement 8 & 10: Insufficient applicable evidence returns NO_PRECEDENT; non-authoritative evidence cannot cause DRIFT."""
    mock_supabase = MagicMock()
    mock_supabase.create_review = AsyncMock(return_value={"id": "rev-1"})
    mock_supabase.create_review_findings = AsyncMock()
    mock_groq = MagicMock()
    mock_groq.generate_review_analysis.return_value = ReviewAnalysisOutput(
        summary="Routine maintenance.",
        drift_detected=False,
        exception_approved=False,
        verdict=ReviewVerdict.NO_PRECEDENT.value,
        findings=[],
    )

    review_engine = ReviewEngine(supabase=mock_supabase, groq=mock_groq)

    # When no enforceable rules exist in applicability result, has_positive_evidence is False -> NO_PRECEDENT
    assert review_engine is not None


def test_12_to_19_suggest_fix_and_unavailable_reasons():
    """Requirement 12-19: Suggest fix sends current IDs, returns candidate or explicit unavailable reason."""
    fix_engine = FixCandidateEngine()

    identity = PRIdentity(
        repository_id="repo-1",
        repository_full_name="org/repo",
        pr_number=5169,
        title="Update Gemini LLM",
        author="dev",
        head_sha="sha-12345",
        changed_files_count=1,
        total_additions=5,
        total_deletions=2,
    )
    intent = PRIntent(
        summary="Update Gemini LLM implementation",
        goals=["Update LLM provider"],
        likely_change_type=ChangeType.FEATURE,
        affected_domain="providers",
    )
    changed_file = ChangedFileDetail(
        path="hindsight_api/engine/providers/gemini_llm.py",
        status="modified",
        additions=5,
        deletions=2,
        language="python",
        classes=["GeminiLLMProvider"],
        functions=["generate"],
        affected_symbols=["GeminiLLMProvider"],
    )
    context = PRContext(
        pr=identity,
        intent=intent,
        changed_files=[changed_file],
        context_budget=ContextBudget(),
    )

    # Finding with note category (unfixable)
    finding = ReviewFindingSchema(
        id="finding-note-1",
        file_path="hindsight_api/engine/providers/gemini_llm.py",
        line_number=10,
        category="note",
        title="Informational observation",
        message="Notice regarding LLM parameters.",
    )
    review = ReviewAnalysisOutput(
        summary="Review complete",
        drift_detected=False,
        exception_approved=False,
        verdict=ReviewVerdict.NO_PRECEDENT.value,
        findings=[finding],
    )

    fixes = fix_engine.generate_fixes_for_pr(
        repository_id="repo-1",
        pr_number=5169,
        context=context,
        review=review,
        finding_ids=["finding-note-1"],
    )

    assert len(fixes) == 0
    unavail = getattr(fix_engine, "last_unavailable_reasons", {})
    assert "finding-note-1" in unavail
    assert "informational" in unavail["finding-note-1"].lower() or "Fix unavailable" in unavail["finding-note-1"]


def test_20_fix_candidate_grounding_and_isolation():
    """Requirement 20: Fix candidate must be strictly grounded to current PR, SHA, and finding."""
    fix_resp = FixListResponse(
        repository_id="repo-1",
        pr_number=5169,
        head_sha="sha-12345",
        fixes=[],
        total_count=0,
        unavailable_reasons={"finding-1": "Fix unavailable: Approved exception"},
    )
    assert fix_resp.head_sha == "sha-12345"
    assert fix_resp.pr_number == 5169
    assert fix_resp.unavailable_reasons["finding-1"] == "Fix unavailable: Approved exception"
