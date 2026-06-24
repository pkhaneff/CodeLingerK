"""
Webhook Payload Parser - Strategy pattern for parsing provider-specific webhooks.

This module normalizes different webhook payload formats to a unified structure,
enabling a single handler to process webhooks from any provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from services.providers.base import GitProviderType
from core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class NormalizedWebhookPayload:
    """
    Normalized webhook payload - same structure for all providers.

    This allows the webhook handler to work with any provider
    without knowing the specific payload format.
    """

    provider: GitProviderType
    event_type: str  # 'pull_request', 'push', etc.
    action: str | None  # 'opened', 'synchronize', etc.
    repo_full_name: str  # 'owner/repo' or 'group/project'
    repo_id: int | None  # Provider-specific repository ID
    pr_number: int  # PR/MR number
    head_sha: str  # Commit SHA
    source_branch: str  # Source branch name
    target_branch: str  # Target branch name
    title: str | None  # PR/MR title
    author: str | None  # PR/MR author username
    should_process: bool  # Whether this event should be processed
    skip_reason: str | None = None  # Reason for skipping (if any)


class BasePayloadParser(ABC):
    """Abstract base class for webhook payload parsers."""

    @abstractmethod
    def parse(self, raw_payload: dict) -> NormalizedWebhookPayload:
        """
        Parse raw webhook payload to normalized format.

        Args:
            raw_payload: Raw JSON payload from webhook

        Returns:
            NormalizedWebhookPayload with unified structure
        """
        pass

    @abstractmethod
    def get_event_header_name(self) -> str:
        """Return the header name containing event type."""
        pass


class GitHubPayloadParser(BasePayloadParser):
    """Parse GitHub webhook payloads."""

    PROCESSABLE_ACTIONS = {'opened', 'reopened', 'synchronize'}

    def get_event_header_name(self) -> str:
        return 'X-GitHub-Event'

    def parse(self, raw: dict) -> NormalizedWebhookPayload:
        """Parse GitHub pull_request webhook payload."""
        action = raw.get('action')
        should_process = action in self.PROCESSABLE_ACTIONS

        # Handle case where this isn't a PR event
        pr = raw.get('pull_request', {})
        repo = raw.get('repository', {})
        user = pr.get('user', {})

        return NormalizedWebhookPayload(
            provider=GitProviderType.GITHUB,
            event_type='pull_request',
            action=action,
            repo_full_name=repo.get('full_name', ''),
            repo_id=repo.get('id'),
            pr_number=raw.get('number', 0),
            head_sha=pr.get('head', {}).get('sha', ''),
            source_branch=pr.get('head', {}).get('ref', ''),
            target_branch=pr.get('base', {}).get('ref', ''),
            title=pr.get('title'),
            author=user.get('login'),
            should_process=should_process,
            skip_reason=None if should_process else f"Action '{action}' not processed",
        )


class GitLabPayloadParser(BasePayloadParser):
    """Parse GitLab webhook payloads."""

    PROCESSABLE_ACTIONS = {'open', 'reopen', 'update'}
    PROCESSABLE_STATES = {'opened', 'reopened', 'updated'}

    def get_event_header_name(self) -> str:
        return 'X-Gitlab-Event'

    def parse(self, raw: dict) -> NormalizedWebhookPayload:
        """Parse GitLab merge_request webhook payload."""
        attrs = raw.get('object_attributes', {})
        project = raw.get('project', {})
        user = raw.get('user', {})

        # GitLab uses both 'action' and 'state' fields
        action = attrs.get('action')
        state = attrs.get('state')

        should_process = (
            action in self.PROCESSABLE_ACTIONS or state in self.PROCESSABLE_STATES
        )

        return NormalizedWebhookPayload(
            provider=GitProviderType.GITLAB,
            event_type='merge_request',
            action=action or state,
            repo_full_name=project.get('path_with_namespace', ''),
            repo_id=project.get('id'),
            pr_number=attrs.get('iid', 0),
            head_sha=attrs.get('last_commit', {}).get('id', ''),
            source_branch=attrs.get('source_branch', ''),
            target_branch=attrs.get('target_branch', ''),
            title=attrs.get('title'),
            author=user.get('username'),
            should_process=should_process,
            skip_reason=(
                None
                if should_process
                else f"Action/state '{action or state}' not processed"
            ),
        )


class WebhookPayloadParser:
    """
    Strategy pattern for parsing provider-specific webhooks.

    Normalizes different payload formats to unified structure.

    Usage:
        parser = WebhookPayloadParser()
        payload = parser.parse(GitProviderType.GITHUB, raw_json)
    """

    _parsers: dict[GitProviderType, BasePayloadParser] = {
        GitProviderType.GITHUB: GitHubPayloadParser(),
        GitProviderType.GITLAB: GitLabPayloadParser(),
    }

    @classmethod
    def register(cls, provider: GitProviderType, parser: BasePayloadParser) -> None:
        """Register a new payload parser for a provider."""
        cls._parsers[provider] = parser

    @classmethod
    def parse(
        cls,
        provider: GitProviderType,
        raw_payload: dict,
    ) -> NormalizedWebhookPayload:
        """
        Parse webhook payload for specified provider.

        Args:
            provider: Git provider type
            raw_payload: Raw JSON payload from webhook

        Returns:
            NormalizedWebhookPayload with unified structure

        Raises:
            ValueError: If no parser registered for provider
        """
        parser = cls._parsers.get(provider)
        if not parser:
            raise ValueError(f'No parser registered for {provider}')

        try:
            return parser.parse(raw_payload)
        except Exception as e:
            logger.error(f'Failed to parse {provider} webhook: {e}')
            # Return a non-processable payload on parse error
            return NormalizedWebhookPayload(
                provider=provider,
                event_type='unknown',
                action=None,
                repo_full_name='',
                repo_id=None,
                pr_number=0,
                head_sha='',
                source_branch='',
                target_branch='',
                title=None,
                author=None,
                should_process=False,
                skip_reason=f'Parse error: {e}',
            )

    @classmethod
    def get_event_header(cls, provider: GitProviderType) -> str:
        """Get the event header name for a provider."""
        parser = cls._parsers.get(provider)
        if parser:
            return parser.get_event_header_name()
        return ''
