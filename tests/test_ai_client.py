import pytest
from unittest.mock import AsyncMock, patch
from apps.ai_reviewer.clients.ai_client import AIClient, AIClientConfig, AIProvider, AIResponse


@pytest.fixture
def client_config():
    return AIClientConfig(
        provider=AIProvider.DEEPSEEK,
        api_key="test-key",
        model="deepseek-chat",
    )


@pytest.mark.asyncio
async def test_complete_json_with_unescaped_newlines(client_config):
    client = AIClient(client_config)

    raw_response = """
    {
      "reason": "This is a message
with an unescaped newline"
    }
    """

    mock_complete = AsyncMock(return_value=AIResponse(
        content=raw_response,
        model="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    ))

    with patch.object(client, "complete", mock_complete):
        result = await client.complete_json("test-prompt")
        assert result == {"reason": "This is a message\nwith an unescaped newline"}


@pytest.mark.asyncio
async def test_complete_json_with_conversational_wrapper(client_config):
    client = AIClient(client_config)

    raw_response = """
    Sure, here is the JSON data you requested:
    ```json
    {
      "status": "success",
      "data": [1, 2, 3]
    }
    ```
    I hope this helps!
    """

    mock_complete = AsyncMock(return_value=AIResponse(
        content=raw_response,
        model="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    ))

    with patch.object(client, "complete", mock_complete):
        result = await client.complete_json("test-prompt")
        assert result == {"status": "success", "data": [1, 2, 3]}


@pytest.mark.asyncio
async def test_complete_json_array_response(client_config):
    client = AIClient(client_config)

    raw_response = """
    [
      {"file": "a.py"},
      {"file": "b.py"}
    ]
    """

    mock_complete = AsyncMock(return_value=AIResponse(
        content=raw_response,
        model="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    ))

    with patch.object(client, "complete", mock_complete):
        result = await client.complete_json("test-prompt")
        assert result == [{"file": "a.py"}, {"file": "b.py"}]


@pytest.mark.asyncio
async def test_complete_json_with_invalid_escapes(client_config):
    client = AIClient(client_config)

    raw_response = r"""
    [
      {
        "file_path": "src/controllers/reportController.js",
        "explanation": "[Issue] ... const reportPath = \`C:\\users\\reports\\${id}\`; ... by providing ..\\..\\...\\passwords."
      }
    ]
    """

    mock_complete = AsyncMock(return_value=AIResponse(
        content=raw_response,
        model="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
    ))

    with patch.object(client, "complete", mock_complete):
        result = await client.complete_json("test-prompt")
        assert len(result) == 1
        assert "C:\\users\\reports\\" in result[0]["explanation"]
        assert "..\\..\\...\\passwords" in result[0]["explanation"]


@pytest.mark.asyncio
async def test_openai_reasoning_model_params():
    config = AIClientConfig(
        provider=AIProvider.OPENAI,
        api_key="test-key",
        model="o3-mini",
        temperature=0.3,
    )
    client = AIClient(config)
    
    mock_create = AsyncMock()
    # Mock return value of chat.completions.create
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(finish_reason="stop", message=AsyncMock(content="test-content"))]
    mock_response.usage = AsyncMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    mock_response.model = "o3-mini"
    mock_create.return_value = mock_response
    
    with patch("openai.resources.chat.completions.AsyncCompletions.create", mock_create):
        await client.complete("test-prompt", system_prompt="test-system", max_tokens=1500)
        
        # Verify it was called with max_completion_tokens and temperature
        mock_create.assert_called_once()
        called_kwargs = mock_create.call_args[1]
        assert "max_completion_tokens" in called_kwargs
        assert called_kwargs["max_completion_tokens"] == 1500
        assert "max_tokens" not in called_kwargs
        assert called_kwargs["temperature"] == 0.3
        assert called_kwargs["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_openai_legacy_reasoning_model_params():
    config = AIClientConfig(
        provider=AIProvider.OPENAI,
        api_key="test-key",
        model="o1-mini",
        temperature=0.3,
    )
    client = AIClient(config)
    
    mock_create = AsyncMock()
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(finish_reason="stop", message=AsyncMock(content="test-content"))]
    mock_response.usage = AsyncMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    mock_response.model = "o1-mini"
    mock_create.return_value = mock_response
    
    with patch("openai.resources.chat.completions.AsyncCompletions.create", mock_create):
        await client.complete("test-prompt", max_tokens=2000)
        
        mock_create.assert_called_once()
        called_kwargs = mock_create.call_args[1]
        assert "max_completion_tokens" in called_kwargs
        assert called_kwargs["max_completion_tokens"] == 2000
        assert "max_tokens" not in called_kwargs
        assert called_kwargs["temperature"] == 1.0  # o1-mini requires temperature = 1.0


@pytest.mark.asyncio
async def test_provider_fallback_on_request_error():
    from unittest.mock import AsyncMock, patch
    from apps.ai_reviewer.clients.ai_client import AIClient, AIClientConfig, AIProvider, AIResponse

    primary_config = AIClientConfig(
        provider=AIProvider.DEEPSEEK,
        api_key="ds-key",
        model="deepseek-chat",
    )
    fallback_config = AIClientConfig(
        provider=AIProvider.OPENAI,
        api_key="oa-key",
        model="gpt-4o",
    )

    client = AIClient(primary_config, fallback_configs=[fallback_config])

    # Mock primary client to throw exception, fallback to succeed
    mock_primary = client._client
    mock_fallback = client._fallback_clients[0]

    with patch.object(mock_primary, "complete", side_effect=ValueError("DeepSeek failed")):
        with patch.object(mock_fallback, "complete", AsyncMock(return_value=AIResponse(
            content="Fallback Success",
            model="gpt-4o",
            provider=AIProvider.OPENAI,
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            finish_reason="stop",
        ))):
            res = await client.complete("test-prompt")
            assert res.content == "Fallback Success"
            assert res.provider == AIProvider.OPENAI


@pytest.mark.asyncio
async def test_provider_fallback_on_json_parse_error():
    from unittest.mock import AsyncMock, patch
    from apps.ai_reviewer.clients.ai_client import AIClient, AIClientConfig, AIProvider, AIResponse

    primary_config = AIClientConfig(
        provider=AIProvider.DEEPSEEK,
        api_key="ds-key",
        model="deepseek-chat",
    )
    fallback_config = AIClientConfig(
        provider=AIProvider.OPENAI,
        api_key="oa-key",
        model="gpt-4o",
    )

    client = AIClient(primary_config, fallback_configs=[fallback_config])

    mock_primary = client._client
    mock_fallback = client._fallback_clients[0]

    # Primary returns invalid JSON, fallback returns valid JSON
    with patch.object(mock_primary, "complete", AsyncMock(return_value=AIResponse(
        content="Invalid JSON String",
        model="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        input_tokens=5,
        output_tokens=5,
        total_tokens=10,
        finish_reason="stop",
    ))):
        with patch.object(mock_fallback, "complete", AsyncMock(return_value=AIResponse(
            content='{"key": "value"}',
            model="gpt-4o",
            provider=AIProvider.OPENAI,
            input_tokens=5,
            output_tokens=5,
            total_tokens=10,
            finish_reason="stop",
        ))):
            res = await client.complete_json("test-prompt")
            assert res == {"key": "value"}


@pytest.mark.asyncio
async def test_openai_model_max_tokens_capping():
    from unittest.mock import AsyncMock, patch
    from apps.ai_reviewer.clients.ai_client import AIClient, AIClientConfig, AIProvider

    config = AIClientConfig(
        provider=AIProvider.OPENAI,
        api_key="oa-key",
        model="gpt-3.5-turbo",
        max_tokens=8192,
    )
    client = AIClient(config)

    mock_create = AsyncMock()
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(finish_reason="stop", message=AsyncMock(content="test-content"))]
    mock_response.usage = AsyncMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    mock_response.model = "gpt-3.5-turbo"
    mock_create.return_value = mock_response

    with patch("openai.resources.chat.completions.AsyncCompletions.create", mock_create):
        # We request 8192 output tokens, but it should be capped to 4096 for gpt-3.5-turbo
        await client.complete("test-prompt", max_tokens=8192)
        mock_create.assert_called_once()
        called_kwargs = mock_create.call_args[1]
        assert called_kwargs["max_tokens"] == 4096




