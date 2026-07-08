"""
Git Provider abstractions and implementations.

This module provides a clean abstraction layer for different Git providers
(GitHub, GitLab, etc.) following SOLID principles:

- GitProvider: Abstract base class defining the interface
- GitHubProvider: GitHub implementation
- GitLabProvider: GitLab implementation
- GitProviderFactory: Factory for creating provider instances

Usage:
    from services.providers import GitProviderFactory, GitProviderType

    provider = GitProviderFactory.create(GitProviderType.GITHUB, access_token)
    user = await provider.get_current_user()
"""

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
from apps.repositories.services.providers.factory import GitProviderFactory

# Import providers to register them with factory
from apps.repositories.services.providers.github import GitHubProvider
from apps.repositories.services.providers.gitlab import GitLabProvider

__all__ = [
    # Base types
    'GitProvider',
    'GitProviderType',
    'UserInfo',
    'RepoInfo',
    'PullRequestInfo',
    'FileChange',
    'ReviewComment',
    'WebhookInfo',
    # Factory
    'GitProviderFactory',
    # Providers
    'GitHubProvider',
    'GitLabProvider',
]
