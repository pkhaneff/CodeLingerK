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

from core.logger import get_logger

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
            f'AI response was truncated because the output reached the configured token limit ({max_tokens} tokens).\n\n'
            f'This usually means the request asked the model to produce too much in one response.\n\n'
            f'Recommended actions:\n'
            f'- Split the review chunk into smaller chunks.\n'
            f'- Reduce max findings per pass.\n'
            f'- Use compact JSON output.\n'
            f'- Render final comments in a separate step.\n'
            f'- Increase max tokens only as a temporary fallback.'
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
        from apps.ai_reviewer.tokenizer import TokenizerFactory
        self.tokenizer = TokenizerFactory.get_tokenizer(config.provider.value if hasattr(config.provider, 'value') else str(config.provider), config.model)

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
                        f'NOT retrying with same token limit.'
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
        Count tokens in text using the configured tokenizer strategy.
        """
        return self.tokenizer.count_tokens(text)



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

        limit_tokens = max_tokens or self.config.max_tokens
        # Cap max output tokens for models known to have a 4096 limit (like gpt-3.5-turbo, gpt-4, gpt-4-turbo)
        if self.config.model.startswith(('gpt-3.5', 'gpt-4')) and not self.config.model.startswith('gpt-4o'):
            limit_tokens = min(limit_tokens, 4096)
        kwargs: dict[str, Any] = {
            'model': self.config.model,
            'messages': messages,
        }

        # OpenAI reasoning models (o1, o3, etc.) do not support 'max_tokens'
        # and require 'max_completion_tokens' instead.
        is_reasoning_model = self.config.model.startswith(('o1', 'o3'))
        if is_reasoning_model:
            kwargs['max_completion_tokens'] = limit_tokens
            # Older reasoning models (o1-mini, o1-preview) only support temperature = 1.0
            if self.config.model.startswith(('o1-mini', 'o1-preview')):
                kwargs['temperature'] = 1.0
            else:
                kwargs['temperature'] = temperature if temperature is not None else self.config.temperature
        else:
            kwargs['max_tokens'] = limit_tokens
            kwargs['temperature'] = temperature if temperature is not None else self.config.temperature
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

    # Build fallback configurations
    fallback_configs = []
    if getattr(settings, 'ai_fallback_providers', ''):
        fallback_providers = [p.strip() for p in settings.ai_fallback_providers.split(',') if p.strip()]
        fallback_models = [m.strip() for m in settings.ai_fallback_models.split(',') if m.strip()]
        
        for idx, fp in enumerate(fallback_providers):
            fp_enum = AIProvider(fp)
            # Find model for fallback
            f_model = fallback_models[idx] if idx < len(fallback_models) else ''
            if not f_model:
                # Default models per provider
                default_models = {
                    'openai': 'gpt-4o-mini',
                    'claude': 'claude-3-5-haiku-latest',
                    'deepseek': 'deepseek-chat',
                    'groq': 'llama-3.1-70b-versatile',
                }
                f_model = default_models.get(fp, settings.ai_model)
                
            f_config = AIClientConfig(
                provider=fp_enum,
                api_key=settings.get_api_key_for_provider(fp),
                model=f_model,
                base_url=settings.get_base_url_for_provider(fp),
                max_tokens=settings.ai_max_tokens,
                temperature=settings.ai_temperature,
                timeout=settings.ai_timeout,
                max_retries=settings.ai_max_retries,
                retry_delay=settings.ai_retry_delay,
            )
            fallback_configs.append(f_config)

    return AIClient(config, fallback_configs=fallback_configs)


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

    def __init__(self, config: AIClientConfig, fallback_configs: list[AIClientConfig] = None):
        """
        Initialize AI client.

        Args:
            config: AI configuration
            fallback_configs: Optional list of fallback configurations
        """
        self.config = config
        self.fallback_configs = fallback_configs or []
        self._client = self._create_client(config)
        self._fallback_clients = [self._create_client(c) for c in self.fallback_configs]

    def _create_client(self, config: AIClientConfig = None) -> BaseAIClient:
        """Create appropriate client based on provider."""
        cfg = config or self.config
        if cfg.provider == AIProvider.CLAUDE:
            return ClaudeClient(cfg)
        elif AIProvider.is_openai_compatible(cfg.provider):
            return OpenAICompatibleClient(cfg)
        else:
            raise ValueError(f'Unsupported AI provider: {cfg.provider}')

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        client: BaseAIClient = None,
    ) -> AIResponse:
        """Generate completion using configured provider with fallback support."""
        if client is not None:
            return await client.complete_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )

        clients_to_try = [self._client] + self._fallback_clients
        last_error = None

        for idx, cl in enumerate(clients_to_try):
            try:
                return await cl.complete_with_retry(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
            except Exception as e:
                if isinstance(e, AITruncationError):
                    raise
                provider_name = cl.config.provider.value
                model_name = cl.config.model
                logger.error(f"Client {provider_name} ({model_name}) completion failed: {e}")
                last_error = e
                if idx < len(clients_to_try) - 1:
                    next_client = clients_to_try[idx + 1]
                    logger.warning(
                        f"Falling back to {next_client.config.provider.value} ({next_client.config.model})"
                    )

        raise last_error or Exception("AI completion failed on all clients in the chain")

    @property
    def tokenizer(self):
        """Get the active tokenizer strategy for the primary client."""
        return self._client.tokenizer

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
        Generate completion expecting JSON response with fallback support.

        Parses the response as JSON. Handles fallbacks for empty responses,
        invalid JSON formatting, timeouts, and API exceptions across all configured providers.
        """
        import json

        clients_to_try = [self._client] + self._fallback_clients
        last_error = None

        for idx, client in enumerate(clients_to_try):
            try:
                json_system = (system_prompt or '') + '\n\nRespond only with valid JSON.'

                # Try JSON format mode first
                response_format = None
                if AIProvider.is_openai_compatible(client.config.provider):
                    response_format = {'type': 'json_object'}

                try:
                    response = await self.complete(
                        prompt=prompt,
                        system_prompt=json_system.strip(),
                        max_tokens=max_tokens,
                        response_format=response_format,
                        client=client,
                    )
                except AITruncationError:
                    raise
                except ValueError as e:
                    # Empty response or transient failure
                    if response_format is not None:
                        logger.warning(
                            f'JSON format mode failed for {client.config.provider.value} ({e}). '
                            f'Retrying without response_format constraint (fallback mode).'
                        )
                        response = await self.complete(
                            prompt=prompt,
                            system_prompt=json_system.strip(),
                            max_tokens=max_tokens,
                            response_format=None,
                            client=client,
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
                    for char_idx, char in enumerate(content_str):
                        if char in ('{', '['):
                            first_char_idx = char_idx
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
                            except json.JSONDecodeError:
                                pass

                    raise ValueError(f'AI response is not valid JSON: {e}')

            except Exception as e:
                if isinstance(e, AITruncationError):
                    raise
                logger.error(
                    f"JSON completion failed on client {client.config.provider.value} ({client.config.model}): {e}"
                )
                last_error = e
                if idx < len(clients_to_try) - 1:
                    next_client = clients_to_try[idx + 1]
                    logger.warning(
                        f"Falling back to JSON completion on {next_client.config.provider.value} ({next_client.config.model})"
                    )

        raise last_error or ValueError("JSON completion failed on all clients in the chain")

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

