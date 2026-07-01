import pytest

from apps.ai_reviewer.services.ai_review_service import AIReviewService


class _FakeAIClient:
    def __init__(self, response):
        self._response = response
        self.model = 'fake-model'

    async def complete_json(self, prompt: str, system_prompt: str):
        return self._response

    def count_tokens(self, text: str) -> int:
        return len(text)


@pytest.mark.asyncio
async def test_run_pass_preserves_list_response_data():
    service = AIReviewService(db=None, ai_client=_FakeAIClient([{'file_path': 'a.py'}]))

    result = await service._run_pass('comments', 'prompt')

    assert isinstance(result.data, list)
    assert result.data == [{'file_path': 'a.py'}]


def test_parse_comments_accepts_raw_list_wrapped_in_dict():
    service = AIReviewService(db=None, ai_client=_FakeAIClient({}))

    comments = service._parse_comments(
        {
            'raw': [
                {
                    'file_path': 'api/routes/reviews.py',
                    'line_start': 10,
                    'severity': 'warning',
                    'category': 'design',
                    'explanation': 'Issue',
                    'suggestion': 'Fix',
                    'confidence': 0.9,
                }
            ]
        }
    )

    assert len(comments) == 1
    assert comments[0].file_path == 'api/routes/reviews.py'
    assert comments[0].line_start == 10

