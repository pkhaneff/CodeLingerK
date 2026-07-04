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


class AITruncationError(ValueError):
    """
    Raised when the AI response is truncated due to max_tokens being hit.

    This is a DETERMINISTIC configuration error, not a transient failure.
    Retrying with the same max_tokens will ALWAYS produce the same truncated
    result, wasting API tokens and time.

    Fix: Increase AI_MAX_TOKENS (or the per-pass AI_MAX_TOKENS_<PASS>) in .env.
    """
    def __init__(self, output_tokens: int, max_tokens: int, attempt: int):
        self.output_tokens = output_tokens
        self.max_tokens = max_tokens
        super().__init__(
            f'AI response truncated: output hit max_tokens={max_tokens} on attempt {attempt}. '
            f'Retrying is futile with the same token limit. '
            f'Fix: set AI_MAX_TOKENS=8192 (or higher) in .env, '
            f'or set AI_MAX_TOKENS_COMMENTS=8192 for the specific pass.'
        )


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

    @property
    def is_truncated(self) -> bool:
        """
        Check if response was cut off due to max_tokens being hit.

        When True, the output is incomplete and cannot be parsed as valid JSON.
        Root cause: AI_MAX_TOKENS is too low for the prompt complexity.
        """
        return self.finish_reason == 'length'

    @property
    def is_content_filtered(self) -> bool:
        """Check if response was blocked by the provider's content policy."""
        return self.finish_reason == 'content_filter'

    @property
    def is_empty(self) -> bool:
        """Check if the response content is empty (any reason)."""
        return not self.content.strip()


