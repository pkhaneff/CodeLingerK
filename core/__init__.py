"""
CodeLingerK Core Package

This package contains the core components for code analysis:
- models: Data models (CodeSymbol, ChangeUnit)
- git_manager: Git operations
- diff_parser: Diff parsing
- change_extractor: Main orchestrator
- parser: Language-specific parsers
- logging_config: Logging utilities
"""

from core.models import CodeSymbol, ChangeUnit
from core.git_manager import GitManager
from core.diff_parser import DiffParser, ParsedDiff, DiffHunk
from core.change_extractor import ChangeExtractor
from core.logging_config import setup_logging, get_logger

__all__ = [
    'CodeSymbol',
    'ChangeUnit',
    'GitManager',
    'DiffParser',
    'ParsedDiff',
    'DiffHunk',
    'ChangeExtractor',
    'setup_logging',
    'get_logger',
]

__version__ = '0.1.0'
