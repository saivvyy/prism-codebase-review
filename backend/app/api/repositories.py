from datetime import datetime, timezone
import logging
from typing import List, Optional
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

logger = logging.getLogger(__name__)

from app.api.auth import extract_session_token, get_optional_current_user
from app.core.exceptions import (
    ConfigurationError,
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from app.schemas.auth import UserSessionProfile
from app.schemas.github import (
    ConnectedRepo,
    ConnectRepoRequest,
    GitHubPR,
    GitHubPRListResponse,
    PRDetailResponse,
)
from app.schemas.bootstrap import BootstrapSourceRequest, BootstrapSummaryResponse
from app.services.auth import AuthService, get_auth_service
from app.services.bootstrap import HistoricalBootstrapService, get_bootstrap_service
from app.services.github import GitHubService, get_github_service
from app.services.supabase import SupabaseService, get_supabase_service

router = APIRouter(prefix="/repositories", tags=["Repositories"])


def handle_service_error(e: Exception, action: str):
    msg = str(e).strip()
    while msg.endswith(":"):
        msg = msg[:-1].strip()
    if not msg:
        msg = f"Failed while {action}: {type(e).__name__}"

    if isinstance(e, ConfigurationError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg)
    elif isinstance(e, GitHubAuthError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)
    elif isinstance(e, GitHubRateLimitError):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)
    elif isinstance(e, GitHubNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    elif isinstance(e, GitHubError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error while {action}: {msg}",
        )


@router.post("/connect", response_model=ConnectedRepo, status_code=status.HTTP_201_CREATED)
async def connect_repository(
    req: ConnectRepoRequest,
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    github: GitHubService = Depends(get_github_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> ConnectedRepo:
    """Connects a single GitHub repository and records it in persistent state.

    Scoped strictly to the authenticated user's workspace.
    """
    raw = req.full_name.strip()
    if "github.com/" in raw:
        raw = raw.split("github.com/")[-1]
    raw = raw.strip("/").removesuffix(".git")
    parts = raw.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository name must be in 'owner/repo' format (e.g. 'octocat/Hello-World').",
        )
    owner, repo_name = parts[0].strip(), parts[1].strip()

    # Determine user-scoped GitHub service: if user is logged in, use their OAuth token for permissions check
    user_token = None
    if session_token:
        user_token = auth.get_github_access_token(session_token)

    effective_gh = GitHubService(token=user_token) if user_token else github

    try:
        # Verify repository exists and authenticated user has permission to access it
        gh_repo = await effective_gh.get_repository(owner, repo_name)
    except GitHubAuthError as auth_err:
        handle_service_error(auth_err, f"verifying repository '{req.full_name}'")
    except Exception as e:
        handle_service_error(e, f"verifying repository '{req.full_name}'")

    if req.github_repo_id and gh_repo.id != req.github_repo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"GitHub repository ID mismatch: expected {req.github_repo_id}, found {gh_repo.id}.",
        )

    user_id = current_user.id if current_user else None

    try:
        # Save to Supabase repository state scoped to user
        record = await supabase.connect_repository(
            github_repo_id=gh_repo.id,
            full_name=gh_repo.full_name,
            default_branch=gh_repo.default_branch,
            user_id=user_id,
        )
        return ConnectedRepo(
            id=record["id"],
            full_name=record["full_name"],
            github_repo_id=record["github_repo_id"],
            default_branch=record["default_branch"],
            webhook_active=record.get("webhook_active", False),
            created_at=record.get("created_at"),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist repository: {e}",
        )


@router.get("", response_model=List[ConnectedRepo])
async def list_repositories(
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> List[ConnectedRepo]:
    """Lists all connected repositories scoped to the authenticated user."""
    records = await supabase.list_repositories(user_id=current_user.id if current_user else None)
    return [
        ConnectedRepo(
            id=r["id"],
            full_name=r["full_name"],
            github_repo_id=r["github_repo_id"],
            default_branch=r["default_branch"],
            webhook_active=r.get("webhook_active", False),
            created_at=r.get("created_at"),
        )
        for r in records
    ]


@router.get("/{repo_id}", response_model=ConnectedRepo)
async def get_repository(
    repo_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> ConnectedRepo:
    """Retrieves a single connected repository scoped to the authenticated user."""
    try:
        uuid.UUID(repo_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid repository ID format: '{repo_id}'",
        )
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found in connected repositories.",
        )
    return ConnectedRepo(
        id=repo["id"],
        full_name=repo["full_name"],
        github_repo_id=repo["github_repo_id"],
        default_branch=repo["default_branch"],
        webhook_active=repo.get("webhook_active", False),
        created_at=repo.get("created_at"),
    )


async def _sync_prs_to_supabase(
    supabase: SupabaseService, repo_id: str, prs: List[GitHubPR]
) -> None:
    """Background task to sync PR metadata into Supabase without blocking the API response."""
    try:
        prs_dicts = [p.model_dump() for p in prs]
        await supabase.upsert_pull_requests_batch(repo_id, prs_dicts)
    except Exception as e:
        logger.debug("Failed background PR sync: %s", e)


@router.get("/{repo_id}/pulls", response_model=GitHubPRListResponse)
async def list_repository_pull_requests(
    repo_id: str,
    background_tasks: BackgroundTasks,
    state: str = "all",
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    github: GitHubService = Depends(get_github_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> GitHubPRListResponse:
    """Lists pull requests for a connected repository, returns explicit fetch metrics, and syncs state in the background."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found in connected repositories.",
        )

    user_token = auth.get_github_access_token(session_token) if session_token else None
    effective_gh = GitHubService(token=user_token) if user_token else github

    owner, repo_name = repo["full_name"].split("/")
    try:
        res = await effective_gh.list_pull_requests(owner, repo_name, state=state)
    except GitHubAuthError as auth_err:
        if user_token and github and github.is_configured:
            logger.warning(
                "User OAuth token invalid/expired (%s); falling back to system GitHub token for %s",
                auth_err,
                repo["full_name"],
            )
            try:
                res = await github.list_pull_requests(owner, repo_name, state=state)
                effective_gh = github
            except Exception as e:
                handle_service_error(e, f"fetching pull requests for '{repo['full_name']}'")
        else:
            handle_service_error(auth_err, f"fetching pull requests for '{repo['full_name']}'")
    except Exception as e:
        handle_service_error(e, f"fetching pull requests for '{repo['full_name']}'")

    if isinstance(res, GitHubPRListResponse):
        response_data = res
        prs = res.items
    else:
        prs = list(res)
        response_data = GitHubPRListResponse(
            items=prs,
            total_fetched=len(prs),
            pages_fetched=1,
            per_page=100,
            fetch_limit=500,
            complete=True,
            truncated=False,
            fetch_duration_ms=0.0,
            last_synced_at=datetime.now(timezone.utc).isoformat(),
        )

    # Deterministic deduplication by canonical PR number at backend boundary
    seen_pr_numbers = set()
    deduped_prs: List[GitHubPR] = []
    for pr in prs:
        if pr.number not in seen_pr_numbers:
            seen_pr_numbers.add(pr.number)
            deduped_prs.append(pr)
    prs = deduped_prs
    response_data.items = deduped_prs
    response_data.total_fetched = len(deduped_prs)

    try:
        open_count = await effective_gh.get_repository_open_pr_count(owner, repo_name)
        response_data.open_pr_count = open_count
        response_data.total_repository_prs = open_count
    except Exception:
        pass

    # Sync into Supabase pull_requests table asynchronously
    background_tasks.add_task(_sync_prs_to_supabase, supabase, repo_id, prs)

    return response_data


@router.get("/{repo_id}/pr-count")
async def get_repository_pr_count(
    repo_id: str,
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    github: GitHubService = Depends(get_github_service),
    supabase: SupabaseService = Depends(get_supabase_service),
):
    """Retrieves the actual open GitHub PR count for a connected repository without fetching all PRs."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found in connected repositories.",
        )
    owner, repo_name = repo["full_name"].split("/")
    user_token = auth.get_github_access_token(session_token) if session_token else None
    effective_gh = GitHubService(token=user_token) if user_token else github
    try:
        count = await effective_gh.get_repository_open_pr_count(owner, repo_name)
    except GitHubAuthError as auth_err:
        if user_token and github and github.is_configured:
            try:
                count = await github.get_repository_open_pr_count(owner, repo_name)
            except Exception as e:
                handle_service_error(e, f"fetching PR count for '{repo['full_name']}'")
        else:
            handle_service_error(auth_err, f"fetching PR count for '{repo['full_name']}'")
    except Exception as e:
        handle_service_error(e, f"fetching PR count for '{repo['full_name']}'")
    return {"repo_id": repo_id, "pr_count": count, "open_pr_count": count}


@router.get("/{repo_id}/open-pr-count")
async def get_repository_open_pr_count(
    repo_id: str,
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    github: GitHubService = Depends(get_github_service),
    supabase: SupabaseService = Depends(get_supabase_service),
):
    """Retrieves the dedicated repository OPEN PR count."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found in connected repositories.",
        )
    owner, repo_name = repo["full_name"].split("/")
    user_token = auth.get_github_access_token(session_token) if session_token else None
    effective_gh = GitHubService(token=user_token) if user_token else github
    try:
        count = await effective_gh.get_repository_open_pr_count(owner, repo_name)
    except GitHubAuthError as auth_err:
        if user_token and github and github.is_configured:
            try:
                count = await github.get_repository_open_pr_count(owner, repo_name)
            except Exception as e:
                handle_service_error(e, f"fetching open PR count for '{repo['full_name']}'")
        else:
            handle_service_error(auth_err, f"fetching open PR count for '{repo['full_name']}'")
    except Exception as e:
        handle_service_error(e, f"fetching open PR count for '{repo['full_name']}'")
    return {"repo_id": repo_id, "open_pr_count": count, "pr_count": count}


@router.get("/{repo_id}/pulls/{pr_number}", response_model=PRDetailResponse)
async def get_pull_request_detail(
    repo_id: str,
    pr_number: int,
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    github: GitHubService = Depends(get_github_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> PRDetailResponse:
    """Fetches full PR details including changed files, commits, and comments."""
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found in connected repositories.",
        )

    user_token = auth.get_github_access_token(session_token) if session_token else None
    effective_gh = GitHubService(token=user_token) if user_token else github

    owner, repo_name = repo["full_name"].split("/")
    try:
        detail = await effective_gh.get_pr_details_full(owner, repo_name, pr_number)
    except GitHubAuthError as auth_err:
        if user_token and github and github.is_configured:
            logger.warning("User OAuth token invalid/expired; falling back to system token for PR #%s", pr_number)
            try:
                detail = await github.get_pr_details_full(owner, repo_name, pr_number)
            except Exception as e:
                handle_service_error(e, f"fetching details for PR #{pr_number}")
        else:
            handle_service_error(auth_err, f"fetching details for PR #{pr_number}")
    except GitHubNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pull request #{pr_number} was not found on GitHub in '{repo['full_name']}'.",
        )
    except Exception as e:
        handle_service_error(e, f"fetching details for PR #{pr_number}")

    # Upsert PR record
    try:
        await supabase.upsert_pull_request(
            repository_id=repo_id,
            pr_number=detail.pr.number,
            title=detail.pr.title,
            author=detail.pr.author,
            head_sha=detail.pr.head_sha,
            base_branch=detail.pr.base_branch,
            status=detail.pr.state,
        )
    except Exception:
        pass

    return detail


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repository(
    repo_id: str,
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    supabase: SupabaseService = Depends(get_supabase_service),
):
    """Disconnects and removes a repository from monitoring, scoped to authenticated user."""
    try:
        uuid.UUID(repo_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid repository ID format: '{repo_id}'",
        )
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to disconnect repositories.",
        )
    try:
        deleted = await supabase.delete_repository(repo_id, user_id=current_user.id)
    except Exception as e:
        logger.error("Database failure while deleting repository %s: %s", repo_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure while disconnecting repository: {e}",
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{repo_id}/bootstrap",
    response_model=BootstrapSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def bootstrap_repository(
    repo_id: str,
    req: Optional[BootstrapSourceRequest] = None,
    session_token: Optional[str] = Depends(extract_session_token),
    current_user: Optional[UserSessionProfile] = Depends(get_optional_current_user),
    auth: AuthService = Depends(get_auth_service),
    supabase: SupabaseService = Depends(get_supabase_service),
    bootstrap: HistoricalBootstrapService = Depends(get_bootstrap_service),
) -> BootstrapSummaryResponse:
    """Discovers, parses, classifies, deduplicates, and retains repository architectural memory

    Evidence-first, bounded, repository-scoped, deduplicated, and provenance-backed.
    """
    user_id = current_user.id if current_user else None
    repo = await supabase.get_repository_by_id(repo_id, user_id=user_id)
    if not repo and "/" in repo_id:
        repo = await supabase.get_repository_by_name(repo_id, user_id=user_id)
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found or access denied.",
        )

    user_token = auth.get_github_access_token(session_token) if session_token else None
    if user_token:
        bootstrap.github = GitHubService(token=user_token)

    try:
        summary = await bootstrap.bootstrap_repository(
            repository_id=repo["id"],
            repository_full_name=repo["full_name"],
            req=req,
        )
        return summary
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error("Repository bootstrap failed for %s: %s", repo_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Repository bootstrap failed: {e}",
        )

