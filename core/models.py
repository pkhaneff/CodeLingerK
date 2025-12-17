from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class CodeSymbol(BaseModel):
    """Represents a parsed code entity (function, class, etc.)"""
    name: str
    type: str  # "Function", "Class", "Method", etc.
    line_start: int
    line_end: int
    column_start: int
    content: str
    body_hash: str
    metadata: Dict[str, Any] = {}

class ChangeUnit(BaseModel):
    """Represents a single code change with context"""
    file_path: str
    change_type: str  # "added", "modified", "deleted", "renamed"
    old_symbol: Optional[CodeSymbol] = None
    new_symbol: Optional[CodeSymbol] = None
    diff_hunk: str
    context: Dict[str, Any] = {}  # Additional context (imports, dependencies, etc.) 