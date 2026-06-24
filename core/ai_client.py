"""
AI Client Abstraction - Unified interface for AI model interactions.

Supports multiple providers through a Protocol-based abstraction:
- Claude (Anthropic)
- OpenAI
- DeepSeek (OpenAI-compatible)
- Groq (OpenAI-compatible)
- Any OpenAI-compatible provider via custom base_url

Configuration via .env:
    AI_PROVIDER=openai          # claude, openai, deepseek, groq, custom
    AI_API_KEY=sk-xxx           # API key for the provider
    AI_BASE_URL=                # Optional: custom base URL for OpenAI-compatible
    AI_MODEL=gpt-4o             # Model name
    AI_MAX_TOKENS=4096
    AI_TEMPERATURE=0.3
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from core.logging_config import get_logger

logger = get_logger(__name__)


class AIProvider(str, Enum):
    """Supported AI providers."""

    CLAUDE = 'claude'
    OPENAI = 'openai'
    DEEPSEEK = 'deepseek'
    GROQ = 'groq'
    CUSTOM = 'custom'  # Any OpenAI-compatible provider

    @classmethod
    def is_openai_compatible(cls, provider: 'AIProvider') -> bool:
        """Check if provider uses OpenAI-compatible API."""
        return provider in {cls.OPENAI, cls.DEEPSEEK, cls.GROQ, cls.CUSTOM}


@dataclass
class AIResponse:
    """Standardized AI response."""

    content: str
    model: str
    provider: AIProvider
    input_tokens: int
    output_tokens: int
    total_tokens: int
    finish_reason: str | None = None

    @property
    def is_complete(self) -> bool:
        """Check if response completed normally."""
        return self.finish_reason in {'end_turn', 'stop', None}


@dataclass
class AIClientConfig:
    """AI client configuration."""

    provider: AIProvider
    api_key: str
    model: str
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.3
    timeout: int = 120
    max_retries: int = 3
    retry_delay: float = 1.0


@runtime_checkable
class AIClientProtocol(Protocol):
    """Protocol for AI client implementations (DIP compliance)."""

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Generate completion for prompt."""
        ...

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        ...


