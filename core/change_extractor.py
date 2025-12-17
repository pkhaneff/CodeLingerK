"""
ChangeExtractor: Core integration layer that orchestrates the parsing pipeline.

This module ties together:
- GitManager: Extract diffs from repository
- DiffParser: Parse raw diffs into structured hunks
- PythonParser: Parse code into AST symbols
- ChangeUnit: Final output with old/new symbols and context
"""

import logging
from typing import List, Optional, Dict
from pathlib import Path

from core.git_manager import GitManager
from core.diff_parser import DiffParser, ParsedDiff
from core.parser.python_parser import PythonParser
from core.models import ChangeUnit, CodeSymbol

logger = logging.getLogger(__name__)

class ChangeExtractor:
    """
    Orchestrates the extraction and structuring of code changes.

    This is the main entry point for Step 1 of the pipeline:
    "Extract & Structure" - converting Git diffs into semantic Change Units.
    """

    def __init__(self, repo_path: str):
        """
        Initialize the ChangeExtractor.

        Args:
            repo_path: Path to the Git repository
        """
        self.repo_path = Path(repo_path)
        self.git_manager = GitManager(str(repo_path))
        self.diff_parser = DiffParser()
        self.python_parser = PythonParser()

    def extract_changes(
        self,
        mode: str = "staged",
        commit_sha: Optional[str] = None,
        base_branch: Optional[str] = None,
        compare_branch: Optional[str] = None
    ) -> List[ChangeUnit]:
        """
        Extract all code changes as structured ChangeUnits.

        Args:
            mode: Type of changes to extract - "staged", "unstaged", "all", "commit", "branch"
            commit_sha: Required if mode="commit"
            base_branch: Required if mode="branch"
            compare_branch: Optional, defaults to "HEAD" if mode="branch"

        Returns:
            List of ChangeUnit objects representing semantic code changes
        """
        logger.info(f"Extracting changes in mode: {mode}")

        # Get raw diffs from Git
        raw_diffs = self._get_raw_diffs(mode, commit_sha, base_branch, compare_branch)

        if not raw_diffs:
            logger.warning("No changes found")
            return []

        # Process each file's diff
        change_units = []
        for diff_data in raw_diffs:
            file_path = diff_data['path']
            diff_text = diff_data['diff']
            change_type = diff_data.get('change_type', 'M')

            logger.debug(f"Processing {file_path} (change_type: {change_type})")

            # Parse diff into structured format
            parsed_diff = self.diff_parser.parse_diff(diff_text)

            # Extract change units for this file
            units = self._extract_file_changes(file_path, parsed_diff, change_type)
            change_units.extend(units)

        logger.info(f"Extracted {len(change_units)} change units")
        return change_units

    def _get_raw_diffs(
        self,
        mode: str,
        commit_sha: Optional[str],
        base_branch: Optional[str],
        compare_branch: Optional[str]
    ) -> List[Dict[str, str]]:
        """Get raw diffs from Git based on mode"""
        if mode == "staged":
            return self.git_manager.get_staged_changes()
        elif mode == "unstaged":
            return self.git_manager.get_unstaged_changes()
        elif mode == "all":
            return self.git_manager.get_all_changes()
        elif mode == "commit":
            if not commit_sha:
                raise ValueError("commit_sha is required for mode='commit'")
            return self.git_manager.get_commit_diff(commit_sha)
        elif mode == "branch":
            if not base_branch:
                raise ValueError("base_branch is required for mode='branch'")
            return self.git_manager.get_branch_diff(base_branch, compare_branch or "HEAD")
        else:
            raise ValueError(f"Invalid mode: {mode}")

    def _extract_file_changes(
        self,
        file_path: str,
        parsed_diff: ParsedDiff,
        change_type: str
    ) -> List[ChangeUnit]:
        """
        Extract semantic change units from a parsed diff.

        Args:
            file_path: Path to the changed file
            parsed_diff: Parsed diff structure
            change_type: Git change type (A/M/D/R)

        Returns:
            List of ChangeUnits for this file
        """
        # Only process Python files for now
        if not file_path.endswith('.py'):
            logger.debug(f"Skipping non-Python file: {file_path}")
            return []

        change_units = []

        # Handle different change types
        if parsed_diff.is_new_file or change_type == 'A':
            # File added - all symbols are new
            units = self._handle_new_file(file_path, parsed_diff)
            change_units.extend(units)

        elif parsed_diff.is_deleted_file or change_type == 'D':
            # File deleted - all symbols are deleted
            units = self._handle_deleted_file(file_path, parsed_diff)
            change_units.extend(units)

        elif parsed_diff.is_renamed or change_type == 'R':
            # File renamed - track both paths
            units = self._handle_renamed_file(file_path, parsed_diff)
            change_units.extend(units)

        else:
            # File modified - compare old vs new symbols
            units = self._handle_modified_file(file_path, parsed_diff)
            change_units.extend(units)

        return change_units

    def _handle_new_file(self, file_path: str, parsed_diff: ParsedDiff) -> List[ChangeUnit]:
        """Handle newly added files"""
        # Get new file content from working tree
        try:
            new_content = self._read_working_file(file_path)
            if not new_content:
                return []

            # Parse symbols
            new_symbols = self.python_parser.parse_file(new_content)

            # Create ChangeUnit for each new symbol
            units = []
            for symbol in new_symbols:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="added",
                    new_symbol=symbol,
                    diff_hunk=self._get_symbol_diff_hunk(symbol, parsed_diff)
                )
                units.append(unit)

            # If no symbols found, create a file-level change unit
            if not units and parsed_diff.hunks:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="added",
                    diff_hunk="\n".join([h.header + "\n" + "\n".join(h.lines) for h in parsed_diff.hunks])
                )
                units.append(unit)

            return units
        except Exception as e:
            logger.error(f"Error handling new file {file_path}: {e}")
            return []

    def _handle_deleted_file(self, file_path: str, parsed_diff: ParsedDiff) -> List[ChangeUnit]:
        """Handle deleted files"""
        try:
            # Get old file content from Git
            old_content = self.git_manager.get_file_content(file_path, "HEAD")
            if not old_content:
                return []

            # Parse symbols
            old_symbols = self.python_parser.parse_file(old_content)

            # Create ChangeUnit for each deleted symbol
            units = []
            for symbol in old_symbols:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="deleted",
                    old_symbol=symbol,
                    diff_hunk=self._get_symbol_diff_hunk(symbol, parsed_diff)
                )
                units.append(unit)

            # If no symbols found, create a file-level change unit
            if not units and parsed_diff.hunks:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="deleted",
                    diff_hunk="\n".join([h.header + "\n" + "\n".join(h.lines) for h in parsed_diff.hunks])
                )
                units.append(unit)

            return units
        except Exception as e:
            logger.error(f"Error handling deleted file {file_path}: {e}")
            return []

    def _handle_renamed_file(self, file_path: str, parsed_diff: ParsedDiff) -> List[ChangeUnit]:
        """Handle renamed files"""
        # For now, treat as modified
        logger.info(f"File renamed: {parsed_diff.old_path} -> {parsed_diff.new_path}")
        return self._handle_modified_file(file_path, parsed_diff)

    def _handle_modified_file(self, file_path: str, parsed_diff: ParsedDiff) -> List[ChangeUnit]:
        """Handle modified files - the most complex case"""
        try:
            # Get old and new content
            old_content = self.git_manager.get_file_content(file_path, "HEAD")
            new_content = self._read_working_file(file_path)

            if not old_content or not new_content:
                logger.warning(f"Could not read old/new content for {file_path}")
                return []

            # Parse both versions
            old_symbols = self.python_parser.parse_file(old_content)
            new_symbols = self.python_parser.parse_file(new_content)

            # Match symbols by name and detect changes
            return self._match_and_compare_symbols(
                file_path,
                old_symbols,
                new_symbols,
                parsed_diff
            )

        except Exception as e:
            logger.error(f"Error handling modified file {file_path}: {e}")
            return []

    def _match_and_compare_symbols(
        self,
        file_path: str,
        old_symbols: List[CodeSymbol],
        new_symbols: List[CodeSymbol],
        parsed_diff: ParsedDiff
    ) -> List[ChangeUnit]:
        """
        Match old and new symbols to detect modifications, additions, and deletions.

        Strategy:
        1. Match by name + type
        2. Compare body_hash to detect modifications
        3. Unmatched old symbols = deleted
        4. Unmatched new symbols = added
        """
        units = []

        # Create lookup dictionaries
        old_map = {(s.name, s.type): s for s in old_symbols}
        new_map = {(s.name, s.type): s for s in new_symbols}

        # Track matched symbols
        matched_keys = set()

        # Find modifications and unchanged symbols
        for key, new_symbol in new_map.items():
            if key in old_map:
                old_symbol = old_map[key]
                matched_keys.add(key)

                # Check if content changed
                if old_symbol.body_hash != new_symbol.body_hash:
                    # Modified
                    unit = ChangeUnit(
                        file_path=file_path,
                        change_type="modified",
                        old_symbol=old_symbol,
                        new_symbol=new_symbol,
                        diff_hunk=self._get_symbol_diff_hunk(new_symbol, parsed_diff)
                    )
                    units.append(unit)
                # If hashes match, symbol is unchanged - skip

        # Find added symbols (in new but not in old)
        for key, new_symbol in new_map.items():
            if key not in old_map:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="added",
                    new_symbol=new_symbol,
                    diff_hunk=self._get_symbol_diff_hunk(new_symbol, parsed_diff)
                )
                units.append(unit)

        # Find deleted symbols (in old but not in new)
        for key, old_symbol in old_map.items():
            if key not in new_map:
                unit = ChangeUnit(
                    file_path=file_path,
                    change_type="deleted",
                    old_symbol=old_symbol,
                    diff_hunk=self._get_symbol_diff_hunk(old_symbol, parsed_diff)
                )
                units.append(unit)

        return units

    def _get_symbol_diff_hunk(self, symbol: CodeSymbol, parsed_diff: ParsedDiff) -> str:
        """
        Find the diff hunk that corresponds to this symbol's line range.

        Args:
            symbol: CodeSymbol to find hunk for
            parsed_diff: ParsedDiff containing hunks

        Returns:
            Relevant diff hunk as string
        """
        relevant_hunks = []

        for hunk in parsed_diff.hunks:
            hunk_start, hunk_end = hunk.get_modified_range()

            # Check if symbol's lines overlap with this hunk
            if self._ranges_overlap(
                symbol.line_start, symbol.line_end,
                hunk_start, hunk_end
            ):
                hunk_text = hunk.header + "\n" + "\n".join(hunk.lines)
                relevant_hunks.append(hunk_text)

        if relevant_hunks:
            return "\n".join(relevant_hunks)
        else:
            # Return all hunks if we can't determine overlap
            return "\n".join([h.header + "\n" + "\n".join(h.lines) for h in parsed_diff.hunks])

    def _ranges_overlap(self, start1: int, end1: int, start2: int, end2: int) -> bool:
        """Check if two line ranges overlap"""
        return not (end1 < start2 or end2 < start1)

    def _read_working_file(self, file_path: str) -> Optional[str]:
        """Read file from working directory"""
        try:
            full_path = self.repo_path / file_path
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading working file {file_path}: {e}")
            return None

    def get_summary(self, change_units: List[ChangeUnit]) -> Dict[str, any]:
        """
        Generate a summary of extracted changes.

        Args:
            change_units: List of ChangeUnits

        Returns:
            Dictionary with summary statistics
        """
        summary = {
            "total_changes": len(change_units),
            "by_type": {
                "added": 0,
                "modified": 0,
                "deleted": 0,
                "renamed": 0
            },
            "by_symbol_type": {},
            "files_affected": set()
        }

        for unit in change_units:
            # Count by change type
            summary["by_type"][unit.change_type] = summary["by_type"].get(unit.change_type, 0) + 1

            # Count by symbol type
            symbol = unit.new_symbol or unit.old_symbol
            if symbol:
                symbol_type = symbol.type
                summary["by_symbol_type"][symbol_type] = summary["by_symbol_type"].get(symbol_type, 0) + 1

            # Track files
            summary["files_affected"].add(unit.file_path)

        # Convert set to list for JSON serialization
        summary["files_affected"] = list(summary["files_affected"])
        summary["num_files"] = len(summary["files_affected"])

        return summary
