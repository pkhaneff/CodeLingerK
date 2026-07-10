"""
Alembic migration environment configuration.

Loads database URL from infra/config.py settings.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import our application config
from infra.config import settings
from infra.database import Base

# Import all models so they are registered with Base.metadata
from apps.auth.models.user import User
from apps.auth.models.role import Role
from apps.auth.models.blacklisted_token import BlacklistedToken
from apps.repositories.models.repository import Repository
from apps.activities.models.activity import Activity
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.layer import Layer, LayerRange
from apps.ai_reviewer.models.review_job import ReviewJob
from apps.ai_reviewer.models.review import Review, ReviewComment
from apps.ai_reviewer.models.review_run import ReviewRun
from apps.ai_reviewer.models.review_surface import ReviewSurface
from apps.ai_reviewer.models.finding import Finding
from apps.ai_reviewer.models.file_review_history import FileReviewHistory
from apps.ai_reviewer.models.memory import RepoRule, IgnoredPattern, AcceptedDecision
from apps.code_analyzer.models.code_graph import IndexedFile, Symbol, SymbolCall, SymbolImport, SymbolInheritance, FileChunk

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override sqlalchemy.url with our settings
config.set_main_option('sqlalchemy.url', settings.database_url_sync)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target_metadata to our models' metadata for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
