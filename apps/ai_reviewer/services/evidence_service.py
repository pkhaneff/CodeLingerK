"""
EvidenceService - Validate and ground AI-generated comments to real diff evidence.

This is the core quality gate of the review pipeline.

Principle:
    NO EVIDENCE = NO COMMENT

Flow:
    GeneratedComment (from LLM)
        ↓
    EvidenceCheck (validate file/line exists in diff)
        ↓
    ValidationResult (PASS / FAIL + reason)
        ↓
    Confidence adjustment (grounded comments get confidence boost)

A comment passes evidence validation only if:
1. The file_path it references actually exists in the snapshot diff.
2. The line_start it references falls within a changed hunk range.
3. The comment is not a duplicate of an already-validated comment.

Comments that fail validation are dropped before being ranked or published.
This prevents the LLM from hallucinating file names or line numbers.
"""

from dataclasses import dataclass, field
from enum import Enum

from core.logging_config import get_logger
from apps.ai_reviewer.services.context_service import FileContext, SnapshotContext

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Validation types
# ─────────────────────────────────────────────

class ValidationStatus(str, Enum):
    """Outcome of evidence validation for a single comment."""
    PASS = 'pass'           # Comment is grounded — keep it
    FAIL_FILE = 'fail_file'       # file_path not in diff
    FAIL_LINE = 'fail_line'       # line_start not in any changed hunk
    FAIL_DUPLICATE = 'fail_dup'   # Duplicate of an already-accepted comment


@dataclass
class EvidenceValidation:
    """
    Validation result for a single GeneratedComment.

    Attributes:
        status: Whether the comment passed evidence checks
        reason: Human-readable explanation for FAIL cases
        grounded_file: The FileContext entry matched (if any)
        grounded_line: Closest hunk line matched (if any)
        confidence_delta: Adjustment to confidence (positive = boost, negative = penalty)
    """
    status: ValidationStatus
    reason: str = ''
    grounded_file: FileContext | None = None
    grounded_line: int | None = None
    confidence_delta: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASS


@dataclass
class EvidenceReport:
    """
    Summary report from the evidence engine for one review pass.

    Attributes:
        total: Total comments submitted
        passed: Comments that passed validation
        dropped_file: Comments dropped due to invalid file_path
        dropped_line: Comments dropped due to invalid line_start
        dropped_duplicate: Comments dropped as duplicates
    """
    total: int = 0
    passed: int = 0
    dropped_file: int = 0
    dropped_line: int = 0
    dropped_duplicate: int = 0

    @property
    def drop_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.total - self.passed) / self.total

    def to_dict(self) -> dict:
        return {
            'total': self.total,
            'passed': self.passed,
            'dropped_file': self.dropped_file,
            'dropped_line': self.dropped_line,
            'dropped_duplicate': self.dropped_duplicate,
            'drop_rate': round(self.drop_rate, 3),
        }


# ─────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────

