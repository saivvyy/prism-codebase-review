import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)

from app.api.auth import get_optional_current_user
from app.core.exceptions import (
    ConfigurationError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GroqAuthError,
    GroqRateLimitError,
    GroqServiceError,
    HindsightMemoryError,
)
from app.schemas.auth import UserSessionProfile
from app.schemas.review import ReviewAnalysisOutput
from app.schemas.walkthrough import PRWalkthrough
from app.schemas.context import PREvidenceResponse, PREvidenceSummary
from app.schemas.fix import (
    FixCandidate,
    FixListResponse,
    GenerateFixesRequest,
    FixCandidateStatus,
    FixValidationStatus,
)
from app.schemas.canonical_memory import (
    LearningCandidate,
    DeveloperFeedbackRequest,
    ConfirmLearningCandidateRequest,
    RejectLearningCandidateRequest,
)
from app.services.review_engine import ReviewEngine, get_review_engine
from app.services.supabase import SupabaseService, get_supabase_service
from app.services.context_engine import ContextEngine, get_context_engine
from app.services.walkthrough_engine import WalkthroughEngine, get_walkthrough_engine
from app.services.fix_engine import FixCandidateEngine, get_fix_candidate_engine
from app.services.learning import ControlledLearningEngine, get_learning_engine

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("/recent", response_model=List[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def list_recent_reviews(
    limit: int = 15,
    repo_id: Optional[str] = Query(None, description="Scope reviews to specific repository ID"),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> List[Dict[str, Any]]:
    """Retrieves recent reviews for dashboard metrics, scoped to repository and user."""
    user_id = current_user.id if current_user else None
    if repo_id:
        repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
        if not repo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{repo_id}' not found or access denied.",
            )
        return await supabase.list_recent_reviews(limit=limit, repository_id=repo_id)

    # When repo_id is omitted, return only reviews belonging to repositories owned by current user
    user_repos = await supabase.list_repositories(user_id=user_id)
    if not user_repos:
        return []
    user_repo_ids = [r["id"] for r in user_repos]
    return await supabase.list_recent_reviews(limit=limit, repository_ids=user_repo_ids)


@router.get("/{repo_id}/{pr_number}/history", response_model=List[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def get_pr_review_history(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> List[Dict[str, Any]]:
    """Fetches all historical review generations for a PR, verifying repository ownership."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    return await supabase.list_reviews_for_pr(repo_id, pr_number)


@router.get("/{repo_id}/{pr_number}/evidence", response_model=PREvidenceResponse, status_code=status.HTTP_200_OK)
async def get_pull_request_evidence(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> PREvidenceResponse:
    """
    Returns deep PR evidence & history (Phase 10): commits, conversation discussions,
    review threads, CI checks, and relevant historical PR relationships.

    Evidence is retrieved read-only from GitHub, bounded, deduplicated, and cached
    strictly by (repo_id, pr_number, head_sha). Evidence is NOT architectural authority.
    """
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", None)
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository full name unavailable.",
        )

    try:
        if "/" in full_name:
            owner, repo_name = full_name.split("/", 1)
        else:
            owner, repo_name = "unknown", full_name

        pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
        context = await context_engine.build_pr_context(
            repository_id=repo_id,
            repository_full_name=full_name,
            pr_detail=pr_detail,
            use_cache=True,
        )


        return PREvidenceResponse(
            repository_id=repo_id,
            repository_full_name=full_name,
            pr_number=pr_number,
            head_sha=context.pr.head_sha,
            commits=context.commits,
            conversation=context.conversation,
            checks=context.checks,
            historical_prs=context.historical_prs,
            summary=context.evidence_summary or PREvidenceSummary(
                total_commits=len(context.commits),
                total_conversation_items=len(context.conversation),
                total_checks=len(context.checks),
                total_historical_prs=len(context.historical_prs),
            ),
        )

    except GitHubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (GitHubAuthError,) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except (GitHubRateLimitError,) as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        logger.exception("Evidence retrieval failed for %s PR#%s", repo_id, pr_number)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evidence retrieval failed: {e}",
        )


@router.get("/{repo_id}/{pr_number}/walkthrough", response_model=PRWalkthrough, status_code=status.HTTP_200_OK)
async def get_pr_walkthrough(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    walkthrough_engine: WalkthroughEngine = Depends(get_walkthrough_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> PRWalkthrough:
    """
    Returns the semantic PR walkthrough derived from the Phase 5 context engine.

    The walkthrough groups changed files by architectural purpose (API, schema, tests, etc.),
    orders them narratively, and estimates cross-cutting impact.

    No new AI inference is performed — all grouping is deterministic from the existing
    structural analysis already produced during PR review.
    """
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", None)
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository full name unavailable.",
        )

    try:
        # Fetch PR details from GitHub
        if "/" in full_name:
            owner, repo_name = full_name.split("/", 1)
        else:
            owner, repo_name = "unknown", full_name

        pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
        context = await context_engine.build_pr_context(
            repository_id=repo_id,
            repository_full_name=full_name,
            pr_detail=pr_detail,
            use_cache=True,
        )

        # Fetch the latest review (if any) for finding overlap counts
        latest_review_dict = await supabase.get_latest_review_for_pr(repo_id, pr_number)
        review: Optional[ReviewAnalysisOutput] = None
        if latest_review_dict:
            try:
                findings_raw = latest_review_dict.get("findings") or []
                from app.schemas.review import ReviewFindingSchema
                parsed_findings = [ReviewFindingSchema(**f) if isinstance(f, dict) else f for f in findings_raw]
                review = ReviewAnalysisOutput(
                    summary=latest_review_dict.get("summary", ""),
                    drift_detected=latest_review_dict.get("drift_detected", False),
                    exception_approved=latest_review_dict.get("exception_approved", False),
                    verdict=latest_review_dict.get("verdict", "NO_PRECEDENT"),
                    findings=parsed_findings,
                )
            except Exception:
                pass  # Review overlay is best-effort; walkthrough still works without it

        return walkthrough_engine.build_walkthrough(context=context, review=review)

    except GitHubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (GitHubAuthError,) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except (GitHubRateLimitError,) as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        logger.exception("Walkthrough engine failed for %s PR#%s", repo_id, pr_number)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Walkthrough engine failed: {e}",
        )


@router.get("/{repo_id}/{pr_number}", response_model=Optional[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def get_pr_review(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> Optional[Dict[str, Any]]:
    """Fetches the latest review with findings for a PR if available, verifying repository ownership."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    review = await supabase.get_latest_review_for_pr(repo_id, pr_number)
    return review


@router.post("/{repo_id}/{pr_number}", response_model=ReviewAnalysisOutput, status_code=status.HTTP_200_OK)
async def trigger_pull_request_review(
    repo_id: str,
    pr_number: int,
    use_cache: bool = Query(True, description="Whether to reuse cached review for matching SHA"),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    engine: ReviewEngine = Depends(get_review_engine),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> ReviewAnalysisOutput:
    """Triggers an Engineering Memory review for a repository pull request.
    Orchestrates: GitHub PR -> Tree-sitter AST -> Hindsight -> Groq -> Supabase.
    """
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )
    try:
        review_result = await engine.review_pull_request(repo_id, pr_number, user_id=user_id, use_cache=use_cache)
        return review_result
    except GitHubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (GitHubAuthError, GroqAuthError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except (GitHubRateLimitError, GroqRateLimitError) as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except (GroqServiceError, HindsightMemoryError) as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Review engine failed: {e}",
        )


# ---------------------------------------------------------------------------
# Phase 8: Context-Aware Fix Endpoints
# ---------------------------------------------------------------------------

@router.post("/{repo_id}/{pr_number}/fixes/generate", response_model=FixListResponse, status_code=status.HTTP_200_OK)
async def generate_pr_fixes(
    repo_id: str,
    pr_number: int,
    request: Optional[GenerateFixesRequest] = None,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    walkthrough_engine: WalkthroughEngine = Depends(get_walkthrough_engine),
    fix_engine: FixCandidateEngine = Depends(get_fix_candidate_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> FixListResponse:
    """Generates context-aware fix candidates for PR review findings."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", None)
    if not full_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Repository full name unavailable.")

    owner, repo_name = full_name.split("/", 1) if "/" in full_name else ("unknown", full_name)

    # 1. Fetch PR details
    try:
        pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
    except GitHubNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 2. Build or fetch context
    context = await context_engine.build_pr_context(
        repository_id=repo_id,
        repository_full_name=full_name,
        pr_detail=pr_detail,
        use_cache=True,
    )

    # 3. Fetch latest review
    latest_review_dict = await supabase.get_latest_review_for_pr(repo_id, pr_number)
    if not latest_review_dict or not latest_review_dict.get("findings"):
        return FixListResponse(
            repository_id=repo_id,
            pr_number=pr_number,
            head_sha=getattr(context.pr, "head_sha", ""),
            fixes=[],
            total_count=0,
            can_apply=False,
        )

    from app.schemas.review import ReviewFindingSchema
    parsed_findings = []
    for f in (latest_review_dict.get("findings") or []):
        parsed = ReviewFindingSchema(**f) if isinstance(f, dict) else f
        if not getattr(parsed, "id", None):
            line_no = getattr(parsed, "line_number", None) or 0
            parsed.id = f"{parsed.file_path}:{line_no}"
        parsed_findings.append(parsed)
    review = ReviewAnalysisOutput(
        summary=latest_review_dict.get("summary", ""),
        drift_detected=latest_review_dict.get("drift_detected", False),
        exception_approved=latest_review_dict.get("exception_approved", False),
        verdict=latest_review_dict.get("verdict", "NO_PRECEDENT"),
        findings=parsed_findings,
    )

    # 4. Optional walkthrough
    walkthrough = walkthrough_engine.build_walkthrough(context=context, review=review)

    # 5. Generate candidates
    finding_ids = request.get_effective_finding_ids() if request else None
    candidates = fix_engine.generate_fixes_for_pr(
        repository_id=repo_id,
        pr_number=pr_number,
        context=context,
        review=review,
        walkthrough=walkthrough,
        finding_ids=finding_ids,
        force_regenerate=request.force_regenerate if request else False,
    )

    # 6. Persist candidates
    for c in candidates:
        await supabase.save_fix_candidate(c.model_dump())

    can_apply = any(c.status == FixCandidateStatus.APPROVED and c.base_sha == context.pr.head_sha for c in candidates)
    unavailable_reasons = getattr(candidates, "unavailable_reasons", getattr(fix_engine, "last_unavailable_reasons", {})) or {}
    return FixListResponse(
        repository_id=repo_id,
        pr_number=pr_number,
        head_sha=context.pr.head_sha,
        fixes=candidates,
        total_count=len(candidates),
        can_apply=can_apply,
        unavailable_reasons=unavailable_reasons,
    )


@router.get("/{repo_id}/{pr_number}/fixes", response_model=FixListResponse, status_code=status.HTTP_200_OK)
async def list_pr_fixes(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> FixListResponse:
    """Lists all generated fix candidates for a PR with live SHA freshness status."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", None)
    owner, repo_name = full_name.split("/", 1) if full_name and "/" in full_name else ("unknown", full_name or repo_id)

    head_sha = "unknown"
    try:
        pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
        head_sha = pr_detail.pr.head_sha
    except Exception:
        pass

    raw_fixes = await supabase.list_fix_candidates_for_pr(repo_id, pr_number)
    fixes = [FixCandidate(**f) for f in raw_fixes]
    can_apply = any(f.status == FixCandidateStatus.APPROVED and f.base_sha == head_sha for f in fixes)

    return FixListResponse(
        repository_id=repo_id,
        pr_number=pr_number,
        head_sha=head_sha,
        fixes=fixes,
        total_count=len(fixes),
        can_apply=can_apply,
    )


@router.post("/{repo_id}/{pr_number}/fixes/{fix_id}/validate", response_model=FixCandidate, status_code=status.HTTP_200_OK)
async def validate_pr_fix(
    repo_id: str,
    pr_number: int,
    fix_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    fix_engine: FixCandidateEngine = Depends(get_fix_candidate_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> FixCandidate:
    """Re-runs validation checks on a fix candidate against current repository HEAD."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository '{repo_id}' not found.")

    raw_fix = await supabase.get_fix_candidate(fix_id)
    if not raw_fix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fix candidate '{fix_id}' not found.")

    candidate = FixCandidate(**raw_fix)
    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", repo_id)
    owner, repo_name = full_name.split("/", 1) if "/" in full_name else ("unknown", full_name)

    pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
    context = await context_engine.build_pr_context(
        repository_id=repo_id,
        repository_full_name=full_name,
        pr_detail=pr_detail,
        use_cache=True,
    )

    validated = fix_engine.validate_fix(candidate, context)
    await supabase.save_fix_candidate(validated.model_dump())
    return validated


@router.post("/{repo_id}/{pr_number}/fixes/{fix_id}/approve", response_model=FixCandidate, status_code=status.HTTP_200_OK)
async def approve_pr_fix(
    repo_id: str,
    pr_number: int,
    fix_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    fix_engine: FixCandidateEngine = Depends(get_fix_candidate_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> FixCandidate:
    """Explicit human approval for a candidate fix."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository '{repo_id}' not found.")

    raw_fix = await supabase.get_fix_candidate(fix_id)
    if not raw_fix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fix candidate '{fix_id}' not found.")

    candidate = FixCandidate(**raw_fix)
    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", repo_id)
    owner, repo_name = full_name.split("/", 1) if "/" in full_name else ("unknown", full_name)

    pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
    context = await context_engine.build_pr_context(
        repository_id=repo_id,
        repository_full_name=full_name,
        pr_detail=pr_detail,
        use_cache=True,
    )

    try:
        approved = fix_engine.approve_fix(candidate, context)
        await supabase.save_fix_candidate(approved.model_dump())
        return approved
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{repo_id}/{pr_number}/fixes/{fix_id}/apply", response_model=FixCandidate, status_code=status.HTTP_200_OK)
async def apply_pr_fix(
    repo_id: str,
    pr_number: int,
    fix_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    context_engine: ContextEngine = Depends(get_context_engine),
    fix_engine: FixCandidateEngine = Depends(get_fix_candidate_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
) -> FixCandidate:
    """Applies an approved fix candidate to the PR context workspace and performs post-apply validation."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository '{repo_id}' not found.")

    raw_fix = await supabase.get_fix_candidate(fix_id)
    if not raw_fix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fix candidate '{fix_id}' not found.")

    candidate = FixCandidate(**raw_fix)
    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", repo_id)
    owner, repo_name = full_name.split("/", 1) if "/" in full_name else ("unknown", full_name)

    pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
    context = await context_engine.build_pr_context(
        repository_id=repo_id,
        repository_full_name=full_name,
        pr_detail=pr_detail,
        use_cache=True,
    )

    try:
        applied_candidate = fix_engine.apply_fix(candidate, context)
        await supabase.save_fix_candidate(applied_candidate.model_dump())
        return applied_candidate
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{repo_id}/{pr_number}/fixes/{fix_id}/reject", response_model=FixCandidate, status_code=status.HTTP_200_OK)
async def reject_pr_fix(
    repo_id: str,
    pr_number: int,
    fix_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    fix_engine: FixCandidateEngine = Depends(get_fix_candidate_engine),
) -> FixCandidate:
    """Explicitly marks a fix candidate as rejected."""
    raw_fix = await supabase.get_fix_candidate(fix_id)
    if not raw_fix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fix candidate '{fix_id}' not found.")

    candidate = FixCandidate(**raw_fix)
    rejected = fix_engine.reject_fix(candidate)
    await supabase.save_fix_candidate(rejected.model_dump())
    return rejected


# =========================================================================
# PHASE 9: DEVELOPER FEEDBACK & CONTROLLED LEARNING ENDPOINTS
# =========================================================================

@router.post(
    "/{repo_id}/{pr_number}/feedback",
    response_model=LearningCandidate,
    status_code=status.HTTP_201_CREATED,
)
async def submit_pr_developer_feedback(
    repo_id: str,
    pr_number: int,
    feedback: DeveloperFeedbackRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    learning_engine: ControlledLearningEngine = Depends(get_learning_engine),
    context_engine: ContextEngine = Depends(get_context_engine),
    review_engine: ReviewEngine = Depends(get_review_engine),
    walkthrough_engine: WalkthroughEngine = Depends(get_walkthrough_engine),
) -> LearningCandidate:
    """Submits developer feedback on a PR review finding, creating a validated LearningCandidate.
    Developer feedback is a LEARNING SIGNAL, NOT automatic authoritative memory.
    Strictly verifies repository access and scopes evidence to this repository.
    """
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    full_name = repo.get("full_name") if isinstance(repo, dict) else getattr(repo, "full_name", repo_id)
    owner, repo_name = full_name.split("/", 1) if "/" in full_name else ("unknown", full_name)

    # 1. Fetch review data and PR context if possible
    review = await supabase.get_latest_review_for_pr(repo_id, pr_number)

    pr_context = None
    walkthrough = None
    try:
        pr_detail = await review_engine.github.get_pr_details_full(owner, repo_name, pr_number)
        pr_context = await context_engine.build_pr_context(
            repository_id=repo_id,
            repository_full_name=full_name,
            pr_detail=pr_detail,
            use_cache=True,
        )
        if review:
            try:
                walkthrough = walkthrough_engine.generate_walkthrough(pr_context, review)
            except Exception:
                pass
    except Exception as e:
        logger.debug("Optional context enrichment failed in feedback submission: %s", e)

    try:
        candidate = await learning_engine.process_developer_feedback(
            repo=repo,
            pr_number=pr_number,
            feedback=feedback,
            pr_context=pr_context,
            review_data=review,
            walkthrough_data=walkthrough,
        )
        return candidate
    except ConfigurationError as e:
        logger.warning("Configuration error in feedback: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        logger.warning("Validation error in feedback: %s", e)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error processing developer feedback: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PRism encountered an error while processing developer feedback.")


@router.get(
    "/{repo_id}/{pr_number}/learning-candidates",
    response_model=List[LearningCandidate],
    status_code=status.HTTP_200_OK,
)
async def list_pr_learning_candidates(
    repo_id: str,
    pr_number: int,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> List[LearningCandidate]:
    """Lists all learning candidates proposed for a PR."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    raw_candidates = await supabase.list_learning_candidates_for_pr(repo_id, pr_number)
    return [LearningCandidate(**c) for c in raw_candidates]


@router.post(
    "/{repo_id}/{pr_number}/learning-candidates/{candidate_id}/validate",
    response_model=LearningCandidate,
    status_code=status.HTTP_200_OK,
)
async def validate_pr_learning_candidate(
    repo_id: str,
    pr_number: int,
    candidate_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    learning_engine: ControlledLearningEngine = Depends(get_learning_engine),
) -> LearningCandidate:
    """Validates a learning candidate for scope, provenance, authority, and conflict detection."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    raw_candidate = await supabase.get_learning_candidate(candidate_id)
    if not raw_candidate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Learning candidate '{candidate_id}' not found.",
        )

    try:
        candidate = LearningCandidate(**raw_candidate)
        validated = await learning_engine.validate_candidate_full(candidate, repo_id)
        await supabase.save_learning_candidate(validated.model_dump())
        return validated
    except ConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error validating candidate: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PRism encountered an error while validating this candidate.")


@router.post(
    "/{repo_id}/{pr_number}/learning-candidates/{candidate_id}/confirm",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def confirm_pr_learning_candidate(
    repo_id: str,
    pr_number: int,
    candidate_id: str,
    confirmation: ConfirmLearningCandidateRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    learning_engine: ControlledLearningEngine = Depends(get_learning_engine),
) -> Dict[str, Any]:
    """Human confirmation gate: persists validated candidate to canonical Supabase memory and Hindsight."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    try:
        result = await learning_engine.confirm_candidate(
            candidate_id=candidate_id,
            expected_repo_id=repo_id,
            confirmation=confirmation,
        )
        # Format response
        return {
            "status": result.get("status"),
            "candidate": result["candidate"].model_dump() if hasattr(result.get("candidate"), "model_dump") else result.get("candidate"),
            "canonical_memory": result["canonical_memory"].model_dump() if hasattr(result.get("canonical_memory"), "model_dump") else result.get("canonical_memory"),
            "superseded_memory": result["superseded_memory"].model_dump() if hasattr(result.get("superseded_memory"), "model_dump") and result.get("superseded_memory") else None,
            "hindsight_sync_status": result.get("hindsight_sync_status"),
        }
    except ConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except HindsightMemoryError as e:
        logger.error("Hindsight memory error during candidate confirmation: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Hindsight semantic memory sync failed: {e}")
    except Exception as e:
        logger.exception("Unexpected error confirming candidate %s: %s", candidate_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PRism encountered an error while confirming this candidate.")


@router.post(
    "/{repo_id}/{pr_number}/learning-candidates/{candidate_id}/reject",
    response_model=LearningCandidate,
    status_code=status.HTTP_200_OK,
)
async def reject_pr_learning_candidate(
    repo_id: str,
    pr_number: int,
    candidate_id: str,
    rejection: RejectLearningCandidateRequest,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
    learning_engine: ControlledLearningEngine = Depends(get_learning_engine),
) -> LearningCandidate:
    """Explicit developer rejection: marks candidate REJECTED without persisting memory."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    try:
        rejected = await learning_engine.reject_candidate(
            candidate_id=candidate_id,
            expected_repo_id=repo_id,
            rejection=rejection,
        )
        return rejected
    except ConfigurationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error rejecting candidate %s: %s", candidate_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PRism encountered an error while rejecting this candidate.")



