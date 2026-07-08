import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from apps.ai_reviewer.integrations.github_sync_service import GitHubSyncService
from apps.ai_reviewer.models.review import Review, ReviewComment
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.review_run import ReviewRun
from apps.ai_reviewer.models.file_review_history import FileReviewHistory

class DummyComment:
    def __init__(self, comment, file_path, line_start, severity, category, suggestion=None, confidence=None):
        self.comment = comment
        self.file_path = file_path
        self.line_start = line_start
        self.severity = severity
        self.category = category
        self.suggestion = suggestion
        self.confidence = confidence


def test_parse_explanation():
    service = GitHubSyncService(db=None)
    explanation = (
        "[Issue] Mutable blacklist allows bypass.\n"
        "[Evidence] line 8 in auth.js: `let blacklist = [];` — Global scope variable.\n"
        "[Impact] Attacker could clear blacklist.\n"
        "[Suggestion] Use static private property."
    )
    
    sections = service._parse_explanation(explanation)
    assert sections['issue'] == "Mutable blacklist allows bypass."
    assert sections['evidence'] == "line 8 in auth.js: `let blacklist = [];` — Global scope variable."
    assert sections['impact'] == "Attacker could clear blacklist."
    assert sections['suggestion'] == "Use static private property."


def test_parse_evidence():
    service = GitHubSyncService(db=None)
    
    # Test case 1: with backticks and em-dash
    ev1 = "line 8 in src/auth.js: `let blacklist = [];` — Global scope variable."
    parsed1 = service._parse_evidence(ev1)
    assert parsed1 is not None
    assert parsed1['line'] == '8'
    assert parsed1['file'] == 'src/auth.js'
    assert parsed1['code'] == 'let blacklist = [];'
    assert parsed1['context'] == 'Global scope variable.'

    # Test case 2: without backticks and without context
    ev2 = "line 42 in worker.py: while true:"
    parsed2 = service._parse_evidence(ev2)
    assert parsed2 is not None
    assert parsed2['line'] == '42'
    assert parsed2['file'] == 'worker.py'
    assert parsed2['code'] == 'while true:'
    assert parsed2['context'] == ''


def test_get_language_from_filename():
    service = GitHubSyncService(db=None)
    assert service._get_language_from_filename("auth.js") == "javascript"
    assert service._get_language_from_filename("src/worker.py") == "python"
    assert service._get_language_from_filename("README") == ""


def test_convert_comments_rich_markdown():
    service = GitHubSyncService(db=None)
    raw_explanation = (
        "[Issue] Mutable blacklist allows bypass.\n"
        "[Evidence] line 8 in auth.js: `let blacklist = [];` — Global scope variable.\n"
        "[Impact] Attacker could clear blacklist.\n"
        "[Suggestion] Use static private property."
    )
    comment = DummyComment(
        comment=raw_explanation,
        file_path="auth.js",
        line_start=8,
        severity="warning",
        category="security",
        suggestion="Use static private property.",
        confidence=0.9
    )
    
    results = service._convert_comments([comment])
    assert len(results) == 1
    body = results[0]['body']
    
    assert "### ⚠️ **Warning** • **security**" in body
    assert "> 🎯 **Issue**\n> Mutable blacklist allows bypass." in body
    assert "> 🔍 **Evidence** (File `auth.js`, Line 8)" in body
    assert "> ```javascript\n> let blacklist = [];\n> ```" in body
    assert "> *Global scope variable.*" in body
    assert "> 💥 **Impact**\n> Attacker could clear blacklist." in body
    assert "> 💡 **Suggestion**\n> Use static private property." in body
    assert "Proposed Fix" not in body  # because suggestion equals sections['suggestion']
    assert "Confidence: 90%" in body


def test_convert_comments_new_markdown():
    service = GitHubSyncService(db=None)
    new_comment_markdown = (
        "**[Functional Correctness] 🟠 Missing null guard**\n\n"
        "`user.profile.name` may throw when profile is null.\n\n"
        "**Why this matters**\n\n"
        "This can cause a runtime exception.\n\n"
        "**Suggested fix**\n\n"
        "Use optional chaining or validate the profile first.\n\n"
        "```suggestion\n"
        "const displayName = user.profile?.name ?? \"Unknown\";\n"
        "```"
    )
    comment = DummyComment(
        comment=new_comment_markdown,
        file_path="auth.js",
        line_start=8,
        severity="warning",
        category="security",
        suggestion="const displayName = user.profile?.name ?? \"Unknown\";",
        confidence=0.9
    )
    
    results = service._convert_comments([comment])
    assert len(results) == 1
    body = results[0]['body']
    
    assert body == new_comment_markdown
    assert "Missing null guard" in body
    assert "```suggestion" in body


def test_parse_finding_details_legacy():
    service = GitHubSyncService(db=None)
    legacy_comment = (
        "[Issue] Mutable blacklist allows bypass.\n"
        "[Evidence] line 8 in auth.js: `let blacklist = [];` — Global scope variable.\n"
        "[Impact] Attacker could clear blacklist.\n"
        "[Suggestion] Use static private property."
    )
    details = service._parse_finding_details(legacy_comment, "security", "Use static private property.")
    assert details['title'] == "Mutable blacklist allows bypass."
    assert details['evidence'] == "line 8 in auth.js: `let blacklist = [];` — Global scope variable."
    assert details['impact'] == "Attacker could clear blacklist."
    assert details['fix'] == "Use static private property."


