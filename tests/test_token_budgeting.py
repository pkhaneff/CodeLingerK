import pytest
from apps.ai_reviewer.tokenizer import TokenizerFactory, SafetyMarginTokenizer, TiktokenTokenizer
from apps.ai_reviewer.services.context_formatter import ReviewContextFormatter
from apps.ai_reviewer.services.context_service import SnapshotContext, FileContext

def test_tokenizer_factory_resolves_providers():
    # OpenAI
    openai_tok = TokenizerFactory.get_tokenizer("openai", "gpt-4o")
    assert isinstance(openai_tok, TiktokenTokenizer)
    
    # DeepSeek
    deepseek_tok = TokenizerFactory.get_tokenizer("deepseek", "deepseek-chat")
    assert isinstance(deepseek_tok, SafetyMarginTokenizer)
    assert deepseek_tok.multiplier == 1.25
    
    # Claude
    claude_tok = TokenizerFactory.get_tokenizer("claude", "claude-3-5-sonnet")
    assert isinstance(claude_tok, SafetyMarginTokenizer)
    assert claude_tok.multiplier == 1.10

def test_context_formatter_consistent_formatting():
    tokenizer = TokenizerFactory.get_tokenizer("openai", "cl100k_base")
    formatter = ReviewContextFormatter(tokenizer)
    
    context = SnapshotContext(
        snapshot_id="test-snap",
        commit_sha="1234567890",
        files=[
            FileContext(
                file_path="src/main.py",
                status="modified",
                additions=10,
                deletions=5,
                hunks=[{
                    "old_start": 1,
                    "old_count": 5,
                    "new_start": 1,
                    "new_count": 10,
                    "added_lines": [{"content": "print('hello')"}],
                    "deleted_lines": [{"content": "print('old')"}],
                }]
            )
        ],
        total_additions=10,
        total_deletions=5,
    )
    
    formatted_text = formatter.format_context(
        context=context,
        rules_section="Rule 1: Code must be clean",
        static_section="Findings: None",
    )
    
    # Check that it contains all expected sections in the unified format
    assert "## IMPORTANT: Data Boundary" in formatted_text
    assert "Rule 1: Code must be clean" in formatted_text
    assert "Findings: None" in formatted_text
    assert "## Pull Request Overview" in formatted_text
    assert "## Changed Files" in formatted_text
    assert "## Diff Content" in formatted_text
    assert "src/main.py" in formatted_text
    assert "-print('old')" in formatted_text
    assert "+print('hello')" in formatted_text

    # Verify count_tokens works and returns an integer
    tokens = formatter.count_tokens(
        context=context,
        rules_section="Rule 1: Code must be clean",
        static_section="Findings: None",
    )
    assert isinstance(tokens, int)
    assert tokens > 0
