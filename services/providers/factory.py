"""
Git Provider Factory - Dependency Injection Container.

This module provides a factory for creating GitProvider instances.
It acts as a DI container that resolves the correct implementation
based on provider type.

Usage:
    from services.providers.factory import GitProviderFactory
    from services.providers.base import GitProviderType

    provider = GitProviderFactory.create(GitProviderType.GITHUB, access_token)
    user = await provider.get_current_user()
"""

from typing import TYPE_CHECKING

from services.providers.base import GitProvider, GitProviderType

if TYPE_CHECKING:
    pass


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
