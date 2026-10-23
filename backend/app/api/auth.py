import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.schemas.auth import (
    AuthSessionResponse,
    AuthStatusResponse,
    LoginUrlResponse,
    OAuthCallbackRequest,
    PublicRepoSearchResponse,
    UserRepoItem,
    UserSessionProfile,
)
from app.services.auth import AuthService, get_auth_service
from app.services.supabase import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def extract_session_token(
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None),
) -> Optional[str]:
    """Extracts session token from Authorization: Bearer <token> or X-Session-Token header."""
    if x_session_token and x_session_token.strip():
        return x_session_token.strip()
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()
    return None


def get_optional_current_user(
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
) -> Optional[UserSessionProfile]:
    """Dependency that returns the current authenticated user profile, or None if unauthenticated."""
    if not session_token:
        return None
    return auth.get_user_profile(session_token)


def get_current_user_required(
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
) -> UserSessionProfile:
    """Dependency that requires a valid authenticated user session."""
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    user = auth.get_user_profile(session_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        )
    return user


@router.get("/github/login", response_model=LoginUrlResponse)
async def get_github_login_url(
    redirect_uri: Optional[str] = Query(None, description="Optional custom frontend callback URL"),
    auth: AuthService = Depends(get_auth_service),
) -> LoginUrlResponse:
    """Generates the GitHub OAuth authorization URL with a cryptographically secure CSRF state nonce.

    If GitHub OAuth is not configured on the backend, configured will be False and url None.
    """
    url, configured, client_id, state = auth.get_authorization_url(redirect_uri=redirect_uri)
    return LoginUrlResponse(
        url=url,
        configured=configured,
        client_id=client_id,
        state=state,
    )


@router.post("/github/callback", response_model=AuthSessionResponse)
async def handle_github_oauth_callback(
    req: OAuthCallbackRequest,
    auth: AuthService = Depends(get_auth_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> AuthSessionResponse:
    """Securely handles the GitHub OAuth callback:
    1. Validates cryptographic state nonce to prevent CSRF attacks.
    2. Exchanges authorization code for server-side GitHub access token.
    3. Fetches authenticated GitHub profile.
    4. Establishes a server-side session and returns an opaque session token.

    Note: The GitHub access token is never returned or leaked to the client.
    """
    # 1. State validation (CSRF defense)
    if not auth.validate_and_consume_state(req.state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state parameter. Please start the login flow again.",
        )

    # 2. Token exchange (server-to-server)
    access_token = await auth.exchange_code_for_token(req.code)

    # 3. Fetch user profile
    user_profile = await auth.fetch_github_user_profile(access_token)

    # 4. Create persistent session
    session_token = await auth.create_session(
        user=user_profile,
        github_access_token=access_token,
        supabase_service=supabase,
    )

    logger.info("Successfully established PRism session for GitHub user @%s (%s)", user_profile.login, user_profile.id)

    return AuthSessionResponse(
        session_token=session_token,
        user=user_profile,
        redirect_to="/repositories/select",
    )


@router.get("/me", response_model=AuthStatusResponse)
async def get_current_user(
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
) -> AuthStatusResponse:
    """Returns the current authenticated user identity if a valid session exists.

    Restores session on page reload or navigation without requiring re-authentication.
    """
    if not session_token:
        return AuthStatusResponse(authenticated=False, user=None)

    profile = auth.get_user_profile(session_token)
    if not profile:
        return AuthStatusResponse(authenticated=False, user=None)

    return AuthStatusResponse(authenticated=True, user=profile)


@router.post("/logout")
async def logout(
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
):
    """Revokes the current PRism session."""
    if session_token:
        auth.delete_session(session_token)
    return {"success": True, "message": "Logged out successfully."}


@router.get("/github/repositories", response_model=List[UserRepoItem])
async def list_user_github_repositories(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(30, ge=1, le=100, description="Items per page"),
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> List[UserRepoItem]:
    """Lists repositories accessible to the authenticated GitHub user for selection.

    Each repository includes connection status in PRism scoped to this authenticated user.
    """
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication session required to list GitHub repositories.",
        )

    return await auth.get_user_repositories(
        session_token=session_token,
        supabase_service=supabase,
        page=page,
        per_page=per_page,
    )


@router.get("/github/search", response_model=PublicRepoSearchResponse)
async def search_public_github_repositories(
    q: str = Query(..., min_length=2, description="Search term for public GitHub repositories"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(30, ge=1, le=100, description="Items per page"),
    session_token: Optional[str] = Depends(extract_session_token),
    auth: AuthService = Depends(get_auth_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> PublicRepoSearchResponse:
    """Queries GitHub's public repository search API without automatically connecting results."""
    clean_q = q.strip()
    if len(clean_q) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must contain at least 2 characters.",
        )
    return await auth.search_public_repositories(
        query=clean_q,
        session_token=session_token,
        supabase_service=supabase,
        page=page,
        per_page=per_page,
    )
