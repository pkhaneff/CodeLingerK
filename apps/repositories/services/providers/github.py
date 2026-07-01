"""
GitHub Provider - Implementation of GitProvider for GitHub API.

This module provides GitHub-specific implementation of the GitProvider interface.
All GitHub API interactions are encapsulated here.
"""

from typing import Any

import httpx

from core.logging_config import get_logger
from apps.repositories.services.providers.base import (
    FileChange,
    GitProvider,
    GitProviderType,
    PullRequestInfo,
    RepoInfo,
    ReviewComment,
    UserInfo,
    WebhookInfo,
)

logger = get_logger(__name__)

GITHUB_API_BASE = 'https://api.github.com'


class GitHubProvider(GitProvider):
    """
    GitHub implementation of GitProvider interface.

    Uses GitHub REST API v3 with user's OAuth access token.
    All responses are normalized to common TypedDict formats.
    """

    def __init__(self, access_token: str):
        """
        Initialize GitHub provider with user's access token.

        Args:
            access_token: GitHub OAuth access token
        """
        self.access_token = access_token
        self._headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

    @property
    def provider_type(self) -> GitProviderType:
        """Return GitHub as provider type."""
        return GitProviderType.GITHUB

    # ─────────────────────────────────────────────────────────────
    # Internal HTTP Methods
    # ─────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict[str, Any] | list[Any]:
        """Make authenticated request to GitHub API."""
        url = f'{GITHUB_API_BASE}{endpoint}'

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=self._headers,
                **kwargs,
            )

            if response.status_code >= 400:
                logger.error(f'GitHub API error: {response.status_code} - {response.text}')
                response.raise_for_status()

            # Handle 204 No Content
            if response.status_code == 204:
                return {}

            return response.json()

    def _parse_repo_identifier(self, repo_identifier: str | int) -> tuple[str, str] | int:
        """
        Parse repository identifier to owner/repo or ID.

        Args:
            repo_identifier: Either 'owner/repo' string or numeric ID

        Returns:
            Tuple of (owner, repo) or int ID
        """
        if isinstance(repo_identifier, int):
            return repo_identifier
        if '/' in str(repo_identifier):
            parts = str(repo_identifier).split('/')
            return parts[0], parts[1]
        return int(repo_identifier)

    # ─────────────────────────────────────────────────────────────
    # User Operations
    # ─────────────────────────────────────────────────────────────

    async def get_current_user(self) -> UserInfo:
        """Get authenticated user info."""
        data = await self._request('GET', '/user')
        return UserInfo(
            provider_id=data['id'],
            username=data['login'],
            email=data.get('email'),
            avatar_url=data.get('avatar_url'),
            name=data.get('name'),
        )

    # ─────────────────────────────────────────────────────────────
    # Repository Operations
    # ─────────────────────────────────────────────────────────────

    async def list_user_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[RepoInfo]:
        """List repositories accessible by authenticated user."""
        params = {
            'page': page,
            'per_page': min(per_page, 100),
            'sort': 'updated',
            'direction': 'desc',
            'type': 'all',
        }
        data = await self._request('GET', '/user/repos', params=params)

        return [self._normalize_repo(repo) for repo in data]

    async def get_repo(self, repo_identifier: str | int) -> RepoInfo:
        """Get repository by ID or full_name."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            return await self.get_repo_by_id(parsed)

        owner, repo = parsed
        data = await self._request('GET', f'/repos/{owner}/{repo}')
        return self._normalize_repo(data)

    async def get_repo_by_id(self, repo_id: int) -> RepoInfo:
        """Get repository by GitHub ID."""
        data = await self._request('GET', f'/repositories/{repo_id}')
        return self._normalize_repo(data)

    def _normalize_repo(self, data: dict) -> RepoInfo:
        """Normalize GitHub repo response to RepoInfo."""
        return RepoInfo(
            provider_id=data['id'],
            name=data['name'],
            full_name=data['full_name'],
            clone_url=data['clone_url'],
            html_url=data['html_url'],
            default_branch=data.get('default_branch', 'main'),
            private=data['private'],
            description=data.get('description'),
        )

    # ─────────────────────────────────────────────────────────────
    # Pull Request Operations
    # ─────────────────────────────────────────────────────────────

    async def get_pr(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> PullRequestInfo:
        """Get pull request details."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            # Need to get repo first to get owner/name
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        data = await self._request('GET', f'/repos/{owner}/{name}/pulls/{pr_number}')
        return self._normalize_pr(data)

    async def get_pr_files(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> list[FileChange]:
        """Get list of files changed in PR."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        params = {'per_page': 100}
        data = await self._request(
            'GET',
            f'/repos/{owner}/{name}/pulls/{pr_number}/files',
            params=params,
        )

        return [self._normalize_file_change(f) for f in data]

    async def post_pr_review(
        self,
        repo_identifier: str | int,
        pr_number: int,
        commit_sha: str,
        body: str,
        comments: list[ReviewComment],
    ) -> dict:
        """Post review with inline comments on PR."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        # Convert ReviewComment to GitHub format
        github_comments = [
            {
                'path': c['path'],
                'body': c['body'],
                'line': c['line'],
                'side': c.get('side', 'RIGHT'),
            }
            for c in comments
        ]

        data = {
            'commit_id': commit_sha,
            'body': body,
            'event': 'COMMENT',
            'comments': github_comments,
        }

        return await self._request(
            'POST',
            f'/repos/{owner}/{name}/pulls/{pr_number}/reviews',
            json=data,
        )

    async def update_pr_body(
        self,
        repo_identifier: str | int,
        pr_number: int,
        body: str,
    ) -> dict:
        """Update PR description body."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        return await self._request(
            'PATCH',
            f'/repos/{owner}/{name}/pulls/{pr_number}',
            json={'body': body},
        )

    def _normalize_pr(self, data: dict) -> PullRequestInfo:
        """Normalize GitHub PR response to PullRequestInfo."""
        return PullRequestInfo(
            number=data['number'],
            title=data['title'],
            state=data['state'],
            head_sha=data['head']['sha'],
            source_branch=data['head']['ref'],
            target_branch=data['base']['ref'],
            html_url=data['html_url'],
        )

    def _normalize_file_change(self, data: dict) -> FileChange:
        """Normalize GitHub file change to FileChange."""
        return FileChange(
            filename=data['filename'],
            status=data['status'],
            patch=data.get('patch'),
            additions=data.get('additions', 0),
            deletions=data.get('deletions', 0),
        )

    # ─────────────────────────────────────────────────────────────
    # Webhook Operations
    # ─────────────────────────────────────────────────────────────

    async def create_webhook(
        self,
        repo_identifier: str | int,
        webhook_url: str,
        secret: str,
        events: list[str],
    ) -> WebhookInfo:
        """Create webhook on repository."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        # Translate normalized events to GitHub events
        github_events = self._translate_events(events)

        data = {
            'name': 'web',
            'active': True,
            'events': github_events,
            'config': {
                'url': webhook_url,
                'content_type': 'json',
                'secret': secret,
                'insecure_ssl': '0',
            },
        }

        result = await self._request('POST', f'/repos/{owner}/{name}/hooks', json=data)
        return self._normalize_webhook(result)

    async def delete_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> None:
        """Delete webhook from repository."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f'{GITHUB_API_BASE}/repos/{owner}/{name}/hooks/{webhook_id}',
                headers=self._headers,
            )
            response.raise_for_status()

    async def get_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> WebhookInfo | None:
        """Get webhook details."""
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

        try:
            result = await self._request('GET', f'/repos/{owner}/{name}/hooks/{webhook_id}')
            return self._normalize_webhook(result)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _translate_events(self, events: list[str]) -> list[str]:
        """Translate normalized event names to GitHub event names."""
        event_map = {
            'pull_request': 'pull_request',
            'push': 'push',
            'issue': 'issues',
            'issue_comment': 'issue_comment',
        }
        return [event_map.get(e, e) for e in events]

    def _normalize_webhook(self, data: dict) -> WebhookInfo:
        """Normalize GitHub webhook response to WebhookInfo."""
        return WebhookInfo(
            id=data['id'],
            url=data['config']['url'],
            events=data['events'],
            active=data['active'],
        )


# Register GitHubProvider with factory
from apps.repositories.services.providers.factory import GitProviderFactory

GitProviderFactory.register(GitProviderType.GITHUB, GitHubProvider)
