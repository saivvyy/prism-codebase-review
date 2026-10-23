from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
from supabase import create_client, Client

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, SupabaseStateError
from app.schemas.canonical_memory import (
    compute_memory_identity,
    get_default_authority,
    normalize_claim_or_statement,
    normalize_knowledge_type,
    normalize_status,
    KnowledgeType,
    MemoryAuthority,
    MemoryStatus,
)

logger = logging.getLogger(__name__)


_UNSET = object()
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_REPOS_FILE = _DATA_DIR / "local_repositories.json"

DEFAULT_WORKSPACE_UUIDS: List[str] = [
    str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace")),
    "00000000-0000-0000-0000-000000000000",
]


class SupabaseService:
    """Manages application state and relational persistence in Supabase PostgreSQL."""

    def __init__(
        self,
        supabase_url: Any = _UNSET,
        supabase_key: Any = _UNSET,
        persist_local: bool = True,
    ):
        settings = get_settings()
        self.supabase_url = settings.SUPABASE_URL if supabase_url is _UNSET else supabase_url
        if supabase_key is _UNSET:
            self.supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
        else:
            self.supabase_key = supabase_key
        self.persist_local = persist_local
        self._client: Optional[Client] = None
        logger.info(
            "SupabaseService initialized: is_configured=%s, SUPABASE_SERVICE_ROLE_KEY configured: %s",
            self.is_configured,
            bool(settings.SUPABASE_SERVICE_ROLE_KEY),
        )
        # In-memory storage for test/unconfigured dev mode
        self._local_repos: Dict[str, Dict[str, Any]] = {}
        self._local_prs: Dict[str, Dict[str, Any]] = {}
        self._local_reviews: Dict[str, Dict[str, Any]] = {}
        self._local_findings: Dict[str, List[Dict[str, Any]]] = {}
        self._local_engineering_memories: Dict[str, Dict[str, Any]] = {}
        self._local_fixes: Dict[str, Dict[str, Any]] = {}
        self._local_learning_candidates: Dict[str, Dict[str, Any]] = {}
        if self.persist_local:
            self._load_local_state()

    def _load_local_state(self) -> None:
        """Loads persisted local repository state if available."""
        if not self.persist_local:
            return
        try:
            if _REPOS_FILE.exists():
                with open(_REPOS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._local_repos.update(data)
        except Exception as e:
            logger.warning("Could not load local repository state from %s: %s", _REPOS_FILE, e)

    def _save_local_state(self) -> None:
        """Saves current local repository state to disk."""
        if not self.persist_local:
            return
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(_REPOS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._local_repos, f, indent=2)
        except Exception as e:
            logger.warning("Could not save local repository state to %s: %s", _REPOS_FILE, e)

    @property
    def is_configured(self) -> bool:
        """Returns True if Supabase credentials are provided."""
        return bool(self.supabase_url and self.supabase_key)

    @property
    def client(self) -> Client:
        """Initializes and returns the Supabase client."""
        if not self.is_configured:
            raise ConfigurationError(
                "Supabase is not configured. SUPABASE_URL and SUPABASE_KEY must be set."
            )
        if self._client is None:
            try:
                self._client = create_client(self.supabase_url, self.supabase_key)
            except Exception as e:
                logger.error("Failed to initialize Supabase client: %s", e)
                raise SupabaseStateError(f"Could not initialize Supabase client: {e}") from e
        return self._client

    async def ensure_profile(
        self,
        user_id: str,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ensures a profile record exists in public.profiles to satisfy relational foreign keys."""
        if not self.is_configured:
            return {"id": user_id, "github_username": username or "offline_user"}
        now = datetime.now(timezone.utc).isoformat()
        try:
            # If the remote database has profiles_id_fkey referencing auth.users(id),
            # ensure the user is registered in auth.users so foreign key constraint is satisfied.
            try:
                if user_id and user_id != "00000000-0000-0000-0000-000000000000":
                    self.client.auth.admin.create_user({
                        "id": user_id,
                        "email": f"{username or user_id[:8]}@prism.local",
                        "email_confirm": True,
                    })
            except Exception:
                pass  # Record already exists or foreign key dropped in migration

            profile_data = {
                "id": user_id,
                "github_username": username or "prism_user",
                "avatar_url": avatar_url,
                "updated_at": now,
            }
            res = self.client.table("profiles").upsert(profile_data, on_conflict="id").execute()
            if res.data:
                return res.data[0]
            return profile_data
        except Exception as e:
            logger.error("Supabase ensure_profile error: %s", e)
            raise SupabaseStateError(f"Database error ensuring user profile: {e}") from e

    async def connect_repository(
        self,
        github_repo_id: int,
        full_name: str,
        default_branch: str = "main",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Saves a connected GitHub repository in Supabase, scoped by user_id."""
        now = datetime.now(timezone.utc).isoformat()
        effective_user_id = user_id or str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace"))
        if self.is_configured:
            try:
                await self.ensure_profile(effective_user_id)
                data = {
                    "github_repo_id": github_repo_id,
                    "full_name": full_name,
                    "default_branch": default_branch,
                    "webhook_active": False,
                    "updated_at": now,
                    "user_id": effective_user_id,
                }
                res = self.client.table("repositories").upsert(data, on_conflict="user_id,github_repo_id").execute()
                if res.data:
                    return res.data[0]
                raise SupabaseStateError("Supabase returned empty data on repository upsert")
            except Exception as e:
                logger.error("Supabase repository connect error: %s", e)
                raise SupabaseStateError(f"Database error while connecting repository: {e}") from e

        # Fallback / Local mock mode scoped to user (only reached when Supabase is not configured)
        existing = next(
            (
                r for r in self._local_repos.values()
                if (
                    (user_id and r.get("user_id") == user_id)
                    or (not user_id and r.get("user_id") in (None, effective_user_id))
                )
                and (r["github_repo_id"] == github_repo_id or r.get("full_name", "").lower() == full_name.lower())
            ),
            None,
        )
        if existing:
            existing["full_name"] = full_name
            existing["default_branch"] = default_branch
            existing["updated_at"] = now
            self._save_local_state()
            return existing

        repo_id = str(uuid.uuid4())
        repo_record = {
            "id": repo_id,
            "user_id": effective_user_id,
            "github_repo_id": github_repo_id,
            "full_name": full_name,
            "default_branch": default_branch,
            "webhook_active": False,
            "created_at": now,
            "updated_at": now,
        }
        self._local_repos[repo_id] = repo_record
        self._save_local_state()
        return repo_record

    async def list_repositories(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists connected repositories, scoped to a user and shared workspace repositories."""
        allowed_user_ids = [user_id, *DEFAULT_WORKSPACE_UUIDS] if user_id else list(DEFAULT_WORKSPACE_UUIDS)
        if self.is_configured:
            try:
                query = self.client.table("repositories").select("*")
                query = query.in_("user_id", allowed_user_ids)
                res = query.order("created_at", desc=True).execute()
                return res.data or []
            except Exception as e:
                logger.error("Supabase list error: %s", e)
                raise SupabaseStateError(f"Database error listing repositories: {e}") from e

        allowed_set = set(allowed_user_ids) | {"", None}
        return [r for r in self._local_repos.values() if r.get("user_id") in allowed_set]

    async def get_repository_by_id(self, repo_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single repository by UUID or full_name, enforcing user authorization boundaries."""
        allowed_user_ids = [user_id, *DEFAULT_WORKSPACE_UUIDS] if user_id else list(DEFAULT_WORKSPACE_UUIDS)
        allowed_set = set(allowed_user_ids) | {"", None}

        if self.is_configured:
            try:
                query = self.client.table("repositories").select("*").eq("id", repo_id)
                query = query.in_("user_id", allowed_user_ids)
                res = query.execute()
                if res.data:
                    return res.data[0]

                if "/" in repo_id:
                    q2 = self.client.table("repositories").select("*").eq("full_name", repo_id)
                    q2 = q2.in_("user_id", allowed_user_ids)
                    res2 = q2.execute()
                    if res2.data:
                        return res2.data[0]

                return None
            except Exception as e:
                logger.error("Supabase get repository error: %s", e)
                raise SupabaseStateError(f"Database error fetching repository: {e}") from e

        found = self._local_repos.get(repo_id)
        if found:
            repo_owner = found.get("user_id")
            if repo_owner and repo_owner not in allowed_set:
                return None
            return found

        # Fallback: If repo_id happens to be an owner/repo full name
        if "/" in repo_id:
            for r in self._local_repos.values():
                if r.get("full_name") == repo_id:
                    repo_owner = r.get("user_id")
                    if repo_owner and repo_owner not in allowed_set:
                        continue
                    return r

        return None

    async def get_repository(self, repo_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Alias for get_repository_by_id."""
        return await self.get_repository_by_id(repo_id, user_id=user_id)

    async def get_repository_by_name(self, full_name: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a single repository by full_name ('owner/repo') enforcing user authorization boundaries."""
        default_unauth_uuids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, "prism.default.workspace")),
            "00000000-0000-0000-0000-000000000000",
            "754ce586-15a7-54c3-9230-797e25c39fae",
        ]
        default_unauth_ids = set(default_unauth_uuids) | {"", None}
        if self.is_configured:
            try:
                query = self.client.table("repositories").select("*").eq("full_name", full_name)
                if user_id:
                    query = query.eq("user_id", user_id)
                else:
                    query = query.in_("user_id", default_unauth_uuids)
                res = query.execute()
                return res.data[0] if res.data else None
            except Exception as e:
                logger.error("Supabase get repository by name error: %s", e)
                raise SupabaseStateError(f"Database error fetching repository by name: {e}") from e

        return next(
            (
                r for r in self._local_repos.values()
                if r.get("full_name") == full_name and (
                    (r.get("user_id") == user_id) if user_id else (not r.get("user_id") or r.get("user_id") in default_unauth_ids)
                )
            ),
            None,
        )

    async def delete_repository(self, repo_id: str, user_id: Optional[str] = None) -> bool:
        """Deletes/disconnects a repository from persistence, verifying user ownership if scoped."""
        if self.is_configured:
            try:
                existing = await self.get_repository_by_id(repo_id, user_id=user_id)
                if not existing:
                    return False
                query = self.client.table("repositories").delete().eq("id", repo_id)
                if user_id:
                    query = query.eq("user_id", user_id)
                query.execute()
                if repo_id in self._local_repos:
                    del self._local_repos[repo_id]
                    self._save_local_state()
                return True
            except Exception as e:
                logger.error("Supabase delete error: %s", e)
                raise SupabaseStateError(f"Database error deleting repository: {e}") from e
        if repo_id in self._local_repos:
            repo = self._local_repos[repo_id]
            if user_id and repo.get("user_id") and repo.get("user_id") != user_id:
                return False
            del self._local_repos[repo_id]
            self._save_local_state()
            return True
        return False

    async def upsert_pull_request(
        self,
        repository_id: str,
        pr_number: int,
        title: str,
        author: str,
        head_sha: str,
        base_branch: str = "main",
        status: str = "open",
    ) -> Dict[str, Any]:
        """Upserts a pull request record."""
        now = datetime.now(timezone.utc).isoformat()
        if self.is_configured:
            try:
                data = {
                    "repository_id": repository_id,
                    "pr_number": pr_number,
                    "title": title,
                    "author": author,
                    "head_sha": head_sha,
                    "base_branch": base_branch,
                    "status": status,
                    "updated_at": now,
                }
                res = self.client.table("pull_requests").upsert(data, on_conflict="repository_id,pr_number").execute()
                if res.data:
                    return res.data[0]
                raise SupabaseStateError("Supabase returned empty data on pull request upsert")
            except Exception as e:
                logger.error("Supabase PR upsert error: %s", e)
                raise SupabaseStateError(f"Database error upserting pull request: {e}") from e

        # Local fallback only when not configured
        pr_key = f"{repository_id}_{pr_number}"
        if pr_key in self._local_prs:
            pr = self._local_prs[pr_key]
            pr.update({
                "title": title,
                "author": author,
                "head_sha": head_sha,
                "base_branch": base_branch,
                "status": status,
                "updated_at": now,
            })
            return pr

        pr_id = str(uuid.uuid4())
        pr_record = {
            "id": pr_id,
            "repository_id": repository_id,
            "pr_number": pr_number,
            "title": title,
            "author": author,
            "head_sha": head_sha,
            "base_branch": base_branch,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        self._local_prs[pr_key] = pr_record
        return pr_record

    async def upsert_pull_requests_batch(
        self,
        repository_id: str,
        prs: List[Dict[str, Any]],
    ) -> None:
        """Batch upserts pull request records asynchronously in a thread without blocking the event loop."""
        if not prs:
            return

        now = datetime.now(timezone.utc).isoformat()
        records = []
        for p in prs:
            records.append({
                "repository_id": repository_id,
                "pr_number": p["number"],
                "title": p.get("title", ""),
                "author": p.get("author", "unknown"),
                "head_sha": p.get("head_sha", ""),
                "base_branch": p.get("base_branch", "main"),
                "status": p.get("state", "open"),
                "updated_at": now,
            })

        if self.is_configured:
            def _sync_batch():
                try:
                    chunk_size = 100
                    for i in range(0, len(records), chunk_size):
                        chunk = records[i:i + chunk_size]
                        self.client.table("pull_requests").upsert(
                            chunk, on_conflict="repository_id,pr_number"
                        ).execute()
                except Exception as e:
                    logger.error("Supabase batch PR upsert error: %s", e)
                    raise SupabaseStateError(f"Database error in batch PR upsert: {e}") from e

            import asyncio
            await asyncio.to_thread(_sync_batch)
            return

        # Update local in-memory fallback only when not configured
        for r in records:
            pr_key = f"{repository_id}_{r['pr_number']}"
            if pr_key in self._local_prs:
                self._local_prs[pr_key].update(r)
            else:
                self._local_prs[pr_key] = {**r, "id": str(uuid.uuid4()), "created_at": now}

    async def list_pull_requests(self, repository_id: str) -> List[Dict[str, Any]]:
        """Lists pull requests for a given repository."""
        if self.is_configured:
            try:
                res = self.client.table("pull_requests").select("*").eq("repository_id", repository_id).execute()
                return res.data or []
            except Exception as e:
                logger.error("Supabase PR list error: %s", e)
                raise SupabaseStateError(f"Database error listing pull requests: {e}") from e

        return [pr for pr in self._local_prs.values() if pr["repository_id"] == repository_id]

    async def create_review(
        self,
        pull_request_id: str,
        commit_sha: str,
        status: str = "completed",
        summary: Optional[str] = None,
        drift_detected: bool = False,
        exception_approved: bool = False,
        error_message: Optional[str] = None,
        historical_context_snapshot: Optional[List[Dict[str, Any]]] = None,
        verdict: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a review record in Supabase with optional historical context snapshot and explicit verdict."""
        now = datetime.now(timezone.utc).isoformat()
        if not verdict:
            if drift_detected:
                verdict = "DRIFT"
            elif historical_context_snapshot and len(historical_context_snapshot) > 0:
                verdict = "CONFORMING"
            else:
                verdict = "NO_PRECEDENT"

        if self.is_configured:
            data: Dict[str, Any] = {
                "pull_request_id": pull_request_id,
                "commit_sha": commit_sha,
                "status": status,
                "summary": summary,
                "drift_detected": drift_detected,
                "exception_approved": exception_approved,
                "error_message": error_message,
                "completed_at": now if status in ("completed", "failed") else None,
                "verdict": verdict,
            }
            if historical_context_snapshot is not None:
                data["historical_context_snapshot"] = historical_context_snapshot

            try:
                res = self.client.table("reviews").insert(data).execute()
                if res.data:
                    row = res.data[0]
                    if "verdict" not in row:
                        row["verdict"] = verdict
                    return row
                raise SupabaseStateError("Supabase returned empty data on review insert")
            except Exception as e:
                err_lower = str(e).lower()
                # If remote DB does not yet have verdict or historical_context_snapshot column, retry without them
                if "verdict" in err_lower or "historical_context_snapshot" in err_lower:
                    try:
                        data.pop("historical_context_snapshot", None)
                        data.pop("verdict", None)
                        res = self.client.table("reviews").insert(data).execute()
                        if res.data:
                            row = res.data[0]
                            row["verdict"] = verdict
                            if historical_context_snapshot is not None:
                                row["historical_context_snapshot"] = historical_context_snapshot
                            return row
                    except Exception as retry_err:
                        logger.error("Supabase review insert retry error: %s", retry_err)
                        raise SupabaseStateError(f"Database error creating review: {retry_err}") from retry_err
                logger.error("Supabase review insert error: %s", e)
                raise SupabaseStateError(f"Database error creating review: {e}") from e

        review_id = str(uuid.uuid4())
        review_record: Dict[str, Any] = {
            "id": review_id,
            "pull_request_id": pull_request_id,
            "commit_sha": commit_sha,
            "status": status,
            "summary": summary,
            "drift_detected": drift_detected,
            "exception_approved": exception_approved,
            "error_message": error_message,
            "historical_context_snapshot": historical_context_snapshot,
            "verdict": verdict,
            "created_at": now,
            "completed_at": now if status in ("completed", "failed") else None,
        }
        self._local_reviews[review_id] = review_record
        return review_record

    async def create_review_findings(
        self,
        review_id: str,
        findings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Stores review findings associated with a review, including provenance snapshots."""
        now = datetime.now(timezone.utc).isoformat()
        if not findings:
            return []

        if self.is_configured:
            try:
                rows = []
                for f in findings:
                    row: Dict[str, Any] = {
                        "review_id": review_id,
                        "file_path": f["file_path"],
                        "line_number": f.get("line_number"),
                        "category": f.get("category", "note"),
                        "title": f.get("title", ""),
                        "message": f.get("message", ""),
                        "historical_pr_ref": f.get("historical_pr_ref"),
                        "hindsight_memory_id": f.get("hindsight_memory_id"),
                        "severity": f.get("severity", "warning"),
                    }
                    if f.get("historical_evidence_snapshot") is not None:
                        row["historical_evidence_snapshot"] = f["historical_evidence_snapshot"]
                    if f.get("provenance_status") is not None:
                        row["provenance_status"] = f["provenance_status"]
                    rows.append(row)
                res = self.client.table("review_findings").insert(rows).execute()
                return res.data or []
            except Exception as e:
                err_lower = str(e).lower()
                if "historical_evidence_snapshot" in err_lower or "provenance_status" in err_lower:
                    try:
                        fallback_rows = []
                        for r in rows:
                            cloned = dict(r)
                            cloned.pop("historical_evidence_snapshot", None)
                            cloned.pop("provenance_status", None)
                            fallback_rows.append(cloned)
                        res = self.client.table("review_findings").insert(fallback_rows).execute()
                        return res.data or []
                    except Exception as retry_err:
                        logger.error("Supabase review_findings retry insert error: %s", retry_err)
                        raise SupabaseStateError(f"Database error creating review findings: {retry_err}") from retry_err
                logger.error("Supabase review_findings insert error: %s", e)
                raise SupabaseStateError(f"Database error creating review findings: {e}") from e

        saved = []
        for f in findings:
            finding_id = str(uuid.uuid4())
            item: Dict[str, Any] = {
                "id": finding_id,
                "review_id": review_id,
                "file_path": f["file_path"],
                "line_number": f.get("line_number"),
                "category": f.get("category", "note"),
                "title": f.get("title", ""),
                "message": f.get("message", ""),
                "decision": f.get("decision"),
                "rationale": f.get("rationale"),
                "action": f.get("action"),
                "scope": f.get("scope"),
                "source": f.get("source"),
                "historical_pr_ref": f.get("historical_pr_ref"),
                "hindsight_memory_id": f.get("hindsight_memory_id"),
                "severity": f.get("severity", "warning"),
                "historical_evidence_snapshot": f.get("historical_evidence_snapshot"),
                "provenance_status": f.get("provenance_status", "none"),
                "created_at": now,
            }
            saved.append(item)

        self._local_findings[review_id] = saved
        return saved

    async def get_review_with_findings(self, review_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a review and its associated findings."""
        if self.is_configured:
            try:
                rev_res = self.client.table("reviews").select("*").eq("id", review_id).execute()
                if rev_res.data:
                    review = rev_res.data[0]
                    f_res = self.client.table("review_findings").select("*").eq("review_id", review_id).execute()
                    review["findings"] = f_res.data or []
                    return review
                return None
            except Exception as e:
                logger.error("Supabase get review error: %s", e)
                raise SupabaseStateError(f"Database error fetching review: {e}") from e

        review = self._local_reviews.get(review_id)
        if review:
            review = dict(review)
            review["findings"] = self._local_findings.get(review_id, [])
            return review
        return None

    async def get_latest_review_for_pr(self, repository_id: str, pr_number: int) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent review with findings for a repository PR."""
        if self.is_configured:
            try:
                p_res = (
                    self.client.table("pull_requests")
                    .select("*")
                    .eq("repository_id", repository_id)
                    .eq("pr_number", pr_number)
                    .execute()
                )
                if not p_res.data:
                    return None
                pr = p_res.data[0]
                pr_id = pr["id"]

                r_res = (
                    self.client.table("reviews")
                    .select("*")
                    .eq("pull_request_id", pr_id)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if not r_res.data:
                    return None
                review = r_res.data[0]

                review_with_findings = await self.get_review_with_findings(review["id"])
                if review_with_findings:
                    review_with_findings["pr_title"] = pr.get("title", "")
                    review_with_findings["pr_number"] = pr.get("pr_number", pr_number)
                    review_with_findings["author"] = pr.get("author", "")
                    review_with_findings["repository_id"] = repository_id
                return review_with_findings
            except Exception as e:
                logger.error("Supabase get latest review for PR error: %s", e)
                raise SupabaseStateError(f"Database error fetching latest review: {e}") from e

        # Local fallback only when not configured
        pr_key = f"{repository_id}_{pr_number}"
        pr = self._local_prs.get(pr_key)
        if not pr:
            return None

        pr_id = pr["id"]
        reviews_for_pr = [r for r in self._local_reviews.values() if r.get("pull_request_id") == pr_id]
        if not reviews_for_pr:
            return None
        reviews_for_pr.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        review = reviews_for_pr[0]

        review_with_findings = await self.get_review_with_findings(review["id"])
        if review_with_findings:
            review_with_findings["pr_title"] = pr.get("title", "")
            review_with_findings["pr_number"] = pr.get("pr_number", pr_number)
            review_with_findings["author"] = pr.get("author", "")
            review_with_findings["repository_id"] = repository_id
        return review_with_findings

    async def list_reviews_for_pr(self, repository_id: str, pr_number: int) -> List[Dict[str, Any]]:
        """Retrieves all historical review generations for a repository PR ordered by creation desc."""
        if self.is_configured:
            try:
                p_res = (
                    self.client.table("pull_requests")
                    .select("*")
                    .eq("repository_id", repository_id)
                    .eq("pr_number", pr_number)
                    .execute()
                )
                if not p_res.data:
                    return []
                pr = p_res.data[0]
                pr_id = pr["id"]

                r_res = (
                    self.client.table("reviews")
                    .select("*")
                    .eq("pull_request_id", pr_id)
                    .order("created_at", desc=True)
                    .execute()
                )
                if not r_res.data:
                    return []
                results = []
                for row in r_res.data:
                    rev = await self.get_review_with_findings(row["id"])
                    if rev:
                        rev["pr_title"] = pr.get("title", "")
                        rev["pr_number"] = pr.get("pr_number", pr_number)
                        rev["author"] = pr.get("author", "")
                        rev["repository_id"] = repository_id
                        results.append(rev)
                return results
            except Exception as e:
                logger.error("Supabase list reviews for PR error: %s", e)
                raise SupabaseStateError(f"Database error listing PR reviews: {e}") from e

        pr_key = f"{repository_id}_{pr_number}"
        pr = self._local_prs.get(pr_key)
        if not pr:
            return []
        pr_id = pr["id"]
        reviews_for_pr = [r for r in self._local_reviews.values() if r.get("pull_request_id") == pr_id]
        reviews_for_pr.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        results = []
        for r in reviews_for_pr:
            rev = await self.get_review_with_findings(r["id"])
            if rev:
                rev["pr_title"] = pr.get("title", "")
                rev["pr_number"] = pr.get("pr_number", pr_number)
                rev["author"] = pr.get("author", "")
                rev["repository_id"] = repository_id
                results.append(rev)
        return results

    async def save_fix_candidate(self, candidate_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Persists a proposed or updated Phase 8 fix candidate."""
        fix_id = candidate_dict.get("fix_id") or str(uuid.uuid4())
        candidate_dict["fix_id"] = fix_id
        
        if self.is_configured:
            try:
                # Attempt persistence in review_fixes table if available
                res = self.client.table("review_fixes").upsert(candidate_dict).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.info("Supabase review_fixes table not available, using local store: %s", e)
        
        self._local_fixes[fix_id] = dict(candidate_dict)
        return candidate_dict

    async def get_fix_candidate(self, fix_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single fix candidate by ID."""
        if self.is_configured:
            try:
                res = self.client.table("review_fixes").select("*").eq("fix_id", fix_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.debug("Falling back to local fix store: %s", e)
        return self._local_fixes.get(fix_id)

    async def list_fix_candidates_for_pr(self, repository_id: str, pr_number: int) -> List[Dict[str, Any]]:
        """Lists all fix candidates proposed for a specific PR."""
        if self.is_configured:
            try:
                res = (
                    self.client.table("review_fixes")
                    .select("*")
                    .eq("repository_id", repository_id)
                    .eq("pr_number", pr_number)
                    .order("created_at", desc=True)
                    .execute()
                )
                if res.data:
                    return res.data
            except Exception as e:
                logger.debug("Falling back to local fix store: %s", e)

        fixes = [
            f for f in self._local_fixes.values()
            if f.get("repository_id") == repository_id and f.get("pr_number") == pr_number
        ]
        fixes.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return fixes

    async def update_fix_candidate(self, fix_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Updates attributes of an existing fix candidate."""
        candidate = await self.get_fix_candidate(fix_id)
        if not candidate:
            return None
        candidate.update(updates)
        return await self.save_fix_candidate(candidate)

    async def save_learning_candidate(self, candidate_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Persists or updates a Phase 9 learning candidate."""
        cand_id = candidate_dict.get("candidate_id") or str(uuid.uuid4())
        candidate_dict["candidate_id"] = cand_id
        if "id" not in candidate_dict:
            candidate_dict["id"] = cand_id

        if self.is_configured:
            try:
                res = self.client.table("learning_candidates").upsert(candidate_dict).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.info("Supabase learning_candidates table not available, using local store: %s", e)

        self._local_learning_candidates[cand_id] = dict(candidate_dict)
        return candidate_dict

    async def get_learning_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single learning candidate by ID."""
        if self.is_configured:
            try:
                res = self.client.table("learning_candidates").select("*").eq("candidate_id", candidate_id).execute()
                if res.data:
                    return res.data[0]
            except Exception as e:
                logger.debug("Falling back to local learning candidate store: %s", e)
        return self._local_learning_candidates.get(candidate_id)

    async def list_learning_candidates_for_pr(self, repository_id: str, pr_number: int) -> List[Dict[str, Any]]:
        """Lists all learning candidates for a specific repository PR."""
        if self.is_configured:
            try:
                res = (
                    self.client.table("learning_candidates")
                    .select("*")
                    .eq("repository_id", repository_id)
                    .eq("pr_number", pr_number)
                    .order("created_at", desc=True)
                    .execute()
                )
                if res.data:
                    return res.data
            except Exception as e:
                logger.debug("Falling back to local learning candidate store: %s", e)

        candidates = [
            c for c in self._local_learning_candidates.values()
            if c.get("repository_id") == repository_id and c.get("pr_number") == pr_number
        ]
        candidates.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return candidates

    async def update_learning_candidate(self, candidate_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Updates attributes of an existing learning candidate."""
        cand = await self.get_learning_candidate(candidate_id)
        if not cand:
            return None
        cand.update(updates)
        return await self.save_learning_candidate(cand)

    async def list_recent_reviews(
        self,
        limit: int = 15,
        repository_id: Optional[str] = None,
        repository_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Lists recent reviews with PR and repository context, optionally scoped to a repository or allowed repositories."""
        recent_reviews = []
        candidate_ids: List[str] = []
        if repository_id:
            candidate_ids.append(repository_id)
            repo = await self.get_repository_by_id(repository_id)
            if not repo and "/" in repository_id:
                repo = await self.get_repository_by_name(repository_id)
            if repo:
                if repo.get("id"):
                    candidate_ids.append(repo["id"])
                if repo.get("full_name"):
                    candidate_ids.append(repo["full_name"])
            candidate_ids = list(set(filter(None, candidate_ids)))
        elif repository_ids is not None:
            candidate_ids = list(set(filter(None, repository_ids)))
            if not candidate_ids:
                return []

        if self.is_configured:
            try:
                query = self.client.table("reviews").select(
                    "*, pull_requests(id, pr_number, title, author, repository_id, repositories(id, full_name)), review_findings(*)"
                ).order("created_at", desc=True)

                if candidate_ids:
                    # Fetch PR IDs for scoped repositories to ensure strict database-level scoping
                    pr_res = (
                        self.client.table("pull_requests")
                        .select("id")
                        .in_("repository_id", candidate_ids)
                        .execute()
                    )
                    pr_ids = [p["id"] for p in (pr_res.data or [])]
                    if not pr_ids:
                        return []
                    query = query.in_("pull_request_id", pr_ids)

                r_res = query.limit(limit).execute()
                if r_res.data:
                    for row in r_res.data:
                        pr_info = row.get("pull_requests") or {}
                        repo_info = pr_info.get("repositories") or {}
                        findings = row.get("review_findings") or []
                        drift_detected = bool(row.get("drift_detected", False))
                        recent_reviews.append({
                            "id": row["id"],
                            "pull_request_id": row["pull_request_id"],
                            "commit_sha": row["commit_sha"],
                            "status": row["status"],
                            "summary": row.get("summary"),
                            "drift_detected": drift_detected,
                            "exception_approved": bool(row.get("exception_approved", False)),
                            "verdict": row.get("verdict") or ("DRIFT" if drift_detected else "NO_PRECEDENT"),
                            "created_at": row.get("created_at"),
                            "completed_at": row.get("completed_at"),
                            "pr_number": pr_info.get("pr_number"),
                            "pr_title": pr_info.get("title"),
                            "author": pr_info.get("author"),
                            "repository_id": pr_info.get("repository_id"),
                            "repository_name": repo_info.get("full_name"),
                            "findings_count": len(findings),
                            "findings": findings,
                        })
                    return recent_reviews
                return []
            except Exception as e:
                logger.error("Supabase list recent reviews error: %s", e)
                raise SupabaseStateError(f"Database error listing recent reviews: {e}") from e

        # Local fallback only when not configured
        all_reviews = list(self._local_reviews.values())
        all_reviews.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        for rev in all_reviews:
            pr = next((p for p in self._local_prs.values() if p["id"] == rev.get("pull_request_id")), None)
            if not pr:
                continue
            if candidate_ids and pr.get("repository_id") not in candidate_ids:
                continue
            repo = next((r for r in self._local_repos.values() if r["id"] == pr.get("repository_id") or r.get("full_name") == pr.get("repository_id")), None)
            findings = self._local_findings.get(rev["id"], [])
            drift_detected = bool(rev.get("drift_detected", False))
            recent_reviews.append({
                "id": rev["id"],
                "pull_request_id": rev.get("pull_request_id"),
                "commit_sha": rev.get("commit_sha"),
                "status": rev.get("status"),
                "summary": rev.get("summary"),
                "drift_detected": drift_detected,
                "exception_approved": bool(rev.get("exception_approved", False)),
                "verdict": rev.get("verdict") or ("DRIFT" if drift_detected else "NO_PRECEDENT"),
                "created_at": rev.get("created_at"),
                "completed_at": rev.get("completed_at"),
                "pr_number": pr.get("pr_number") if pr else None,
                "pr_title": pr.get("title") if pr else "",
                "author": pr.get("author") if pr else "",
                "repository_id": pr.get("repository_id") if pr else "",
                "repository_name": repo.get("full_name") if repo else "",
                "findings_count": len(findings),
                "findings": findings,
            })
            if len(recent_reviews) >= limit:
                break
        return recent_reviews

    async def create_engineering_memory(
        self,
        repository_id: str,
        statement: Optional[str] = None,
        claim: Optional[str] = None,
        type: str = "DOCUMENTED_DECISION",
        authority: Optional[str] = None,
        rationale: Optional[str] = None,
        scope: Optional[str] = None,
        status: str = "ACTIVE",
        source_type: str = "manual",
        source_ref: Optional[str] = None,
        source_url: Optional[str] = None,
        source_pr_number: Optional[int] = None,
        source_commit_sha: Optional[str] = None,
        supersedes: Optional[str] = None,
        superseded_by: Optional[str] = None,
        repository_full_name: Optional[str] = None,
        identity_hash: Optional[str] = None,
        review_id: Optional[str] = None,
        pr_number: Optional[int] = None,
        confidence: str = "supported",
        evidence_items: Optional[List[str]] = None,
        hindsight_document_id: Optional[str] = None,
        sync_state: str = "SYNCED",
        observation_count: int = 1,
        first_observed_at: Optional[str] = None,
        last_observed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persists a verified engineering memory into Supabase with structured provenance and deduplication."""
        now = datetime.now(timezone.utc).isoformat()
        mem_id = str(uuid.uuid4())

        effective_statement = normalize_claim_or_statement(statement or claim or "")
        effective_pr = source_pr_number if source_pr_number is not None else pr_number
        k_type = normalize_knowledge_type(type)
        auth = authority or get_default_authority(k_type).value
        norm_status = normalize_status(status).value

        id_hash = identity_hash or compute_memory_identity(
            repository_id=repository_id,
            statement=effective_statement,
            scope=scope,
            knowledge_type=k_type,
        )

        # 1. Deduplication check within the repository
        existing = await self.find_existing_memory(
            repository_id=repository_id,
            claim=effective_statement,
            identity_hash=id_hash,
        )
        if existing:
            existing_evidence = list(existing.get("evidence_items") or [])
            if source_ref and source_ref not in existing_evidence:
                existing_evidence.append(source_ref)
            obs_count = int(existing.get("observation_count") or 1) + 1
            updated = await self.update_engineering_memory(
                existing["id"],
                {
                    "evidence_items": existing_evidence,
                    "confidence": "supported",
                    "status": norm_status,
                    "rationale": rationale or existing.get("rationale"),
                    "scope": scope or existing.get("scope"),
                    "observation_count": obs_count,
                    "last_observed_at": now,
                    "sync_state": sync_state or existing.get("sync_state", "SYNCED"),
                },
            )
            return updated or existing

        evidence = evidence_items or ([source_ref] if source_ref else [])
        effective_type = type if (type and type in ("architecture_decision", "rejected_pattern", "approved_exception", "review_decision", "lesson_learned", "DOCUMENTED_DECISION", "APPROVED_EXCEPTION", "REJECTED_PATTERN", "OBSERVED_CONVENTION", "REVIEW_OBSERVATION")) else k_type.value
        record = {
            "id": mem_id,
            "memory_id": mem_id,
            "repository_id": repository_id,
            "repository_full_name": repository_full_name,
            "type": effective_type,
            "authority": auth,
            "statement": effective_statement,
            "claim": effective_statement,
            "rationale": rationale.strip() if rationale else "Not explicitly documented in available GitHub evidence.",
            "scope": scope.strip() if scope else "Repository architecture",
            "status": norm_status,
            "source_type": source_type,
            "source_ref": source_ref,
            "source_url": source_url,
            "source_pr_number": effective_pr,
            "source_commit_sha": source_commit_sha,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "identity_hash": id_hash,
            "review_id": review_id,
            "pr_number": effective_pr,
            "confidence": confidence,
            "evidence_items": evidence,
            "hindsight_document_id": hindsight_document_id,
            "sync_state": sync_state,
            "observation_count": observation_count,
            "first_observed_at": first_observed_at or now,
            "last_observed_at": last_observed_at or now,
            "created_at": now,
            "updated_at": now,
        }

        if self.is_configured:
            insert_payload = dict(record)
            try:
                res = self.client.table("engineering_memories").insert(insert_payload).execute()
                if res.data:
                    row = res.data[0]
                    # Ensure canonical fields are on returned row
                    for k, v in record.items():
                        if k not in row:
                            row[k] = v
                    return row
                raise SupabaseStateError("Supabase returned empty data on engineering memory insert")
            except Exception as e:
                # If remote DB does not yet have canonical columns, fallback to base legacy columns
                col_err = any(col in str(e).lower() for col in ("statement", "authority", "repository_full_name", "source_commit_sha", "source_pr_number", "identity_hash", "supersedes", "superseded_by", "sync_state", "observation_count", "first_observed_at", "last_observed_at"))
                if col_err:
                    legacy_payload = {
                        "id": mem_id,
                        "repository_id": repository_id,
                        "type": k_type.value.lower() if k_type.value.lower() in ("architecture_decision", "rejected_pattern", "approved_exception", "review_decision", "lesson_learned") else "architecture_decision",
                        "claim": effective_statement,
                        "rationale": record["rationale"],
                        "scope": record["scope"],
                        "status": "active" if norm_status == "ACTIVE" else ("superseded" if norm_status == "SUPERSEDED" else "deprecated"),
                        "source_type": source_type if source_type in ("manual", "pr_review", "github_issue", "team_agreement") else "manual",
                        "source_ref": source_ref,
                        "source_url": source_url,
                        "review_id": review_id,
                        "pr_number": effective_pr,
                        "confidence": confidence,
                        "evidence_items": evidence,
                        "hindsight_document_id": hindsight_document_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                    try:
                        res = self.client.table("engineering_memories").insert(legacy_payload).execute()
                        if res.data:
                            row = res.data[0]
                            for k, v in record.items():
                                if k not in row:
                                    row[k] = v
                            return row
                    except Exception as retry_err:
                        logger.error("Supabase engineering_memories legacy insert error: %s", retry_err)
                        raise SupabaseStateError(f"Database error inserting engineering memory: {retry_err}") from retry_err
                logger.error("Supabase engineering_memories insert error: %s", e)
                raise SupabaseStateError(f"Database error inserting engineering memory: {e}") from e

        self._local_engineering_memories[mem_id] = record
        return record

    async def list_engineering_memories(
        self,
        repository_id: str,
        memory_type: Optional[str] = None,
        status: Optional[str] = "ACTIVE",
        authority: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Lists engineering memories strictly scoped to a repository with canonical status/authority filters."""
        norm_type = normalize_knowledge_type(memory_type).value if memory_type and memory_type != "all" else None
        target_status = normalize_status(status).value if status and status != "all" else None

        if self.is_configured:
            try:
                query = (
                    self.client.table("engineering_memories")
                    .select("*")
                    .eq("repository_id", repository_id)
                )
                res = query.order("created_at", desc=True).limit(limit * 2).execute()
                raw_rows = res.data or []
                matched = []
                for row in raw_rows:
                    row_statement = normalize_claim_or_statement(row.get("statement") or row.get("claim") or "")
                    row["statement"] = row_statement
                    row["claim"] = row_statement
                    row_ktype = normalize_knowledge_type(row.get("type", "DOCUMENTED_DECISION"))
                    row["type"] = row_ktype.value
                    row["authority"] = row.get("authority") or get_default_authority(row_ktype).value
                    row_st = normalize_status(row.get("status", "ACTIVE")).value
                    row["status"] = row_st
                    row["memory_id"] = row.get("id")

                    if norm_type and row["type"] != norm_type:
                        # Check legacy compatibility
                        if memory_type and row.get("type") != memory_type:
                            continue
                    if target_status and row["status"] != target_status:
                        continue
                    if authority and row.get("authority") != authority:
                        continue
                    matched.append(row)
                return matched[:limit]
            except Exception as e:
                logger.error("Supabase engineering_memories list error: %s", e)
                raise SupabaseStateError(f"Database error listing engineering memories: {e}") from e

        # Local fallback
        matched = []
        for mem in self._local_engineering_memories.values():
            if mem.get("repository_id") != repository_id:
                continue
            mem_type_val = normalize_knowledge_type(mem.get("type", "")).value
            if norm_type and mem_type_val != norm_type and mem.get("type") != memory_type:
                continue
            mem_st = normalize_status(mem.get("status", "ACTIVE")).value
            if target_status and mem_st != target_status:
                continue
            if authority and mem.get("authority") != authority:
                continue
            matched.append(mem)
        matched.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matched[:limit]

    async def get_engineering_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetches single engineering memory by ID with canonical enrichment."""
        row = None
        if self.is_configured:
            try:
                res = self.client.table("engineering_memories").select("*").eq("id", memory_id).execute()
                row = res.data[0] if res.data else None
            except Exception as e:
                logger.error("Supabase engineering_memories fetch error: %s", e)
                raise SupabaseStateError(f"Database error fetching engineering memory: {e}") from e
        else:
            row = self._local_engineering_memories.get(memory_id)

        if row:
            row = dict(row)
            row_statement = normalize_claim_or_statement(row.get("statement") or row.get("claim") or "")
            row["statement"] = row_statement
            row["claim"] = row_statement
            row_ktype = normalize_knowledge_type(row.get("type", "DOCUMENTED_DECISION"))
            row["type"] = row_ktype.value
            row["authority"] = row.get("authority") or get_default_authority(row_ktype).value
            row["status"] = normalize_status(row.get("status", "ACTIVE")).value
            row["memory_id"] = row.get("id")
        return row

    async def find_existing_memory(
        self,
        repository_id: str,
        claim: Optional[str] = None,
        statement: Optional[str] = None,
        identity_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Finds existing memory with matching identity or statement in repository for deduplication."""
        target_text = normalize_claim_or_statement(statement or claim or "").lower()

        # Check local storage first
        for mem in self._local_engineering_memories.values():
            if mem.get("repository_id") == repository_id:
                if identity_hash and mem.get("identity_hash") == identity_hash:
                    return mem
                mem_claim = normalize_claim_or_statement(mem.get("statement") or mem.get("claim") or "").lower()
                if target_text and mem_claim == target_text:
                    return mem

        if self.is_configured:
            try:
                res = (
                    self.client.table("engineering_memories")
                    .select("*")
                    .eq("repository_id", repository_id)
                    .execute()
                )
                if res.data:
                    for row in res.data:
                        if identity_hash and row.get("identity_hash") == identity_hash:
                            return row
                        row_claim = normalize_claim_or_statement(row.get("statement") or row.get("claim") or "").lower()
                        if target_text and row_claim == target_text:
                            return row
                return None
            except Exception as e:
                logger.error("Supabase find_existing_memory error: %s", e)
                raise SupabaseStateError(f"Database error finding existing memory: {e}") from e

        return None

    async def update_engineering_memory(
        self,
        memory_id: str,
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Updates an existing engineering memory (e.g. status, evidence, rationale)."""
        now = datetime.now(timezone.utc).isoformat()
        data["updated_at"] = now
        if "status" in data:
            data["status"] = normalize_status(data["status"]).value
        if "type" in data:
            data["type"] = normalize_knowledge_type(data["type"]).value

        if self.is_configured:
            try:
                res = (
                    self.client.table("engineering_memories")
                    .update(data)
                    .eq("id", memory_id)
                    .execute()
                )
                return res.data[0] if res.data else None
            except Exception as e:
                # If error is due to canonical column not on remote table, filter and retry
                col_err = any(c in str(e).lower() for c in ("statement", "authority", "source_pr_number", "identity_hash", "supersedes", "superseded_by", "sync_state", "observation_count", "first_observed_at", "last_observed_at"))
                if col_err:
                    clean_data = {k: v for k, v in data.items() if k not in ("statement", "authority", "source_pr_number", "identity_hash", "supersedes", "superseded_by", "sync_state", "observation_count", "first_observed_at", "last_observed_at")}
                    try:
                        res = self.client.table("engineering_memories").update(clean_data).eq("id", memory_id).execute()
                        return res.data[0] if res.data else None
                    except Exception as retry_err:
                        logger.error("Supabase update_engineering_memory retry error: %s", retry_err)
                        raise SupabaseStateError(f"Database error updating engineering memory: {retry_err}") from retry_err
                logger.error("Supabase update_engineering_memory error: %s", e)
                raise SupabaseStateError(f"Database error updating engineering memory: {e}") from e

        if memory_id in self._local_engineering_memories:
            self._local_engineering_memories[memory_id].update(data)
            return self._local_engineering_memories[memory_id]
        return None

    async def supersede_memory(
        self,
        memory_id: str,
        new_memory_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Supersedes an existing memory with a new active memory."""
        old_mem = await self.get_engineering_memory(memory_id)
        if not old_mem:
            raise SupabaseStateError(f"Engineering memory '{memory_id}' not found to supersede.")

        new_memory_data["supersedes"] = memory_id
        new_memory_data["repository_id"] = old_mem["repository_id"]
        new_memory_data["status"] = "ACTIVE"
        created = await self.create_engineering_memory(**new_memory_data)

        await self.update_engineering_memory(memory_id, {
            "status": "SUPERSEDED",
            "superseded_by": created["id"],
        })
        return created

    async def revoke_memory(self, memory_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Revokes an engineering memory."""
        now = datetime.now(timezone.utc).isoformat()
        return await self.update_engineering_memory(memory_id, {
            "status": "REVOKED",
            "updated_at": now,
        })

    async def delete_engineering_memory(self, memory_id: str) -> bool:
        """Deletes or marks an engineering memory as deprecated."""
        if self.is_configured:
            try:
                self.client.table("engineering_memories").delete().eq("id", memory_id).execute()
                return True
            except Exception as e:
                logger.error("Supabase delete_engineering_memory error: %s", e)
                raise SupabaseStateError(f"Database error deleting engineering memory: {e}") from e

        if memory_id in self._local_engineering_memories:
            del self._local_engineering_memories[memory_id]
            return True
        return False


_supabase_service: Optional[SupabaseService] = None


def get_supabase_service(reload: bool = False) -> SupabaseService:
    """Returns singleton instance of SupabaseService."""
    global _supabase_service
    if reload or _supabase_service is None:
        _supabase_service = SupabaseService()
    return _supabase_service
