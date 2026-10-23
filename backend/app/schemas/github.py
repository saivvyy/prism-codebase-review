from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    login: str
    id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None


class GitHubRepo(BaseModel):
    id: int
    full_name: str
    name: str
    owner: str
    owner_avatar_url: Optional[str] = None
    owner_type: Optional[str] = None
    default_branch: str = "main"
    private: bool = False
    description: Optional[str] = None
    html_url: Optional[str] = None
    stargazers_count: Optional[int] = 0
    updated_at: Optional[str] = None
    permissions: Optional[Dict[str, bool]] = None


class ConnectRepoRequest(BaseModel):
    full_name: str = Field(..., description="Repository in 'owner/repo' format", examples=["octocat/Hello-World"])
    github_repo_id: Optional[int] = Field(None, description="Canonical numeric GitHub repository ID")


class ConnectedRepo(BaseModel):
    id: str
    full_name: str
    github_repo_id: int
    default_branch: str = "main"
    webhook_active: bool = False
    created_at: Optional[str] = None
    pull_requests_count: Optional[int] = None
    open_pr_count: Optional[int] = None


class GitHubPR(BaseModel):
    id: int
    number: int
    title: str
    state: str = "open"
    author: str
    head_sha: str
    base_branch: str
    base_sha: Optional[str] = None
    html_url: Optional[str] = None
    body: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
    merged_at: Optional[str] = None
    is_merged: bool = False


class GitHubFileDiff(BaseModel):
    filename: str
    status: str  # modified, added, removed
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: Optional[str] = None


class GitHubCommit(BaseModel):
    sha: str
    author_name: str
    message: str
    committed_at: Optional[str] = None


class GitHubComment(BaseModel):
    id: int
    user: str
    body: str
    path: Optional[str] = None
    line: Optional[int] = None
    created_at: Optional[str] = None


class PRDetailResponse(BaseModel):
    pr: GitHubPR
    files: List[GitHubFileDiff] = []
    commits: List[GitHubCommit] = []
    comments: List[GitHubComment] = []


class GitHubPRListResponse(BaseModel):
    items: List[GitHubPR] = []
    total_fetched: int = 0
    pages_fetched: int = 0
    per_page: int = 100
    fetch_limit: int = 500
    complete: bool = True
    truncated: bool = False
    fetch_duration_ms: float = 0.0
    last_synced_at: Optional[str] = None
    total_repository_prs: Optional[int] = None
    open_pr_count: Optional[int] = None

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

