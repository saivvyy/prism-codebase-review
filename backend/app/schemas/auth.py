from typing import List, Optional
from pydantic import BaseModel, Field


class UserSessionProfile(BaseModel):
    """Safe authenticated user identity (no server secrets or tokens)."""

    id: str = Field(..., description="Unique internal user ID")
    github_user_id: int = Field(..., description="GitHub numeric user ID")
    login: str = Field(..., description="GitHub username/login")
    name: Optional[str] = Field(None, description="Display name if available")
    avatar_url: Optional[str] = Field(None, description="GitHub avatar image URL")
    email: Optional[str] = Field(None, description="User email if available")


class AuthStatusResponse(BaseModel):
    """Current session authentication status and profile."""

    authenticated: bool = Field(..., description="True if a valid session exists")
    user: Optional[UserSessionProfile] = Field(None, description="Authenticated user profile")


class LoginUrlResponse(BaseModel):
    """Response containing GitHub OAuth login URL with secure CSRF state."""

    url: Optional[str] = Field(None, description="OAuth authorization URL")
    configured: bool = Field(..., description="True if GitHub OAuth is configured")
    client_id: Optional[str] = Field(None, description="Public GitHub OAuth Client ID")
    state: Optional[str] = Field(None, description="Cryptographic CSRF state nonce")


class OAuthCallbackRequest(BaseModel):
    """Payload sent by frontend callback to exchange OAuth code."""

    code: str = Field(..., description="GitHub OAuth authorization code")
    state: str = Field(..., description="CSRF state nonce for validation")


class AuthSessionResponse(BaseModel):
    """Response on successful OAuth code exchange."""

    session_token: str = Field(..., description="PRism application session token (NOT GitHub access token)")
    user: UserSessionProfile = Field(..., description="Authenticated user profile")
    redirect_to: str = Field("/repositories/select", description="Destination path after login")


class UserRepoItem(BaseModel):
    """Repository accessible to the authenticated user on GitHub."""

    id: int = Field(..., description="GitHub repository ID")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Repository full name (owner/name)")
    owner: str = Field(..., description="Owner login")
    owner_avatar_url: Optional[str] = Field(None, description="Owner avatar image URL")
    owner_type: Optional[str] = Field(None, description="Owner type (e.g. User, Organization)")
    private: bool = Field(False, description="Whether repo is private")
    description: Optional[str] = Field(None, description="Repository description")
    default_branch: str = Field("main", description="Default branch name")
    html_url: Optional[str] = Field(None, description="GitHub URL")
    stargazers_count: Optional[int] = Field(0, description="GitHub stargazers count")
    updated_at: Optional[str] = Field(None, description="ISO timestamp of last update")
    access_role: Optional[str] = Field(None, description="Access role context: owner, collaborator, organization_member, etc.")
    connected: bool = Field(False, description="Whether repo is already connected in PRism")
    connected_repo_id: Optional[str] = Field(None, description="Connected repo ID if connected")


class PublicRepoSearchResponse(BaseModel):
    """Response containing paginated results from GitHub public repository search."""

    total_count: int = Field(..., description="Total matching repositories found on GitHub")
    items: List[UserRepoItem] = Field(..., description="List of repositories on current page")
    page: int = Field(1, description="Current page number")
    per_page: int = Field(30, description="Items per page")
