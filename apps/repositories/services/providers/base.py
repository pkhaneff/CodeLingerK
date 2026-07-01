"""
Git Provider base abstractions.

This module defines the abstract interface that all Git providers must implement.
Following Dependency Inversion Principle: depend on abstractions, not concretions.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import TypedDict


class GitProviderType(str, Enum):
    """Supported Git providers."""

    GITHUB = 'github'
    GITLAB = 'gitlab'


class UserInfo(TypedDict):
    """
    Normalized user info from any provider.

    All providers must return user data in this format.
    """

    provider_id: int
    username: str
    email: str | None
    avatar_url: str | None
    name: str | None


class RepoInfo(TypedDict):
    """
    Normalized repository info from any provider.

    All providers must return repository data in this format.
    """

    provider_id: int
    name: str
    full_name: str
    clone_url: str
    html_url: str
    default_branch: str
    private: bool
    description: str | None


class PullRequestInfo(TypedDict):
    """
    Normalized PR/MR info from any provider.

    GitHub calls it Pull Request, GitLab calls it Merge Request.
    This normalizes both to a common structure.
    """

    number: int
    title: str
    state: str
    head_sha: str
    source_branch: str
    target_branch: str
    html_url: str


class FileChange(TypedDict):
    """
    Normalized file change info from PR/MR.

    Represents a single file changed in a pull request.
    """

    filename: str
    status: str  # added, modified, removed, renamed
    patch: str | None
    additions: int
    deletions: int


class ReviewComment(TypedDict):
    """
    Normalized review comment to post on PR/MR.

    Represents an inline comment on a specific file and line.
    """

    path: str
    body: str
    line: int
    side: str  # LEFT or RIGHT


class WebhookInfo(TypedDict):
    """
    Normalized webhook info.

    Represents a webhook created on a repository.
    """

    id: int
    url: str
    events: list[str]
    active: bool


class GitProvider(ABC):
    """
    Abstract base class for Git providers.

    All provider implementations must implement these methods.
    This is the ABSTRACTION that services depend on (Dependency Inversion).

    Example usage:
        class GitHubProvider(GitProvider):
            def __init__(self, access_token: str):
                self.access_token = access_token

            async def get_current_user(self) -> UserInfo:
                # GitHub-specific implementation
                ...

    Services then depend on GitProvider, not GitHubProvider:
        class RepositoryService:
            def __init__(self, provider: GitProvider):
                self.provider = provider  # Could be GitHub or GitLab
    """

    @property
    @abstractmethod
    def provider_type(self) -> GitProviderType:
        """Return the provider type."""
        pass

    # ─────────────────────────────────────────────────────────────
    # User Operations
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_current_user(self) -> UserInfo:
        """
        Get authenticated user info.

        Returns:
            UserInfo with normalized user data
        """
        pass

    # ─────────────────────────────────────────────────────────────
    # Repository Operations
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def list_user_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[RepoInfo]:
        """
        List repositories accessible by authenticated user.

        Args:
            page: Page number (1-indexed)
            per_page: Number of results per page (max 100)

        Returns:
            List of RepoInfo with normalized repository data
        """
        pass

    @abstractmethod
    async def get_repo(self, repo_identifier: str | int) -> RepoInfo:
        """
        Get repository by ID or full_name.

        Args:
            repo_identifier: Repository ID (int) or full_name (str)
                - GitHub: owner/repo or numeric ID
                - GitLab: group/project or numeric ID

        Returns:
            RepoInfo with normalized repository data
        """
        pass

    @abstractmethod
    async def get_repo_by_id(self, repo_id: int) -> RepoInfo:
        """
        Get repository by provider-specific ID.

        Args:
            repo_id: Numeric repository ID from provider

        Returns:
            RepoInfo with normalized repository data
        """
        pass

    # ─────────────────────────────────────────────────────────────
    # Pull Request / Merge Request Operations
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_pr(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> PullRequestInfo:
        """
        Get PR/MR details.

        Args:
            repo_identifier: Repository ID or full_name
            pr_number: Pull request number (iid for GitLab)

        Returns:
            PullRequestInfo with normalized PR data
        """
        pass

    @abstractmethod
    async def get_pr_files(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> list[FileChange]:
        """
        Get list of files changed in PR/MR.

        Args:
            repo_identifier: Repository ID or full_name
            pr_number: Pull request number

        Returns:
            List of FileChange with file diff information
        """
        pass

    @abstractmethod
    async def post_pr_review(
        self,
        repo_identifier: str | int,
        pr_number: int,
        commit_sha: str,
        body: str,
        comments: list[ReviewComment],
    ) -> dict:
        """
        Post review with inline comments on PR/MR.

        Args:
            repo_identifier: Repository ID or full_name
            pr_number: Pull request number
            commit_sha: SHA of the commit to review
            body: Review summary body
            comments: List of inline comments

        Returns:
            Provider-specific response with review ID
        """
        pass

    @abstractmethod
    async def update_pr_body(
        self,
        repo_identifier: str | int,
        pr_number: int,
        body: str,
    ) -> dict:
        """
        Update PR/MR description body.

        Args:
            repo_identifier: Repository ID or full_name
            pr_number: Pull request number
            body: New body content for the PR description

        Returns:
            Provider-specific response with updated PR data
        """
        pass

    # ─────────────────────────────────────────────────────────────
    # Webhook Operations
    # ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def create_webhook(
        self,
        repo_identifier: str | int,
        webhook_url: str,
        secret: str,
        events: list[str],
    ) -> WebhookInfo:
        """
        Create webhook on repository.

        Args:
            repo_identifier: Repository ID or full_name
            webhook_url: URL to receive webhook events
            secret: Secret for signature verification
            events: List of events to subscribe to
                - Use normalized event names: 'pull_request', 'push'
                - Provider will translate to native format

        Returns:
            WebhookInfo with created webhook details
        """
        pass

    @abstractmethod
    async def delete_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> None:
        """
        Delete webhook from repository.

        Args:
            repo_identifier: Repository ID or full_name
            webhook_id: Webhook ID to delete
        """
        pass

    @abstractmethod
    async def get_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> WebhookInfo | None:
        """
        Get webhook details.

        Args:
            repo_identifier: Repository ID or full_name
            webhook_id: Webhook ID

        Returns:
            WebhookInfo or None if not found
        """
        pass
