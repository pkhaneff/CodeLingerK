import tiktoken
from typing import Protocol


class Tokenizer(Protocol):
    """
    Tokenizer Protocol for counting tokens.
    Defines a unified interface for all providers and models.
    """
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...


class TiktokenTokenizer:
    """
    Tokenizer wrapper around openai's tiktoken.
    Used for OpenAI models and fallback token estimation.
    """
    def __init__(self, model_or_encoding_name: str = "cl100k_base"):
        self.model_or_encoding_name = model_or_encoding_name
        try:
            self.encoding = tiktoken.encoding_for_model(model_or_encoding_name)
        except KeyError:
            try:
                self.encoding = tiktoken.get_encoding(model_or_encoding_name)
            except ValueError:
                self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(self.encoding.encode(text))


class SafetyMarginTokenizer:
    """
    Wrapper that adds a safety buffer percentage to a base tokenizer.
    Helps estimate token counts for external models (DeepSeek, Claude, Gemini)
    whose tokenizers are not locally installed or loaded, preventing truncation.
    """
    def __init__(self, base_tokenizer: Tokenizer, multiplier: float = 1.15):
        self.base_tokenizer = base_tokenizer
        self.multiplier = multiplier

    def count_tokens(self, text: str) -> int:
        count = self.base_tokenizer.count_tokens(text)
        return int(count * self.multiplier)


class CharCountTokenizer:
    """
    Simple fallback tokenizer dividing character length by 4.
    Guaranteed to run with zero dependencies.
    """
    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(text) // 4


class TokenizerFactory:
    """
    Factory to resolve the appropriate Tokenizer based on provider and model.
    """
    @staticmethod
    def get_tokenizer(provider: str, model_name: str) -> Tokenizer:
        provider_lower = provider.lower() if provider else ""
        model_lower = model_name.lower() if model_name else ""
        
        # 1. DeepSeek: Uses BPE. Typically requires a ~20-25% safety margin over cl100k_base on code.
        if provider_lower == "deepseek" or "deepseek" in model_lower:
            base = TiktokenTokenizer("cl100k_base")
            return SafetyMarginTokenizer(base, multiplier=1.25)
            
        # 2. Claude: Anthropic's tokenizer is similar to o200k/cl100k. A 10% safety margin is safe.
        elif provider_lower == "claude" or "claude" in model_lower:
            base = TiktokenTokenizer("cl100k_base")
            return SafetyMarginTokenizer(base, multiplier=1.10)
            
        # 3. Gemini: Google's tokenizer can be approximated using cl100k_base with a 5% margin.
        elif provider_lower == "gemini" or "gemini" in model_lower:
            base = TiktokenTokenizer("cl100k_base")
            return SafetyMarginTokenizer(base, multiplier=1.05)
            
        # 4. OpenAI: standard tiktoken
        elif provider_lower == "openai" or "gpt" in model_lower or model_lower.startswith(("o1", "o3")):
            if model_lower.startswith(("gpt-4o", "o1", "o3")):
                return TiktokenTokenizer("o200k_base")
            return TiktokenTokenizer(model_name or "gpt-4")
            
        # Default Fallback
        return TiktokenTokenizer("cl100k_base")
