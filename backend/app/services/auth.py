from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import uuid
import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.schemas.auth import PublicRepoSearchResponse, UserRepoItem, UserSessionProfile
from app.services.github import GitHubService
from app.services.supabase import SupabaseService

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SESSIONS_FILE = _DATA_DIR / "local_sessions.json"
_STATE_TTL_SECONDS = 600  # 10 minutes
_SESSION_TTL_SECONDS = 86400 * 30  # 30 days


class AuthService:
    """Manages GitHub OAuth flow, CSRF state validation, and secure session management.

    CRITICAL SECURITY INVARIANTS:
    1. GitHub access tokens are stored strictly on the server/backend.
    2. The frontend only receives an opaque PRism application session token.
    3. CSRF state nonces are cryptographically random, expire in 10 minutes, and are single-use.
    4. OAuth credentials are read from environment variables and never hardcoded.
    """

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.GITHUB_CLIENT_ID
        self.client_secret = settings.GITHUB_CLIENT_SECRET
        self.default_redirect_uri = settings.GITHUB_REDIRECT_URI

        # In-memory maps
        self._pending_states: Dict[str, float] = {}  # state -> timestamp
        self._sessions: Dict[str, Dict[str, Any]] = {}  # session_token -> session_data
        self._load_local_sessions()

    @property
    def is_configured(self) -> bool:
        """Returns True if GitHub OAuth credentials (Client ID and Secret) are configured."""
        return bool(self.client_id and self.client_secret)

    def _load_local_sessions(self) -> None:
        """Loads sessions from local disk for persistence across server restarts in dev."""
        try:
            if _SESSIONS_FILE.exists():
                with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        now = time.time()
                        # Only restore non-expired sessions
                        for token, s in data.items():
                            if s.get("expires_at", 0) > now:
                                self._sessions[token] = s
        except Exception as e:
            logger.warning("Could not load local session state: %s", e)

    def _save_local_sessions(self) -> None:
        """Saves current active sessions to disk."""
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(_SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, indent=2)
        except Exception as e:
            logger.warning("Could not save local session state: %s", e)

    def purge_expired_states(self) -> None:
        """Removes CSRF states older than TTL."""
        now = time.time()
        expired = [s for s, t in self._pending_states.items() if now - t > _STATE_TTL_SECONDS]
        for s in expired:
            self._pending_states.pop(s, None)

    def generate_state_nonce(self) -> str:
        """Generates a secure, cryptographically random CSRF state nonce."""
        self.purge_expired_states()
        state = secrets.token_urlsafe(32)
        self._pending_states[state] = time.time()
        return state

    def validate_and_consume_state(self, state: str) -> bool:
        """Validates that state exists, is not expired, and consumes it (single-use)."""
        self.purge_expired_states()
        if not state:
            return False
        timestamp = self._pending_states.pop(state, None)
        if timestamp is None:
            return False
        return (time.time() - timestamp) <= _STATE_TTL_SECONDS

    def get_authorization_url(
        self, redirect_uri: Optional[str] = None
    ) -> Tuple[Optional[str], bool, Optional[str], Optional[str]]:
        """Constructs GitHub OAuth authorization URL with CSRF state nonce.

        Returns: (url, is_configured, client_id, state)
        """
        if not self.is_configured:
            return None, False, None, None

        effective_redirect = (
            redirect_uri
            or self.default_redirect_uri
            or "http://localhost:5173/auth/callback"
        )
        state = self.generate_state_nonce()

        params = {
            "client_id": self.client_id,
            "redirect_uri": effective_redirect,
            "scope": "read:user,repo",
            "state": state,
        }
        auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
        return auth_url, True, self.client_id, state

    async def exchange_code_for_token(
        self, code: str, redirect_uri: Optional[str] = None
    ) -> str:
        """Exchanges GitHub authorization code for server-side access token."""
        if not self.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub OAuth is not configured on this server.",
            )

        effective_redirect = (
            redirect_uri
            or self.default_redirect_uri
            or "http://localhost:5173/auth/callback"
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "PRism-CodeReview",
                    },
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "redirect_uri": effective_redirect,
                    },
                )
            except Exception as e:
                logger.error("Failed to reach GitHub OAuth token endpoint: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to communicate with GitHub OAuth service: {e}",
                )

        if res.is_error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"GitHub OAuth token exchange failed with status {res.status_code}.",
            )

        data = res.json()
        if "error" in data:
            error_desc = data.get("error_description", data.get("error"))
            logger.warning("GitHub OAuth token error: %s (%s)", data.get("error"), error_desc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub OAuth authorization failed: {error_desc}",
            )

        access_token = data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="GitHub did not return an access token.",
            )

        return access_token

    async def fetch_github_user_profile(self, access_token: str) -> UserSessionProfile:
        """Fetches the authenticated user profile from GitHub API using the server-side access token."""
        gh = GitHubService(token=access_token)
        try:
            gh_user = await gh.get_authenticated_user()
        except Exception as e:
            logger.error("Failed to fetch user profile with OAuth access token: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch GitHub profile for authenticated user: {e}",
            )

        # Generate a deterministic internal UUID for this GitHub user identity
        user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"github.user.{gh_user.id}"))
        return UserSessionProfile(
            id=user_uuid,
            github_user_id=gh_user.id,
            login=gh_user.login,
            name=gh_user.name,
            avatar_url=gh_user.avatar_url,
            email=None,
        )

    async def create_session(
        self,
        user: UserSessionProfile,
        github_access_token: str,
        supabase_service: Optional[SupabaseService] = None,
    ) -> str:
        """Establishes an authenticated session, stores token securely server-side, and returns session token."""
        session_token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + _SESSION_TTL_SECONDS

        session_record = {
            "session_token": session_token,
            "user": user.model_dump(),
            "github_access_token": github_access_token,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        }
        self._sessions[session_token] = session_record
        self._save_local_sessions()

        # Persist profile in Supabase if configured
        if supabase_service and supabase_service.is_configured:
            try:
                supabase_service.client.table("profiles").upsert(
                    {
                        "id": user.id,
                        "github_username": user.login,
                        "avatar_url": user.avatar_url,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).execute()
            except Exception as e:
                logger.debug("Non-critical profile upsert to Supabase: %s", e)

        return session_token

    def get_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """Retrieves and validates an active session by session token."""
        if not session_token:
            return None
        session = self._sessions.get(session_token)
        if not session:
            return None
        if session.get("expires_at", 0) <= time.time():
            self._sessions.pop(session_token, None)
            self._save_local_sessions()
            return None
        return session

    def get_user_profile(self, session_token: str) -> Optional[UserSessionProfile]:
        """Returns the safe UserSessionProfile for a session."""
        session = self.get_session(session_token)
        if not session:
            return None
        return UserSessionProfile(**session["user"])

    def delete_session(self, session_token: str) -> bool:
        """Revokes and removes an authenticated session."""
        if session_token in self._sessions:
            del self._sessions[session_token]
            self._save_local_sessions()
            return True
        return False

    def get_github_access_token(self, session_token: str) -> Optional[str]:
        """Returns the GitHub OAuth access token for an active session."""
        session = self.get_session(session_token)
        if not session:
            return None
        return session.get("github_access_token")

    @staticmethod
    def _determine_access_role(
        user_login: str,
        owner: str,
        owner_type: Optional[str],
        permissions: Optional[Dict[str, bool]],
        private: bool,
    ) -> str:
        """Determines the user's access context to the repository."""
        if user_login and owner.lower() == user_login.lower():
            return "owner"
        if permissions:
            if permissions.get("admin"):
                return "admin"
            if permissions.get("maintain"):
                return "maintainer"
            if permissions.get("push"):
                return "write"
            if permissions.get("triage"):
                return "triage"
            if permissions.get("pull"):
                return "read"
        if owner_type == "Organization":
            return "organization_member"
        if private:
            return "collaborator"
        return "public"

    async def get_user_repositories(
        self,
        session_token: str,
        supabase_service: SupabaseService,
        page: int = 1,
        per_page: int = 30,
    ) -> List[UserRepoItem]:
        """Lists repositories from GitHub accessible to this user, annotated with PRism connection status.

        Strictly scopes connected status to the authenticated user's own connections.
        """
        session = self.get_session(session_token)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Active session required to fetch user repositories.",
            )

        token = session.get("github_access_token")
        if not token:
            # Fallback to configured GITHUB_TOKEN if in dev
            settings = get_settings()
            token = settings.GITHUB_TOKEN

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No GitHub access token available in current session.",
            )

        user_login = session.get("user", {}).get("login", "")
        user_internal_id = session.get("user", {}).get("id")

        gh = GitHubService(token=token)
        try:
            gh_repos = await gh.list_user_repositories(
                page=page,
                per_page=per_page,
                affiliation="owner,collaborator,organization_member",
            )
        except Exception as e:
            logger.error("Failed to fetch user repositories from GitHub API: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch repositories from GitHub: {e}",
            )

        # Cross-reference with existing connected repositories in PRism SCOPED TO THIS USER ONLY
        connected_repos = await supabase_service.list_repositories(user_id=user_internal_id)
        connected_map = {r["github_repo_id"]: r["id"] for r in connected_repos}
        connected_by_name = {r["full_name"].lower(): r["id"] for r in connected_repos}

        items: List[UserRepoItem] = []
        for r in gh_repos:
            conn_id = connected_map.get(r.id) or connected_by_name.get(r.full_name.lower())
            role = self._determine_access_role(
                user_login=user_login,
                owner=r.owner,
                owner_type=r.owner_type,
                permissions=r.permissions,
                private=r.private,
            )
            items.append(
                UserRepoItem(
                    id=r.id,
                    name=r.name,
                    full_name=r.full_name,
                    owner=r.owner,
                    owner_avatar_url=r.owner_avatar_url,
                    owner_type=r.owner_type,
                    private=r.private,
                    description=r.description,
                    default_branch=r.default_branch,
                    html_url=r.html_url,
                    stargazers_count=r.stargazers_count or 0,
                    updated_at=r.updated_at,
                    access_role=role,
                    connected=bool(conn_id),
                    connected_repo_id=conn_id,
                )
            )
        return items

    async def search_public_repositories(
        self,
        query: str,
        session_token: Optional[str],
        supabase_service: SupabaseService,
        page: int = 1,
        per_page: int = 30,
    ) -> PublicRepoSearchResponse:
        """Searches GitHub public repositories without automatically connecting them."""
        token = None
        user_internal_id = None
        user_login = ""

        if session_token:
            session = self.get_session(session_token)
            if session:
                token = session.get("github_access_token")
                user_internal_id = session.get("user", {}).get("id")
                user_login = session.get("user", {}).get("login", "")

        if not token:
            settings = get_settings()
            token = settings.GITHUB_TOKEN

        gh = GitHubService(token=token)
        try:
            total_count, gh_repos = await gh.search_public_repositories(
                query=query,
                page=page,
                per_page=per_page,
            )
        except Exception as e:
            logger.error("Failed to search public repositories from GitHub API: %s", e)
            if "rate limit" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="GitHub API rate limit exceeded while searching. Please try again in a few moments.",
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to search GitHub repositories: {e}",
            )

        # Cross-reference with connected repositories scoped to this user only
        connected_repos = await supabase_service.list_repositories(user_id=user_internal_id) if user_internal_id else []
        connected_map = {r["github_repo_id"]: r["id"] for r in connected_repos}
        connected_by_name = {r["full_name"].lower(): r["id"] for r in connected_repos}

        items: List[UserRepoItem] = []
        for r in gh_repos:
            conn_id = connected_map.get(r.id) or connected_by_name.get(r.full_name.lower())
            role = self._determine_access_role(
                user_login=user_login,
                owner=r.owner,
                owner_type=r.owner_type,
                permissions=r.permissions,
                private=r.private,
            )
            items.append(
                UserRepoItem(
                    id=r.id,
                    name=r.name,
                    full_name=r.full_name,
                    owner=r.owner,
                    owner_avatar_url=r.owner_avatar_url,
                    owner_type=r.owner_type,
                    private=r.private,
                    description=r.description,
                    default_branch=r.default_branch,
                    html_url=r.html_url,
                    stargazers_count=r.stargazers_count or 0,
                    updated_at=r.updated_at,
                    access_role=role,
                    connected=bool(conn_id),
                    connected_repo_id=conn_id,
                )
            )

        return PublicRepoSearchResponse(
            total_count=total_count,
            items=items,
            page=page,
            per_page=per_page,
        )


_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """Dependency provider for AuthService singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