@dataclass
class AIClientConfig:
    """AI client configuration."""

    provider: AIProvider
    api_key: str
    model: str
    base_url: str | None = None
    max_tokens: int = 8192
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
        response_format: dict[str, Any] | None = None,
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
        response_format: dict[str, Any] | None = None,
    ) -> AIResponse:
        """
        Complete with automatic retry on transient errors.

        Retry triggers on:
        - Network/HTTP exceptions (original behavior)
        - Empty content response (new): happens when max_tokens is too low,
          content is filtered, or the provider has a transient issue.
          Empty HTTP 200 responses do NOT raise exceptions at the HTTP layer,
          so we must detect and handle them explicitly here.
        """
        last_error: Exception | None = None
        effective_max_tokens = max_tokens or self.config.max_tokens

        for attempt in range(self.config.max_retries):
            try:
                response = await self.complete(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=effective_max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )

                # Log response diagnostics on every call for observability
                logger.debug(
                    f'AI response: model={response.model}, '
                    f'input_tokens={response.input_tokens}, '
                    f'output_tokens={response.output_tokens}, '
                    f'finish_reason={response.finish_reason}'
                )

                # Truncation (finish_reason='length') is a DETERMINISTIC config error:
                # the same prompt + same max_tokens will ALWAYS produce the same
                # truncated output. Retrying is futile and wastes API tokens.
                # Raise immediately so the caller can handle it (e.g., fail the pass
                # and let the pipeline integrity check decide whether to retry the job).
                if response.is_truncated:
                    logger.error(
                        f'AI response truncated (finish_reason=length) on attempt {attempt + 1}. '
                        f'output_tokens={response.output_tokens} hit max_tokens={effective_max_tokens}. '
                        f'NOT retrying — increase AI_MAX_TOKENS in .env to fix this.'
                    )
                    raise AITruncationError(
                        output_tokens=response.output_tokens,
                        max_tokens=effective_max_tokens,
                        attempt=attempt + 1,
                    )

                # Treat empty content as a transient error and retry.
                # DeepSeek (and other providers) occasionally return HTTP 200
                # with an empty body — this is NOT a normal "no issues found"
                # response; it indicates a provider-side failure.
                if response.is_empty:
                    empty_msg = (
                        f'AI returned empty content on attempt {attempt + 1} '
                        f'(finish_reason={response.finish_reason}). '
                        f'Possible causes: rate limit, content filter, or transient error.'
                    )
                    logger.warning(empty_msg)
                    last_error = ValueError(empty_msg)
                    if attempt < self.config.max_retries - 1:
                        delay = self.config.retry_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                    continue

                return response

            except AITruncationError:
                # Truncation is deterministic — re-raise immediately without retry.
                # The generic `except Exception` below must NOT catch this.
                raise
            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(
                        f'AI request failed (attempt {attempt + 1}), '
                        f'retrying in {delay}s: {e}'
                    )
                    await asyncio.sleep(delay)

        raise last_error or Exception('AI request failed after all retries')

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AIResponse:
        """Override in subclass."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text.
        
        Attempts to use tiktoken matching the configured model name,
        falling back to cl100k_base or character-based estimation on failure.
        """
        if not text:
            return 0
        try:
            import tiktoken
            model_name = self.config.model
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            # Fallback to character-based estimation (approx. 4 characters per token)
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
        response_format: dict[str, Any] | None = None,
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
        response_format: dict[str, Any] | None = None,
    ) -> AIResponse:
        """Generate completion using OpenAI-compatible API."""
        client = self._get_client()

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        kwargs: dict[str, Any] = {
            'model': self.config.model,
            'messages': messages,
            'max_tokens': max_tokens or self.config.max_tokens,
            'temperature': temperature
            if temperature is not None
            else self.config.temperature,
        }
        if response_format is not None:
            kwargs['response_format'] = response_format

        response = await client.chat.completions.create(**kwargs)

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
        response_format: dict[str, Any] | None = None,
    ) -> AIResponse:
        """Generate completion using configured provider."""
        return await self._client.complete_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return self._client.count_tokens(text)

    async def complete_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Generate completion expecting JSON response.

        Parses the response as JSON. Handles the DeepSeek-specific behavior
        where `response_format={'type': 'json_object'}` occasionally causes
        empty responses — in that case, we retry without the format constraint.

        Args:
            prompt: Prompt requesting JSON output
            system_prompt: Optional system prompt
            max_tokens: Override max output tokens for this call

        Returns:
            Parsed JSON dict

        Raises:
            ValueError: If response is not valid JSON after all retries
        """
        import json

        json_system = (system_prompt or '') + '\n\nRespond only with valid JSON.'

        # Attempt 1: Use structured JSON output format (preferred — more reliable)
        response_format = None
        if AIProvider.is_openai_compatible(self.config.provider):
            response_format = {'type': 'json_object'}

        try:
            response = await self.complete(
                prompt=prompt,
                system_prompt=json_system.strip(),
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except AITruncationError:
            # Truncation is a config error — the fallback (no response_format)
            # would use the SAME max_tokens and get truncated again.
            # Re-raise immediately so _run_pass records it as a pass failure.
            raise
        except ValueError as e:
            # Empty response after all retries (transient provider failure).
            # Attempt fallback: retry WITHOUT response_format constraint.
            # Some providers (notably DeepSeek) produce empty responses when
            # forced into strict JSON mode but the output is complex — removing
            # the constraint allows the model to respond more freely.
            if response_format is not None:
                logger.warning(
                    f'JSON format mode failed after retries ({e}). '
                    f'Retrying without response_format constraint (fallback mode).'
                )
                response = await self.complete(
                    prompt=prompt,
                    system_prompt=json_system.strip(),
                    max_tokens=max_tokens,
                    response_format=None,  # No format constraint
                )
            else:
                raise

        content = response.content.strip()

        # Clean up markdown code blocks if they are present
        if content.startswith('```json'):
            content = content[7:]
        elif content.startswith('```'):
            content = content[3:]
        if content.endswith('```'):
            content = content[:-3]

        content_str = content.strip()

        try:
            # Use strict=False to allow literal newlines/control characters inside JSON string literals
            return json.loads(content_str, strict=False)
        except json.JSONDecodeError as e:
            # Try to clean invalid escape sequences and parse again
            try:
                cleaned_content = _clean_invalid_json_escapes(content_str)
                return json.loads(cleaned_content, strict=False)
            except json.JSONDecodeError:
                pass

            # Fallback: search for first '{'/'[' and last '}'/']' to extract JSON
            first_char_idx = -1
            start_char = ''
            for idx, char in enumerate(content_str):
                if char in ('{', '['):
                    first_char_idx = idx
                    start_char = char
                    break

            if first_char_idx != -1:
                end_char = '}' if start_char == '{' else ']'
                last_char_idx = content_str.rfind(end_char)
                if last_char_idx != -1 and last_char_idx > first_char_idx:
                    json_str = content_str[first_char_idx:last_char_idx + 1]
                    try:
                        cleaned_json = _clean_invalid_json_escapes(json_str)
                        return json.loads(cleaned_json, strict=False)
                    except json.JSONDecodeError as inner_e:
                        logger.error(
                            f'Failed to parse extracted JSON block: {inner_e}. '
                            f'Original content: {content_str[:500]}'
                        )

            logger.error(
                f'Failed to parse AI response as JSON: {e}. '
                f'Content (first 500 chars): {content_str[:500]}'
            )
            raise ValueError(f'AI response is not valid JSON: {e}')

    @property
    def provider(self) -> AIProvider:
        """Get current provider."""
        return self.config.provider

    @property
    def model(self) -> str:
        """Get current model."""
        return self.config.model


def _clean_invalid_json_escapes(s: str) -> str:
    r"""
    Clean invalid backslash escape sequences from a JSON string.
    Preserves valid JSON escape sequences: \", \\, \/, \b, \f, \n, \r, \t, and \uXXXX.
    For invalid ones (like \` or \'), removes the backslash.
    For other characters (like \p in \passwords or \u in C:\users), double-escapes to \\.
    """
    result = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '\\':
            if i + 1 < n:
                next_char = s[i+1]
                is_valid = False
                if next_char in '"\\/bfnrt':
                    is_valid = True
                elif next_char == 'u':
                    if i + 5 < n:
                        hex_part = s[i+2:i+6]
                        if all(c in '0123456789abcdefABCDEF' for c in hex_part):
                            is_valid = True
                
                if is_valid:
                    result.append('\\')
                    result.append(next_char)
                    i += 2
                else:
                    if next_char in ("'", "`"):
                        result.append(next_char)
                    else:
                        result.append('\\\\')
                        result.append(next_char)
                    i += 2
            else:
                result.append('\\\\')
                i += 1
        else:
            result.append(s[i])
            i += 1
    return "".join(result)

