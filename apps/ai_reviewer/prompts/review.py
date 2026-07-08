from typing import Any


def get_understanding_prompt() -> str:
    """Return prompt template for understanding pass."""
    return """You are generating a PR walkthrough comment.

Your output must help a reviewer understand what changed in the PR.

Return JSON only.

Required JSON shape:
{
  "summary": "1-2 sentence overview of the PR changes",
  "changes": [
    {
      "files": ["path/to/file.py", "path/to/related_file.py"],
      "summary": "one sentence summary of the change"
    }
  ],
  "sequence_diagram": {
    "include": true,
    "title": "short description of the flow",
    "mermaid": "sequenceDiagram..."
  }
}

Rules:
- Do not include findings, severity, risk, review status, metadata, or push history.
- Do not describe the AI review process.
- The sequence diagram must describe the application flow changed by the PR.
- If there is no meaningful runtime or business flow change, set sequence_diagram.include=false.
- Group related files into one row.
- Use at most 7 change rows.
- Exclude IDE files, lockfiles, generated files, and low-signal config files.
- Do not use "General" as an area.
- Use neutral language: updates, replaces, adds, refactors, adjusts.
- Avoid strong verification language: fixed, eliminated, guaranteed, fully secure.
- Keep each change summary short."""


def get_risks_prompt() -> str:
    """Return prompt template for risks pass."""
    return """You are a senior engineer reviewing code for potential risks.
Based on the understanding from Pass 1, identify what could go wrong.

CRITICAL RULES:
- Each issue MUST include file_path and line from the provided diff.
- ONLY reference files and lines that LITERALLY appear in the diff.
- If you cannot determine the exact line, set line to null.
- If no issues are found, return empty arrays. Do NOT invent issues to fill the list.
- Output ONLY valid JSON. No markdown fences, no explanation, no preamble.

Respond with a JSON object:
{
  "breaking_changes": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "security_concerns": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "performance_issues": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "data_integrity": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "risk_level": "low|medium|high|critical"
}"""


def get_quality_prompt() -> str:
    """Return prompt template for quality pass."""
    return """You are a code quality expert reviewing for clean code principles.
Focus on simplification and maintainability.

CRITICAL RULES:
- Each issue MUST include file_path and line from the provided diff.
- ONLY reference files and lines that LITERALLY appear in the diff.
- If you cannot determine the exact line, set line to null.
- If no issues are found, return empty arrays. Do NOT invent issues to fill the list.
- Output ONLY valid JSON. No markdown fences, no explanation, no preamble.

Respond with a JSON object:
{
  "complexity_issues": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "duplication": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "naming_issues": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "design_smells": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "simplification_opportunities": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ]
}"""


def get_business_prompt() -> str:
    """Return prompt template for business pass."""
    return """You are reviewing code for business logic correctness.
Consider if the implementation matches the intent.

CRITICAL RULES:
- Each issue MUST include file_path and line from the provided diff.
- ONLY reference files and lines that LITERALLY appear in the diff.
- If you cannot determine the exact line, set line to null.
- If no issues are found, return empty arrays. Do NOT invent issues to fill the list.
- Output ONLY valid JSON. No markdown fences, no explanation, no preamble.

Respond with a JSON object:
{
  "intent_violations": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "edge_cases": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "validation_gaps": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ],
  "business_risks": [
    {"issue": "description", "file_path": "path/to/file.py", "line": 42}
  ]
}"""


def get_category_prompt() -> str:
    return """- category: Must be one of the following:
    * Security: Authorization, authentication, secret leaks, SQL/command injection, unsafe deserialization.
    * Functional Correctness: Logic bugs, incorrect behavior, incorrect business logic implementation.
    * Stability: Runtime crashes, race conditions, memory leaks, system crashes.
    * Performance: Slow code, N+1 queries, CPU/memory inefficiencies, resource/connection leaks.
    * Data Integrity: Incorrect data updates, race conditions affecting data state, validation gaps.
    * Error Handling: Missing exception handling, unhandled edge cases in errors, silent failures.
    * Maintainability: Duplicate code, readability, high cyclomatic complexity, poor naming, dead code.
    * Test Coverage: Missing important tests, incorrect assertions, missing edge cases in test files."""


