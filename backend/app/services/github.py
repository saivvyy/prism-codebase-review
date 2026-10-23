import asyncio
from datetime import datetime, timezone
import logging
import re
import time
import os
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    ConfigurationError,
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from app.schemas.context import (
    CheckEvidence,
    CommitEvidence,
    ConversationEvidence,
    HistoricalPREvidence,
    PREvidenceSummary,
)
from app.schemas.github import (
    GitHubComment,
    GitHubCommit,
    GitHubFileDiff,
    GitHubPR,
    GitHubPRListResponse,
    GitHubRepo,
    GitHubUser,
    PRDetailResponse,
)

logger = logging.getLogger(__name__)

# Bounded production limits for Phase 10 deep PR evidence
MAX_COMMITS = 50
MAX_CONVERSATION_ITEMS = 100
MAX_REVIEW_COMMENTS = 100
MAX_CHECKS = 100
MAX_HISTORICAL_PRS = 20


_UNSET = object()


def _format_error_cause(e: Exception) -> str:
    """Formats an exception into a descriptive, non-empty cause string with no trailing colons."""
    if isinstance(e, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return f"Request timed out ({type(e).__name__})"
    if isinstance(e, httpx.TimeoutException):
        return "Request timed out"
    if isinstance(e, httpx.ConnectError):
        return f"Network connection failed ({type(e).__name__})"
    if isinstance(e, httpx.HTTPStatusError):
        return f"GitHub returned HTTP {e.response.status_code}"
    cause = str(e).strip()
    while cause.endswith(":"):
        cause = cause[:-1].strip()
    if not cause:
        return type(e).__name__
    return cause


class GitHubService:
    """Service for interacting with GitHub REST API."""

    BASE_URL = "https://api.github.com"

    # Shared in-flight deduplication map and 60s TTL cache for PR details
    _pr_details_in_flight: Dict[str, asyncio.Future] = {}
    _pr_details_cache: Dict[str, Tuple[PRDetailResponse, float]] = {}
    _CACHE_TTL_SECONDS: float = 60.0

    def __init__(self, token: Any = _UNSET):
        settings = get_settings()
        self.token = settings.GITHUB_TOKEN if token is _UNSET else token

    @property
    def is_configured(self) -> bool:
        """Returns True if a GitHub token is configured."""
        return bool(self.token)

    def _get_headers(self, accept: str = "application/vnd.github.v3+json", require_auth: bool = True) -> Dict[str, str]:
        if require_auth and not self.is_configured:
            raise ConfigurationError("GitHub token is not configured. Set GITHUB_TOKEN in environment.")
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "EngineeringMemory-Agent",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


    def _handle_response_error(self, response: httpx.Response, context: str) -> None:
        status = response.status_code
        headers = getattr(response, "headers", None) or {}
        if status in (401, 403) and "rate limit" not in response.text.lower() and headers.get("x-ratelimit-remaining") != "0":
            if "Bad credentials" in response.text or status == 401:
                raise GitHubAuthError(f"GitHub authentication failed during {context}: Invalid or expired token.")
        is_rate_limit = (
            status == 429
            or "rate limit" in response.text.lower()
            or headers.get("x-ratelimit-remaining") == "0"
        )
        if status in (403, 429) and is_rate_limit:
            reset_time = headers.get("x-ratelimit-reset")
            msg = f"GitHub API rate limit exceeded during {context}."
            if reset_time:
                msg += f" Reset at epoch {reset_time}."
            raise GitHubRateLimitError(msg)
        if status == 404:
            raise GitHubNotFoundError(f"Requested resource not found on GitHub during {context}.")
        if response.is_error:
            raise GitHubError(f"GitHub API error ({status}) during {context}: {response.text}")

    async def get_authenticated_user(self) -> GitHubUser:
        """Fetches the currently authenticated GitHub user."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/user",
                    headers=self._get_headers(),
                    timeout=15.0,
                )
                self._handle_response_error(res, "get_authenticated_user")
                data = res.json()
                return GitHubUser(
                    login=data["login"],
                    id=data["id"],
                    name=data.get("name"),
                    avatar_url=data.get("avatar_url"),
                    html_url=data.get("html_url"),
                )
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during get_authenticated_user: %s", e)
                raise GitHubError(f"Failed to reach GitHub API: {e}") from e

    async def list_user_repositories(
        self,
        page: int = 1,
        per_page: int = 30,
        affiliation: str = "owner,collaborator,organization_member",
    ) -> List[GitHubRepo]:
        """Lists repositories accessible to the authenticated user (owned, collaborated, org-member)."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/user/repos",
                    headers=self._get_headers(),
                    params={
                        "page": page,
                        "per_page": per_page,
                        "sort": "updated",
                        "affiliation": affiliation,
                    },
                    timeout=15.0,
                )
                self._handle_response_error(res, "list_user_repositories")
                repos = []
                for item in res.json():
                    owner_data = item.get("owner", {})
                    owner_login = owner_data.get("login") if isinstance(owner_data, dict) else (item.get("owner") or "")
                    repos.append(
                        GitHubRepo(
                            id=item["id"],
                            full_name=item["full_name"],
                            name=item["name"],
                            owner=owner_login,
                            owner_avatar_url=owner_data.get("avatar_url") if isinstance(owner_data, dict) else None,
                            owner_type=owner_data.get("type") if isinstance(owner_data, dict) else None,
                            default_branch=item.get("default_branch", "main"),
                            private=item.get("private", False),
                            description=item.get("description"),
                            html_url=item.get("html_url"),
                            stargazers_count=item.get("stargazers_count", 0),
                            updated_at=item.get("updated_at"),
                            permissions=item.get("permissions"),
                        )
                    )
                return repos
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during list_user_repositories: %s", e)
                raise GitHubError(f"Failed to list GitHub repositories: {e}") from e

    async def search_public_repositories(
        self,
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> Tuple[int, List[GitHubRepo]]:
        """Searches public repositories on GitHub via GET /search/repositories."""
        q = query.strip()
        if not q:
            return 0, []

        search_q = f"{q} is:public" if "is:public" not in q and "is:private" not in q else q
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/search/repositories",
                    headers=self._get_headers(require_auth=False),
                    params={
                        "q": search_q,
                        "page": page,
                        "per_page": per_page,
                        "sort": "updated",
                    },
                    timeout=15.0,
                )
                self._handle_response_error(res, f"search_public_repositories ('{q}')")
                data = res.json()
                total_count = data.get("total_count", 0)
                repos: List[GitHubRepo] = []
                for item in data.get("items", []):
                    owner_data = item.get("owner", {})
                    owner_login = owner_data.get("login") if isinstance(owner_data, dict) else (item.get("owner") or "")
                    repos.append(
                        GitHubRepo(
                            id=item["id"],
                            full_name=item["full_name"],
                            name=item["name"],
                            owner=owner_login,
                            owner_avatar_url=owner_data.get("avatar_url") if isinstance(owner_data, dict) else None,
                            owner_type=owner_data.get("type") if isinstance(owner_data, dict) else None,
                            default_branch=item.get("default_branch", "main"),
                            private=item.get("private", False),
                            description=item.get("description"),
                            html_url=item.get("html_url"),
                            stargazers_count=item.get("stargazers_count", 0),
                            updated_at=item.get("updated_at"),
                            permissions=item.get("permissions"),
                        )
                    )
                return total_count, repos
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during search_public_repositories: %s", e)
                raise GitHubError(f"Failed to search GitHub repositories: {e}") from e

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        """Retrieves details of a specific repository."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}",
                    headers=self._get_headers(require_auth=False),
                    timeout=15.0,
                )
                self._handle_response_error(res, f"get_repository ({owner}/{repo})")
                data = res.json()
                owner_data = data.get("owner", {})
                owner_login = owner_data.get("login") if isinstance(owner_data, dict) else (data.get("owner") or "")
                return GitHubRepo(
                    id=data["id"],
                    full_name=data["full_name"],
                    name=data["name"],
                    owner=owner_login,
                    owner_avatar_url=owner_data.get("avatar_url") if isinstance(owner_data, dict) else None,
                    owner_type=owner_data.get("type") if isinstance(owner_data, dict) else None,
                    default_branch=data.get("default_branch", "main"),
                    private=data.get("private", False),
                    description=data.get("description"),
                    html_url=data.get("html_url"),
                    stargazers_count=data.get("stargazers_count", 0),
                    updated_at=data.get("updated_at"),
                    permissions=data.get("permissions"),
                )
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during get_repository: %s", e)
                raise GitHubError(f"Failed to fetch repository '{owner}/{repo}': {e}") from e

    @staticmethod
    def _parse_last_page_from_link_header(link_header: str) -> Optional[int]:
        """Parses the page number from rel="last" in GitHub's Link header."""
        for part in link_header.split(","):
            section = part.strip()
            if 'rel="last"' in section or "rel='last'" in section or 'rel=last' in section:
                match = re.search(r'[?&]page=(\d+)', section)
                if match:
                    return int(match.group(1))
        return None

    async def get_repository_open_pr_count(self, owner: str, repo: str) -> int:
        """Retrieves actual open GitHub PR count using GitHub pagination metadata / Link header.
        Queries GitHub specifically with: GET /repos/{owner}/{repo}/pulls?state=open&per_page=1
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls",
                    headers=self._get_headers(require_auth=False),
                    params={"state": "open", "per_page": 1},
                    timeout=10.0,
                )
                if res.status_code == 200:
                    link_header = res.headers.get("link", "") or res.headers.get("Link", "")
                    if link_header:
                        last_page = self._parse_last_page_from_link_header(link_header)
                        if last_page is not None:
                            return last_page
                    data = res.json()
                    return len(data) if isinstance(data, list) else 0
            except Exception as e:
                logger.debug("Open PR count query error for %s/%s: %s", owner, repo, e)
        return 0

    async def get_repository_total_pr_count(self, owner: str, repo: str) -> int:
        """Retrieves actual total GitHub PR count across all states (open, closed, merged)."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                search_res = await client.get(
                    f"{self.BASE_URL}/search/issues",
                    headers=self._get_headers(require_auth=False),
                    params={"q": f"repo:{owner}/{repo} type:pr", "per_page": 1},
                    timeout=10.0,
                )
                if search_res.status_code == 200:
                    data = search_res.json()
                    if "total_count" in data and isinstance(data["total_count"], int):
                        return data["total_count"]
            except Exception as e:
                logger.debug("Search API count error for %s/%s: %s", owner, repo, e)

            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls",
                    headers=self._get_headers(require_auth=False),
                    params={"state": "all", "per_page": 1},
                    timeout=10.0,
                )
                if res.status_code == 200:
                    link_header = res.headers.get("link", "") or res.headers.get("Link", "")
                    if link_header:
                        last_page = self._parse_last_page_from_link_header(link_header)
                        if last_page is not None:
                            return last_page
                    data = res.json()
                    return len(data) if isinstance(data, list) else 0
            except Exception as e:
                logger.debug("Total PR count Link fallback error for %s/%s: %s", owner, repo, e)
        return 0

    async def get_repository_pr_count(self, owner: str, repo: str, state: str = "open") -> int:
        """Retrieves actual GitHub PR count for a repository. Defaults to state='open'."""
        if state == "open":
            return await self.get_repository_open_pr_count(owner, repo)
        return await self.get_repository_total_pr_count(owner, repo)

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        per_page: int = 100,
        fetch_limit: int = 500,
    ) -> GitHubPRListResponse:
        """Lists pull requests for a given repository by following GitHub pagination.
        Retrieves both open and closed/merged PRs, enforces a hard maximum of 500 PRs,
        deduplicates across pages, and returns explicit fetch metrics.
        """
        start_time = time.perf_counter()
        all_prs: List[GitHubPR] = []
        seen_ids = set()
        seen_numbers = set()
        page = 1
        pages_fetched = 0
        complete = False
        truncated = False

        gh_state = "closed" if state == "merged" else (state if state in ("open", "closed", "all") else "all")
        fetch_limit = min(fetch_limit, 500) if fetch_limit is not None else 500
        max_pages = max(1, (fetch_limit + per_page - 1) // per_page)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                while page <= max_pages and len(all_prs) < fetch_limit:
                    res = await client.get(
                        f"{self.BASE_URL}/repos/{owner}/{repo}/pulls",
                        headers=self._get_headers(require_auth=False),
                        params={"state": gh_state, "per_page": per_page, "page": page},
                        timeout=20.0,
                    )
                    self._handle_response_error(res, f"list_pull_requests ({owner}/{repo}, page {page})")
                    pages_fetched += 1

                    raw_prs = res.json()
                    if not raw_prs:
                        # Empty final page reached
                        complete = True
                        break

                    for p in raw_prs:
                        pr_id = p["id"]
                        pr_num = p.get("number")
                        if pr_id in seen_ids or (pr_num is not None and pr_num in seen_numbers):
                            continue
                        seen_ids.add(pr_id)
                        if pr_num is not None:
                            seen_numbers.add(pr_num)

                        merged_at = p.get("merged_at")
                        is_merged = bool(merged_at)
                        raw_state = p.get("state", "open")
                        if raw_state == "closed":
                            pr_state = "merged" if is_merged else "closed"
                        else:
                            pr_state = "open"

                        # Apply state filtering if requested
                        if state == "open" and pr_state != "open":
                            continue
                        elif state == "merged" and not is_merged:
                            continue
                        elif state == "closed" and pr_state not in ("closed", "merged"):
                            continue

                        base_obj = p.get("base") or {}
                        head_obj = p.get("head") or {}
                        user_obj = p.get("user") or {}

                        all_prs.append(
                            GitHubPR(
                                id=pr_id,
                                number=p["number"],
                                title=p.get("title", ""),
                                state=pr_state,
                                author=user_obj.get("login", "unknown"),
                                head_sha=head_obj.get("sha", ""),
                                base_branch=base_obj.get("ref", "main"),
                                base_sha=base_obj.get("sha"),
                                html_url=p.get("html_url"),
                                body=p.get("body"),
                                created_at=p.get("created_at"),
                                updated_at=p.get("updated_at"),
                                closed_at=p.get("closed_at"),
                                merged_at=merged_at,
                                is_merged=is_merged,
                            )
                        )

                        if len(all_prs) >= fetch_limit:
                            break

                    link_header = res.headers.get("link", "") or res.headers.get("Link", "")
                    has_next = bool(link_header and 'rel="next"' in link_header)

                    if len(all_prs) >= fetch_limit:
                        # Limit reached
                        if len(raw_prs) < per_page and not has_next:
                            complete = True
                            truncated = False
                        else:
                            truncated = True
                            complete = False
                        break

                    # Check pagination completion conditions
                    if len(raw_prs) < per_page or (link_header and not has_next):
                        complete = True
                        break

                    page += 1
                else:
                    if len(all_prs) >= fetch_limit:
                        truncated = True
                        complete = False
                    else:
                        complete = True

                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                last_synced_at = datetime.now(timezone.utc).isoformat()

                return GitHubPRListResponse(
                    items=all_prs,
                    total_fetched=len(all_prs),
                    pages_fetched=pages_fetched,
                    per_page=per_page,
                    fetch_limit=fetch_limit,
                    complete=complete,
                    truncated=truncated,
                    fetch_duration_ms=duration_ms,
                    last_synced_at=last_synced_at,
                )
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                cause = _format_error_cause(e)
                logger.error("Network error during list_pull_requests: %s", cause)
                raise GitHubError(f"Failed to fetch pull requests for '{owner}/{repo}': {cause}") from e

    async def get_pull_request(self, owner: str, repo: str, pull_number: int) -> GitHubPR:
        """Retrieves details for a single pull request."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}",
                    headers=self._get_headers(require_auth=False),
                    timeout=25.0,
                )
                self._handle_response_error(res, f"get_pull_request ({owner}/{repo}#{pull_number})")
                p = res.json()
                merged_at = p.get("merged_at")
                is_merged = bool(merged_at)
                raw_state = p.get("state", "open")
                if raw_state == "closed":
                    pr_state = "merged" if is_merged else "closed"
                else:
                    pr_state = "open"

                base_obj = p.get("base") or {}
                head_obj = p.get("head") or {}
                user_obj = p.get("user") or {}

                return GitHubPR(
                    id=p["id"],
                    number=p["number"],
                    title=p.get("title", ""),
                    state=pr_state,
                    author=user_obj.get("login", "unknown"),
                    head_sha=head_obj.get("sha", ""),
                    base_branch=base_obj.get("ref", "main"),
                    base_sha=base_obj.get("sha"),
                    html_url=p.get("html_url"),
                    body=p.get("body"),
                    created_at=p.get("created_at"),
                    updated_at=p.get("updated_at"),
                    closed_at=p.get("closed_at"),
                    merged_at=merged_at,
                    is_merged=is_merged,
                )
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                cause = _format_error_cause(e)
                logger.error("Network error during get_pull_request: %s", cause)
                raise GitHubError(f"Failed to fetch PR #{pull_number} for '{owner}/{repo}': {cause}") from e

    async def get_pr_files(self, owner: str, repo: str, pull_number: int) -> List[GitHubFileDiff]:
        """Retrieves changed files and their patch diffs for a pull request."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/files",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": 100},
                    timeout=25.0,
                )
                self._handle_response_error(res, f"get_pr_files ({owner}/{repo}#{pull_number})")
                diffs = []
                for f in res.json():
                    diffs.append(
                        GitHubFileDiff(
                            filename=f["filename"],
                            status=f["status"],
                            additions=f.get("additions", 0),
                            deletions=f.get("deletions", 0),
                            changes=f.get("changes", 0),
                            patch=f.get("patch"),
                        )
                    )
                return diffs
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                cause = _format_error_cause(e)
                logger.error("Network error during get_pr_files: %s", cause)
                raise GitHubError(f"Failed to fetch files for PR #{pull_number}: {cause}") from e

    async def get_pr_commits(self, owner: str, repo: str, pull_number: int) -> List[GitHubCommit]:
        """Retrieves commit metadata for a pull request."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/commits",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": 50},
                    timeout=25.0,
                )
                self._handle_response_error(res, f"get_pr_commits ({owner}/{repo}#{pull_number})")
                commits = []
                for c in res.json():
                    commits.append(
                        GitHubCommit(
                            sha=c["sha"],
                            author_name=c["commit"]["author"]["name"],
                            message=c["commit"]["message"],
                            committed_at=c["commit"]["author"].get("date"),
                        )
                    )
                return commits
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                cause = _format_error_cause(e)
                logger.error("Network error during get_pr_commits: %s", cause)
                raise GitHubError(f"Failed to fetch commits for PR #{pull_number}: {cause}") from e

    async def get_pr_comments(self, owner: str, repo: str, pull_number: int) -> List[GitHubComment]:
        """Retrieves review comments on a pull request."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/comments",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": 50},
                    timeout=25.0,
                )
                self._handle_response_error(res, f"get_pr_comments ({owner}/{repo}#{pull_number})")
                comments = []
                for c in res.json():
                    comments.append(
                        GitHubComment(
                            id=c["id"],
                            user=c["user"]["login"],
                            body=c["body"],
                            path=c.get("path"),
                            line=c.get("line"),
                            created_at=c.get("created_at"),
                        )
                    )
                return comments
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                cause = _format_error_cause(e)
                logger.error("Network error during get_pr_comments: %s", cause)
                raise GitHubError(f"Failed to fetch comments for PR #{pull_number}: {cause}") from e

    async def get_pr_details_full(self, owner: str, repo: str, pull_number: int) -> PRDetailResponse:
        """Assembles full pull request review context: PR metadata, diffs, commits, comments concurrently.

        Uses in-flight deduplication and a 60-second TTL cache to prevent thundering herds across parallel callers.
        """
        import time as _time
        cache_key = f"{owner.lower()}/{repo.lower()}:{pull_number}:{self.token or 'default'}"
        now = _time.time()

        # 1. Check TTL cache
        cached_entry = self._pr_details_cache.get(cache_key)
        if cached_entry:
            cached_data, cached_at = cached_entry
            if (now - cached_at) < self._CACHE_TTL_SECONDS:
                return cached_data

        # 2. In-flight request deduplication
        if cache_key in self._pr_details_in_flight:
            return await self._pr_details_in_flight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pr_details_in_flight[cache_key] = future

        try:
            pr, files, commits, comments = await asyncio.gather(
                self.get_pull_request(owner, repo, pull_number),
                self.get_pr_files(owner, repo, pull_number),
                self.get_pr_commits(owner, repo, pull_number),
                self.get_pr_comments(owner, repo, pull_number),
            )
            result = PRDetailResponse(
                pr=pr,
                files=files,
                commits=commits,
                comments=comments,
            )
            self._pr_details_cache[cache_key] = (result, _time.time())
            if not future.done():
                future.set_result(result)
            return result
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            self._pr_details_in_flight.pop(cache_key, None)

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: Optional[str] = None,
    ) -> Optional[str]:
        """Fetches decoded string content of a file in the repository."""
        import base64
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                params = {"ref": ref} if ref else {}
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
                    headers=self._get_headers(require_auth=False),
                    params=params,
                    timeout=15.0,
                )
                if res.status_code == 404:
                    return None
                self._handle_response_error(res, f"get_file_contents ({owner}/{repo}/{path})")
                data = res.json()
                if isinstance(data, dict):
                    content = data.get("content", "")
                    encoding = data.get("encoding", "")
                    if encoding == "base64" and content:
                        return base64.b64decode(content).decode("utf-8", errors="replace")
                    return content
                return None
            except GitHubNotFoundError:
                return None
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during get_file_contents: %s", e)
                raise GitHubError(f"Failed to fetch content for '{path}' in '{owner}/{repo}': {e}") from e

    async def list_directory_contents(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lists contents (files and subdirectories) of a path in the repository."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                params = {"ref": ref} if ref else {}
                url_path = path.strip("/")
                url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{url_path}" if url_path else f"{self.BASE_URL}/repos/{owner}/{repo}/contents"
                res = await client.get(
                    url,
                    headers=self._get_headers(require_auth=False),
                    params=params,
                    timeout=15.0,
                )
                if res.status_code == 404:
                    return []
                self._handle_response_error(res, f"list_directory_contents ({owner}/{repo}/{path})")
                data = res.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                return []
            except GitHubNotFoundError:
                return []
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during list_directory_contents: %s", e)
                raise GitHubError(f"Failed to list directory '{path}' in '{owner}/{repo}': {e}") from e

    async def get_pr_reviews(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> List[Dict[str, Any]]:
        """Retrieves official reviews submitted for a pull request."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": 50},
                    timeout=15.0,
                )
                if res.status_code == 404:
                    return []
                self._handle_response_error(res, f"get_pr_reviews ({owner}/{repo}#{pull_number})")
                data = res.json()
                return data if isinstance(data, list) else []
            except GitHubNotFoundError:
                return []
            except (ConfigurationError, GitHubError):
                raise
            except Exception as e:
                logger.error("Network error during get_pr_reviews: %s", e)
                raise GitHubError(f"Failed to fetch reviews for PR #{pull_number}: {e}") from e

    async def get_pr_commits_evidence(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        max_commits: int = MAX_COMMITS,
    ) -> Tuple[List[CommitEvidence], bool]:
        """Retrieves bounded commit history for a PR, returning (commits, truncated)."""
        limit = min(max_commits, MAX_COMMITS)
        commits: List[CommitEvidence] = []
        seen_shas: Set[str] = set()
        page = 1
        per_page = min(50, limit)
        truncated = False

        async with httpx.AsyncClient(follow_redirects=True) as client:
            while len(commits) < limit:
                try:
                    res = await client.get(
                        f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/commits",
                        headers=self._get_headers(require_auth=False),
                        params={"per_page": per_page, "page": page},
                        timeout=15.0,
                    )
                    if res.status_code == 404:
                        return [], False
                    self._handle_response_error(res, f"get_pr_commits_evidence ({owner}/{repo}#{pull_number})")
                    data = res.json()
                    if not data or not isinstance(data, list):
                        break

                    for c in data:
                        sha = c.get("sha")
                        if not sha or sha in seen_shas:
                            continue
                        seen_shas.add(sha)

                        commit_data = c.get("commit") or {}
                        author_data = commit_data.get("author") or {}
                        committer_data = commit_data.get("committer") or {}
                        gh_author = c.get("author") or {}

                        author_name = (
                            gh_author.get("login")
                            or author_data.get("name")
                            or "unknown"
                        )
                        message = commit_data.get("message") or ""
                        authored_at = author_data.get("date")
                        committed_at = committer_data.get("date") or authored_at

                        stats = c.get("stats") or {}
                        files = [f.get("filename") for f in c.get("files", []) if f.get("filename")]

                        commits.append(
                            CommitEvidence(
                                sha=sha,
                                message=message,
                                author=author_name,
                                authored_at=authored_at,
                                committed_at=committed_at,
                                additions=stats.get("additions", 0),
                                deletions=stats.get("deletions", 0),
                                changed_files_count=len(files) if files else len(c.get("files", [])),
                                changed_files=files,
                                url=c.get("html_url"),
                            )
                        )
                        if len(commits) >= limit:
                            break

                    link_header = res.headers.get("link", "") or res.headers.get("Link", "")
                    has_next = bool(link_header and 'rel="next"' in link_header)
                    if len(data) < per_page or not has_next:
                        break
                    page += 1
                except GitHubNotFoundError:
                    return [], False
                except Exception as e:
                    logger.warning("Error fetching PR commits evidence: %s", e)
                    break

        if len(commits) >= limit:
            truncated = True

        # Deterministic sort: chronological by committed_at/authored_at ascending, then sha
        commits.sort(key=lambda x: (x.committed_at or x.authored_at or "", x.sha))
        return commits, truncated

    async def get_pr_conversation_evidence(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        max_items: int = MAX_CONVERSATION_ITEMS,
    ) -> Tuple[List[ConversationEvidence], bool]:
        """Retrieves bounded conversation evidence across issue comments, review comments, and reviews."""
        limit = min(max_items, MAX_CONVERSATION_ITEMS)
        items: List[ConversationEvidence] = []
        seen_ids: Set[str] = set()
        truncated = False

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # 1. Issue comments (general discussion)
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/issues/{pull_number}/comments",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": min(50, limit)},
                    timeout=15.0,
                )
                if res.status_code == 200 and isinstance(res.json(), list):
                    for c in res.json():
                        cid = f"issue_comment-{c.get('id')}"
                        if cid in seen_ids:
                            continue
                        seen_ids.add(cid)
                        user_obj = c.get("user") or {}
                        items.append(
                            ConversationEvidence(
                                id=cid,
                                author=user_obj.get("login", "unknown"),
                                body=c.get("body", "") or "",
                                created_at=c.get("created_at") or "",
                                updated_at=c.get("updated_at"),
                                source_type="issue_comment",
                                url=c.get("html_url"),
                            )
                        )
            except Exception as e:
                logger.debug("Could not fetch issue comments: %s", e)

            # 2. PR review comments (inline diff comments)
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/comments",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": min(50, limit)},
                    timeout=15.0,
                )
                if res.status_code == 200 and isinstance(res.json(), list):
                    for c in res.json():
                        cid = f"review_comment-{c.get('id')}"
                        if cid in seen_ids:
                            continue
                        seen_ids.add(cid)
                        user_obj = c.get("user") or {}
                        items.append(
                            ConversationEvidence(
                                id=cid,
                                author=user_obj.get("login", "unknown"),
                                body=c.get("body", "") or "",
                                created_at=c.get("created_at") or "",
                                updated_at=c.get("updated_at"),
                                source_type="review_comment",
                                url=c.get("html_url"),
                                file_path=c.get("path"),
                                line=c.get("line") or c.get("original_line"),
                                side=c.get("side"),
                                commit_sha=c.get("commit_id"),
                            )
                        )
            except Exception as e:
                logger.debug("Could not fetch review comments: %s", e)

            # 3. PR reviews (official review submissions)
            try:
                res = await client.get(
                    f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
                    headers=self._get_headers(require_auth=False),
                    params={"per_page": min(50, limit)},
                    timeout=15.0,
                )
                if res.status_code == 200 and isinstance(res.json(), list):
                    for r in res.json():
                        cid = f"review-{r.get('id')}"
                        if cid in seen_ids:
                            continue
                        seen_ids.add(cid)
                        user_obj = r.get("user") or {}
                        body = r.get("body", "") or ""
                        state = r.get("state", "COMMENTED")
                        items.append(
                            ConversationEvidence(
                                id=cid,
                                author=user_obj.get("login", "unknown"),
                                body=body or f"Review state: {state}",
                                created_at=r.get("submitted_at") or "",
                                source_type="review",
                                url=r.get("html_url"),
                                commit_sha=r.get("commit_id"),
                                review_state=state,
                            )
                        )
            except Exception as e:
                logger.debug("Could not fetch reviews: %s", e)

        # Deterministic sort: chronological by created_at ascending, then id
        items.sort(key=lambda x: (x.created_at, x.id))

        if len(items) > limit:
            truncated = True
            items = items[:limit]

        return items, truncated

    async def get_pr_checks_evidence(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        max_checks: int = MAX_CHECKS,
    ) -> Tuple[List[CheckEvidence], bool]:
        """Retrieves CI checks and validation status for a commit SHA."""
        limit = min(max_checks, MAX_CHECKS)
        checks: List[CheckEvidence] = []
        seen_ids: Set[str] = set()
        truncated = False

        async with httpx.AsyncClient(follow_redirects=True) as client:
            async def _fetch_check_runs():
                try:
                    res = await client.get(
                        f"{self.BASE_URL}/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                        headers=self._get_headers(require_auth=False),
                        params={"per_page": min(100, limit)},
                        timeout=15.0,
                    )
                    if res.status_code == 200:
                        data = res.json()
                        check_runs = data.get("check_runs", []) if isinstance(data, dict) else []
                        for cr in check_runs:
                            cid = f"check_run-{cr.get('id')}"
                            if cid in seen_ids:
                                continue
                            seen_ids.add(cid)
                            app_obj = cr.get("app") or {}
                            checks.append(
                                CheckEvidence(
                                    id=cid,
                                    name=cr.get("name") or "Check",
                                    status=cr.get("status") or "completed",
                                    conclusion=cr.get("conclusion"),
                                    workflow=app_obj.get("name"),
                                    started_at=cr.get("started_at"),
                                    completed_at=cr.get("completed_at"),
                                    url=cr.get("html_url"),
                                )
                            )
                except Exception as e:
                    logger.debug("Could not fetch check-runs for SHA %s: %s", head_sha, e)

            async def _fetch_statuses():
                try:
                    res = await client.get(
                        f"{self.BASE_URL}/repos/{owner}/{repo}/commits/{head_sha}/status",
                        headers=self._get_headers(require_auth=False),
                        timeout=15.0,
                    )
                    if res.status_code == 200:
                        data = res.json()
                        statuses = data.get("statuses", []) if isinstance(data, dict) else []
                        for st in statuses:
                            cid = f"status-{st.get('id')}"
                            if cid in seen_ids:
                                continue
                            seen_ids.add(cid)
                            raw_state = st.get("state")  # success, failure, error, pending
                            checks.append(
                                CheckEvidence(
                                    id=cid,
                                    name=st.get("context") or "Status",
                                    status="completed" if raw_state != "pending" else "in_progress",
                                    conclusion=raw_state,
                                    started_at=st.get("created_at"),
                                    completed_at=st.get("updated_at"),
                                    url=st.get("target_url"),
                                )
                            )
                except Exception as e:
                    logger.debug("Could not fetch status for SHA %s: %s", head_sha, e)

            await asyncio.gather(_fetch_check_runs(), _fetch_statuses(), return_exceptions=True)

        # Deterministic sort by name ascending, then id
        checks.sort(key=lambda x: (x.name, x.id))

        if len(checks) > limit:
            truncated = True
            checks = checks[:limit]

        return checks, truncated

    async def get_historical_prs_evidence(
        self,
        owner: str,
        repo: str,
        current_pr_number: int,
        changed_files: List[str],
        symbols: List[str],
        max_prs: int = MAX_HISTORICAL_PRS,
    ) -> Tuple[List[HistoricalPREvidence], bool]:
        """Discovers bounded historical PRs and ranks them by deterministic overlap rationale."""
        limit = min(max_prs, MAX_HISTORICAL_PRS)
        truncated = False

        # Retrieve a bounded set of up to 50 recent PRs
        pr_list_resp = await self.list_pull_requests(owner, repo, state="all", per_page=50, fetch_limit=50)
        repo_full_name = f"{owner}/{repo}"

        file_basenames = {os.path.basename(f).lower() for f in changed_files if f}
        symbol_set = {s.lower() for s in symbols if s and len(s) > 2}

        scored_candidates: List[Tuple[float, HistoricalPREvidence]] = []

        # Phase 1: Fast text-based candidate scoring in memory (<1ms)
        pre_scored: List[Dict[str, Any]] = []
        for idx, p in enumerate(pr_list_resp.items):
            if p.number == current_pr_number:
                continue

            text_to_search = f"{p.title} {p.body or ''}".lower()
            matching_files: List[str] = []
            matching_symbols: List[str] = []
            score = 0.0

            # Check for exact file path or distinct basename overlaps (skip generic boilerplate names)
            generic_files = {"package.json", "package-lock.json", "index.ts", "index.js", "readme.md", "__init__.py"}
            for f in changed_files:
                f_lower = f.lower()
                base = os.path.basename(f).lower()
                if f_lower in text_to_search:
                    if f not in matching_files:
                        matching_files.append(f)
                    score += 5.0
                elif base in text_to_search and base not in generic_files and len(base) > 5:
                    if f not in matching_files:
                        matching_files.append(f)
                    score += 3.0

            # Check for symbol overlaps
            for sym in symbols:
                if sym.lower() in text_to_search and len(sym) > 3:
                    matching_symbols.append(sym)
                    score += 2.0

            # Explicit references
            if f"#{current_pr_number}" in text_to_search or f"pr {current_pr_number}" in text_to_search:
                score += 10.0

            pre_scored.append({
                "pr": p,
                "score": score,
                "matching_files": matching_files,
                "matching_symbols": matching_symbols,
                "text_to_search": text_to_search,
            })

        # Phase 2: Cheap deterministic filter: prioritize candidates with Phase 1 signals or top candidates up to 5
        candidates_with_score = [item for item in pre_scored if item["score"] > 0]
        if candidates_with_score:
            candidates_to_enrich = candidates_with_score[:5]
        else:
            candidates_to_enrich = pre_scored[:min(len(pre_scored), 5)]

        if changed_files and candidates_to_enrich:
            async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
                async def _enrich_files(item: Dict[str, Any]) -> None:
                    p = item["pr"]
                    try:
                        res = await client.get(
                            f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{p.number}/files",
                            headers=self._get_headers(require_auth=False),
                            params={"per_page": 50},
                        )
                        if res.status_code == 200:
                            for d in res.json():
                                fname = d.get("filename", "")
                                if fname in changed_files:
                                    if fname not in item["matching_files"]:
                                        item["matching_files"].append(fname)
                                    item["score"] += 5.0
                                elif os.path.basename(fname).lower() in file_basenames and os.path.basename(fname).lower() not in generic_files:
                                    if fname not in item["matching_files"]:
                                        item["matching_files"].append(fname)
                                    item["score"] += 3.0
                    except Exception:
                        pass

                await asyncio.gather(*[_enrich_files(item) for item in candidates_to_enrich], return_exceptions=True)

        for item in pre_scored:
            p = item["pr"]
            score = item["score"]
            matching_files = item["matching_files"]
            matching_symbols = item["matching_symbols"]

            if score > 0:
                reasons: List[str] = []
                if matching_files:
                    file_names_str = ", ".join(os.path.basename(f) for f in matching_files[:2])
                    reasons.append(f"Shares overlapping files ({file_names_str}) with current PR")
                if matching_symbols:
                    reasons.append(f"Touches symbols: {', '.join(matching_symbols[:3])}")
                if not reasons:
                    reasons.append("Related repository development context")

                evidence = HistoricalPREvidence(
                    repository_full_name=repo_full_name,
                    pr_number=p.number,
                    title=p.title,
                    state=p.state,
                    is_merged=bool(p.is_merged),
                    relevant_files=matching_files[:5],
                    relevant_symbols=matching_symbols[:5],
                    relevance_reason="; ".join(reasons),
                    source_url=p.html_url,
                )
                scored_candidates.append((score, evidence))

        # Deterministic sort: score descending, then pr_number descending
        scored_candidates.sort(key=lambda item: (-item[0], -item[1].pr_number))

        if len(scored_candidates) > limit:
            truncated = True

        return [item[1] for item in scored_candidates[:limit]], truncated



_github_service: Optional[GitHubService] = None


def get_github_service() -> GitHubService:
    """Returns singleton instance of GitHubService."""
    global _github_service
    if _github_service is None:
        _github_service = GitHubService()
    return _github_service
