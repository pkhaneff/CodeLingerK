"""
GitHubSyncService - Sync reviews to Git providers.

Posts AI-generated review summary and inline comments as bot review.
Tracks sync status and handles errors gracefully.
"""

from datetime import datetime
import re
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.logger import get_logger
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.review import Review, ReviewComment
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review_run import ReviewRun
from apps.ai_reviewer.models.review_surface import ReviewSurface
from apps.ai_reviewer.models.finding import Finding
from apps.ai_reviewer.models.file_review_history import FileReviewHistory
from apps.auth.models.user import User
from infra.config import settings
from apps.repositories.services.providers.base import GitProvider, ReviewComment as ProviderReviewComment
from apps.repositories.services.providers.factory import GitProviderFactory, GitProviderType

logger = get_logger(__name__)


class GitHubSyncService:
    """
    Service for syncing reviews to Git providers.

    Responsibilities:
    - Post review summary as bot review comment
    - Post inline comments via review API
    - Track provider_comment_id for each comment
    - Handle sync errors gracefully
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize sync service.

        Args:
            db: Database session
        """
        self.db = db

    async def sync_review(
        self,
        review: Review,
        snapshot: Snapshot,
    ) -> dict[str, Any]:
        """
        Sync review to GitHub.

        Args:
            review: Review to sync
            snapshot: Snapshot with PR information

        Returns:
            Sync result with posted comment IDs
        """
        logger.info(f'Syncing review {review.id[:8]} to GitHub')

        # Get PR and repository info
        pull_request = await self._get_pull_request(snapshot.pull_request_id)
        if not pull_request:
            raise ValueError(f'PullRequest not found: {snapshot.pull_request_id}')

        repository = await self._get_repository(pull_request.repository_id)
        if not repository:
            raise ValueError(f'Repository not found: {pull_request.repository_id}')

        # Determine review mode
        is_incremental = len(pull_request.snapshots) > 1
        mode = 'incremental' if is_incremental else 'full'

        base_sha = "base"
        if is_incremental and len(pull_request.snapshots) > 1:
            base_sha = pull_request.snapshots[1].commit_sha

        # Create a ReviewRun record to track this execution
        review_run = ReviewRun(
            repo=repository.full_name,
            pr_number=pull_request.pr_number,
            mode=mode,
            base_sha=base_sha,
            head_sha=snapshot.commit_sha,
            status='pending',
            reviewed_files_count=len(snapshot.files_changed)
        )
        self.db.add(review_run)
        await self.db.flush()

        # Create provider instance
        provider_type = GitProviderType(repository.provider)

        if provider_type == GitProviderType.GITHUB and settings.github_app_enabled:
            git_provider = GitProviderFactory.create_github_app()
            logger.info('Using GitHub App provider for bot comments')
        else:
            owner = await self._get_repository_owner(repository.id)
            if not owner:
                raise ValueError('Repository owner not found')

            access_token = owner.get_access_token(repository.provider)
            if not access_token:
                raise ValueError(f'No access token for {repository.provider}')

            git_provider = GitProviderFactory.create(provider_type, access_token)

        # Get all comments generated for this review
        stmt = select(ReviewComment).where(ReviewComment.review_id == review.id)
        comments = list((await self.db.execute(stmt)).scalars().all())

        # Load previous findings for deduplication
        stmt_findings = select(Finding).where(
            Finding.repo == repository.full_name,
            Finding.pr_number == pull_request.pr_number
        )
        previous_findings = list((await self.db.execute(stmt_findings)).scalars().all())
        previous_by_sig = {f.signature: f for f in previous_findings}

        # Track lifecycle stats
        new_count = 0
        duplicate_count = 0
        resolved_count = 0
        existing_count = 0

        # Calculate signatures and map states
        current_run_signatures = set()
        comments_to_post = []
        severity_counts = {'critical': 0, 'major': 0, 'minor': 0, 'info': 0}

        for comment in comments:
            details = self._parse_finding_details(comment.comment, comment.category, comment.suggestion)
            sig = self._calculate_signature(comment.file_path, comment.category, details['title'], details['evidence'])
            current_run_signatures.add(sig)

            sev = comment.severity.lower()
            if sev == 'error':
                sev = 'critical'
            elif sev == 'warning':
                sev = 'major'
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            if sig in previous_by_sig:
                prev_finding = previous_by_sig[sig]
                prev_finding.last_seen_sha = snapshot.commit_sha
                
                if prev_finding.github_comment_id:
                    prev_finding.status = 'existing'
                    existing_count += 1
                    duplicate_count += 1
                else:
                    prev_finding.status = 'new'
                    new_count += 1
                    comments_to_post.append((comment, prev_finding))
            else:
                new_finding = Finding(
                    repo=repository.full_name,
                    pr_number=pull_request.pr_number,
                    signature=sig,
                    file_path=comment.file_path,
                    line=comment.line_start,
                    category=comment.category,
                    severity=comment.severity,
                    title=details['title'],
                    evidence=details['evidence'],
                    impact=details['impact'],
                    fix=details['fix'],
                    first_seen_sha=snapshot.commit_sha,
                    last_seen_sha=snapshot.commit_sha,
                    status='new'
                )
                self.db.add(new_finding)
                new_count += 1
                comments_to_post.append((comment, new_finding))

        # Check for resolved findings on reviewed files
        reviewed_files = set(snapshot.files_changed)
        for comment in comments:
            reviewed_files.add(comment.file_path)

        for sig, prev_finding in previous_by_sig.items():
            if sig not in current_run_signatures:
                if prev_finding.file_path in reviewed_files:
                    if prev_finding.status != 'resolved':
                        prev_finding.status = 'resolved'
                        resolved_count += 1

        # Populate file results labels and history
        file_results = {}
        comments_by_file = {}
        for comment in comments:
            comments_by_file.setdefault(comment.file_path, []).append(comment)

        # Load context to see if skipped or lockfile
        from apps.ai_reviewer.services.context_service import ContextService
        context_svc = ContextService(self.db)
        context = await context_svc.build_context(snapshot)
        file_contexts = {f.file_path: f for f in context.files}

        # Memory service ignored patterns
        from apps.ai_reviewer.services.memory_service import MemoryService
        import fnmatch
        memory = MemoryService(self.db, repository_id=snapshot.pull_request.repository_id)
        memory_context = await memory.load()
        ignored_patterns = memory_context.ignored_patterns or []

        all_skipped = True
        for path in snapshot.files_changed:
            file_context = file_contexts.get(path)
            
            is_ignored = False
            for pattern_obj in ignored_patterns:
                if fnmatch.fnmatch(path, pattern_obj.pattern):
                    is_ignored = True
                    break

            filename = path.split('/')[-1]
            lockfiles = {
                'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
                'Cargo.lock', 'poetry.lock', 'mix.lock',
                'composer.lock', 'gemfile.lock', 'go.sum'
            }
            generated_patterns = ['dist/', 'build/', 'generated/', 'node_modules/']
            is_generated = any(pattern in path for pattern in generated_patterns) or \
                           filename.endswith('.gen.py') or filename.endswith('.g.cs')
            
            binary_extensions = {
                '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip',
                '.gz', '.tar', '.class', '.jar', '.exe', '.dll', '.so', '.dylib', '.woff', '.woff2', '.ttf', '.eot'
            }
            ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''

            is_lockfile = filename.lower() in lockfiles
            is_binary = ext in binary_extensions
            is_too_large = file_context and (file_context.additions + file_context.deletions > 2000)
            is_no_changes = file_context and (file_context.additions + file_context.deletions == 0)

            if not (is_ignored or is_lockfile or is_generated or is_binary or is_too_large or is_no_changes):
                all_skipped = False

            label, skipped_reason = self._get_file_review_label(
                path, comments_by_file, is_incremental, is_ignored, is_lockfile, is_generated, is_binary, is_too_large, is_no_changes
            )
            file_results[path] = label

            # Update or create FileReviewHistory
            stmt_hist = select(FileReviewHistory).where(
                FileReviewHistory.repo == repository.full_name,
                FileReviewHistory.pr_number == pull_request.pr_number,
                FileReviewHistory.file_path == path
            )
            hist = (await self.db.execute(stmt_hist)).scalar_one_or_none()

            file_findings_count = len(comments_by_file.get(path, []))
            if hist:
                hist.last_seen_sha = snapshot.commit_sha
                hist.review_status = label
                hist.findings_count = file_findings_count
                hist.skipped_reason = skipped_reason
            else:
                hist = FileReviewHistory(
                    repo=repository.full_name,
                    pr_number=pull_request.pr_number,
                    file_path=path,
                    first_seen_sha=snapshot.commit_sha,
                    last_seen_sha=snapshot.commit_sha,
                    review_status=label,
                    findings_count=file_findings_count,
                    skipped_reason=skipped_reason
                )
                self.db.add(hist)

        # Sync review to provider
        try:
            # ─────────────────────────────────────────────────────────────
            # 1. Update Surface 1: PR Description
            # ─────────────────────────────────────────────────────────────
            pr_info = await git_provider.get_pr(repository.full_name, pull_request.pr_number)
            original_body = pr_info.get('body') or ""

            # Check verdict mapping
            verdict_str = "Reviewed"
            if review.verdict == 'changes_requested':
                verdict_str = "Changes requested"
            elif review.verdict == 'needs_discussion':
                verdict_str = "Needs discussion"

            risk_level = "Low"
            if any(c.severity.lower() in ('critical', 'error') for c in comments):
                risk_level = "High"
            elif any(c.severity.lower() in ('major', 'warning') for c in comments):
                risk_level = "Medium"

            ai_passes = review.ai_passes or {}
            understanding = {}
            for k, v in ai_passes.items():
                if 'understanding' in k and isinstance(v, dict):
                    understanding = v
                    break

            summary_sentence = (understanding.get('summary', '') or review.summary or "Reviewed the latest changes.").strip()
            if not summary_sentence.endswith('.'):
                summary_sentence += '.'

            # (AI PR Summary generation and PR body update have been completely removed)

            # ─────────────────────────────────────────────────────────────
            # 2. Update Surface 2: Walkthrough Comment
            # ─────────────────────────────────────────────────────────────
            WALKTHROUGH_MARKER = "<!-- ai-review-walkthrough -->"
            
            # Fetch all completed runs and file history to build walkthrough details
            stmt_runs = select(ReviewRun).where(
                ReviewRun.repo == repository.full_name,
                ReviewRun.pr_number == pull_request.pr_number,
                ReviewRun.status == 'completed'
            ).order_by(ReviewRun.created_at.asc())
            completed_runs = list((await self.db.execute(stmt_runs)).scalars().all())
            # Add current run to temporary list for formatting
            completed_runs.append(review_run)

            stmt_all_hist = select(FileReviewHistory).where(
                FileReviewHistory.repo == repository.full_name,
                FileReviewHistory.pr_number == pull_request.pr_number
            )
            all_file_hist = list((await self.db.execute(stmt_all_hist)).scalars().all())

            walkthrough_body = await self._build_walkthrough_body(
                review=review,
                snapshot=snapshot,
                new_count=new_count,
                duplicate_count=duplicate_count,
                resolved_count=resolved_count,
                existing_count=existing_count,
                severity_counts=severity_counts,
                file_results=file_results,
                comments_to_post=comments_to_post,
                base_sha=base_sha,
                completed_runs=completed_runs,
                all_file_hist=all_file_hist,
            )

            existing_comments = await git_provider.list_pr_comments(
                repo_identifier=repository.full_name,
                pr_number=pull_request.pr_number,
            )
            
            existing_walkthrough_id = None
            for c in existing_comments:
                if WALKTHROUGH_MARKER in (c.get('body') or ''):
                    existing_walkthrough_id = c.get('id')
                    break

            full_walkthrough_body = f"{WALKTHROUGH_MARKER}\n\n{walkthrough_body.strip()}"

            if existing_walkthrough_id is not None:
                logger.info(f"Updating existing walkthrough comment: {existing_walkthrough_id}")
                await git_provider.update_pr_comment(
                    repo_identifier=repository.full_name,
                    comment_id=existing_walkthrough_id,
                    body=full_walkthrough_body,
                )
            else:
                logger.info("Creating new walkthrough comment")
                new_comment_res = await git_provider.create_pr_comment(
                    repo_identifier=repository.full_name,
                    pr_number=pull_request.pr_number,
                    body=full_walkthrough_body,
                )
                
                # Save surface to DB
                surface = ReviewSurface(
                    repo=repository.full_name,
                    pr_number=pull_request.pr_number,
                    surface_type='walkthrough_comment',
                    marker=WALKTHROUGH_MARKER,
                    github_id=new_comment_res.get('id'),
                    last_updated_sha=snapshot.commit_sha
                )
                self.db.add(surface)

            # ─────────────────────────────────────────────────────────────
            # 3. Update Surface 3: Pull Review and Inline Comments
            # ─────────────────────────────────────────────────────────────
            result = {}
            provider_comments = self._convert_comments(comments_to_post)

            if provider_comments:
                result = await git_provider.post_pr_review(
                    repo_identifier=repository.full_name,
                    pr_number=pull_request.pr_number,
                    commit_sha=snapshot.commit_sha,
                    body="",
                    comments=provider_comments,
                )

                if 'id' in result:
                    review.github_review_id = result['id']

                # Map GitHub inline comment IDs back to findings
                if 'comments' in result and isinstance(result['comments'], list):
                    for idx, gh_c in enumerate(result['comments']):
                        gh_comment_id = gh_c.get('id')
                        if idx < len(comments_to_post):
                            comment_obj, finding_obj = comments_to_post[idx]
                            finding_obj.github_comment_id = gh_comment_id
                            comment_obj.provider_comment_id = gh_comment_id

            # Mark all comments in this review run as synced
            for comment in comments:
                comment.is_synced = True
                comment.sync_error = None

            # Mark review run as completed
            review_run.status = 'completed'
            review_run.new_findings_count = new_count
            review_run.duplicate_findings_count = duplicate_count
            review_run.resolved_findings_count = resolved_count
            review_run.posted_comments_count = len(provider_comments)
            review_run.completed_at = datetime.utcnow()

            await self.db.flush()

            logger.info(
                f'Synced review to provider: review_id={result.get("id") if result else None}, '
                f'posted_comments={len(provider_comments)}'
            )

            return {
                'status': 'synced',
                'github_review_id': result.get('id') if result else None,
                'comments_synced': len(comments_to_post),
            }

        except Exception as e:
            logger.error(f'Failed to sync review to provider: {e}', exc_info=True)

            # Update ReviewRun status to failed
            review_run.status = 'failed'
            await self.db.flush()

            for comment in comments:
                comment.sync_error = str(e)
            await self.db.flush()

            return {
                'status': 'error',
                'error': str(e),
                'comments_synced': 0,
            }

    def _calculate_signature(self, file_path: str, category: str, title: str, evidence: str) -> str:
        """Calculate unique signature for a finding."""
        norm_file = file_path.strip().lower()
        norm_cat = (category or "").strip().lower()
        norm_title = (title or "").strip().lower()
        norm_ev = (evidence or "").strip().lower()
        norm_ev = "".join(norm_ev.split())  # strip all whitespace to normalize formatting
        raw_sig = f"{norm_file}:{norm_cat}:{norm_title}:{norm_ev}"
        return hashlib.sha256(raw_sig.encode('utf-8')).hexdigest()

    def _parse_finding_details(self, comment_text: str, category: str, suggestion: str) -> dict:
        """Parse structured categories from comment text (legacy or new format)."""
        title = None
        evidence = None
        impact = None
        fix = suggestion or ""

        # Match new format
        match_title = re.search(r'\*\*\[(.*?)\]\s*(?:.[🔴🟠🟡🔵]?\s*)?(.*?)\*\*', comment_text)
        if match_title:
            title = match_title.group(2).strip()
            parts = comment_text.split("**Why this matters:**")
            if len(parts) > 1:
                evidence_part = parts[0].split(match_title.group(0))[-1].strip()
                evidence = evidence_part
                
                rest = parts[1]
                fix_parts = rest.split("**Suggested fix:**")
                impact = fix_parts[0].strip()
                if len(fix_parts) > 1:
                    fix = fix_parts[1].strip()
            else:
                evidence = comment_text.split(match_title.group(0))[-1].strip()
        else:
            # Match legacy format
            sections = {}
            patterns = {
                'issue': r'\[Issue\]\s*(.*?)(?=\[Evidence\]|\[Impact\]|\[Suggestion\]|$)',
                'evidence': r'\[Evidence\]\s*(.*?)(?=\[Issue\]|\[Impact\]|\[Suggestion\]|$)',
                'impact': r'\[Impact\]\s*(.*?)(?=\[Issue\]|\[Evidence\]|\[Suggestion\]|$)',
                'suggestion': r'\[Suggestion\]\s*(.*?)(?=\[Issue\]|\[Evidence\]|\[Impact\]|$)'
            }
            for key, pattern in patterns.items():
                match = re.search(pattern, comment_text, re.DOTALL | re.IGNORECASE)
                if match:
                    sections[key] = match.group(1).strip()
            
            if sections:
                title = sections.get('issue')
                evidence = sections.get('evidence')
                impact = sections.get('impact')
                fix = sections.get('suggestion') or suggestion or ""

        if fix:
            fix_clean = fix.strip()
            match_sug = re.search(r'```suggestion\n(.*?)\n```', fix_clean, re.DOTALL)
            if match_sug:
                fix_clean = match_sug.group(1)
            fix = fix_clean

        if not title:
            lines = [l.strip() for l in comment_text.split('\n') if l.strip()]
            title = lines[0] if lines else f"Finding in {category or 'General'}"
            if len(title) > 250:
                title = title[:247] + "..."
        if not evidence:
            evidence = comment_text

        return {
            'title': title,
            'evidence': evidence,
            'impact': impact or "No detailed impact provided.",
            'fix': fix
        }

    def _format_inline_comment(self, category: str, severity: str, title: str, evidence: str, impact: str, fix: str) -> str:
        """Format inline comment to match Surface 3 template."""
        sev_icons = {
            'critical': '🔴',
            'error': '🔴',
            'warning': '🟠',
            'major': '🟠',
            'minor': '🟡',
            'info': '🔵',
        }
        icon = sev_icons.get(severity.lower(), '🔵')
        
        is_code = '\n' in fix or any(sym in fix for sym in [';', '{', '}', 'def ', 'const ', 'let ', 'class ', 'import ', 'var '])
        if is_code and "```" not in fix:
            fix_md = f"```\n{fix}\n```"
        else:
            fix_md = fix

        body_parts = [
            f"**[{category}] {icon} {title}**",
            "",
            evidence,
            "",
            "**Why this matters:**  ",
            impact,
            "",
            "**Suggested fix:**  ",
            fix_md
        ]
        return "\n".join(body_parts)

    def _get_file_review_label(self, path: str, comments_by_file: dict, is_incremental: bool, is_ignored: bool, is_lockfile: bool, is_generated: bool, is_binary: bool, is_too_large: bool, is_no_changes: bool) -> tuple[str, str | None]:
        """Determine review result label for a file."""
        if is_ignored:
            return "skipped: ignored by config", "ignored by config"
        elif is_lockfile:
            return "skipped: lockfile", "lockfile"
        elif is_generated:
            return "skipped: generated file", "generated file"
        elif is_binary:
            return "skipped: binary file", "binary file"
        elif is_too_large:
            return "skipped: file too large", "file too large"
        elif is_no_changes:
            return "no reviewable changes", "no reviewable changes"
        
        file_comments = comments_by_file.get(path, [])
        if file_comments:
            counts = {}
            for c in file_comments:
                sev = c.severity.lower()
                if sev == 'error':
                    sev = 'critical'
                elif sev == 'warning':
                    sev = 'major'
                counts[sev] = counts.get(sev, 0) + 1
            
            parts = []
            for s in ['critical', 'major', 'minor', 'info']:
                if counts.get(s):
                    parts.append(f"{counts[s]} {s}")
            
            findings_label = "new finding" if is_incremental else "finding"
            total_file_comments = sum(counts.values())
            if total_file_comments > 1:
                findings_label = "new findings" if is_incremental else "findings"
            
            return ", ".join(parts) + f" {findings_label}", None
        else:
            return "no new issues found" if is_incremental else "no issues found", None

    def _get_findings_sentence(self, new_count: int, severity_counts: dict, is_incremental: bool) -> str:
        """Construct findings summary sentence."""
        def number_to_word(n: int) -> str:
            words = {
                0: 'zero', 1: 'one', 2: 'two', 3: 'three', 4: 'four',
                5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine'
            }
            return words.get(n, str(n))

        if new_count == 0:
            if is_incremental:
                return "No new issues were found in the latest push. Previously reported findings were not repeated."
            else:
                return "No blocking issues were found in the reviewed changes."
                
        crit = severity_counts.get('critical', 0)
        maj = severity_counts.get('major', 0)
        min_c = severity_counts.get('minor', 0)
        inf = severity_counts.get('info', 0)
        
        parts = []
        if crit > 0:
            parts.append(f"{number_to_word(crit)} critical issue{'s' if crit > 1 else ''}")
        if maj > 0:
            parts.append(f"{number_to_word(maj)} major correctness issue{'s' if maj > 1 else ''}")
        if min_c > 0:
            parts.append(f"{number_to_word(min_c)} minor issue{'s' if min_c > 1 else ''}")
        if inf > 0:
            parts.append(f"{number_to_word(inf)} info issue{'s' if inf > 1 else ''}")
            
        if len(parts) > 1:
            return f"I found {', '.join(parts[:-1])} and {parts[-1]} that should be addressed before merging."
        elif parts:
            return f"I found {parts[0]} that should be addressed before merging."
        return "No blocking issues were found in the reviewed changes."

    def _upsert_pr_body(self, original_body: str | None, summary_block: str) -> str:
        """Upsert AI PR Summary block into PR description body."""
        original_body = original_body or ""
        START = "<!-- ai-pr-summary:start -->"
        END = "<!-- ai-pr-summary:end -->"
        
        ai_block = f"{START}\n\n{summary_block.strip()}\n\n{END}"
        
        if START in original_body and END in original_body:
            before = original_body.split(START)[0].rstrip()
            after = original_body.split(END)[1].lstrip()
            return f"{before}\n\n{ai_block}\n\n{after}".strip()
            
        return f"{original_body.rstrip()}\n\n{ai_block}".strip()

    async def _build_walkthrough_body(
        self,
        review: Review,
        snapshot: Snapshot,
        new_count: int,
        duplicate_count: int,
        resolved_count: int,
        existing_count: int,
        severity_counts: dict,
        file_results: dict,
        comments_to_post: list,
        base_sha: str,
        completed_runs: list,
        all_file_hist: list,
    ) -> str:
        """Build walkthrough comment body."""
        
        # 1. Neutral verbs rewriter
        def rewrite_with_neutral_verbs(text: str) -> str:
            import re
            word_map = {
                'fixed': 'updates',
                'Fixed': 'Updates',
                'mitigated': 'replaces/adds safeguards for',
                'Mitigated': 'Replaces/Adds safeguards for',
                'eliminated': 'replaces/removes',
                'Eliminated': 'Replaces/Removes',
                'corrected': 'updates',
                'Corrected': 'Updates',
                'sanitized': 'adds sanitization around',
                'Sanitized': 'Adds sanitization around',
                'safe': 'safer',
                'Safe': 'Safer',
                'secure': 'restricted',
                'Secure': 'Restricted',
                'guaranteed': 'validated',
                'Guaranteed': 'Validated',
            }
            
            def replace_word(match):
                word = match.group(0)
                return word_map.get(word, word_map.get(word.lower(), word))
            
            pattern = re.compile(
                r'\b(' + '|'.join(re.escape(k) for k in word_map.keys()) + r')\b',
                re.IGNORECASE
            )
            return pattern.sub(replace_word, text)

        # 2. Ignored files definition
        lockfiles = {
            'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
            'cargo.lock', 'poetry.lock', 'mix.lock',
            'composer.lock', 'gemfile.lock', 'go.sum'
        }
        generated_patterns = ['dist/', 'build/', 'generated/', 'node_modules/']
        binary_extensions = {
            '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip',
            '.gz', '.tar', '.class', '.jar', '.exe', '.dll', '.so', '.dylib', '.woff', '.woff2', '.ttf', '.eot'
        }

        def is_ignored_file(p: str) -> bool:
            filename = p.split('/')[-1]
            ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
            is_idea = p.startswith('.idea/')
            is_lockfile = filename.lower() in lockfiles
            is_generated = any(pattern in p for pattern in generated_patterns) or filename.endswith('.gen.py') or filename.endswith('.g.cs')
            is_binary = ext in binary_extensions
            return is_idea or is_lockfile or is_generated or is_binary

        # Get changed source files for fallback
        source_files = []
        files_to_check = snapshot.files_changed or []
        for path in files_to_check:
            if not is_ignored_file(path):
                source_files.append(path)

        # 3. Extract understanding pass from review.ai_passes
        ai_passes = review.ai_passes or {}
        understanding = {}
        for k, v in ai_passes.items():
            if 'understanding' in k and isinstance(v, dict):
                understanding = v
                break

        # 4. Validate and build Walkthrough summary
        summary = understanding.get('summary') or "This PR updates several source files in the application."
        summary = rewrite_with_neutral_verbs(str(summary).strip())

        # 5. Validate and build Changes
        valid_changes = []
        for change in understanding.get('changes', []):
            if not isinstance(change, dict):
                continue
            files = change.get('files')
            c_summary = change.get('summary')
            if not files or not c_summary:
                continue
            if not isinstance(files, list):
                files = [files]
            
            # Clean file paths: drop General, IDE/generated/lockfile files
            cleaned_files = []
            for f in files:
                f_str = str(f).strip()
                if f_str.lower() == 'general':
                    continue
                if not is_ignored_file(f_str):
                    cleaned_files.append(f_str)
            
            if not cleaned_files:
                continue
                
            c_summary = rewrite_with_neutral_verbs(str(c_summary).strip())
            valid_changes.append({
                'files': cleaned_files,
                'summary': c_summary
            })

        # Generate fallback row from changed source files if empty
        if not valid_changes:
            for f in source_files:
                valid_changes.append({
                    'files': [f],
                    'summary': 'Updated application logic in this file'
                })

        # Keep top 7 grouped rows
        if len(valid_changes) > 7:
            valid_changes = valid_changes[:7]

        # 6. Sequence Diagram validation
        diagram = understanding.get('sequence_diagram') or {}
        include_diagram = False
        diagram_title = ""
        diagram_mermaid = ""
        
        if isinstance(diagram, dict) and diagram.get('include') and diagram.get('mermaid'):
            mermaid_text = str(diagram.get('mermaid', '')).strip()
            
            if 'sequenceDiagram' in mermaid_text:
                import re
                if not re.search(r'\b(github|bot|ai)\b', mermaid_text, re.IGNORECASE):
                    include_diagram = True
                    diagram_title = str(diagram.get('title') or '').strip()
                    
                    high_level_flow = (
                        "sequenceDiagram\n"
                        "    participant Client\n"
                        "    participant API\n"
                        "    participant ChangedModule as Changed Module\n"
                        "    participant Storage\n\n"
                        "    Client->>API: Send request\n"
                        "    API->>ChangedModule: Route to updated logic\n"
                        "    ChangedModule->>ChangedModule: Validate and process input\n"
                        "    ChangedModule->>Storage: Read/write data if needed\n"
                        "    Storage-->>ChangedModule: Return data\n"
                        "    ChangedModule-->>API: Return result\n"
                        "    API-->>Client: Return response"
                    )
                    
                    if len(mermaid_text.splitlines()) > 20:
                        mermaid_text = high_level_flow
                        diagram_title = "High-level request flow"
                        
                    diagram_mermaid = mermaid_text

        # 7. Render Markdown
        md = []
        md.append("## 🤖 Review Walkthrough")
        md.append("")
        md.append(summary)
        md.append("")
        md.append("### Changes")
        md.append("")
        md.append("| File(s) | Summary |")
        md.append("|---|---|")
        
        for change in valid_changes:
            files_str = ", ".join(f"`{f}`" for f in change['files'])
            change_summary = change['summary']
            md.append(f"| {files_str} | {change_summary} |")
            
        if include_diagram and diagram_mermaid:
            md.append("")
            md.append("### Sequence Diagram")
            if diagram_title:
                title_clean = diagram_title.strip()
                if title_clean and not title_clean.endswith(('.', '!', '?')):
                    title_clean += '.'
                md.append("")
                md.append(title_clean)
            md.append("")
            md.append("```mermaid")
            md.append(diagram_mermaid)
            md.append("```")

        return "\n".join(md)

    def _parse_explanation(self, explanation: str) -> dict:
        """Parse raw explanation into sections (Issue, Evidence, Impact, Suggestion)."""
        sections = {}
        patterns = {
            'issue': r'\[Issue\]\s*(.*?)(?=\[Evidence\]|\[Impact\]|\[Suggestion\]|$)',
            'evidence': r'\[Evidence\]\s*(.*?)(?=\[Issue\]|\[Impact\]|\[Suggestion\]|$)',
            'impact': r'\[Impact\]\s*(.*?)(?=\[Issue\]|\[Evidence\]|\[Suggestion\]|$)',
            'suggestion': r'\[Suggestion\]\s*(.*?)(?=\[Issue\]|\[Evidence\]|\[Impact\]|$)'
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, explanation, re.DOTALL | re.IGNORECASE)
            if match:
                sections[key] = match.group(1).strip()
        return sections

    def _parse_evidence(self, evidence_text: str) -> dict | None:
        """Extract line number, file name, code snippet, and context from Evidence section."""
        pattern = r'line\s+(\d+)\s+in\s+([^\s:]+):\s*(`*)(.*?)\3(?:\s*(?:—|–|--)\s*(.*))?$'
        match = re.search(pattern, evidence_text, re.DOTALL)
        if not match:
            return None
        
        line_num = match.group(1)
        file_path = match.group(2)
        code_snippet = match.group(4).strip()
        context = match.group(5).strip() if match.group(5) else ""
        
        return {
            'line': line_num,
            'file': file_path,
            'code': code_snippet,
            'context': context
        }

    def _get_language_from_filename(self, filename: str) -> str:
        """Get syntax highlighting language name from filename extension."""
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        mapping = {
            'py': 'python',
            'js': 'javascript',
            'jsx': 'javascript',
            'ts': 'typescript',
            'tsx': 'typescript',
            'go': 'go',
            'rs': 'rust',
            'java': 'java',
            'c': 'c',
            'cpp': 'cpp',
            'h': 'c',
            'hpp': 'cpp',
            'cs': 'csharp',
            'rb': 'ruby',
            'php': 'php',
            'sh': 'bash',
            'yml': 'yaml',
            'yaml': 'yaml',
            'json': 'json',
            'md': 'markdown',
            'html': 'html',
            'css': 'css',
            'sql': 'sql',
        }
        return mapping.get(ext, '')

    def _convert_comments(
        self,
        comments: list[Any],
    ) -> list[ProviderReviewComment]:
        """Convert comments to provider format with rich Markdown."""
        provider_comments = []
        for item in comments:
            if isinstance(item, tuple):
                comment, finding = item
                body = self._format_inline_comment(
                    category=finding.category or comment.category or "General",
                    severity=finding.severity or comment.severity or "info",
                    title=finding.title,
                    evidence=finding.evidence,
                    impact=finding.impact,
                    fix=finding.fix
                )
                path = finding.file_path
                line = finding.line or 1
            else:
                comment = item
                if "[Issue]" in comment.comment:
                    body_parts = []
                    severity_badge = {
                        'critical': '🚨 **Critical**',
                        'error': '❌ **Error**',
                        'warning': '⚠️ **Warning**',
                        'info': 'ℹ️ **Info**',
                    }
                    badge = severity_badge.get(comment.severity.lower(), '📝 **Review**')
                    category_str = f" • **{comment.category}**" if comment.category else ""
                    
                    body_parts.append(f"### {badge}{category_str}")
                    body_parts.append("")

                    sections = self._parse_explanation(comment.comment)

                    if sections and 'issue' in sections:
                        body_parts.append(f"> 🎯 **Issue**\n> {sections['issue']}")
                        body_parts.append("")

                        if 'evidence' in sections:
                            evidence_data = self._parse_evidence(sections['evidence'])
                            if evidence_data:
                                lang = self._get_language_from_filename(evidence_data['file'])
                                body_parts.append(f"> 🔍 **Evidence** (File `{evidence_data['file']}`, Line {evidence_data['line']})")
                                body_parts.append(f"> ```{lang}")
                                for line_text in evidence_data['code'].split('\n'):
                                    body_parts.append(f"> {line_text}")
                                body_parts.append(f"> ```")
                                if evidence_data['context']:
                                    body_parts.append(f"> *{evidence_data['context']}*")
                            else:
                                body_parts.append(f"> 🔍 **Evidence**\n> {sections['evidence']}")
                            body_parts.append("")

                        if 'impact' in sections:
                            body_parts.append(f"> 💥 **Impact**\n> {sections['impact']}")
                            body_parts.append("")

                        if 'suggestion' in sections:
                            body_parts.append(f"> 💡 **Suggestion**\n> {sections['suggestion']}")
                            body_parts.append("")
                    else:
                        body_parts.append(comment.comment)
                        body_parts.append("")

                    if comment.suggestion:
                        suggestion_clean = comment.suggestion.strip()
                        sections_suggestion = sections.get('suggestion', '').strip()
                        
                        if suggestion_clean != sections_suggestion:
                            lang = self._get_language_from_filename(comment.file_path)
                            body_parts.append("**Proposed Fix:**")
                            body_parts.append(f"```{lang}")
                            body_parts.append(comment.suggestion)
                            body_parts.append("```")
                            body_parts.append("")

                    if comment.confidence:
                        confidence_pct = int(comment.confidence * 100)
                        body_parts.append(f"*Confidence: {confidence_pct}%*")
                    
                    body = '\n'.join(body_parts)
                else:
                    body = comment.comment
                path = comment.file_path
                line = comment.line_start or 1

            provider_comments.append({
                'path': path,
                'body': body,
                'line': line,
                'side': 'RIGHT',
            })
        return provider_comments

    async def _get_pull_request(self, pr_id: str) -> PullRequest | None:
        """Get PullRequest by ID."""
        result = await self.db.execute(
            select(PullRequest)
            .options(selectinload(PullRequest.snapshots))
            .where(PullRequest.id == pr_id)
        )
        return result.scalar_one_or_none()

    async def _get_repository(self, repo_id: str) -> Repository | None:
        """Get Repository by ID."""
        result = await self.db.execute(
            select(Repository).where(Repository.id == repo_id)
        )
        return result.scalar_one_or_none()

    async def _get_repository_owner(self, repo_id: str) -> User | None:
        """Get Repository owner."""
        result = await self.db.execute(
            select(Repository)
            .options(selectinload(Repository.owner))
            .where(Repository.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        return repo.owner if repo else None

    async def _get_unsynced_comments(
        self,
        review_id: str,
    ) -> list[ReviewComment]:
        """Get unsynced comments for a review."""
        result = await self.db.execute(
            select(ReviewComment)
            .where(
                ReviewComment.review_id == review_id,
                ReviewComment.is_synced == False,  # noqa: E712
            )
            .order_by(ReviewComment.created_at)
        )
        return list(result.scalars().all())

    async def retry_failed_comments(
        self,
        review_id: str,
    ) -> dict[str, Any]:
        """
        Retry syncing failed comments.

        Args:
            review_id: Review ID to retry

        Returns:
            Retry result
        """
        result = await self.db.execute(
            select(Review)
            .options(selectinload(Review.snapshot))
            .where(Review.id == review_id)
        )
        review = result.scalar_one_or_none()

        if not review:
            raise ValueError(f'Review not found: {review_id}')

        if not review.snapshot:
            raise ValueError(f'Review has no associated snapshot')

        return await self.sync_review(review, review.snapshot)
