"""
AI Client dependency for FastAPI dependency injection.

Usage in routes:
    from apps.ai_reviewer.api.dependencies import get_ai_client

    @router.post('/review')
    async def create_review(
        ai_client: Annotated[AIClient, Depends(get_ai_client)],
    ):
        response = await ai_client.complete("...")
"""

from functools import lru_cache

from apps.ai_reviewer.clients.ai_client import AIClient, create_ai_client_from_settings


@lru_cache
def get_ai_client() -> AIClient:
    """
    Get cached AI client instance.

    The client is cached for the lifetime of the application.
    Configuration is loaded from environment variables.

    Returns:
        Configured AIClient instance
    """
    return create_ai_client_from_settings()