def get_comments_prompt() -> str:
    """Return prompt template for comments pass.

    Enforces the CodeRabbit-style finding schema (Phase 2):
    - category: Phase 5 categories
    - severity: critical | major | minor | info
    - title: Short title of the issue
    - file_path: File path from diff
    - line: Line number where the issue occurs
    - problem: What is wrong
    - impact: Why it matters / what breaks
    - suggestion: Recommended fix description
    - code_suggestion: Optional raw code for github suggestion block
    - confidence: high | medium | low
    """
    prompt = """You are generating structured, evidence-backed code review comments.
Based on all previous analysis, generate specific inline findings.

MANDATORY OUTPUT STRUCTURE:
Each finding MUST follow the Finding Schema:
- category: One of the allowed categories.
- severity: One of: critical | major | minor | info.
- title: Short title of the finding (concise, 3-6 words).
- file_path: Relative path of the file from the diff.
- line: Integer line number where the issue occurs.
- problem: Description of what is wrong (exactly 1 short sentence).
- impact: Description of why it matters / consequences (exactly 1 short sentence).
- suggestion: Actionable recommended fix description (exactly 1 short sentence).
- code_suggestion: Optional raw code to be inserted into a GitHub suggestion block. Specify only the exact replacement code, NOT markdown code fences. Keep it as compact as possible.
- confidence: One of: high | medium | low.

CRITICAL GROUNDING AND COMPACTNESS RULES:
- Keep all fields short and concise. Do NOT write markdown in the text fields.
- Do NOT include long explanations or code blocks.
- ONLY reference file_path values that LITERALLY appear in "## Changed Files" section.
- The line number must be an integer within the range [NEW_START, NEW_START + count] for added/modified lines in the diff hunk headers: @@ -old,count +NEW_START,count @@
- If you cannot determine the exact line, OMIT line rather than guessing.
- If no issues warrant comments, return an empty array []. Do NOT fabricate issues.
- Output ONLY valid JSON array. No markdown fences, no explanation, no preamble.

Respond with a JSON array:
[
  {
    "category": "Functional Correctness",
    "severity": "major",
    "title": "Missing null guard",
    "file_path": "src/user.service.ts",
    "line": 42,
    "problem": "`user.profile.name` may throw when profile is null.",
    "impact": "This can cause a runtime exception.",
    "suggestion": "Use optional chaining or validate the profile first.",
    "code_suggestion": "const name = user.profile?.name ?? \\"Unknown\\";",
    "confidence": "high"
  }
]

RULES:
- Max 10 findings per chunk, Max 20 comments per file, Max 50 comments total.
- Find all concrete issues in the changed lines. Report every distinct issue you find. Do not stop after the most severe issues. Include minor issues if they affect correctness, maintainability, performance, or security. If multiple issues exist in the same file, report all of them. If multiple issues exist on the same line but represent different problems, report them separately. Ignore only purely stylistic suggestions.
- severity: critical | major | minor | info
- category: Security | Functional Correctness | Stability | Performance | Data Integrity | Error Handling | Maintainability | Test Coverage
- confidence: high | medium | low"""
    return prompt.replace(
        "- category: Security | Functional Correctness | Stability | Performance | Data Integrity | Error Handling | Maintainability | Test Coverage",
        get_category_prompt()
    )


def get_analysis_prompt() -> str:
    """Return prompt template for combined analysis pass (understanding, risks, quality, business)."""
    return """You are a senior engineer reviewing code changes.
Perform a comprehensive review and return analysis in a single JSON object.

Required JSON shape:
{
  "understanding": {
    "summary": "1-2 sentence overview of the PR changes",
    "changes": [
      {
        "files": ["path/to/file.py", "path/to/related_file.py"],
        "summary": "one sentence summary of the change"
      }
    ],
    "sequence_diagram": {
      "include": true,
      "title": "short description of the flow",
      "mermaid": "sequenceDiagram..."
    }
  },
  "risks": {
    "breaking_changes": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "security_concerns": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "performance_issues": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "data_integrity": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "risk_level": "low|medium|high|critical"
  },
  "quality": {
    "complexity_issues": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "duplication": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "naming_issues": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "design_smells": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "simplification_opportunities": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ]
  },
  "business": {
    "intent_violations": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "edge_cases": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "validation_gaps": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ],
    "business_risks": [
      {"issue": "description", "file_path": "path/to/file.py", "line": 42}
    ]
  }
}

CRITICAL RULES:
- For risks, quality, and business: Each issue MUST include file_path and line from the provided diff.
- ONLY reference files and lines that LITERALLY appear in the diff.
- If you cannot determine the exact line, set line to null.
- If no issues are found for a category, return empty array []. Do NOT invent issues.
- The sequence diagram must describe the application flow changed by the PR. Set sequence_diagram.include=false if there is no flow change.
- Keep summaries and issue descriptions short and concise (one sentence each).
- Output ONLY valid JSON. No markdown fences, no explanation, no preamble.
"""


