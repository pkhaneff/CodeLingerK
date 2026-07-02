from enum import Enum
from typing import List
from pydantic import BaseModel, Field, model_validator

class LanguageEnum(str, Enum):
    python = "python"
    javascript = "javascript"
    typescript = "typescript"
    go = "go"
    java = "java"

class SeverityEnum(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"

class CategoryEnum(str, Enum):
    security = "security"
    logic = "logic"
    performance = "performance"
    maintainability = "maintainability"
    bug_risk = "bug-risk"

class SampleMetadata(BaseModel):
    sample_id: str = Field(..., description="Unique identifier for the sample, e.g., sample_001")
    language: LanguageEnum = Field(..., description="Primary programming language")
    repo: str = Field(..., description="Repository name")
    base_commit: str = Field(..., description="Git commit SHA of the base branch")
    head_commit: str = Field(..., description="Git commit SHA of the head branch (with the bug/issue)")
    pr_size_lines: int = Field(..., description="Total number of lines added/modified/deleted in the PR", ge=0)
    created_at: str = Field(..., description="Date when this sample was created/annotated (YYYY-MM-DD)")

class GroundTruthFinding(BaseModel):
    id: str = Field(..., pattern=r"^BUG-\d{3,4}$", description="Unique finding ID, e.g., BUG-001")
    file: str = Field(..., description="Relative file path from repo root")
    line_start: int = Field(..., ge=1, description="Start line in the head commit file")
    line_end: int = Field(..., ge=1, description="End line in the head commit file")
    severity: SeverityEnum = Field(..., description="Impact severity")
    category: CategoryEnum = Field(..., description="Bug classification")
    message: str = Field(..., description="Clear explanation of the bug")
    evidence: List[str] = Field(..., description="Specific code snippets or tokens that serve as evidence of the bug")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Annotation confidence")

    @model_validator(mode="after")
    def validate_line_range(self) -> 'GroundTruthFinding':
        if self.line_start > self.line_end:
            raise ValueError("line_start must be less than or equal to line_end")
        return self

class AIFinding(BaseModel):
    file: str = Field(..., description="Relative file path")
    line_start: int = Field(..., ge=1, description="Start line of the finding")
    line_end: int = Field(..., ge=1, description="End line of the finding")
    category: CategoryEnum = Field(..., description="Bug classification")
    message: str = Field(..., description="Review comments")

    @model_validator(mode="after")
    def validate_line_range(self) -> 'AIFinding':
        if self.line_start > self.line_end:
            raise ValueError("line_start must be less than or equal to line_end")
        return self
