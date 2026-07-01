"""
API models for webhook payloads and responses
"""
from pydantic import BaseModel, ConfigDict
from typing import List, Optional, Dict, Any


class GitHubCommit(BaseModel):
    """GitHub commit info from webhook"""
    model_config = ConfigDict(extra='ignore')

    id: str
    message: str
    author: Dict[str, Any] = {}
    url: str = ''
    added: List[str] = []
    removed: List[str] = []
    modified: List[str] = []


class GitHubRepository(BaseModel):
    """GitHub repository info"""
    model_config = ConfigDict(extra='ignore')

    name: str
    full_name: str
    html_url: str = ''
    clone_url: str = ''


class GitHubPushPayload(BaseModel):
    """GitHub push event webhook payload"""
    model_config = ConfigDict(extra='ignore')

    ref: str
    before: str
    after: str
    repository: GitHubRepository
    commits: List[GitHubCommit] = []
    head_commit: Optional[GitHubCommit] = None


class GitHubPullRequest(BaseModel):
    """GitHub PR info"""
    model_config = ConfigDict(extra='ignore')

    number: int
    title: str = ''
    state: str = ''
    html_url: str = ''
    head: Dict[str, Any] = {}
    base: Dict[str, Any] = {}


class GitHubPRPayload(BaseModel):
    """GitHub pull request event webhook payload"""
    model_config = ConfigDict(extra='ignore')

    action: str
    number: int
    pull_request: GitHubPullRequest
    repository: GitHubRepository


class ChangeUnitResponse(BaseModel):
    """Simplified change unit for API response"""
    file_path: str
    change_type: str
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    lines: Optional[str] = None  # e.g., "15-44"


class ReviewResponse(BaseModel):
    """Response from code review analysis"""
    status: str
    commit_sha: str
    changes_detected: int
    changes: List[ChangeUnitResponse]
    message: str


# ─────────────────────────────────────────────────────────────
# GitLab Webhook Payload Models
# ─────────────────────────────────────────────────────────────


class GitLabProject(BaseModel):
    """GitLab project info from webhook"""
    model_config = ConfigDict(extra='ignore')

    id: int
    name: str
    path_with_namespace: str
    web_url: str = ''
    http_url_to_repo: str = ''


class GitLabLastCommit(BaseModel):
    """GitLab last commit info"""
    model_config = ConfigDict(extra='ignore')

    id: str
    message: str = ''
    url: str = ''


class GitLabObjectAttributes(BaseModel):
    """GitLab merge request object attributes"""
    model_config = ConfigDict(extra='ignore')

    iid: int
    title: str = ''
    state: str = ''
    action: str | None = None
    source_branch: str = ''
    target_branch: str = ''
    last_commit: GitLabLastCommit


class GitLabMRPayload(BaseModel):
    """GitLab merge request event webhook payload"""
    model_config = ConfigDict(extra='ignore')

    object_kind: str
    event_type: str = ''
    project: GitLabProject
    object_attributes: GitLabObjectAttributes
