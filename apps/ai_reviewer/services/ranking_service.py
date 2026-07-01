"""
RankingService - Score and filter AI review findings.

Applies the V1 ranking formula from the Architecture Spec:

    Score = severity_weight × confidence × blast_radius × reproducibility

Findings below the minimum score threshold (0.6) are dropped.
This prevents low-signal comments from polluting the review.

Score Components:
    severity_weight:  Numeric weight per severity level (critical=1.0, info=0.2)
    confidence:       LLM-reported confidence 0.0–1.0 (from GeneratedComment)
    blast_radius:     How many files/layers the finding's file affects (0.5–1.0)
    reproducibility:  Estimated reproducibility based on category (0.5–1.0)

The final ranked list is sorted by score descending, giving the most important
findings the highest priority for display and comment posting.
"""

from dataclasses import dataclass
from enum import Enum

from core.logging_config import get_logger
from apps.ai_reviewer.models.layer import Layer

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

# Minimum score to keep a finding
MIN_SCORE_THRESHOLD = 0.35

# Severity → numeric weight mapping
SEVERITY_WEIGHTS: dict[str, float] = {
    'critical': 1.0,
    'error': 0.85,
    'warning': 0.65,
    'info': 0.2,
}

# Default severity weight if not matched
DEFAULT_SEVERITY_WEIGHT = 0.4

# Category → reproducibility score
# Categories where the issue is nearly always reproducible get a higher score.
CATEGORY_REPRODUCIBILITY: dict[str, float] = {
    'bug': 0.95,
    'security': 1.0,
    'performance': 0.8,
    'design': 0.6,
    'maintainability': 0.5,
    'testing': 0.55,
    'documentation': 0.3,
}

DEFAULT_REPRODUCIBILITY = 0.6

# Layer type → blast radius factor
# High-risk layers (auth, db, api) amplify findings in them.
LAYER_BLAST_FACTORS: dict[str, float] = {
    'auth': 1.0,
    'db': 0.95,
    'api': 0.9,
    'service': 0.85,
    'config': 0.85,
    'model': 0.75,
    'infra': 0.7,
    'ui': 0.6,
    'util': 0.55,
    'test': 0.45,
    'unknown': 0.5,
}

DEFAULT_BLAST_FACTOR = 0.6


# ─────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────

@dataclass
class ScoredComment:
    """
    A GeneratedComment with a calculated ranking score.

    Attributes:
        comment: The original GeneratedComment object
        score: Final ranking score (0.0 – 1.0)
        severity_weight: Weight applied for severity
        blast_radius: Blast radius factor for the file's layer
        reproducibility: Reproducibility factor for the category
        kept: Whether the comment passed the minimum threshold
    """
    comment: object  # GeneratedComment
    score: float
    severity_weight: float
    blast_radius: float
    reproducibility: float
    kept: bool


@dataclass
class RankingReport:
    """
    Summary of the ranking pass.

    Attributes:
        total: Total comments before ranking
        kept: Comments kept after filtering
        dropped: Comments dropped below threshold
        threshold: The score threshold used
    """
    total: int
    kept: int
    dropped: int
    threshold: float

    @property
    def drop_rate(self) -> float:
        return self.dropped / self.total if self.total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            'total': self.total,
            'kept': self.kept,
            'dropped': self.dropped,
            'threshold': self.threshold,
            'drop_rate': round(self.drop_rate, 3),
        }


# ─────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────

