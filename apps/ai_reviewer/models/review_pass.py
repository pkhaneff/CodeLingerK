from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from apps.ai_reviewer.services.evidence_service import EvidenceReport
from apps.ai_reviewer.services.ranking_service import RankingReport


class ReviewPipelineError(Exception):
    """
    Raised when the AI review pipeline cannot produce a reliable verdict.
    """

    def __init__(self, failed_passes: list[str], total_passes: int):
        self.failed_passes = failed_passes
        self.total_passes = total_passes
        names = ', '.join(failed_passes)
        super().__init__(
            f'Review pipeline failed: {len(failed_passes)}/{total_passes} passes failed '
            f'({names}). Cannot produce a reliable verdict. Worker will retry.'
        )


class ReviewCategory(str, Enum):
    """Review comment categories."""
    BUG = 'bug'
    SECURITY = 'security'
    PERFORMANCE = 'performance'
    DESIGN = 'design'
    MAINTAINABILITY = 'maintainability'
    TESTING = 'testing'
    DOCUMENTATION = 'documentation'


@dataclass
class ReviewPass:
    """Result of a single review pass."""

    name: str
    prompt: str
    response: str
    tokens_used: int
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedComment:
    """AI-generated review comment."""

    file_path: str
    line_start: int
    line_end: int | None
    severity: str
    category: str
    explanation: str
    suggestion: str | None
    confidence: float
    title: str = ''


@dataclass
class ReviewResult:
    """Complete result of AI review."""

    passes: list[ReviewPass]
    comments: list[GeneratedComment]
    summary: str
    verdict: str
    total_tokens: int
    duration_ms: int
    # V1 quality gate metadata
    evidence_report: EvidenceReport | None = None
    ranking_report: RankingReport | None = None
    static_findings_count: int = 0
    incremental_files_skipped: int = 0
    # Number of AI passes that failed (0 = clean, 1-2 = partial)
    pipeline_failures: int = 0


@dataclass
class ExternalContext:
    """
    External context to enhance AI review accuracy.

    Provides additional information beyond the diff itself.
    """

    pr_title: str | None = None
    pr_description: str | None = None
    linked_issues: list[str] | None = None
    coding_conventions: str | None = None
    tech_stack: str | None = None
