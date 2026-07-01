"""
GitHub API service - Interact with GitHub REST API.
"""

from typing import Any

import httpx

from infra.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

GITHUB_API_BASE = 'https://api.github.com'


class GitHubService:
    """
    Service for interacting with GitHub API.

    Uses user's OAuth access token for authenticated requests.
    """

    def __init__(self, access_token: str):
        """
        Initialize GitHub service with user's access token.

        Args:
            access_token: GitHub OAuth access token
        """
        self.access_token = access_token
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

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
                headers=self.headers,
                **kwargs,
            )

            if response.status_code >= 400:
                logger.error(f'GitHub API error: {response.status_code} - {response.text}')
                response.raise_for_status()

            return response.json()

    async def get_user(self) -> dict[str, Any]:
        """Get authenticated user info."""
        return await self._request('GET', '/user')

    async def list_user_repos(
        self,
        page: int = 1,
        per_page: int = 30,
        sort: str = 'updated',
        direction: str = 'desc',
    ) -> list[dict[str, Any]]:
        """
        List repositories for authenticated user.

        Args:
            page: Page number
            per_page: Results per page (max 100)
            sort: Sort by (created, updated, pushed, full_name)
            direction: Sort direction (asc, desc)

        Returns:
            List of repository objects
        """
        params = {
            'page': page,
            'per_page': min(per_page, 100),
            'sort': sort,
            'direction': direction,
            'type': 'all',  # all, owner, public, private, member
        }
        return await self._request('GET', '/user/repos', params=params)

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """
        Get repository details.

        Args:
            owner: Repository owner (username or org)
            repo: Repository name

        Returns:
            Repository object
        """
        return await self._request('GET', f'/repos/{owner}/{repo}')

    async def get_repo_by_id(self, repo_id: int) -> dict[str, Any]:
        """
        Get repository by GitHub ID.

        Args:
            repo_id: GitHub repository ID

        Returns:
            Repository object
        """
        return await self._request('GET', f'/repositories/{repo_id}')

    async def list_repo_branches(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """List branches in a repository."""
        params = {'per_page': per_page}
        return await self._request('GET', f'/repos/{owner}/{repo}/branches', params=params)

    async def get_repo_contents(
        self,
        owner: str,
        repo: str,
        path: str = '',
        ref: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """
        Get repository contents at a path.

        Args:
            owner: Repository owner
            repo: Repository name
            path: Path within repository
            ref: Branch, tag, or commit SHA

        Returns:
            File/directory contents
        """
        endpoint = f'/repos/{owner}/{repo}/contents/{path}'
        params = {}
        if ref:
            params['ref'] = ref
        return await self._request('GET', endpoint, params=params)

    async def create_webhook(
        self,
        owner: str,
        repo: str,
        webhook_url: str,
        secret: str,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a webhook on the repository.

        Args:
            owner: Repository owner
            repo: Repository name
            webhook_url: URL to receive webhook events
            secret: Secret for webhook signature verification
            events: List of events to subscribe to

        Returns:
            Created webhook object
        """
        if events is None:
            events = ['push', 'pull_request']

        data = {
            'name': 'web',
            'active': True,
            'events': events,
            'config': {
                'url': webhook_url,
                'content_type': 'json',
                'secret': secret,
                'insecure_ssl': '0',
            },
        }
        return await self._request('POST', f'/repos/{owner}/{repo}/hooks', json=data)

    async def delete_webhook(
        self,
        owner: str,
        repo: str,
        hook_id: int,
    ) -> None:
        """Delete a webhook from repository."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f'{GITHUB_API_BASE}/repos/{owner}/{repo}/hooks/{hook_id}',
                headers=self.headers,
            )
            response.raise_for_status()

    async def list_webhooks(
        self,
        owner: str,
        repo: str,
    ) -> list[dict[str, Any]]:
        """List webhooks on a repository."""
        return await self._request('GET', f'/repos/{owner}/{repo}/hooks')

    async def get_webhook(
        self,
        owner: str,
        repo: str,
        hook_id: int,
    ) -> dict[str, Any] | None:
        """
        Get a specific webhook by ID.

        Returns:
            Webhook data if exists, None if not found (404).
        """
        try:
            return await self._request('GET', f'/repos/{owner}/{repo}/hooks/{hook_id}')
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> dict[str, Any]:
        """Get pull request details."""
        return await self._request('GET', f'/repos/{owner}/{repo}/pulls/{pr_number}')

    async def create_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        event: str = 'COMMENT',
        comments: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Create a review on a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            body: Review body text
            event: Review event (APPROVE, REQUEST_CHANGES, COMMENT)
            comments: List of line comments

        Returns:
            Created review object
        """
        data = {
            'body': body,
            'event': event,
        }
        if comments:
            data['comments'] = comments

        return await self._request(
            'POST',
            f'/repos/{owner}/{repo}/pulls/{pr_number}/reviews',
            json=data,
        )

    async def create_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Create a comment on a pull request."""
        return await self._request(
            'POST',
            f'/repos/{owner}/{repo}/issues/{pr_number}/comments',
            json={'body': body},
        )

    async def get_pull_request_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get list of files changed in a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            per_page: Results per page (max 100)

        Returns:
            List of file objects with filename, status, additions, deletions, patch
        """
        params = {'per_page': min(per_page, 100)}
        return await self._request(
            'GET',
            f'/repos/{owner}/{repo}/pulls/{pr_number}/files',
            params=params,
        )

    async def create_pr_review_with_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str = '',
        event: str = 'COMMENT',
        comments: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Create a review on a PR with multiple inline comments at once.

        This is the preferred way to add inline comments on file diffs.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number
            commit_id: SHA of the head commit
            body: Review body text (summary)
            event: Review event (APPROVE, REQUEST_CHANGES, COMMENT)
            comments: List of comment objects with:
                - path: File path (required)
                - body: Comment text (required)
                - line: Line number in new file (required for single line)
                - side: 'LEFT' (old) or 'RIGHT' (new), default 'RIGHT'
                - start_line: For multi-line comments
                - start_side: Side for start_line

        Returns:
            Created review object
        """
        data = {
            'commit_id': commit_id,
            'body': body,
            'event': event,
        }

        if comments:
            data['comments'] = comments

        return await self._request(
            'POST',
            f'/repos/{owner}/{repo}/pulls/{pr_number}/reviews',
            json=data,
        )