def format_issue(issue: dict | str) -> str:
    """Format a single issue for display in prompt."""
    if isinstance(issue, dict):
        text = issue.get('issue', str(issue))
        file_path = issue.get('file_path')
        line = issue.get('line')
        if file_path and line:
            return f'- [{file_path}:{line}] {text}'
        elif file_path:
            return f'- [{file_path}] {text}'
        return f'- {text}'
    return f'- {issue}'


def format_issues_list(issues: list, max_items: int | None = None) -> str:
    """Format a list of issues for display in prompt."""
    if not issues:
        return '(none)'
    from infra.config import settings
    limit = max_items if max_items is not None else settings.review_intermediate_issue_limit
    return '\n'.join(format_issue(i) for i in issues[:limit])


def build_analysis_prompt(context: str) -> str:
    """Build prompt for combined analysis pass."""
    return f'''Analyze the following code changes and provide a detailed analysis.

{context}

Provide your analysis in the specified JSON format.'''


def build_understanding_prompt(context: str) -> str:
    """Build prompt for understanding pass."""
    return f'''Analyze the following code changes and provide your understanding.

{context}

Provide your analysis in the specified JSON format.'''


def build_risks_prompt(
    context: str,
    understanding: dict[str, Any],
) -> str:
    """Build prompt for risks pass."""
    summary = understanding.get('summary') or 'N/A'
    intent = understanding.get('intent') or summary
    complexity = understanding.get('complexity') or 'N/A'
    return f'''Based on the following code changes and understanding, identify risks.

## Previous Understanding
Summary: {summary}
Intent: {intent}
Complexity: {complexity}

## Code Changes
{context}

Provide your risk analysis in the specified JSON format.'''


def build_quality_prompt(
    context: str,
    understanding: dict[str, Any],
) -> str:
    """Build prompt for quality pass."""
    summary = understanding.get('summary') or 'N/A'
    scope = understanding.get('scope') or 'N/A'
    return f'''Review the following code changes for quality issues.

## Context
Summary: {summary}
Scope: {scope}

## Code Changes
{context}

Provide your quality analysis in the specified JSON format.'''


def build_business_prompt(
    context: str,
    understanding: dict[str, Any],
) -> str:
    """Build prompt for business pass."""
    summary = understanding.get('summary') or 'N/A'
    intent = understanding.get('intent') or summary
    key_changes = understanding.get('key_changes', [])
    if not key_changes and 'changes' in understanding:
        key_changes = [c.get('summary', '') for c in understanding.get('changes', []) if c.get('summary')]
    return f'''Review if the implementation matches the stated intent.

## Developer Intent
{intent}

## Key Changes
{', '.join(key_changes)}

## Code Changes
{context}

Provide your business logic analysis in the specified JSON format.'''


def build_comments_prompt(
    context: str,
    understanding: dict[str, Any],
    risks: dict[str, Any],
    quality: dict[str, Any],
    business: dict[str, Any],
) -> str:
    """Build prompt for comments pass."""
    return f'''Generate actionable code review comments based on the analysis.

## Analysis Summary
Risk Level: {risks.get('risk_level', 'unknown')}
Security Concerns: {len(risks.get('security_concerns', []))}
Quality Issues: {len(quality.get('complexity_issues', []))}
Business Risks: {len(business.get('business_risks', []))}

## Issues Found (with locations from previous analysis)

### Security Concerns
{format_issues_list(risks.get('security_concerns', []))}

### Breaking Changes
{format_issues_list(risks.get('breaking_changes', []))}

### Performance Issues
{format_issues_list(risks.get('performance_issues', []))}

### Quality Issues
{format_issues_list(quality.get('complexity_issues', []))}

### Design Smells
{format_issues_list(quality.get('design_smells', []))}

### Business Logic Issues
{format_issues_list(business.get('intent_violations', []))}

### Edge Cases
{format_issues_list(business.get('edge_cases', []))}

## Code Changes (ONLY reference files/lines from this section)
{context}

Generate specific, actionable comments for the issues above.
Use the file_path and line from the issues when available.
Focus on the most impactful issues. Be constructive and helpful.'''



