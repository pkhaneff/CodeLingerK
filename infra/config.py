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

    # GitHub App (for webhooks)
    github_app_id: str = ''
    github_app_private_key_path: str = ''
    github_webhook_secret: str = ''

    # Webhook configuration
    # Public URL where GitHub will send webhook events
    # Example: https://your-domain.com or https://abc123.ngrok.io
    webhook_base_url: str = ''

    @property
    def webhook_url(self) -> str:
        """Full webhook URL for PR events."""
        if not self.webhook_base_url:
            return ''
        # Must match: main.py prefix='/webhook' + webhooks.py '/github/pull_request'
        return f'{self.webhook_base_url.rstrip("/")}/webhook/github/pull_request'

    # JWT
    jwt_secret_key: str = 'change-me-in-production'
    jwt_algorithm: str = 'HS256'
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # Repository storage
    repo_storage_path: str = '/tmp/codelingerk/repos'


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
