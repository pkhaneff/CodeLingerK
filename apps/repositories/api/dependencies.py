"""
FastAPI dependencies for Git provider injection.

These dependencies implement Dependency Injection pattern,
allowing routes and services to receive the correct GitProvider
instance based on context.
"""

from fastapi import Depends, HTTPException

from apps.auth.api.middleware import get_current_user
from apps.auth.models.user import User
from apps.repositories.services.providers.base import GitProvider, GitProviderType
from apps.repositories.services.providers.factory import GitProviderFactory


def get_user_provider_token(user: User, provider: GitProviderType) -> str:
    """
    Get user's access token for specified provider.

    Args:
        user: Authenticated user
        provider: Git provider type

    Returns:
        Access token string

    Raises:
        HTTPException: If user not connected to provider
    """
    token = user.get_access_token(provider.value)
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f'Not authenticated with {provider.value}. Please connect your {provider.value} account.',
        )
    return token


async def get_git_provider(
    provider: GitProviderType,
    current_user: User = Depends(get_current_user),
) -> GitProvider:
    """
    FastAPI dependency that injects the correct GitProvider.

    This is the main DI entry point for routes that need
    to interact with Git providers.

    Usage:
        @router.get('/repos/{provider}')
        async def list_repos(
            provider: GitProviderType,
            git_provider: GitProvider = Depends(get_git_provider),
        ):
            return await git_provider.list_user_repos()

    Args:
        provider: Git provider type from path/query parameter
        current_user: Authenticated user (injected)

    Returns:
        GitProvider instance for the specified provider

    Raises:
        HTTPException: If provider not supported or user not connected
    """
    if not GitProviderFactory.is_supported(provider):
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported provider: {provider.value}. Supported: {[p.value for p in GitProviderFactory.supported_providers()]}',
        )

    token = get_user_provider_token(current_user, provider)
    return GitProviderFactory.create(provider, token)


def create_provider_for_user(user: User, provider: GitProviderType) -> GitProvider:
    """
    Create GitProvider instance for a user.

    Non-async version for use in services.

    Args:
        user: User model instance
        provider: Git provider type

    Returns:
        GitProvider instance

    Raises:
        ValueError: If user not connected to provider
    """
    token = user.get_access_token(provider.value)
    if not token:
        raise ValueError(f'User not connected to {provider.value}')

    return GitProviderFactory.create(provider, token)
