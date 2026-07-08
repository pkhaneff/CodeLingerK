import pytest

from apps.ai_reviewer.services.ai_review_service import AIReviewService


class _FakeAIClient:
    def __init__(self, response):
        self._response = response
        self.model = 'fake-model'

    async def complete_json(self, prompt: str, system_prompt: str, max_tokens: int | None = None):
        return self._response

    def count_tokens(self, text: str) -> int:
        return len(text)


@pytest.mark.asyncio
async def test_run_pass_preserves_list_response_data():
    from unittest.mock import MagicMock, patch
    mock_settings = MagicMock()
    mock_settings.get_pass_max_tokens = MagicMock(return_value=4000)
    mock_settings.ai_provider_comments = ""
    mock_settings.ai_model_comments = ""
    mock_settings.ai_model_context_window = 64000
    mock_settings.ai_max_tokens = 8192

    with patch('apps.ai_reviewer.services.ai_review_service.settings', mock_settings):
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


def test_parse_comments_accepts_single_comment_dict():
    service = AIReviewService(db=None, ai_client=_FakeAIClient({}))
    comment_dict = {
        'file_path': 'src/controllers/discount_controller.py',
        'line_start': 12,
        'severity': 'critical',
        'category': 'security',
        'explanation': 'Issue',
        'suggestion': 'Fix',
        'confidence': 0.95,
    }
    comments = service._parse_comments(comment_dict)
    assert len(comments) == 1
    assert comments[0].file_path == 'src/controllers/discount_controller.py'
    assert comments[0].line_start == 12
    assert comments[0].severity == 'critical'


def test_parse_comments_scans_nested_lists():
    service = AIReviewService(db=None, ai_client=_FakeAIClient({}))
    comments = service._parse_comments(
        {
            'some_random_key': [
                {
                    'file_path': 'src/utils/statistics_calculator.py',
                    'line_start': 9,
                    'severity': 'warning',
                    'category': 'performance',
                    'explanation': 'Issue',
                    'suggestion': 'Fix',
                    'confidence': 0.8,
                }
            ]
        }
    )
    assert len(comments) == 1
    assert comments[0].file_path == 'src/utils/statistics_calculator.py'
    assert comments[0].line_start == 9


def test_parse_comments_new_schema_rendering():
    service = AIReviewService(db=None, ai_client=_FakeAIClient({}))
    new_finding = {
        "category": "Functional Correctness",
        "severity": "major",
        "title": "Missing null guard",
        "file_path": "src/user.service.ts",
        "line": 42,
        "problem": "`user.profile.name` may throw when profile is null.",
        "impact": "This can cause a runtime exception.",
        "suggestion": "Use optional chaining or validate the profile first.",
        "code_suggestion": "const name = user.profile?.name ?? \"Unknown\";",
        "confidence": "high"
    }
    
    comments = service._parse_comments(new_finding)
    assert len(comments) == 1
    comment = comments[0]
    
    assert comment.file_path == "src/user.service.ts"
    assert comment.line_start == 42
    assert comment.severity == "major"
    assert comment.category == "Functional Correctness"
    assert comment.confidence == 0.9  # mapped from 'high'
    assert comment.suggestion == "const name = user.profile?.name ?? \"Unknown\";"
    
    # Verify pre-rendered markdown structure
    explanation = comment.explanation
    assert "**[Functional Correctness] 🟠 Missing null guard**" in explanation
    assert "`user.profile.name` may throw when profile is null." in explanation
    assert "**Why this matters**" in explanation
    assert "This can cause a runtime exception." in explanation
    assert "**Suggested fix**" in explanation
    assert "Use optional chaining or validate the profile first." in explanation
    assert "```suggestion" in explanation
    assert "const name = user.profile?.name ?? \"Unknown\";" in explanation


