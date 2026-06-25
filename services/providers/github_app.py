"""
GitHub App authentication provider.

Uses GitHub App credentials to authenticate as a bot for posting reviews.
Comments posted using App authentication appear as "AppName [bot]".
"""

import time
import logging
from typing import Any

import httpx
import jwt

from infra.config import settings
from services.providers.base import (
    FileChange,
    GitProvider,
    GitProviderType,
    PullRequestInfo,
    RepoInfo,
    ReviewComment,
    UserInfo,
    WebhookInfo,
)

logger = logging.getLogger(__name__)

GITHUB_API_BASE = 'https://api.github.com'


class GitHubAppProvider(GitProvider):
    """
    GitHub provider using App authentication.

    This provider authenticates as a GitHub App (bot) rather than a user.
    All actions (comments, reviews) will appear as coming from the App.

    Usage:
        provider = GitHubAppProvider()
        await provider.post_pr_review(...)  # Comment appears as "CodeLingerK [bot]"
    """

    def __init__(
        self,
        app_id: str | None = None,
        private_key: str | None = None,
        installation_id: str | None = None,
    ):
        """
        Initialize GitHub App provider.

        Args:
            app_id: GitHub App ID (defaults to settings)
            private_key: Private key PEM content (defaults to settings)
            installation_id: Installation ID (defaults to settings)
        """
        self.app_id = app_id or settings.github_app_id
        self.private_key = private_key or settings.github_app_private_key
        self.installation_id = installation_id or settings.github_app_installation_id

        if not all([self.app_id, self.private_key, self.installation_id]):
            raise ValueError(
                'GitHub App not configured. Set GITHUB_APP_ID, '
                'GITHUB_APP_PRIVATE_KEY_PATH, and GITHUB_APP_INSTALLATION_ID'
            )

        self._installation_token: str | None = None
        self._token_expires_at: float = 0

    @property
    def provider_type(self) -> GitProviderType:
        """Return GitHub as provider type."""
        return GitProviderType.GITHUB

    def _generate_jwt(self) -> str:
        """Generate JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            'iat': now - 60,  # Issued 60 seconds ago (clock drift)
            'exp': now + 600,  # Expires in 10 minutes
            'iss': self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm='RS256')

    async def _get_installation_token(self) -> str:
        """
        Get installation access token.

        Caches the token until it expires.
        """
        if self._installation_token and time.time() < self._token_expires_at - 60:
            return self._installation_token

        jwt_token = self._generate_jwt()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f'{GITHUB_API_BASE}/app/installations/{self.installation_id}/access_tokens',
                headers={
                    'Authorization': f'Bearer {jwt_token}',
                    'Accept': 'application/vnd.github+json',
                    'X-GitHub-Api-Version': '2022-11-28',
                },
            )

            if response.status_code >= 400:
                logger.error(f'Failed to get installation token: {response.text}')
                response.raise_for_status()

            data = response.json()
            self._installation_token = data['token']
            # Token expires in 1 hour, parse the expiration time
            # Format: "2024-01-15T12:00:00Z"
            self._token_expires_at = time.time() + 3600  # Default 1 hour

            logger.debug('GitHub App installation token refreshed')
            return self._installation_token

    async def _get_headers(self) -> dict[str, str]:
        """Get headers with installation token."""
        token = await self._get_installation_token()
        return {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict[str, Any] | list[Any]:
        """Make authenticated request to GitHub API using App token."""
        url = f'{GITHUB_API_BASE}{endpoint}'
        headers = await self._get_headers()

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                **kwargs,
            )

            if response.status_code >= 400:
                logger.error(f'GitHub API error: {response.status_code} - {response.text}')
                response.raise_for_status()

            if response.status_code == 204:
                return {}

            return response.json()

    def _parse_repo_identifier(self, repo_identifier: str | int) -> tuple[str, str] | int:
        """Parse repository identifier to owner/repo or ID."""
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
        """Get authenticated app info."""
        data = await self._request('GET', '/app')
        return UserInfo(
            provider_id=data['id'],
            username=data['slug'],
            email=None,
            avatar_url=data.get('owner', {}).get('avatar_url'),
            name=data['name'],
        )

    # ─────────────────────────────────────────────────────────────
    # Repository Operations
    # ─────────────────────────────────────────────────────────────

    async def list_user_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[RepoInfo]:
        """List repositories accessible by the App installation."""
        params = {
            'page': page,
            'per_page': min(per_page, 100),
        }
        data = await self._request(
            'GET',
            '/installation/repositories',
            params=params,
        )
        return [self._normalize_repo(repo) for repo in data.get('repositories', [])]

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
        """
        Post review with inline comments on PR.

        This will appear as coming from "CodeLingerK [bot]".
        """
        parsed = self._parse_repo_identifier(repo_identifier)

        if isinstance(parsed, int):
            repo = await self.get_repo_by_id(parsed)
            owner, name = repo['full_name'].split('/')
        else:
            owner, name = parsed

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

        logger.info(f'Posting review as bot on {owner}/{name}#{pr_number}')

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

        headers = await self._get_headers()
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f'{GITHUB_API_BASE}/repos/{owner}/{name}/hooks/{webhook_id}',
                headers=headers,
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
            result = await self._request(
                'GET',
                f'/repos/{owner}/{name}/hooks/{webhook_id}',
            )
            return self._normalize_webhook(result)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _translate_events(self, events: list[str]) -> list[str]:
        """Translate normalized event names to GitHub events."""
        event_map = {
            'pull_request': 'pull_request',
            'push': 'push',
            'issue': 'issues',
        }
        return [event_map.get(e, e) for e in events]

    def _normalize_webhook(self, data: dict) -> WebhookInfo:
        """Normalize GitHub webhook response."""
        return WebhookInfo(
            id=data['id'],
            url=data['config']['url'],
            events=data['events'],
            active=data['active'],
        )