class BaseAIClient:
    """Base class for AI clients with common functionality."""

    def __init__(self, config: AIClientConfig):
        self.config = config

    async def complete_with_retry(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Complete with automatic retry on transient errors."""
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                return await self.complete(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2**attempt)
                    logger.warning(
                        f'AI request failed (attempt {attempt + 1}), '
                        f'retrying in {delay}s: {e}'
                    )
                    await asyncio.sleep(delay)

        raise last_error or Exception('AI request failed')

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Override in subclass."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        """Estimate token count (rough: 4 chars per token)."""
        return len(text) // 4


class ClaudeClient(BaseAIClient):
    """Anthropic Claude AI client."""

    def __init__(self, config: AIClientConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        """Lazy initialize Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(
                    api_key=self.config.api_key,
                    timeout=self.config.timeout,
                )
            except ImportError:
                raise ImportError(
                    'anthropic package required. Install with: pip install anthropic'
                )
        return self._client

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Generate completion using Claude."""
        client = self._get_client()

        messages = [{'role': 'user', 'content': prompt}]

        kwargs: dict[str, Any] = {
            'model': self.config.model,
            'max_tokens': max_tokens or self.config.max_tokens,
            'messages': messages,
        }

        if system_prompt:
            kwargs['system'] = system_prompt

        if temperature is not None:
            kwargs['temperature'] = temperature
        else:
            kwargs['temperature'] = self.config.temperature

        response = await client.messages.create(**kwargs)

        content = ''
        if response.content:
            content = response.content[0].text

        return AIResponse(
            content=content,
            model=response.model,
            provider=AIProvider.CLAUDE,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            finish_reason=response.stop_reason,
        )


class OpenAICompatibleClient(BaseAIClient):
    """
    OpenAI-compatible client for multiple providers.

    Works with:
    - OpenAI (default)
    - DeepSeek (base_url: https://api.deepseek.com)
    - Groq (base_url: https://api.groq.com/openai/v1)
    - Any OpenAI-compatible API
    """

    def __init__(self, config: AIClientConfig):
        super().__init__(config)
        self._client = None

    def _get_client(self):
        """Lazy initialize OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                client_kwargs = {
                    'api_key': self.config.api_key,
                    'timeout': self.config.timeout,
                }

                if self.config.base_url:
                    client_kwargs['base_url'] = self.config.base_url

                self._client = AsyncOpenAI(**client_kwargs)
            except ImportError:
                raise ImportError(
                    'openai package required. Install with: pip install openai'
                )
        return self._client

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Generate completion using OpenAI-compatible API."""
        client = self._get_client()

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        response = await client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature
            if temperature is not None
            else self.config.temperature,
        )

        choice = response.choices[0]
        usage = response.usage

        return AIResponse(
            content=choice.message.content or '',
            model=response.model,
            provider=self.config.provider,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            finish_reason=choice.finish_reason,
        )


def create_ai_client_from_settings() -> 'AIClient':
    """
    Factory function to create AI client from application settings.

    This is the recommended way to get an AI client instance.
    Configuration is read from environment variables via Settings.

    Returns:
        Configured AIClient instance
    """
    from infra.config import settings

    provider = AIProvider(settings.ai_provider)

    config = AIClientConfig(
        provider=provider,
        api_key=settings.effective_ai_api_key,
        model=settings.ai_model,
        base_url=settings.effective_ai_base_url,
        max_tokens=settings.ai_max_tokens,
        temperature=settings.ai_temperature,
        timeout=settings.ai_timeout,
        max_retries=settings.ai_max_retries,
        retry_delay=settings.ai_retry_delay,
    )

    return AIClient(config)


class AIClient:
    """
    Unified AI client with provider abstraction.

    Usage with DI (recommended):
        # In FastAPI dependency
        def get_ai_client() -> AIClient:
            return create_ai_client_from_settings()

        # In service
        class MyService:
            def __init__(self, ai_client: AIClient):
                self.ai_client = ai_client

    Usage with explicit config:
        config = AIClientConfig(
            provider=AIProvider.OPENAI,
            api_key='sk-xxx',
            model='gpt-4o',
        )
        client = AIClient(config)

    Environment configuration (.env):
        AI_PROVIDER=openai          # claude, openai, deepseek, groq, custom
        AI_API_KEY=sk-xxx
        AI_MODEL=gpt-4o
        AI_BASE_URL=                # For custom providers
    """

    def __init__(self, config: AIClientConfig):
        """
        Initialize AI client.

        Args:
            config: AI configuration
        """
        self.config = config
        self._client = self._create_client()

    def _create_client(self) -> BaseAIClient:
        """Create appropriate client based on provider."""
        if self.config.provider == AIProvider.CLAUDE:
            return ClaudeClient(self.config)
        elif AIProvider.is_openai_compatible(self.config.provider):
            return OpenAICompatibleClient(self.config)
        else:
            raise ValueError(f'Unsupported AI provider: {self.config.provider}')

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AIResponse:
        """Generate completion using configured provider."""
        return await self._client.complete_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._client.count_tokens(text)

    async def complete_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate completion expecting JSON response.

        Parses the response as JSON.

        Args:
            prompt: Prompt requesting JSON output
            system_prompt: Optional system prompt

        Returns:
            Parsed JSON dict

        Raises:
            ValueError: If response is not valid JSON
        """
        import json

        json_system = (system_prompt or '') + '\n\nRespond only with valid JSON.'

        response = await self.complete(
            prompt=prompt,
            system_prompt=json_system.strip(),
        )

        content = response.content.strip()

        if content.startswith('```json'):
            content = content[7:]
        if content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]

        try:
            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f'Failed to parse AI response as JSON: {e}')
            raise ValueError(f'AI response is not valid JSON: {e}')

    @property
    def provider(self) -> AIProvider:
        """Get current provider."""
        return self.config.provider

    @property
    def model(self) -> str:
        """Get current model."""
        return self.config.model
