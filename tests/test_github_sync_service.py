import pytest
from unittest.mock import MagicMock
from apps.ai_reviewer.services.github_sync_service import GitHubSyncService

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
        suggestion="Use static private property.",  # Redundant with [Suggestion] text, should not add extra Proposed Fix block
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

    # Test case 3: Different suggestion code should append Proposed Fix
    comment_diff_sugg = DummyComment(
        comment=raw_explanation,
        file_path="auth.js",
        line_start=8,
        severity="warning",
        category="security",
        suggestion="class Auth {\n  static #blacklist = [];\n}",
        confidence=0.9
    )
    results_diff = service._convert_comments([comment_diff_sugg])
    body_diff = results_diff[0]['body']
    assert "**Proposed Fix:**" in body_diff
    assert "static #blacklist" in body_diff
