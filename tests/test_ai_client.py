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

