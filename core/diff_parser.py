import re
from typing import List, Optional, Tuple
from pydantic import BaseModel

class DiffHunk(BaseModel):
    """Represents a single hunk of changes in a diff"""
    old_start: int  
    old_count: int 
    new_start: int  
    new_count: int 
    header: str   
    lines: List[str] 

    def get_added_lines(self) -> List[Tuple[int, str]]:
        """Returns list of (line_number, content) for added lines"""
        added = []
        current_line = self.new_start
        for line in self.lines:
            if line.startswith('+') and not line.startswith('+++'):
                added.append((current_line, line[1:]))
                current_line += 1
            elif not line.startswith('-'):
                current_line += 1
        return added

    def get_deleted_lines(self) -> List[Tuple[int, str]]:
        """Returns list of (line_number, content) for deleted lines"""
        deleted = []
        current_line = self.old_start
        for line in self.lines:
            if line.startswith('-') and not line.startswith('---'):
                deleted.append((current_line, line[1:]))
                current_line += 1
            elif not line.startswith('+'):
                current_line += 1
        return deleted

    def get_modified_range(self) -> Tuple[int, int]:
        """Returns the range of lines affected in the new file"""
        return (self.new_start, self.new_start + self.new_count - 1)

class ParsedDiff(BaseModel):
    """Represents a parsed diff for a single file"""
    file_path: str
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    is_new_file: bool = False
    is_deleted_file: bool = False
    is_renamed: bool = False
    hunks: List[DiffHunk] = []

class DiffParser:
    """Parses Git diff output into structured format"""

    HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
    FILE_HEADER_OLD = re.compile(r'^--- (.+)$')
    FILE_HEADER_NEW = re.compile(r'^\+\+\+ (.+)$')
    NEW_FILE = re.compile(r'^new file mode')
    DELETED_FILE = re.compile(r'^deleted file mode')
    RENAME_FROM = re.compile(r'^rename from (.+)$')
    RENAME_TO = re.compile(r'^rename to (.+)$')

    @classmethod
    def parse_diff(cls, diff_text: str) -> ParsedDiff:
        """
        Parse a unified diff string into structured format.

        Args:
            diff_text: Raw diff output from Git

        Returns:
            ParsedDiff object with structured data
        """
        lines = diff_text.split('\n')

        parsed = ParsedDiff(file_path="")
        current_hunk: Optional[List[str]] = None
        current_hunk_header = None

        i = 0
        while i < len(lines):
            line = lines[i]

            # Parse file headers
            if line.startswith('---'):
                match = cls.FILE_HEADER_OLD.match(line)
                if match:
                    parsed.old_path = match.group(1)
                    # Extract clean file path (remove a/ prefix)
                    if parsed.old_path.startswith('a/'):
                        parsed.file_path = parsed.old_path[2:]
                    else:
                        parsed.file_path = parsed.old_path

            elif line.startswith('+++'):
                match = cls.FILE_HEADER_NEW.match(line)
                if match:
                    parsed.new_path = match.group(1)
                    # Extract clean file path (remove b/ prefix)
                    if parsed.new_path.startswith('b/'):
                        parsed.file_path = parsed.new_path[2:]
                    elif not parsed.file_path:
                        parsed.file_path = parsed.new_path

            # Parse file status
            elif cls.NEW_FILE.match(line):
                parsed.is_new_file = True
            elif cls.DELETED_FILE.match(line):
                parsed.is_deleted_file = True
            elif cls.RENAME_FROM.match(line):
                match = cls.RENAME_FROM.match(line)
                parsed.old_path = match.group(1)
                parsed.is_renamed = True
            elif cls.RENAME_TO.match(line):
                match = cls.RENAME_TO.match(line)
                parsed.new_path = match.group(1)
                parsed.file_path = match.group(1)

            # Parse hunk headers
            elif line.startswith('@@'):
                # Save previous hunk if exists
                if current_hunk is not None and current_hunk_header:
                    hunk = cls._create_hunk(current_hunk_header, current_hunk)
                    if hunk:
                        parsed.hunks.append(hunk)

                # Start new hunk
                current_hunk_header = line
                current_hunk = []

            # Collect hunk lines
            elif current_hunk is not None:
                if line.startswith(('+', '-', ' ')):
                    current_hunk.append(line)
                elif not line.strip():  # Empty line in hunk
                    current_hunk.append(' ')

            i += 1

        # Save last hunk
        if current_hunk is not None and current_hunk_header:
            hunk = cls._create_hunk(current_hunk_header, current_hunk)
            if hunk:
                parsed.hunks.append(hunk)

        return parsed

    @classmethod
    def _create_hunk(cls, header: str, lines: List[str]) -> Optional[DiffHunk]:
        """Create a DiffHunk from header and lines"""
        match = cls.HUNK_HEADER.match(header)
        if not match:
            return None

        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) else 1

        return DiffHunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            header=header,
            lines=lines
        )

    @classmethod
    def parse_multi_file_diff(cls, diff_text: str) -> List[ParsedDiff]:
        """
        Parse diff text that may contain multiple files.

        Args:
            diff_text: Raw diff output potentially containing multiple files

        Returns:
            List of ParsedDiff objects, one per file
        """
        # Split by 'diff --git' which marks file boundaries
        file_sections = re.split(r'^(?=diff --git)', diff_text, flags=re.MULTILINE)

        results = []
        for section in file_sections:
            if section.strip():
                try:
                    parsed = cls.parse_diff(section)
                    if parsed.file_path:  # Only add if we found a file path
                        results.append(parsed)
                except Exception:
                    # Skip malformed diffs
                    continue

        return results