class EvidenceService:
    """
    Validate AI-generated comments against the actual PR diff.

    Enforces the principle: NO EVIDENCE = NO COMMENT.

    Usage:
        service = EvidenceService(context)
        validated, report = service.validate(raw_comments)
    """

    # How far a line can be from a hunk boundary and still count as grounded.
    # This accounts for LLMs reporting the hunk header line vs. the actual change.
    LINE_TOLERANCE = 5

    def __init__(self, context: SnapshotContext):
        """
        Initialize evidence service.

        Args:
            context: The SnapshotContext containing parsed diff information.
        """
        self.context = context
        self._file_index = self._build_file_index()
        self._hunk_index = self._build_hunk_index()

    def _build_file_index(self) -> dict[str, FileContext]:
        """
        Build a fast lookup from file_path → FileContext.

        Normalizes paths to be consistent with the diff.
        """
        index: dict[str, FileContext] = {}
        for file_ctx in self.context.files:
            index[file_ctx.file_path] = file_ctx
            # Also index without leading slash or ./ prefix
            normalized = file_ctx.file_path.lstrip('./')
            index[normalized] = file_ctx
        return index

    def _build_hunk_index(self) -> dict[str, list[tuple[int, int]]]:
        """
        Build a lookup from file_path → list of (start_line, end_line) hunk ranges.

        Used to quickly check whether a line number falls within any changed hunk.
        """
        index: dict[str, list[tuple[int, int]]] = {}
        for file_ctx in self.context.files:
            ranges = []
            for hunk in file_ctx.hunks:
                new_start = hunk.get('new_start', 1)
                new_count = hunk.get('new_count', 1)
                hunk_end = new_start + new_count - 1
                ranges.append((new_start, max(hunk_end, new_start)))
            index[file_ctx.file_path] = ranges
            # Also normalize path
            normalized = file_ctx.file_path.lstrip('./')
            index[normalized] = ranges
        return index

    def validate_comment(
        self,
        file_path: str,
        line_start: int | None,
        seen_signatures: set[str],
    ) -> EvidenceValidation:
        """
        Validate a single comment against the diff.

        Args:
            file_path: The file the comment references.
            line_start: The line number the comment references.
            seen_signatures: Set of already-accepted comment signatures to detect dups.

        Returns:
            EvidenceValidation with pass/fail status and grounding details.
        """
        # Check 1: Does the file exist in the diff?
        grounded_file = self._file_index.get(file_path)
        if grounded_file is None:
            return EvidenceValidation(
                status=ValidationStatus.FAIL_FILE,
                reason=(
                    f"File '{file_path}' not found in snapshot diff. "
                    f"Changed files: {[f.file_path for f in self.context.files[:5]]}"
                ),
                confidence_delta=-0.5,
            )

        # Check 2: If a line is provided, does it fall within a hunk?
        grounded_line: int | None = None
        if line_start is not None:
            hunk_ranges = self._hunk_index.get(file_path, [])
            in_range = False

            for hunk_start, hunk_end in hunk_ranges:
                # Check with tolerance window
                if (hunk_start - self.LINE_TOLERANCE) <= line_start <= (hunk_end + self.LINE_TOLERANCE):
                    in_range = True
                    grounded_line = line_start
                    break

            if not in_range and hunk_ranges:
                # Line is outside all hunks — penalize but don't hard drop
                # (LLM may point to the function signature on a nearby line)
                return EvidenceValidation(
                    status=ValidationStatus.FAIL_LINE,
                    reason=(
                        f"Line {line_start} in '{file_path}' is not within any changed hunk. "
                        f"Hunk ranges: {hunk_ranges[:3]}"
                    ),
                    grounded_file=grounded_file,
                    confidence_delta=-0.2,
                )

        # Check 3: Duplicate detection
        signature = f'{file_path}:{line_start}'
        if signature in seen_signatures:
            return EvidenceValidation(
                status=ValidationStatus.FAIL_DUPLICATE,
                reason=f'Duplicate comment at {signature}',
                confidence_delta=-1.0,
            )

        return EvidenceValidation(
            status=ValidationStatus.PASS,
            grounded_file=grounded_file,
            grounded_line=grounded_line,
            # Boost confidence slightly if line is precisely grounded
            confidence_delta=0.05 if grounded_line is not None else 0.0,
        )

    def filter_comments(
        self,
        comments: list,
        min_confidence: float = 0.5,
    ) -> tuple[list, EvidenceReport]:
        """
        Filter a list of GeneratedComment objects using evidence validation.

        Comments without file/line evidence are dropped.
        Confidence is adjusted based on grounding quality.

        Args:
            comments: List of GeneratedComment dataclass objects.
            min_confidence: Minimum confidence threshold after adjustment.

        Returns:
            Tuple of (validated_comments, EvidenceReport)
        """
        report = EvidenceReport(total=len(comments))
        validated = []
        seen_signatures: set[str] = set()

        for comment in comments:
            file_path = getattr(comment, 'file_path', '')
            line_start = getattr(comment, 'line_start', None)

            result = self.validate_comment(file_path, line_start, seen_signatures)

            if result.status == ValidationStatus.FAIL_FILE:
                report.dropped_file += 1
                logger.debug(f'Evidence FAIL (file): {result.reason}')
                continue

            if result.status == ValidationStatus.FAIL_LINE:
                report.dropped_line += 1
                logger.debug(f'Evidence FAIL (line): {result.reason}')
                continue

            if result.status == ValidationStatus.FAIL_DUPLICATE:
                report.dropped_duplicate += 1
                logger.debug(f'Evidence FAIL (dup): {result.reason}')
                continue

            # Comment passed — apply confidence delta
            original_confidence = getattr(comment, 'confidence', 0.5)
            adjusted_confidence = min(1.0, max(0.0, original_confidence + result.confidence_delta))

            # Check minimum confidence threshold
            if adjusted_confidence < min_confidence:
                report.dropped_file += 1  # Count as low-evidence drop
                logger.debug(
                    f'Evidence DROP (low confidence {adjusted_confidence:.2f}): '
                    f'{file_path}:{line_start}'
                )
                continue

            # Mutate confidence on the comment
            object.__setattr__(comment, 'confidence', adjusted_confidence) if hasattr(comment, '__dataclass_fields__') else setattr(comment, 'confidence', adjusted_confidence)

            signature = f'{file_path}:{line_start}'
            seen_signatures.add(signature)
            validated.append(comment)
            report.passed += 1

        logger.info(
            f'Evidence engine: {report.passed}/{report.total} comments passed '
            f'(drop rate: {report.drop_rate:.1%})'
        )

        if report.drop_rate > 0.5:
            logger.warning(
                f'High hallucination rate detected: {report.drop_rate:.0%} of comments '
                f'had no diff evidence. Consider improving the grounding prompt.'
            )

        return validated, report
