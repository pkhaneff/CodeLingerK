"""
Application configuration using pydantic-settings.
All settings are loaded from environment variables.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',  # Ignore extra env vars not in the model
    )

    # Application
    app_name: str = 'CodeLingerK'
    debug: bool = False
    log_level: str = 'INFO'

    # PostgreSQL
    postgres_host: str = 'localhost'
    postgres_port: int = 5432
    postgres_db: str = 'codelingerk_dev'
    postgres_user: str = 'dev'
    postgres_password: str = 'devpass'

    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection URL."""
        return (
            f'postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )

    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection URL (for Alembic migrations)."""
        return (
            f'postgresql://{self.postgres_user}:{self.postgres_password}'
            f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
        )

    # Redis
    redis_url: str = 'redis://localhost:6379'

    # GitHub OAuth
    github_client_id: str = ''
    github_client_secret: str = ''
    github_redirect_uri: str = 'http://localhost:8000/api/v1/auth/github/callback'

    # GitHub App (for bot comments)
    github_app_id: str = ''
    github_app_private_key_path: str = ''
    github_app_installation_id: str = ''
    github_webhook_secret: str = ''

    @property
    def github_app_private_key(self) -> str | None:
        """Read GitHub App private key from file."""
        if not self.github_app_private_key_path:
            return None
        try:
            from pathlib import Path
            key_path = Path(self.github_app_private_key_path)
            if key_path.exists():
                return key_path.read_text()
        except Exception:
            pass
        return None

    @property
    def github_app_enabled(self) -> bool:
        """Check if GitHub App is configured."""
        return bool(
            self.github_app_id
            and self.github_app_private_key
            and self.github_app_installation_id
        )

    # GitLab OAuth (gitlab.com only)
    gitlab_client_id: str = ''
    gitlab_client_secret: str = ''
    gitlab_redirect_uri: str = 'http://localhost:8000/api/v1/auth/gitlab/callback'
    gitlab_webhook_secret: str = ''

    # Webhook configuration
    # Public URL where providers will send webhook events
    # Example: https://your-domain.com or https://abc123.ngrok.io
    webhook_base_url: str = ''

    @property
    def webhook_url(self) -> str:
        """Full webhook URL for GitHub PR events."""
        if not self.webhook_base_url:
            return ''
        return f'{self.webhook_base_url.rstrip("/")}/webhook/github/pull_request'

    @property
    def gitlab_webhook_url(self) -> str:
        """Full webhook URL for GitLab MR events."""
        if not self.webhook_base_url:
            return ''
        return f'{self.webhook_base_url.rstrip("/")}/webhook/gitlab/merge_request'

    def get_webhook_url(self, provider: str) -> str:
        """Get webhook URL for specified provider."""
        if provider == 'github':
            return self.webhook_url
        elif provider == 'gitlab':
            return self.gitlab_webhook_url
        return ''

    # JWT
    jwt_secret_key: str = 'change-me-in-production'
    jwt_algorithm: str = 'HS256'
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Repository storage
    repo_storage_path: str = '/tmp/codelingerk/repos'

    # AI Provider Configuration
    # Supported providers: claude, openai, deepseek, groq, custom
    # For OpenAI-compatible providers (deepseek, groq, custom), use AI_BASE_URL
    ai_provider: str = 'openai'
    ai_api_key: str = ''  # Unified API key for any provider
    ai_base_url: str = ''  # Base URL for OpenAI-compatible providers (e.g., https://api.deepseek.com)
    ai_model: str = 'gpt-4o'  # Model name varies by provider
    ai_max_tokens: int = 4096
    ai_temperature: float = 0.3
    ai_timeout: int = 120
    ai_max_retries: int = 3
    ai_retry_delay: float = 1.0

    # Legacy keys (deprecated - use ai_api_key instead)
    anthropic_api_key: str = ''
    openai_api_key: str = ''

    @property
    def effective_ai_api_key(self) -> str:
        """Get the effective API key (unified or legacy)."""
        if self.ai_api_key:
            return self.ai_api_key
        if self.ai_provider == 'claude':
            return self.anthropic_api_key
        return self.openai_api_key

    @property
    def effective_ai_base_url(self) -> str | None:
        """Get base URL for OpenAI-compatible providers."""
        if self.ai_base_url:
            return self.ai_base_url
        # Default base URLs for known providers
        provider_urls = {
            'deepseek': 'https://api.deepseek.com',
            'groq': 'https://api.groq.com/openai/v1',
        }
        return provider_urls.get(self.ai_provider)

    # Review Settings
    review_max_context_tokens: int = 50000
    review_max_comments_per_file: int = 5
    review_max_comments_per_pr: int = 20

    # Queue Settings
    queue_job_timeout_seconds: int = 300
    queue_max_retries: int = 3


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
