"""
Git Provider Factory - Dependency Injection Container.

This module provides a factory for creating GitProvider instances.
It acts as a DI container that resolves the correct implementation
based on provider type.

Usage:
    from apps.repositories.services.providers.factory import GitProviderFactory
    from apps.repositories.services.providers.base import GitProviderType

    # User OAuth provider
    provider = GitProviderFactory.create(GitProviderType.GITHUB, access_token)
    user = await provider.get_current_user()

    # GitHub App provider (for bot comments)
    bot_provider = GitProviderFactory.create_github_app()
    await bot_provider.post_pr_review(...)  # Appears as "CodeLingerK [bot]"
"""

import logging
from typing import TYPE_CHECKING

from infra.config import settings
from apps.repositories.services.providers.base import GitProvider, GitProviderType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GitProviderFactory:
    """
    Factory for creating GitProvider instances.

    This is the Dependency Injection container that resolves
    the correct provider implementation based on type.

    Following Open/Closed Principle:
    - Open for extension: new providers can be registered
    - Closed for modification: existing code doesn't change

    Example:
        # Create a provider
        provider = GitProviderFactory.create(GitProviderType.GITHUB, token)

        # Register a new provider type (extension)
        GitProviderFactory.register(GitProviderType.BITBUCKET, BitbucketProvider)
    """

    _providers: dict[GitProviderType, type[GitProvider]] = {}

    @classmethod
    def create(
        cls,
        provider_type: GitProviderType,
        access_token: str,
    ) -> GitProvider:
        """
        Create a GitProvider instance.

        Args:
            provider_type: The type of provider to create
            access_token: OAuth access token for the provider

        Returns:
            GitProvider instance (GitHubProvider, GitLabProvider, etc.)

        Raises:
            ValueError: If provider_type is not supported
        """
        provider_class = cls._providers.get(provider_type)
        if not provider_class:
            raise ValueError(
                f'Unsupported provider: {provider_type}. '
                f'Available: {list(cls._providers.keys())}'
            )

        return provider_class(access_token)

    @classmethod
    def register(
        cls,
        provider_type: GitProviderType,
        provider_class: type[GitProvider],
    ) -> None:
        """
        Register a new provider implementation.

        Allows extending with new providers without modifying existing code.
        This follows the Open/Closed Principle.

        Args:
            provider_type: The provider type to register
            provider_class: The class implementing GitProvider

        Example:
            GitProviderFactory.register(GitProviderType.BITBUCKET, BitbucketProvider)
        """
        cls._providers[provider_type] = provider_class

    @classmethod
    def is_supported(cls, provider_type: GitProviderType) -> bool:
        """Check if a provider type is supported."""
        return provider_type in cls._providers

    @classmethod
    def supported_providers(cls) -> list[GitProviderType]:
        """Get list of supported provider types."""
        return list(cls._providers.keys())

    @classmethod
    def create_github_app(cls) -> GitProvider:
        """
        Create a GitHub App provider for bot operations.

        Uses GitHub App credentials from settings to authenticate.
        Reviews/comments posted via this provider will appear as
        "CodeLingerK [bot]" instead of a user account.

        Returns:
            GitHubAppProvider instance

        Raises:
            ValueError: If GitHub App is not configured
        """
        if not settings.github_app_enabled:
            raise ValueError(
                'GitHub App not configured. Set GITHUB_APP_ID, '
                'GITHUB_APP_PRIVATE_KEY_PATH, and GITHUB_APP_INSTALLATION_ID'
            )

        from apps.repositories.services.providers.github_app import GitHubAppProvider

        logger.info('Creating GitHub App provider for bot operations')
        return GitHubAppProvider()
