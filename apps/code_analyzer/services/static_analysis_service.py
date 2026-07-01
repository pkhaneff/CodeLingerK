"""
StaticAnalysisService - Run static analysis tools on snapshot files.

Runs linters (ruff, mypy) against files changed in a PR snapshot.
Produces grounded findings with real file paths and line numbers,
which are then injected into the LLM review context to reduce hallucination.

Tools used:
- ruff: Fast Python linter (replaces flake8/pyflakes/isort)
- mypy: Optional static type checker

Output is a list of Finding objects, each with:
  - file_path: Relative path in the repo
  - line: Actual line number from the tool
  - rule: Linter rule code (e.g. E501, F401)
  - message: Human-readable message
  - severity: 'error' | 'warning' | 'info'
  - tool: Which tool produced this finding
"""

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core.logging_config import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Data Types
# ─────────────────────────────────────────────

@dataclass
class StaticFinding:
    """
    A single static analysis finding grounded to a real file/line.

    Attributes:
        file_path: Relative path of the file (matching snapshot diff paths)
        line: Line number (1-indexed), or None if not applicable
        col: Column number (1-indexed), or None
        rule: Linter rule code (e.g. 'E501', 'F401', 'RUF100')
        message: Human-readable description of the issue
        severity: 'error' | 'warning' | 'info'
        tool: Tool that generated this finding ('ruff' | 'mypy')
    """
    file_path: str
    line: int | None
    col: int | None
    rule: str
    message: str
    severity: str  # 'error' | 'warning' | 'info'
    tool: str


@dataclass
class StaticAnalysisResult:
    """
    Aggregated result from all static analysis tools.

    Attributes:
        findings: All findings from all tools
        tools_run: Names of tools that were successfully run
        tools_unavailable: Names of tools not found in PATH
        errors: Any tool execution errors
    """
    findings: list[StaticFinding] = field(default_factory=list)
    tools_run: list[str] = field(default_factory=list)
    tools_unavailable: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'error')

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'warning')

    def to_prompt_section(self) -> str:
        """
        Format findings as a markdown section for injection into LLM prompts.

        Returns:
            Formatted string ready to embed in a prompt context block.
        """
        if not self.findings:
            return '## Static Analysis\nNo static analysis issues found.\n'

        parts = ['## Static Analysis (Grounded Findings)']
        parts.append(
            f'Tools run: {", ".join(self.tools_run) or "none"} | '
            f'Errors: {self.error_count} | Warnings: {self.warning_count}'
        )
        parts.append('')

        # Group by file
        by_file: dict[str, list[StaticFinding]] = {}
        for f in self.findings:
            by_file.setdefault(f.file_path, []).append(f)

        for file_path, file_findings in sorted(by_file.items()):
            parts.append(f'### {file_path}')
            for finding in file_findings:
                loc = f':{finding.line}' if finding.line else ''
                parts.append(
                    f'- [{finding.severity.upper()}] {finding.rule} '
                    f'(line{loc}): {finding.message}'
                )
            parts.append('')

        return '\n'.join(parts)


# ─────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────