def test_parse_finding_details_new():
    service = GitHubSyncService(db=None)
    new_comment = (
        "**[Security] 🔴 Incomplete AST sanitization**\n\n"
        "The AST filter still allows unsafe nodes during formula evaluation.\n\n"
        "**Why this matters:**\n"
        "A crafted formula may execute unintended behavior.\n\n"
        "**Suggested fix:**\n"
        "Restrict the allowed AST node list."
    )
    details = service._parse_finding_details(new_comment, "Security", "Restrict the allowed AST node list.")
    assert details['title'] == "Incomplete AST sanitization"
    assert details['evidence'] == "The AST filter still allows unsafe nodes during formula evaluation."
    assert details['impact'] == "A crafted formula may execute unintended behavior."
    assert details['fix'] == "Restrict the allowed AST node list."


def test_calculate_signature():
    service = GitHubSyncService(db=None)
    sig1 = service._calculate_signature("auth.py", "Security", "Title", "Evidence text")
    sig2 = service._calculate_signature("auth.py", "Security", "Title", "Evidence   text")
    assert sig1 == sig2  # Whitespace normalized signature should be identical


def test_upsert_pr_body():
    service = GitHubSyncService(db=None)
    original = "Hello World\nKeep this description."
    summary = "## 🤖 AI PR Summary\nChanges are great!"
    
    # 1. No markers exist -> append
    new_body = service._upsert_pr_body(original, summary)
    assert "Keep this description." in new_body
    assert "<!-- ai-pr-summary:start -->" in new_body
    assert "Changes are great!" in new_body
    
    # 2. Markers exist -> replace
    modified_original = f"Hello World\n<!-- ai-pr-summary:start -->\nOLD SUMMARY\n<!-- ai-pr-summary:end -->\nKeep this description."
    updated_body = service._upsert_pr_body(modified_original, summary)
    assert "OLD SUMMARY" not in updated_body
    assert "Changes are great!" in updated_body
    assert "Hello World" in updated_body
    assert "Keep this description." in updated_body


@pytest.mark.asyncio
async def test_build_walkthrough_body_rendering():
    mock_db = MagicMock()
    service = GitHubSyncService(db=mock_db)

    review = MagicMock(spec=Review)
    review.summary = "Executive summary text."
    review.ai_passes = {
        "pass_1_understanding": {
            "summary": "This PR updates security-sensitive paths.",
            "changes": [
                {
                    "files": ["src/controllers/discount_controller.py"],
                    "summary": "Fixed discount formula evaluation"  # Disallowed word 'Fixed' to test replacement
                },
                {
                    "files": ["src/models/coupon.py"],
                    "summary": "Updates coupon traversal logic"
                }
            ],
            "sequence_diagram": {
                "include": True,
                "title": "Updated request flow",
                "mermaid": "sequenceDiagram\n    Client->>API: request"
            }
        }
    }
    review.review_type = "pull_request"

    snapshot = MagicMock(spec=Snapshot)
    snapshot.files_changed = ["src/controllers/discount_controller.py", "src/models/coupon.py"]
    snapshot.commit_sha = "headsha1234567890"

    completed_run = MagicMock(spec=ReviewRun)
    completed_run.base_sha = "basesha"
    completed_run.head_sha = "headsha"
    completed_run.mode = "incremental"
    completed_run.new_findings_count = 2
    completed_run.resolved_findings_count = 0
    completed_run.duplicate_findings_count = 1
    completed_run.created_at = datetime.utcnow()

    hist_item = MagicMock(spec=FileReviewHistory)
    hist_item.file_path = "src/controllers/discount_controller.py"
    hist_item.first_seen_sha = "basesha"
    hist_item.last_seen_sha = "headsha"
    hist_item.review_status = "1 critical finding"
    hist_item.findings_count = 1
    hist_item.skipped_reason = None

    body = await service._build_walkthrough_body(
        review=review,
        snapshot=snapshot,
        new_count=1,
        duplicate_count=1,
        resolved_count=0,
        existing_count=0,
        severity_counts={'critical': 1, 'major': 0, 'minor': 0, 'info': 0},
        file_results={"src/controllers/discount_controller.py": "1 critical finding"},
        comments_to_post=[],
        base_sha="basesha",
        completed_runs=[completed_run],
        all_file_hist=[hist_item]
    )

    assert "## 🤖 Review Walkthrough" in body
    assert "This PR updates security-sensitive paths." in body
    assert "Updates discount formula evaluation" in body  # 'Fixed' replaced with 'Updates'
    assert "src/controllers/discount_controller.py" in body
    assert "### Sequence Diagram" in body
    assert "Updated request flow." in body
    assert "sequenceDiagram" in body
    assert "🔴 Critical" not in body
    assert "Push Review History" not in body
    assert "Notes" not in body
    assert "Files Reviewed" not in body
