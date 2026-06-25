"""
GitLab Provider - Implementation of GitProvider for GitLab API.

This module provides GitLab-specific implementation of the GitProvider interface.
All GitLab API interactions are encapsulated here.

Supports gitlab.com only.
"""

from typing import Any
from urllib.parse import quote

import httpx

from core.logging_config import get_logger
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

logger = get_logger(__name__)

GITLAB_API_BASE = 'https://gitlab.com/api/v4'


class GitLabProvider(GitProvider):
    """
    GitLab implementation of GitProvider interface.

    Uses GitLab REST API v4 with user's OAuth access token.
    All responses are normalized to common TypedDict formats.

    Note: GitLab uses different terminology:
    - Repository = Project
    - Pull Request = Merge Request
    - PR Number = MR IID (Internal ID)
    """

    def __init__(self, access_token: str):
        """
        Initialize GitLab provider with user's access token.

        Args:
            access_token: GitLab OAuth access token
        """
        self.access_token = access_token
        self._headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }

    @property
    def provider_type(self) -> GitProviderType:
        """Return GitLab as provider type."""
        return GitProviderType.GITLAB

    # ─────────────────────────────────────────────────────────────
    # Internal HTTP Methods
    # ─────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> dict[str, Any] | list[Any]:
        """Make authenticated request to GitLab API."""
        url = f'{GITLAB_API_BASE}{endpoint}'

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=self._headers,
                **kwargs,
            )

            if response.status_code >= 400:
                logger.error(f'GitLab API error: {response.status_code} - {response.text}')
                response.raise_for_status()

            # Handle 204 No Content
            if response.status_code == 204:
                return {}

            return response.json()

    def _encode_project_path(self, path: str) -> str:
        """
        URL-encode project path for GitLab API.

        GitLab requires project paths to be URL-encoded.
        Example: 'group/project' -> 'group%2Fproject'
        """
        return quote(path, safe='')

    def _parse_repo_identifier(self, repo_identifier: str | int) -> str:
        """
        Parse repository identifier to GitLab project ID or encoded path.

        Args:
            repo_identifier: Either 'group/project' string or numeric ID

        Returns:
            String suitable for GitLab API (encoded path or ID string)
        """
        if isinstance(repo_identifier, int):
            return str(repo_identifier)
        # If it's a path, URL-encode it
        if '/' in str(repo_identifier):
            return self._encode_project_path(str(repo_identifier))
        return str(repo_identifier)

    # ─────────────────────────────────────────────────────────────
    # User Operations
    # ─────────────────────────────────────────────────────────────

    async def get_current_user(self) -> UserInfo:
        """Get authenticated user info."""
        data = await self._request('GET', '/user')
        return UserInfo(
            provider_id=data['id'],
            username=data['username'],
            email=data.get('email'),
            avatar_url=data.get('avatar_url'),
            name=data.get('name'),
        )

    # ─────────────────────────────────────────────────────────────
    # Repository (Project) Operations
    # ─────────────────────────────────────────────────────────────

    async def list_user_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[RepoInfo]:
        """List projects accessible by authenticated user."""
        params = {
            'membership': True,
            'page': page,
            'per_page': min(per_page, 100),
            'order_by': 'updated_at',
            'sort': 'desc',
        }
        data = await self._request('GET', '/projects', params=params)

        return [self._normalize_repo(project) for project in data]

    async def get_repo(self, repo_identifier: str | int) -> RepoInfo:
        """Get project by ID or path."""
        project_id = self._parse_repo_identifier(repo_identifier)
        data = await self._request('GET', f'/projects/{project_id}')
        return self._normalize_repo(data)

    async def get_repo_by_id(self, repo_id: int) -> RepoInfo:
        """Get project by GitLab ID."""
        data = await self._request('GET', f'/projects/{repo_id}')
        return self._normalize_repo(data)

    def _normalize_repo(self, data: dict) -> RepoInfo:
        """Normalize GitLab project response to RepoInfo."""
        return RepoInfo(
            provider_id=data['id'],
            name=data['name'],
            full_name=data['path_with_namespace'],
            clone_url=data['http_url_to_repo'],
            html_url=data['web_url'],
            default_branch=data.get('default_branch', 'main'),
            private=data['visibility'] == 'private',
            description=data.get('description'),
        )

    # ─────────────────────────────────────────────────────────────
    # Merge Request Operations
    # ─────────────────────────────────────────────────────────────

    async def get_pr(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> PullRequestInfo:
        """
        Get merge request details.

        Args:
            repo_identifier: Project ID or path
            pr_number: MR IID (internal ID within project)
        """
        project_id = self._parse_repo_identifier(repo_identifier)
        data = await self._request('GET', f'/projects/{project_id}/merge_requests/{pr_number}')
        return self._normalize_mr(data)

    async def get_pr_files(
        self,
        repo_identifier: str | int,
        pr_number: int,
    ) -> list[FileChange]:
        """
        Get list of files changed in merge request.

        GitLab provides changes in the MR changes endpoint.
        """
        project_id = self._parse_repo_identifier(repo_identifier)
        data = await self._request(
            'GET',
            f'/projects/{project_id}/merge_requests/{pr_number}/changes',
        )

        changes = data.get('changes', [])
        return [self._normalize_file_change(c) for c in changes]

    async def post_pr_review(
        self,
        repo_identifier: str | int,
        pr_number: int,
        commit_sha: str,
        body: str,
        comments: list[ReviewComment],
    ) -> dict:
        """
        Post review with inline comments on merge request.

        GitLab uses discussions for inline comments.
        Each comment creates a new discussion thread.
        """
        project_id = self._parse_repo_identifier(repo_identifier)

        # First, post the summary as a note
        await self._request(
            'POST',
            f'/projects/{project_id}/merge_requests/{pr_number}/notes',
            json={'body': body},
        )

        # Then post each inline comment as a discussion
        results = []
        for comment in comments:
            discussion_data = {
                'body': comment['body'],
                'position': {
                    'base_sha': commit_sha,
                    'start_sha': commit_sha,
                    'head_sha': commit_sha,
                    'position_type': 'text',
                    'new_path': comment['path'],
                    'new_line': comment['line'],
                },
            }

            try:
                result = await self._request(
                    'POST',
                    f'/projects/{project_id}/merge_requests/{pr_number}/discussions',
                    json=discussion_data,
                )
                results.append(result)
            except httpx.HTTPStatusError as e:
                logger.warning(f'Failed to post inline comment on {comment["path"]}: {e}')
                # Fallback: post as regular note
                fallback_body = f"**{comment['path']}** (line {comment['line']})\n\n{comment['body']}"
                await self._request(
                    'POST',
                    f'/projects/{project_id}/merge_requests/{pr_number}/notes',
                    json={'body': fallback_body},
                )

        return {'discussions': results, 'count': len(comments)}

    async def update_pr_body(
        self,
        repo_identifier: str | int,
        pr_number: int,
        body: str,
    ) -> dict:
        """Update merge request description."""
        project_id = self._parse_repo_identifier(repo_identifier)

        return await self._request(
            'PUT',
            f'/projects/{project_id}/merge_requests/{pr_number}',
            json={'description': body},
        )

    def _normalize_mr(self, data: dict) -> PullRequestInfo:
        """Normalize GitLab MR response to PullRequestInfo."""
        return PullRequestInfo(
            number=data['iid'],
            title=data['title'],
            state=data['state'],
            head_sha=data.get('sha') or data.get('diff_refs', {}).get('head_sha', ''),
            source_branch=data['source_branch'],
            target_branch=data['target_branch'],
            html_url=data['web_url'],
        )

    def _normalize_file_change(self, data: dict) -> FileChange:
        """Normalize GitLab file change to FileChange."""
        # GitLab uses 'diff' instead of 'patch'
        return FileChange(
            filename=data.get('new_path') or data.get('old_path', ''),
            status=self._map_change_status(data),
            patch=data.get('diff'),
            additions=0,  # GitLab doesn't provide these in changes endpoint
            deletions=0,
        )

    def _map_change_status(self, data: dict) -> str:
        """Map GitLab change flags to normalized status."""
        if data.get('new_file'):
            return 'added'
        if data.get('deleted_file'):
            return 'removed'
        if data.get('renamed_file'):
            return 'renamed'
        return 'modified'

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
        """
        Create webhook on project.

        GitLab uses a token-based authentication for webhooks,
        sent as X-Gitlab-Token header.
        """
        project_id = self._parse_repo_identifier(repo_identifier)

        # Translate normalized events to GitLab webhook options
        webhook_options = self._translate_events_to_options(events)

        data = {
            'url': webhook_url,
            'token': secret,  # GitLab sends this as X-Gitlab-Token header
            **webhook_options,
        }

        result = await self._request('POST', f'/projects/{project_id}/hooks', json=data)
        return self._normalize_webhook(result)

    async def delete_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> None:
        """Delete webhook from project."""
        project_id = self._parse_repo_identifier(repo_identifier)

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f'{GITLAB_API_BASE}/projects/{project_id}/hooks/{webhook_id}',
                headers=self._headers,
            )
            response.raise_for_status()

    async def get_webhook(
        self,
        repo_identifier: str | int,
        webhook_id: int,
    ) -> WebhookInfo | None:
        """Get webhook details."""
        project_id = self._parse_repo_identifier(repo_identifier)

        try:
            result = await self._request('GET', f'/projects/{project_id}/hooks/{webhook_id}')
            return self._normalize_webhook(result)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _translate_events_to_options(self, events: list[str]) -> dict[str, bool]:
        """
        Translate normalized event names to GitLab webhook options.

        GitLab uses boolean flags for each event type.
        """
        event_map = {
            'pull_request': 'merge_requests_events',
            'push': 'push_events',
            'issue': 'issues_events',
            'issue_comment': 'note_events',
        }

        options = {
            'push_events': False,
            'merge_requests_events': False,
            'issues_events': False,
            'note_events': False,
            'tag_push_events': False,
            'job_events': False,
            'pipeline_events': False,
            'wiki_page_events': False,
        }

        for event in events:
            gitlab_option = event_map.get(event)
            if gitlab_option:
                options[gitlab_option] = True

        return options

    def _normalize_webhook(self, data: dict) -> WebhookInfo:
        """Normalize GitLab webhook response to WebhookInfo."""
        # Reconstruct events list from boolean flags
        events = []
        if data.get('push_events'):
            events.append('push')
        if data.get('merge_requests_events'):
            events.append('pull_request')
        if data.get('issues_events'):
            events.append('issue')
        if data.get('note_events'):
            events.append('issue_comment')

        return WebhookInfo(
            id=data['id'],
            url=data['url'],
            events=events,
            active=True,  # GitLab doesn't have an 'active' field, hooks are always active
        )


# Register GitLabProvider with factory
from services.providers.factory import GitProviderFactory

GitProviderFactory.register(GitProviderType.GITLAB, GitLabProvider)
