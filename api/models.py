"""
API models for webhook payloads and responses
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class GitHubCommit(BaseModel):
    """GitHub commit info from webhook"""
    id: str
    message: str
    author: Dict[str, str]
    url: str
    added: List[str] = []
    removed: List[str] = []
    modified: List[str] = []


class GitHubRepository(BaseModel):
    """GitHub repository info"""
    name: str
    full_name: str
    html_url: str
    clone_url: str


class GitHubPushPayload(BaseModel):
    """GitHub push event webhook payload"""
    ref: str
    before: str
    after: str
    repository: GitHubRepository
    commits: List[GitHubCommit]
    head_commit: Optional[GitHubCommit] = None


class GitHubPullRequest(BaseModel):
    """GitHub PR info"""
    number: int
    title: str
    state: str
    html_url: str
    head: Dict[str, Any]
    base: Dict[str, Any]


class GitHubPRPayload(BaseModel):
    """GitHub pull request event webhook payload"""
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