@pytest.mark.asyncio
async def test_review_snapshot_auto_split_retry_on_truncation():
    from unittest.mock import MagicMock, AsyncMock, patch
    from apps.ai_reviewer.models.snapshot import Snapshot
    from apps.ai_reviewer.services.context_service import SnapshotContext, FileContext
    from apps.ai_reviewer.clients.ai_client import AITruncationError
    from apps.ai_reviewer.services.ai_review_service import AIReviewService

    snapshot = MagicMock(spec=Snapshot)
    snapshot.id = 'snap-123'
    
    file_a = FileContext(file_path='a.py', status='modified', additions=10, deletions=5, hunks=[])
    file_b = FileContext(file_path='b.py', status='modified', additions=20, deletions=2, hunks=[])
    context = SnapshotContext(
        snapshot_id='snap-123',
        commit_sha='sha-123',
        files=[file_a, file_b],
        total_additions=30,
        total_deletions=7,
        estimated_tokens=60000
    )
    context.level2_functions = {}
    context.level3_dependencies = {}
    context.level4_semantic_search = {}

    mock_settings = MagicMock()
    mock_settings.ai_soft_budget = 40000
    mock_settings.ai_model_context_window = 64000
    mock_settings.ai_enable_auto_split_on_truncation = True
    mock_settings.get_pass_max_tokens = MagicMock(return_value=4000)
    mock_settings.review_min_confidence = 0.5
    mock_settings.review_min_score_threshold = 0.1
    mock_settings.review_max_comments_per_file = 5
    mock_settings.review_max_comments_total = 10
    
    mock_client = MagicMock()
    mock_client.model = 'fake-model'
    mock_client.count_tokens = MagicMock(return_value=100)
    
    call_count = 0
    async def complete_json_side_effect(prompt, system_prompt, max_tokens=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise AITruncationError(output_tokens=4000, max_tokens=4000, attempt=1)
        elif 'understanding' in prompt or 'understanding' in system_prompt:
            return {'summary': 'A summary', 'changes': [], 'sequence_diagram': {'include': False}}
        elif 'risks' in prompt:
            return {'risk_level': 'low', 'security_concerns': []}
        elif 'quality' in prompt:
            return {'complexity_issues': []}
        elif 'business' in prompt:
            return {'intent_violations': []}
        else:
            return []
            
    mock_client.complete_json = AsyncMock(side_effect=complete_json_side_effect)
    
    mock_db = MagicMock()
    mock_db_result = MagicMock()
    mock_db_result.scalars = MagicMock()
    mock_db_result.scalars.all = MagicMock(return_value=[])
    mock_db.execute = AsyncMock(return_value=mock_db_result)

    service = AIReviewService(db=mock_db, ai_client=mock_client)
    service.repo_dir = None
    
    with patch('apps.ai_reviewer.services.ai_review_service.settings', mock_settings):
        with patch('apps.ai_reviewer.services.context_service.ContextService.build_review_prompt_context', return_value='dummy diff context'):
            result = await service.review_snapshot(
                snapshot=snapshot,
                context=context,
                layers=[],
                external=None
            )
            
            assert result is not None
            assert call_count > 1


@pytest.mark.asyncio
async def test_unpack_analysis_pass():
    from apps.ai_reviewer.services.ai_review_service import AIReviewService, ReviewPass

    service = AIReviewService(db=None, ai_client=None)

    analysis_data = {
        "understanding": {"summary": "Walkthrough summary", "changes": []},
        "risks": {"risk_level": "medium", "security_concerns": [{"issue": "Concern"}]},
        "quality": {"complexity_issues": []},
        "business": {"intent_violations": []}
    }

    analysis_pass = ReviewPass(
        name="analysis",
        prompt="prompt",
        response="response",
        tokens_used=1000,
        duration_ms=200,
        data=analysis_data
    )

    und, risk, qual, bus = service._unpack_analysis_pass(analysis_pass)

    assert und.name == "understanding"
    assert und.data == analysis_data["understanding"]
    assert risk.name == "risks"
    assert risk.data == analysis_data["risks"]
    assert qual.name == "quality"
    assert qual.data == analysis_data["quality"]
    assert bus.name == "business"
    assert bus.data == analysis_data["business"]
    assert und.tokens_used == 250
    assert und.duration_ms == 200


@pytest.mark.asyncio
async def test_get_client_for_pass_with_overrides():
    from unittest.mock import MagicMock, patch
    from apps.ai_reviewer.services.ai_review_service import AIReviewService

    mock_client = MagicMock()
    mock_client.config.provider.value = "deepseek"
    mock_client.config.model = "deepseek-chat"

    service = AIReviewService(db=None, ai_client=mock_client)

    # Mock settings with pass override values
    mock_settings = MagicMock()
    mock_settings.ai_provider_comments = "openai"
    mock_settings.ai_model_comments = "gpt-4o-mini"
    mock_settings.get_api_key_for_provider = MagicMock(return_value="oa-override-key")
    mock_settings.get_base_url_for_provider = MagicMock(return_value=None)
    mock_settings.ai_max_tokens = 8192
    mock_settings.ai_temperature = 0.3
    mock_settings.ai_timeout = 45
    mock_settings.ai_max_retries = 1
    mock_settings.ai_retry_delay = 1.0

    with patch('apps.ai_reviewer.services.ai_review_service.settings', mock_settings):
        # Resolve client for "comments" pass which has overrides
        override_client = service._get_client_for_pass("comments")
        assert override_client.config.provider.value == "openai"
        assert override_client.config.model == "gpt-4o-mini"
        assert override_client.config.api_key == "oa-override-key"

        # Resolve client for "understanding" which has no override (returns self.ai_client)
        mock_settings.ai_provider_understanding = ""
        mock_settings.ai_model_understanding = ""
        default_resolved = service._get_client_for_pass("understanding")
        assert default_resolved == mock_client


@pytest.mark.asyncio
async def test_get_client_for_pass_auto_detect_provider():
    from unittest.mock import MagicMock, patch
    from apps.ai_reviewer.services.ai_review_service import AIReviewService

    mock_client = MagicMock()
    mock_client.config.provider.value = "deepseek"
    mock_client.config.model = "deepseek-chat"

    service = AIReviewService(db=None, ai_client=mock_client)

    mock_settings = MagicMock()
    # model comments overridden, but provider comments is left empty
    mock_settings.ai_provider_comments = ""
    mock_settings.ai_model_comments = "gpt-3.5-turbo"
    mock_settings.get_api_key_for_provider = MagicMock(return_value="oa-key")
    mock_settings.get_base_url_for_provider = MagicMock(return_value=None)
    mock_settings.ai_max_tokens = 8192
    mock_settings.ai_temperature = 0.3
    mock_settings.ai_timeout = 45
    mock_settings.ai_max_retries = 1
    mock_settings.ai_retry_delay = 1.0

    with patch('apps.ai_reviewer.services.ai_review_service.settings', mock_settings):
        override_client = service._get_client_for_pass("comments")
        # Should auto-detect provider as "openai" based on "gpt-3.5-turbo" model prefix
        assert override_client.config.provider.value == "openai"
        assert override_client.config.model == "gpt-3.5-turbo"
        assert override_client.config.api_key == "oa-key"


