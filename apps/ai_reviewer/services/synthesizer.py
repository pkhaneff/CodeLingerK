from typing import Any
from core.logger import get_logger

logger = get_logger(__name__)


class Synthesizer:
    """Merges and de-duplicates review comments from multiple chunks."""

    def merge_comments(self, all_comments: list[Any]) -> list[Any]:
        """Merge comments, de-duplicating by file_path, line_start, and normalized body suggestion."""
        seen = set()
        unique_comments = []

        for comment in all_comments:
            normalized_body = ''.join(comment.explanation.split()).lower()
            key = (comment.file_path, comment.line_start, normalized_body)

            if key not in seen:
                seen.add(key)
                unique_comments.append(comment)
            else:
                logger.info(f'Synthesizer: de-duplicated comment on {comment.file_path}:{comment.line_start}')

        return unique_comments