class StaticAnalysisService:
    """
    Run static analysis tools against changed files in a snapshot.

    Usage:
        service = StaticAnalysisService(repo_dir='/path/to/cloned/repo')
        result = await service.analyze(changed_files=['src/foo.py', 'src/bar.py'])
    """

    def __init__(
        self,
        repo_dir: str,
        timeout_seconds: int = 30,
    ):
        """
        Initialize static analysis service.

        Args:
            repo_dir: Absolute path to the cloned repository directory.
            timeout_seconds: Max time to wait for any single tool.
        """
        self.repo_dir = Path(repo_dir)
        self.timeout_seconds = timeout_seconds

    async def analyze(
        self,
        changed_files: list[str],
    ) -> StaticAnalysisResult:
        """
        Run all available static analysis tools against changed files.

        Only analyzes Python files for now. Other file types are silently skipped.

        Args:
            changed_files: List of relative file paths from the diff.

        Returns:
            StaticAnalysisResult with all findings.
        """
        result = StaticAnalysisResult()

        # Filter to Python files only (for now)
        python_files = [f for f in changed_files if f.endswith('.py')]

        if not python_files:
            logger.info('No Python files to analyze statically')
            return result

        logger.info(
            f'Running static analysis on {len(python_files)} Python files '
            f'in {self.repo_dir}'
        )

        # Run tools concurrently
        tasks = []

        if shutil.which('ruff'):
            tasks.append(self._run_ruff(python_files))
        else:
            result.tools_unavailable.append('ruff')
            logger.warning('ruff not found in PATH, skipping')

        if shutil.which('mypy'):
            tasks.append(self._run_mypy(python_files))
        else:
            result.tools_unavailable.append('mypy')
            logger.debug('mypy not found in PATH, skipping')

        if not tasks:
            logger.warning('No static analysis tools available')
            return result

        tool_results = await asyncio.gather(*tasks, return_exceptions=True)

        for tool_result in tool_results:
            if isinstance(tool_result, Exception):
                result.errors.append(str(tool_result))
                logger.error(f'Static analysis tool error: {tool_result}')
            elif isinstance(tool_result, tuple):
                tool_name, findings = tool_result
                result.tools_run.append(tool_name)
                result.findings.extend(findings)

        logger.info(
            f'Static analysis complete: {len(result.findings)} findings '
            f'from {result.tools_run}'
        )

        return result

    async def _run_ruff(
        self,
        python_files: list[str],
    ) -> tuple[str, list[StaticFinding]]:
        """
        Run ruff linter and parse JSON output.

        Args:
            python_files: List of relative Python file paths.

        Returns:
            Tuple of ('ruff', list of StaticFinding)
        """
        # Build absolute paths
        abs_paths = [
            str(self.repo_dir / f)
            for f in python_files
            if (self.repo_dir / f).exists()
        ]

        if not abs_paths:
            return ('ruff', [])

        cmd = ['ruff', 'check', '--output-format=json', '--no-fix'] + abs_paths

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.repo_dir),
                ),
                timeout=self.timeout_seconds,
            )
            stdout, stderr = await proc.communicate()
        except asyncio.TimeoutError:
            logger.warning(f'ruff timed out after {self.timeout_seconds}s')
            return ('ruff', [])
        except Exception as e:
            logger.error(f'Failed to run ruff: {e}')
            return ('ruff', [])

        findings = []

        if stdout:
            try:
                ruff_output = json.loads(stdout.decode())
                findings = self._parse_ruff_output(ruff_output)
            except json.JSONDecodeError as e:
                logger.warning(f'Failed to parse ruff JSON output: {e}')

        return ('ruff', findings)

    def _parse_ruff_output(
        self,
        ruff_output: list[dict],
    ) -> list[StaticFinding]:
        """
        Parse ruff JSON output into StaticFinding objects.

        Ruff JSON format:
        [
          {
            "filename": "/abs/path/to/file.py",
            "location": {"row": 42, "column": 10},
            "code": "E501",
            "message": "Line too long (100 > 88 characters)"
          }
        ]
        """
        findings = []

        for item in ruff_output:
            filename = item.get('filename', '')
            # Convert absolute path back to relative path
            try:
                rel_path = str(Path(filename).relative_to(self.repo_dir))
            except ValueError:
                rel_path = filename

            location = item.get('location', {})
            code = item.get('code', 'UNKNOWN')

            # Map ruff severity by rule prefix
            severity = self._ruff_severity(code)

            finding = StaticFinding(
                file_path=rel_path,
                line=location.get('row'),
                col=location.get('column'),
                rule=code,
                message=item.get('message', ''),
                severity=severity,
                tool='ruff',
            )
            findings.append(finding)

        return findings

    def _ruff_severity(self, code: str) -> str:
        """Map ruff rule code to severity level."""
        # E (pycodestyle errors) and F (pyflakes) are errors
        # W (warnings) are warnings
        # Everything else is info
        if code.startswith(('E', 'F')):
            return 'error'
        elif code.startswith('W'):
            return 'warning'
        else:
            return 'info'

    async def _run_mypy(
        self,
        python_files: list[str],
    ) -> tuple[str, list[StaticFinding]]:
        """
        Run mypy type checker and parse output.

        Args:
            python_files: List of relative Python file paths.

        Returns:
            Tuple of ('mypy', list of StaticFinding)
        """
        abs_paths = [
            str(self.repo_dir / f)
            for f in python_files
            if (self.repo_dir / f).exists()
        ]

        if not abs_paths:
            return ('mypy', [])

        cmd = [
            'mypy',
            '--no-error-summary',
            '--show-error-codes',
            '--no-color-output',
        ] + abs_paths

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.repo_dir),
                ),
                timeout=self.timeout_seconds,
            )
            stdout, _ = await proc.communicate()
        except asyncio.TimeoutError:
            logger.warning(f'mypy timed out after {self.timeout_seconds}s')
            return ('mypy', [])
        except Exception as e:
            logger.error(f'Failed to run mypy: {e}')
            return ('mypy', [])

        findings = []
        if stdout:
            findings = self._parse_mypy_output(stdout.decode())

        return ('mypy', findings)

    def _parse_mypy_output(self, output: str) -> list[StaticFinding]:
        """
        Parse mypy text output into StaticFinding objects.

        Mypy format:
            path/to/file.py:42: error: Incompatible return value  [return-value]
        """
        findings = []

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split(':', 3)
            if len(parts) < 3:
                continue

            try:
                file_path = parts[0]
                line_no = int(parts[1]) if parts[1].strip().isdigit() else None
                rest = parts[2].strip() if len(parts) > 2 else ''

                # rest looks like: "error: message  [code]"
                if rest.startswith('error:'):
                    severity = 'error'
                    message_part = rest[len('error:'):].strip()
                elif rest.startswith('warning:'):
                    severity = 'warning'
                    message_part = rest[len('warning:'):].strip()
                elif rest.startswith('note:'):
                    continue  # Skip mypy notes
                else:
                    severity = 'info'
                    message_part = rest

                # Extract rule code from end: [return-value]
                rule = 'mypy'
                if message_part.endswith(']') and '[' in message_part:
                    rule_start = message_part.rfind('[')
                    rule = message_part[rule_start + 1:-1]
                    message_part = message_part[:rule_start].strip()

                # Convert to relative path
                try:
                    rel_path = str(Path(file_path).relative_to(self.repo_dir))
                except ValueError:
                    rel_path = file_path

                findings.append(StaticFinding(
                    file_path=rel_path,
                    line=line_no,
                    col=None,
                    rule=f'mypy:{rule}',
                    message=message_part,
                    severity=severity,
                    tool='mypy',
                ))

            except (ValueError, IndexError):
                continue

        return findings