class RankingService:
    """
    Score and rank AI review findings using the V1 scoring formula.

    Usage:
        ranker = RankingService(layers=layers)
        ranked_comments, report = ranker.rank(validated_comments)
    """

    def __init__(
        self,
        layers: list[Layer],
        threshold: float = MIN_SCORE_THRESHOLD,
    ):
        """
        Initialize ranking service.

        Args:
            layers: Layer records for this snapshot (used to determine blast radius)
            threshold: Minimum score to keep a finding (default 0.6)
        """
        self.layers = layers
        self.threshold = threshold
        self._file_to_layer = self._build_file_layer_map()

    def _build_file_layer_map(self) -> dict[str, str]:
        """
        Build a lookup from file_path → layer_type for blast radius calculation.
        """
        mapping: dict[str, str] = {}
        for layer in self.layers:
            layer_type = getattr(layer, 'layer_type', 'unknown')
            for file_path in (layer.files or []):
                mapping[file_path] = layer_type
        return mapping

    def _get_blast_radius(self, file_path: str) -> float:
        """
        Get blast radius factor for a file based on its layer type.

        Args:
            file_path: Relative file path

        Returns:
            Blast radius factor between 0.0 and 1.0
        """
        layer_type = self._file_to_layer.get(file_path, 'unknown')
        return LAYER_BLAST_FACTORS.get(layer_type, DEFAULT_BLAST_FACTOR)

    def _get_reproducibility(self, category: str) -> float:
        """
        Get reproducibility score for a finding category.

        Args:
            category: Finding category (bug, security, performance, etc.)

        Returns:
            Reproducibility score between 0.0 and 1.0
        """
        return CATEGORY_REPRODUCIBILITY.get(category.lower(), DEFAULT_REPRODUCIBILITY)

    def _get_severity_weight(self, severity: str) -> float:
        """
        Get numeric weight for a severity level.

        Args:
            severity: Severity string (critical, error, warning, info)

        Returns:
            Weight between 0.0 and 1.0
        """
        return SEVERITY_WEIGHTS.get(severity.lower(), DEFAULT_SEVERITY_WEIGHT)

    def score_comment(self, comment) -> ScoredComment:
        """
        Calculate the ranking score for a single comment.

        Formula:
            score = severity_weight × confidence × blast_radius × reproducibility

        Args:
            comment: A GeneratedComment dataclass object

        Returns:
            ScoredComment with score and component values
        """
        file_path = getattr(comment, 'file_path', '')
        severity = getattr(comment, 'severity', 'info')
        category = getattr(comment, 'category', 'design')
        confidence = getattr(comment, 'confidence', 0.5)

        severity_weight = self._get_severity_weight(severity)
        blast_radius = self._get_blast_radius(file_path)
        reproducibility = self._get_reproducibility(category)

        # Clamp confidence to valid range
        confidence = min(1.0, max(0.0, confidence))

        score = severity_weight * confidence * blast_radius * reproducibility

        logger.debug(
            f'Score {score:.3f} = sev({severity_weight:.2f}) × '
            f'conf({confidence:.2f}) × blast({blast_radius:.2f}) × '
            f'repro({reproducibility:.2f}) — {file_path}'
        )

        return ScoredComment(
            comment=comment,
            score=round(score, 4),
            severity_weight=severity_weight,
            blast_radius=blast_radius,
            reproducibility=reproducibility,
            kept=score >= self.threshold,
        )

    def rank(
        self,
        comments: list,
    ) -> tuple[list, RankingReport]:
        """
        Score, filter, and sort a list of comments.

        Applies the full ranking formula and drops findings below threshold.

        Args:
            comments: List of GeneratedComment objects (already evidence-validated)

        Returns:
            Tuple of (ranked_kept_comments, RankingReport)
        """
        if not comments:
            return [], RankingReport(total=0, kept=0, dropped=0, threshold=self.threshold)

        scored = [self.score_comment(c) for c in comments]

        # Sort by score descending
        scored.sort(key=lambda s: s.score, reverse=True)

        kept = [s.comment for s in scored if s.kept]
        dropped_count = len([s for s in scored if not s.kept])

        report = RankingReport(
            total=len(comments),
            kept=len(kept),
            dropped=dropped_count,
            threshold=self.threshold,
        )

        logger.info(
            f'Ranking: {report.kept}/{report.total} findings kept '
            f'(threshold={self.threshold}, drop_rate={report.drop_rate:.1%})'
        )

        if dropped_count > 0:
            dropped_scores = [
                f'{s.score:.3f}' for s in scored if not s.kept
            ]
            logger.debug(
                f'Dropped scores: {dropped_scores}'
            )

        return kept, report
