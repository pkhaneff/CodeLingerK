"""
Base parser abstraction for language-specific parsers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSymbol:
    """Represents a parsed code symbol (function, class, method)."""

    name: str
    type: str  # 'function', 'class', 'method'
    file_path: str
    line_start: int
    line_end: int
    content: str
    content_hash: str
    signature: str = ''
    docstring: str | None = None
    parent_class: str | None = None  # For methods
    decorators: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedImport:
    """Represents an import statement."""

    module: str
    name: str | None  # For 'from X import Y', Y is the name
    alias: str | None
    is_relative: bool
    line_number: int


@dataclass
class ParsedCall:
    """Represents a function/method call."""

    caller: str  # Function/method name that makes the call
    callee: str  # Function/method being called
    line_number: int
    is_method_call: bool = False
    receiver: str | None = None  # For method calls: obj.method()


@dataclass
class ParsedInheritance:
    """Represents class inheritance."""

    child_class: str
    parent_class: str
    line_number: int


@dataclass
class ParsedFile:
    """Complete parsed representation of a source file."""

    file_path: str
    language: str
    content_hash: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    calls: list[ParsedCall] = field(default_factory=list)
    inheritances: list[ParsedInheritance] = field(default_factory=list)


class BaseParser(ABC):
    """Abstract base class for language-specific parsers."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Return the language this parser handles."""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """Return list of file extensions this parser handles."""
        pass

    @abstractmethod
    def parse(self, file_path: str, content: str) -> ParsedFile:
        """
        Parse a source file and extract all code structures.

        Args:
            file_path: Path to the file (for context)
            content: File content as string

        Returns:
            ParsedFile with all extracted information
        """
        pass

    @abstractmethod
    def can_parse(self, file_path: str) -> bool:
        """Check if this parser can handle the given file."""
        pass
