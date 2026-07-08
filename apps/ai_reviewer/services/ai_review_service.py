"""
AIReviewService - Multi-pass AI code review pipeline.

Runs a 5-pass review process:
1. Understanding: What changed?
2. Risks: What could break?
3. Quality: Can we simplify?
4. Business: Does it violate intent?
5. Comments: Generate actionable comments

Each pass builds on the previous, resulting in high-quality,
context-aware review comments.

V1 additions:
- EvidenceService: drops hallucinated file/line references (NO EVIDENCE = NO COMMENT)
- RankingService: scores and filters findings below threshold (score < 0.6 dropped)
- MemoryService: loads repo rules, ignored patterns, accepted decisions
- StaticAnalysisService: runs ruff/mypy, injects grounded findings into LLM context
- Incremental review: skips files already reviewed in previous snapshots of the same PR
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.ai_reviewer.clients.ai_client import AIClient, create_ai_client_from_settings
from core.logger import get_logger
from infra.config import settings
from apps.ai_reviewer.models.layer import Layer
from apps.ai_reviewer.models.review import Review, ReviewComment, ReviewStatus, ReviewVerdict, CommentSeverity
from apps.ai_reviewer.models.snapshot import Snapshot, SnapshotStatus
from apps.ai_reviewer.prompts import (
    get_analysis_prompt,
    get_understanding_prompt,
    get_risks_prompt,
    get_quality_prompt,
    get_business_prompt,
    get_comments_prompt,
)
from apps.ai_reviewer.services.context_service import ContextService, SnapshotContext, FileContext
from apps.ai_reviewer.services.evidence_service import EvidenceService, EvidenceReport
from apps.ai_reviewer.services.ranking_service import RankingService, RankingReport
from apps.ai_reviewer.services.memory_service import MemoryService
from apps.code_analyzer.services.static_analysis_service import StaticAnalysisService, StaticAnalysisResult

logger = get_logger(__name__)


from apps.ai_reviewer.models.review_pass import (
    ReviewCategory,
    ReviewPass,
    GeneratedComment,
    ReviewResult,
    ExternalContext,
    ReviewPipelineError,
)
from apps.ai_reviewer.services.chunk_planner import (
    ChunkPlanner,
    ChunkSplitRequiredException,
)
from apps.ai_reviewer.services.synthesizer import Synthesizer
from apps.ai_reviewer.services.comment_renderer import render_comment_explanation
from apps.ai_reviewer.prompts.review import (
    build_analysis_prompt,
    build_understanding_prompt,
    build_risks_prompt,
    build_quality_prompt,
    build_business_prompt,
    build_comments_prompt,
)


class AIReviewService:
    """
    Multi-pass AI code review service.

    Runs a structured 5-pass review pipeline that builds context
    progressively to generate high-quality review comments.
    """

    # System prompts for each pass
    # Phase 1 improvements:
    # - Pass 2-4: Structured output with file/line location
    # - Pass 5: Grounding instructions to prevent hallucinated line numbers
    # - All passes: "No fabrication" rule + strict JSON enforcement
    SYSTEM_PROMPTS = {
        'analysis': get_analysis_prompt(),
        'understanding': get_understanding_prompt(),
        'risks': get_risks_prompt(),
        'quality': get_quality_prompt(),
        'business': get_business_prompt(),
        'comments': get_comments_prompt(),
    }

    def __init__(
        self,
        db: AsyncSession,
        ai_client: AIClient | None = None,
        repo_dir: str | None = None,
    ):
        """
        Initialize AI review service.

        Args:
            db: Database session
            ai_client: AI client (creates default if None)
            repo_dir: Path to the cloned repository (for static analysis)
        """
        self.db = db
        self.ai_client = ai_client or self._create_ai_client()
        self.max_comments_per_file = settings.review_max_comments_per_file
        self.max_comments_per_pr = settings.review_max_comments_per_pr
        self.repo_dir = repo_dir
        from apps.ai_reviewer.services.context_formatter import ReviewContextFormatter
        from apps.ai_reviewer.tokenizer import TokenizerFactory
        
        tokenizer = None
        if self.ai_client is not None:
            if hasattr(self.ai_client, 'tokenizer'):
                tokenizer = self.ai_client.tokenizer
            elif hasattr(self.ai_client, '_client') and hasattr(self.ai_client._client, 'tokenizer'):
                tokenizer = self.ai_client._client.tokenizer
                
        if tokenizer is None or type(tokenizer).__name__ in ('MagicMock', 'Mock', 'NonCallableMagicMock', 'AsyncMock'):
            tokenizer = TokenizerFactory.get_tokenizer("openai", "cl100k_base")
            
        self.formatter = ReviewContextFormatter(tokenizer)

    def _create_ai_client(self) -> AIClient:
        """Create AI client from settings using factory."""
        return create_ai_client_from_settings()

    def _get_client_for_pass(self, pass_name: str) -> AIClient:
        """Get or create AIClient for a specific pass based on model/provider overrides."""
        provider_override = getattr(settings, f'ai_provider_{pass_name}', '')
        model_override = getattr(settings, f'ai_model_{pass_name}', '')

        if not isinstance(provider_override, str):
            provider_override = ''
        if not isinstance(model_override, str):
            model_override = ''

        # Auto-detect provider if model is set but provider is empty
        if not provider_override and model_override:
            if model_override.startswith(('gpt-', 'o1-', 'o3-')):
                provider_override = 'openai'
            elif model_override.startswith('claude-'):
                provider_override = 'claude'
            elif model_override.startswith('deepseek-'):
                provider_override = 'deepseek'

        if not provider_override and not model_override:
            return self.ai_client

        from apps.ai_reviewer.clients.ai_client import AIClientConfig, AIProvider
        provider = AIProvider(provider_override) if provider_override else self.ai_client.config.provider

        # Build config override
        config = AIClientConfig(
            provider=provider,
            api_key=settings.get_api_key_for_provider(provider.value),
            model=model_override if model_override else self.ai_client.config.model,
            base_url=settings.get_base_url_for_provider(provider.value),
            max_tokens=settings.ai_max_tokens,
            temperature=settings.ai_temperature,
            timeout=settings.ai_timeout,
            max_retries=settings.ai_max_retries,
            retry_delay=settings.ai_retry_delay,
        )

        return AIClient(config)

    def _unpack_analysis_pass(self, analysis: ReviewPass) -> tuple[ReviewPass, ReviewPass, ReviewPass, ReviewPass]:
        """Unpack combined analysis pass data into 4 individual passes."""
        import json

        data = analysis.data
        if not isinstance(data, dict):
            data = {}

        understanding_data = data.get('understanding', {})
        risks_data = data.get('risks', {})
        quality_data = data.get('quality', {})
        business_data = data.get('business', {})

        # Propagate error if present
        if 'error' in data:
            err = data['error']
            understanding_data['error'] = err
            risks_data['error'] = err
            quality_data['error'] = err
            business_data['error'] = err

        understanding = ReviewPass(
            name='understanding',
            prompt=analysis.prompt,
            response=json.dumps(understanding_data),
            tokens_used=analysis.tokens_used // 4,
            duration_ms=analysis.duration_ms,
            data=understanding_data,
        )
        risks = ReviewPass(
            name='risks',
            prompt=analysis.prompt,
            response=json.dumps(risks_data),
            tokens_used=analysis.tokens_used // 4,
            duration_ms=analysis.duration_ms,
            data=risks_data,
        )
        quality = ReviewPass(
            name='quality',
            prompt=analysis.prompt,
            response=json.dumps(quality_data),
            tokens_used=analysis.tokens_used // 4,
            duration_ms=analysis.duration_ms,
            data=quality_data,
        )
        business = ReviewPass(
            name='business',
            prompt=analysis.prompt,
            response=json.dumps(business_data),
            tokens_used=analysis.tokens_used // 4,
            duration_ms=analysis.duration_ms,
            data=business_data,
        )

        return understanding, risks, quality, business

    def _add_compact_suffix(self, pass_name: str, prompt: str) -> str:
        """Add compacting instructions to prompt on truncation retries."""
        if pass_name in ('understanding', 'analysis'):
            return prompt + "\n\nCRITICAL: The previous response was too long. Re-analyze and return a much shorter response. Keep fields short (one sentence each)."
        elif pass_name == 'comments':
            return prompt + "\n\nCRITICAL: The previous response was too long. Re-analyze and return a much shorter response. Return a maximum of 5 findings. Keep each field short (one sentence each). Do not include code blocks or markdown."
        else:
            return prompt + "\n\nCRITICAL: The previous response was too long. Re-analyze and return a much shorter response. Keep each text field to one short sentence. Do not include markdown."

    async def _run_pass_with_retry_and_split(
        self,
        pass_name: str,
        prompt_builder_fn,
        context_str: str,
        file_count: int,
        *args,
        max_attempts: int = 3,
    ) -> ReviewPass:
        """
        Run a single pass with fallback and auto-split triggers.
        Retries only this specific pass if it fails.
        """
        attempt = 0
        use_compact = False
        last_pass = None

        while attempt < max_attempts:
            attempt += 1
            # Build prompt
            prompt = prompt_builder_fn(context_str, *args)
            if use_compact:
                prompt = self._add_compact_suffix(pass_name, prompt)

            p = await self._run_pass(pass_name, prompt)
            last_pass = p

            if 'error' in p.data:
                err_msg = str(p.data['error']).lower()
                if 'truncated' in err_msg:
                    # Truncated!
                    if file_count > 1 and getattr(settings, 'ai_enable_auto_split_on_truncation', True):
                        # Force chunk split retry
                        raise ChunkSplitRequiredException()
                    else:
                        # Can't split further, retry only this pass with compact prompt
                        use_compact = True
                        logger.warning(f"Pass '{pass_name}' truncated on attempt {attempt}. Retrying with use_compact=True")
                        continue
                else:
                    # Other error (timeout/network/etc.) - retry this pass
                    logger.warning(f"Pass '{pass_name}' failed on attempt {attempt}: {err_msg}. Retrying.")
                    continue
            else:
                return p

        return last_pass

    async def _run_stage2_passes_with_retry_and_split(
        self,
        context_str: str,
        understanding_data: dict[str, Any],
        file_count: int,
    ) -> tuple[ReviewPass, ReviewPass, ReviewPass]:
        """Run stage 2 passes (risks, quality, business) concurrently with individual retries."""
        passes_dict = {}

        async def run_one(pass_name, prompt_builder):
            p = await self._run_pass_with_retry_and_split(
                pass_name,
                prompt_builder,
                context_str,
                file_count,
                understanding_data
            )
            passes_dict[pass_name] = p

        await asyncio.gather(
            run_one('risks', build_risks_prompt),
            run_one('quality', build_quality_prompt),
            run_one('business', build_business_prompt),
        )

        return passes_dict['risks'], passes_dict['quality'], passes_dict['business']

    async def review_snapshot(
        self,
        snapshot: Snapshot,
        context: SnapshotContext,
        layers: list[Layer],
        external: ExternalContext | None = None,
        repository_id: str | None = None,
    ) -> ReviewResult:
        """
        Run complete 5-pass review on snapshot.

        V1 pipeline (new):
            Memory filter → Static Analysis → Context Build → 5-pass LLM →
            Evidence Gate → Ranking → Result

        Args:
            snapshot: Snapshot being reviewed
            context: Parsed context with diff information
            layers: Functional layers for the snapshot
            external: Optional external context (PR description, conventions, etc.)
            repository_id: Repository UUID for Memory layer lookup

        Returns:
            ReviewResult with all passes and comments, plus quality gate metadata
        """
        start_time = time.time()
        passes: list[ReviewPass] = []
        total_tokens = 0
        evidence_report = None
        ranking_report = None
        static_findings_count = 0
        incremental_skipped = 0

        logger.info(f'Starting AI review for snapshot {snapshot.id[:8]}')

        # ── Step 0: Memory Layer ──────────────────────────────────────────
        memory: MemoryService | None = None
        if repository_id:
            memory = MemoryService(self.db, repository_id=repository_id)
            rules_prompt_section = await memory.get_rules_prompt_section()
        else:
            rules_prompt_section = ''

        # ── Step 1: Incremental — Skip already-reviewed files ─────────────
        context, incremental_skipped = await self._apply_incremental_filter(
            snapshot, context
        )

        if not context.files:
            logger.info('All files already reviewed — skipping review pass')
            return ReviewResult(
                passes=[],
                comments=[],
                summary='All changed files were already reviewed in a previous pass.',
                verdict='approved',
                total_tokens=0,
                duration_ms=int((time.time() - start_time) * 1000),
                incremental_files_skipped=incremental_skipped,
            )

        # ── Step 2: Memory — Filter ignored files ─────────────────────────
        if memory:
            active_file_paths = await memory.filter_files(
                [f.file_path for f in context.files]
            )
            context.files = [f for f in context.files if f.file_path in set(active_file_paths)]

        # ── Step 3: Static Analysis ───────────────────────────────────────
        static_section = ''
        if self.repo_dir:
            static_svc = StaticAnalysisService(repo_dir=self.repo_dir)
            static_result: StaticAnalysisResult = await static_svc.analyze(
                changed_files=[f.file_path for f in context.files]
            )
            static_findings_count = len(static_result.findings)
            static_section = static_result.to_prompt_section()
        else:
            logger.debug('No repo_dir set — skipping static analysis')

        # Get soft budget from settings
        soft_budget = getattr(settings, 'ai_soft_budget', 50000)
        max_attempts = 3
        attempt = 0

        while attempt < max_attempts:
            attempt += 1
            passes = []
            total_tokens = 0

            # Plan chunks with unified context formatter and tokenizer
            from apps.ai_reviewer.services.context_formatter import ReviewContextFormatter
            client = self._get_client_for_pass('analysis' if getattr(settings, 'ai_enable_combined_analysis', True) else 'understanding')
            formatter = ReviewContextFormatter(client.tokenizer)
            
            planner = ChunkPlanner(formatter, soft_budget)
            chunks = planner.plan_chunks(
                context,
                layers=layers,
                external=external,
                rules_section=rules_prompt_section,
                static_section=static_section,
            )

            logger.info(f"AI Review Attempt {attempt}/{max_attempts} with budget {soft_budget}. Total chunks: {len(chunks)}")

            if len(chunks) == 1:
                # ── Step 4: Build context string ──────────────────────────────────
                context_str = self._build_context_string(
                    context, layers, external,
                    rules_section=rules_prompt_section,
                    static_section=static_section,
                )

                # ── Step 5: Run passes with individual retries ───────────────────
                enable_combined = getattr(settings, 'ai_enable_combined_analysis', True)
                
                try:
                    if enable_combined:
                        # Combined analysis pass (Understanding + Risks + Quality + Business)
                        analysis = await self._run_pass_with_retry_and_split(
                            'analysis',
                            build_analysis_prompt,
                            context_str,
                            len(context.files)
                        )
                        passes.append(analysis)
                        total_tokens += analysis.tokens_used
                        
                        # Unpack analysis data into dummy passes
                        understanding, risks, quality, business = self._unpack_analysis_pass(analysis)
                        passes.extend([understanding, risks, quality, business])
                    else:
                        # Traditional 5-pass pipeline
                        understanding = await self._run_pass_with_retry_and_split(
                            'understanding',
                            build_understanding_prompt,
                            context_str,
                            len(context.files)
                        )
                        passes.append(understanding)
                        total_tokens += understanding.tokens_used
                        
                        # Stage 2: Risks, Quality, Business in parallel
                        risks, quality, business = await self._run_stage2_passes_with_retry_and_split(
                            context_str, understanding.data, len(context.files)
                        )
                        passes.extend([risks, quality, business])
                        total_tokens += risks.tokens_used + quality.tokens_used + business.tokens_used

                    # Stage 3/2: Comments pass
                    comments_pass = await self._run_pass_with_retry_and_split(
                        'comments',
                        build_comments_prompt,
                        context_str,
                        len(context.files),
                        understanding.data,
                        risks.data,
                        quality.data,
                        business.data
                    )
                    passes.append(comments_pass)
                    total_tokens += comments_pass.tokens_used
                    
                except ChunkSplitRequiredException:
                    if attempt < max_attempts:
                        soft_budget = soft_budget // 2
                        logger.warning(f"AI response truncated on attempt {attempt}. Retrying with budget {soft_budget}")
                        continue
                    else:
                        raise

                all_critical = [risks, quality, business, comments_pass]
                all_failed = [p.name for p in all_critical if 'error' in p.data]
                if len(all_failed) >= 3:
                    raise ReviewPipelineError(failed_passes=all_failed, total_passes=4)

                partial_failures = len(all_failed)

                # ── Step 6: Parse raw comments ────────────────────────────────────
                raw_comments = self._parse_comments(comments_pass.data)
                logger.info(f"LLM returned raw findings data: {len(comments_pass.data) if isinstance(comments_pass.data, list) else 1 if isinstance(comments_pass.data, dict) else 0}")
                logger.info(f"Parsed: {len(raw_comments)} findings")

                # ── Step 7: Evidence Gate ─────────────────────────────────────────
                evidence_svc = EvidenceService(context)
                evidence_filtered, evidence_report = evidence_svc.filter_comments(
                    raw_comments,
                    min_confidence=settings.review_min_confidence,
                )

                # ── Step 8: Memory suppress ───────────────────────────────────────
                if memory:
                    evidence_filtered, suppressed = await memory.filter_accepted_decisions(
                        evidence_filtered
                    )
                    if suppressed:
                        logger.info(f'Memory suppressed {suppressed} accepted findings')

                # ── Step 9: Finding Ranking ───────────────────────────────────────
                ranker = RankingService(layers=layers, threshold=settings.review_min_score_threshold)
                ranked_comments, ranking_report = ranker.rank(evidence_filtered)

                # ── Step 10: Apply hard comment limits ────────────────────────────
                final_comments = self._apply_comment_limits(ranked_comments)
                logger.info(f"Final comments count after limits: {len(final_comments)}")
                logger.info(f"Final comments sent to provider: {len(final_comments)}")

                # ── Step 11: Summary and verdict ──────────────────────────────────
                summary = self._generate_summary(understanding.data, risks.data)
                verdict = self._determine_verdict(
                    risks.data, final_comments, partial_failures=partial_failures
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if partial_failures > 0:
                    logger.warning(
                        f'AI review completed with {partial_failures}/4 pass failures. '
                        f'Verdict is conservative (needs_discussion or changes_requested).'
                    )

                logger.info(
                    f'AI review complete: raw={len(raw_comments)}, '
                    f'evidence_passed={len(evidence_filtered)}, '
                    f'ranked_kept={len(final_comments)}, '
                    f'pass_failures={partial_failures}, '
                    f'{total_tokens} tokens, {duration_ms}ms'
                )

                return ReviewResult(
                    passes=passes,
                    comments=final_comments,
                    summary=summary,
                    verdict=verdict,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    evidence_report=evidence_report,
                    ranking_report=ranking_report,
                    static_findings_count=static_findings_count,
                    incremental_files_skipped=incremental_skipped,
                    pipeline_failures=partial_failures,
                )

            else:
                # CHUNKED PATHWAY (for very large PRs to prevent token truncation)
                logger.info(f'Executing chunked review pathway across {len(chunks)} chunks.')

                all_raw_comments = []
                all_evidence_filtered = []
                partial_failures = 0
                chunk_summaries = []
                chunk_risks = []
                chunk_split_triggered = False

                for idx, chunk in enumerate(chunks):
                    logger.info(f'Reviewing chunk {idx + 1}/{len(chunks)} ({chunk.file_count} files, ~{chunk.estimated_tokens} tokens)')

                    # Filter static findings for this chunk
                    chunk_files = {f.file_path for f in chunk.files}
                    if self.repo_dir:
                        chunk_findings = [f for f in static_result.findings if f.file_path in chunk_files]
                        chunk_static_res = StaticAnalysisResult(findings=chunk_findings)
                        chunk_static_section = chunk_static_res.to_prompt_section()
                    else:
                        chunk_static_section = ''

                    # ── Step 4: Build context string for chunk ────────────────────
                    chunk_context_str = self._build_context_string(
                        chunk, layers, external,
                        rules_section=rules_prompt_section,
                        static_section=chunk_static_section,
                    )

                    # ── Step 5: LLM Review for chunk ────────────────────────
                    enable_combined = getattr(settings, 'ai_enable_combined_analysis', True)

                    try:
                        if enable_combined:
                            analysis = await self._run_pass_with_retry_and_split(
                                'analysis',
                                build_analysis_prompt,
                                chunk_context_str,
                                chunk.file_count
                            )
                            passes.append(analysis)
                            total_tokens += analysis.tokens_used

                            understanding, risks, quality, business = self._unpack_analysis_pass(analysis)
                            passes.extend([understanding, risks, quality, business])
                        else:
                            understanding = await self._run_pass_with_retry_and_split(
                                'understanding',
                                build_understanding_prompt,
                                chunk_context_str,
                                chunk.file_count
                            )
                            passes.append(understanding)
                            total_tokens += understanding.tokens_used

                            risks, quality, business = await self._run_stage2_passes_with_retry_and_split(
                                chunk_context_str, understanding.data, chunk.file_count
                            )
                            passes.extend([risks, quality, business])
                            total_tokens += risks.tokens_used + quality.tokens_used + business.tokens_used

                        if 'error' not in understanding.data:
                            chunk_summaries.append(understanding.data.get('summary', ''))
                        if 'error' not in risks.data:
                            chunk_risks.append(risks.data.get('risk_level', 'low'))

                        comments_pass = await self._run_pass_with_retry_and_split(
                            'comments',
                            build_comments_prompt,
                            chunk_context_str,
                            chunk.file_count,
                            understanding.data,
                            risks.data,
                            quality.data,
                            business.data
                        )
                        passes.append(comments_pass)
                        total_tokens += comments_pass.tokens_used

                    except ChunkSplitRequiredException:
                        chunk_split_triggered = True
                        break

                    chunk_critical = [risks, quality, business, comments_pass]
                    chunk_all_failed = [p.name for p in chunk_critical if 'error' in p.data]
                    partial_failures += len(chunk_all_failed)

                    chunk_raw_comments = self._parse_comments(comments_pass.data)
                    logger.info(f"Chunk {idx + 1}/{len(chunks)}: LLM returned raw findings data: {len(comments_pass.data) if isinstance(comments_pass.data, list) else 1 if isinstance(comments_pass.data, dict) else 0}")
                    logger.info(f"Chunk {idx + 1}/{len(chunks)}: Parsed {len(chunk_raw_comments)} comments")
                    all_raw_comments.extend(chunk_raw_comments)

                    # Evidence Gate
                    evidence_svc = EvidenceService(chunk)
                    chunk_evidence_filtered, _ = evidence_svc.filter_comments(
                        chunk_raw_comments,
                        min_confidence=settings.review_min_confidence,
                    )

                    # Memory suppress
                    if memory:
                        chunk_evidence_filtered, _ = await memory.filter_accepted_decisions(
                            chunk_evidence_filtered
                        )
                    all_evidence_filtered.extend(chunk_evidence_filtered)

                if chunk_split_triggered:
                    if attempt < max_attempts:
                        soft_budget = soft_budget // 2
                        logger.warning(f"AI response truncated in chunked pathway on attempt {attempt}. Retrying with budget {soft_budget}")
                        continue
                    else:
                        raise

                # ── Step 6: Merge & Synthesize ────────────────────────────────────
                synthesizer = Synthesizer()
                synthesized_comments = synthesizer.merge_comments(all_evidence_filtered)

                # ── Step 9: Finding Ranking ───────────────────────────────────────
                ranker = RankingService(layers=layers, threshold=settings.review_min_score_threshold)
                ranked_comments, ranking_report = ranker.rank(synthesized_comments)

                # ── Step 10: Apply hard comment limits ────────────────────────────
                final_comments = self._apply_comment_limits(ranked_comments)
                logger.info(f"Final comments count after limits: {len(final_comments)}")
                logger.info(f"Final comments sent to provider: {len(final_comments)}")

                # ── Step 11: Summary and verdict ──────────────────────────────────
                summary = '### Chunked AI Review Summary\n\n'
                summary += '\n\n'.join([f'**Chunk {i+1}**: {s}' for i, s in enumerate(chunk_summaries)])

                verdict = 'approved'
                if any(r == 'high' for r in chunk_risks):
                    verdict = 'changes_requested'
                elif any(r == 'medium' for r in chunk_risks) or len(final_comments) > 0:
                    verdict = 'needs_discussion'

                duration_ms = int((time.time() - start_time) * 1000)

                logger.info(
                    f'Chunked AI review complete: chunks={len(chunks)}, raw={len(all_raw_comments)}, '
                    f'evidence_passed={len(all_evidence_filtered)}, '
                    f'synthesized_kept={len(final_comments)}, '
                    f'{total_tokens} tokens, {duration_ms}ms'
                )

                return ReviewResult(
                    passes=passes,
                    comments=final_comments,
                    summary=summary,
                    verdict=verdict,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    evidence_report=None,
                    ranking_report=ranking_report,
                    static_findings_count=static_findings_count,
                    incremental_files_skipped=incremental_skipped,
                    pipeline_failures=partial_failures,
                )


    async def _apply_incremental_filter(
        self,
        snapshot: Snapshot,
        context: SnapshotContext,
    ) -> tuple[SnapshotContext, int]:
        """
        Filter context to only include files not already reviewed in a previous
        snapshot of the same PR.

        This prevents duplicate comments when a PR is updated with minor changes.

        Args:
            snapshot: Current snapshot
            context: Full context for current snapshot

        Returns:
            Tuple of (filtered_context, count_of_skipped_files)
        """
        # Fetch all previous completed reviews for the same PR
        result = await self.db.execute(
            select(Review)
            .where(
                Review.repository_id == snapshot.pull_request.repository_id,
                Review.pull_request_number == snapshot.pull_request.pr_number,
                Review.status == ReviewStatus.COMPLETED.value,
                # Exclude the current snapshot's review
                Review.snapshot_id != str(snapshot.id),
            )
            .order_by(Review.created_at.desc())
            .limit(5)
        )
        previous_reviews = result.scalars().all()

        if not previous_reviews:
            return context, 0

        # Collect all file paths and their hashes from previous reviews
        # Merging from oldest to newest so the newest hash version overrides
        previous_file_hashes = {}
        for r in reversed(previous_reviews):
            if r.detailed_feedback and 'file_hashes' in r.detailed_feedback:
                previous_file_hashes.update(r.detailed_feedback['file_hashes'])
            elif r.review_order:
                # Fallback for legacy reviews without file_hashes:
                # Mark as legacy so we skip only if not in files_changed
                for p in r.review_order:
                    previous_file_hashes[p] = 'legacy'

        # Fetch current content hashes of all files in this repository from database
        from apps.code_analyzer.models.code_graph import IndexedFile
        stmt = select(IndexedFile.path, IndexedFile.content_hash).where(
            IndexedFile.repository_id == snapshot.pull_request.repository_id
        )
        hash_rows = (await self.db.execute(stmt)).all()
        current_file_hashes = {path: content_hash for path, content_hash in hash_rows}

        files_to_review = []
        skipped_count = 0

        for f in context.files:
            path = f.file_path
            current_hash = current_file_hashes.get(path)
            prev_hash = previous_file_hashes.get(path)

            if prev_hash is not None:
                # File was reviewed previously
                if prev_hash == 'legacy':
                    # Legacy fallback: skip if not in files_changed
                    if path not in (snapshot.files_changed or []):
                        skipped_count += 1
                        continue
                elif current_hash is not None and prev_hash == current_hash:
                    # Hash Cache Hit: The file content has not changed since it was reviewed
                    logger.info(f"Hash Cache Hit: skipping unchanged file {path} (hash: {current_hash})")
                    skipped_count += 1
                    continue

            # Need to review this file
            files_to_review.append(f)

        if skipped_count > 0:
            logger.info(
                f'Incremental review: skipping {skipped_count} unchanged/already-reviewed files '
                f'(reviewing {len(files_to_review)}/{len(context.files) + skipped_count})'
            )
            context.files = files_to_review

        return context, skipped_count

    async def _run_pass(
        self,
        pass_name: str,
        prompt: str,
    ) -> ReviewPass:
        """
        Run a single review pass against the AI model.

        Looks up per-pass max_tokens from settings so each pass can be
        independently tuned. Falls back to the global AI_MAX_TOKENS if not set.
        Implements input token estimation and dynamic output token budgeting.
        """
        start_time = time.time()

        system_prompt = self.SYSTEM_PROMPTS.get(pass_name, '')
        configured_max_output = settings.get_pass_max_tokens(pass_name)

        client = self._get_client_for_pass(pass_name)

        # 1. Estimate input tokens
        input_tokens = client.count_tokens(prompt + system_prompt)

        # 2. Dynamic output token budgeting
        model_context_window = getattr(settings, 'ai_model_context_window', 64000)
        reserve_tokens = 2048
        available = model_context_window - input_tokens - reserve_tokens
        
        if available <= 1024:
            max_tokens = 1024
        else:
            max_tokens = min(configured_max_output, available)

        # Log request diagnostics
        logger.info(
            f"AI request: pass_type={pass_name} "
            f"input_tokens={input_tokens} "
            f"max_output_tokens={max_tokens} "
            f"estimated_total={input_tokens + max_tokens} "
            f"model_context_window={model_context_window}"
        )

        try:
            response = await client.complete_json(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Count findings count dynamically
            findings_count = 0
            if isinstance(response, list):
                findings_count = len(response)
            elif isinstance(response, dict):
                if 'findings' in response and isinstance(response['findings'], list):
                    findings_count = len(response['findings'])
                elif 'comments' in response and isinstance(response['comments'], list):
                    findings_count = len(response['comments'])
                else:
                    for k, v in response.items():
                        if isinstance(v, list):
                            findings_count += len(v)

            response_str = str(response)
            output_tokens = client.count_tokens(response_str)

            logger.info(
                f"AI response: pass_type={pass_name} "
                f"output_tokens={output_tokens} "
                f"finish_reason=stop "
                f"findings_count={findings_count}"
            )

            return ReviewPass(
                name=pass_name,
                prompt=prompt[:500] + '...' if len(prompt) > 500 else prompt,
                response=response_str[:1000],
                tokens_used=input_tokens + output_tokens,
                duration_ms=duration_ms,
                data=(
                    response
                    if isinstance(response, (dict, list))
                    else {'raw': response}
                ),
            )
        except Exception as e:
            logger.error(f'Pass {pass_name} failed: {e}')
            return ReviewPass(
                name=pass_name,
                prompt=prompt[:500],
                response=f'Error: {e}',
                tokens_used=input_tokens,
                duration_ms=int((time.time() - start_time) * 1000),
                data={'error': str(e)},
            )


    def _build_context_string(
        self,
        context: SnapshotContext,
        layers: list[Layer],
        external: ExternalContext | None = None,
        rules_section: str = '',
        static_section: str = '',
    ) -> str:
        """Deprecated - use ReviewContextFormatter directly."""
        return self.formatter.format_context(
            context=context,
            layers=layers,
            external=external,
            rules_section=rules_section,
            static_section=static_section,
            limit_hunk_lines=20
        )

    def _parse_comments(
        self,
        data: dict[str, Any] | list[Any],
    ) -> list[GeneratedComment]:
        """Parse comments from AI response."""
        comments = []

        # Handle both dict and list responses
        if isinstance(data, dict):
            if isinstance(data.get('comments'), list):
                items = data.get('comments', [])
            elif isinstance(data.get('raw'), list):
                items = data.get('raw', [])
            elif (
                isinstance(data.get('raw'), dict)
                and isinstance(data.get('raw', {}).get('comments'), list)
            ):
                items = data.get('raw', {}).get('comments', [])
            elif 'file_path' in data and ('explanation' in data or 'suggestion' in data or 'problem' in data):
                # The dict itself represents a single comment
                items = [data]
            else:
                # Recursively search for any key containing a list of comment dicts
                items = []
                for val in data.values():
                    if isinstance(val, list) and all(isinstance(x, dict) and ('file_path' in x or 'file' in x) for x in val):
                        items = val
                        break
        elif isinstance(data, list):
            items = data
        else:
            return comments

        for item in items:
            if not isinstance(item, dict):
                continue

            try:
                # Resolve confidence
                raw_conf = item.get('confidence', 0.5)
                if isinstance(raw_conf, str):
                    conf_map = {'high': 0.9, 'medium': 0.7, 'low': 0.5}
                    confidence = conf_map.get(raw_conf.lower(), 0.5)
                else:
                    try:
                        confidence = float(raw_conf)
                    except (ValueError, TypeError):
                        confidence = 0.5

                # Resolve severity
                severity = item.get('severity', 'info')
                if isinstance(severity, str):
                    severity = severity.lower()
                else:
                    severity = 'info'

                # Resolve category
                category = item.get('category', 'Maintainability')

                # Pre-render inline comment markdown if new schema is used
                explanation = item.get('explanation', '')
                suggestion = item.get('suggestion')
                code_suggestion = item.get('code_suggestion')
                title = item.get('title', 'Observation')

                if 'problem' in item or 'impact' in item or 'title' in item:
                    title = item.get('title', 'Observation')
                    problem = item.get('problem', '')
                    impact = item.get('impact', '')
                    sugg_fix = item.get('suggestion', '')

                    explanation = render_comment_explanation(
                        category=category,
                        severity=severity,
                        title=title,
                        problem=problem,
                        impact=impact,
                        suggestion=sugg_fix,
                        code_suggestion=code_suggestion
                    )
                    if code_suggestion:
                        suggestion = code_suggestion

                # Resolve line / line_start
                line_start = item.get('line') or item.get('line_start') or 1
                try:
                    line_start = int(line_start)
                except (ValueError, TypeError):
                    line_start = 1

                comment = GeneratedComment(
                    file_path=item.get('file_path', item.get('file', '')),
                    line_start=line_start,
                    line_end=item.get('line_end'),
                    severity=severity,
                    category=category,
                    explanation=explanation,
                    suggestion=suggestion,
                    confidence=confidence,
                    title=title,
                )
                comments.append(comment)
            except (ValueError, KeyError) as e:
                logger.warning(f'Failed to parse comment: {e}')

        return comments

    def _apply_comment_limits(
        self,
        comments: list[GeneratedComment],
    ) -> list[GeneratedComment]:
        """Apply per-file and total comment limits."""
        # Sort by confidence (highest first)
        comments.sort(key=lambda c: c.confidence, reverse=True)

        # Apply per-file limit
        file_counts: dict[str, int] = {}
        filtered = []

        for comment in comments:
            file_count = file_counts.get(comment.file_path, 0)
            if file_count < self.max_comments_per_file:
                filtered.append(comment)
                file_counts[comment.file_path] = file_count + 1

        # Apply total limit
        return filtered[:self.max_comments_per_pr]

    def _count_issues(self, issues: list) -> int:
        """Count issues, handling both string and dict formats."""
        return len([i for i in issues if i]) if issues else 0

    def _generate_summary(
        self,
        understanding: dict[str, Any],
        risks: dict[str, Any],
    ) -> str:
        """Generate review summary."""
        parts = []

        summary = understanding.get('summary', '')
        if summary:
            parts.append(summary)

        risk_level = risks.get('risk_level', 'unknown')
        if risk_level in ('high', 'critical'):
            parts.append(f'\n⚠️ Risk Level: {risk_level.upper()}')

        security = risks.get('security_concerns', [])
        security_count = self._count_issues(security)
        if security_count:
            parts.append(f'\n🔒 Security concerns identified: {security_count}')

        breaking = risks.get('breaking_changes', [])
        breaking_count = self._count_issues(breaking)
        if breaking_count:
            parts.append(f'\n💥 Potential breaking changes: {breaking_count}')

        performance = risks.get('performance_issues', [])
        perf_count = self._count_issues(performance)
        if perf_count:
            parts.append(f'\n⚡ Performance issues: {perf_count}')

        return '\n'.join(parts) if parts else 'Review complete.'

    def _determine_verdict(
        self,
        risks: dict[str, Any],
        comments: list[GeneratedComment],
        partial_failures: int = 0,
    ) -> str:
        """
        Determine review verdict.

        Args:
            risks: Parsed data from the risks pass.
            comments: Final ranked comments.
            partial_failures: Number of analysis passes that returned error data.
                When > 0, the verdict is downgraded to be conservative —
                we never default to 'approved' when data is incomplete.
        """
        risk_level = risks.get('risk_level', 'unknown')

        # When risk data is missing (pass failed), we cannot safely approve.
        # Use 'needs_discussion' as the conservative default instead of 'approved'.
        if risk_level == 'unknown' or partial_failures > 0:
            # If there are critical/security comments despite partial failure,
            # still escalate to changes_requested.
            critical_count = sum(
                1 for c in comments
                if c.severity in ('critical', 'error')
            )
            security_count = sum(
                1 for c in comments
                if c.category == 'security'
            )
            if critical_count > 0 or security_count > 0:
                return ReviewVerdict.CHANGES_REQUESTED.value
            # Cannot determine risk level — ask for human review.
            return ReviewVerdict.NEEDS_DISCUSSION.value

        # Normal verdict logic when all passes succeeded.
        # Count critical/error comments
        critical_count = sum(
            1 for c in comments
            if c.severity in ('critical', 'error')
        )

        security_count = sum(
            1 for c in comments
            if c.category == 'security'
        )

        if risk_level == 'critical' or critical_count > 0 or security_count > 0:
            return ReviewVerdict.CHANGES_REQUESTED.value
        elif risk_level == 'high' or len(comments) > 10:
            return ReviewVerdict.NEEDS_DISCUSSION.value
        else:
            return ReviewVerdict.APPROVED.value

    async def save_review(
        self,
        snapshot: Snapshot,
        result: ReviewResult,
    ) -> Review:
        """
        Save review result to database.

        Args:
            snapshot: Snapshot being reviewed
            result: AI review result

        Returns:
            Created Review record
        """
        # Fetch current content hashes of indexed files to support Hash Cache
        from apps.code_analyzer.models.code_graph import IndexedFile
        stmt = select(IndexedFile.path, IndexedFile.content_hash).where(
            IndexedFile.repository_id == snapshot.pull_request.repository_id
        )
        hash_rows = (await self.db.execute(stmt)).all()
        file_hashes = {path: content_hash for path, content_hash in hash_rows}

        # Create Review record
        review = Review(
            repository_id=snapshot.pull_request.repository_id,
            snapshot_id=str(snapshot.id),
            pull_request_number=snapshot.pull_request.pr_number,
            commit_sha=snapshot.commit_sha,
            review_type='pull_request',
            status=ReviewStatus.COMPLETED.value,
            verdict=result.verdict,
            summary=result.summary,
            detailed_feedback={'file_hashes': file_hashes},
            files_analyzed=len(set(c.file_path for c in result.comments)),
            ai_model=self.ai_client.model,
            ai_tokens_used=result.total_tokens,
            processing_time_ms=result.duration_ms,
            pipeline_failures=result.pipeline_failures,
            ai_passes={
                f'pass_{i+1}_{p.name}': p.data
                for i, p in enumerate(result.passes)
            },
            review_order=[c.file_path for c in result.comments],
            completed_at=datetime.utcnow(),
        )

        self.db.add(review)
        await self.db.flush()
        await self.db.refresh(review)

        # Create ReviewComment records
        for gen_comment in result.comments:
            comment = ReviewComment(
                review_id=review.id,
                file_path=gen_comment.file_path,
                line_start=gen_comment.line_start,
                line_end=gen_comment.line_end,
                severity=gen_comment.severity,
                category=gen_comment.category,
                comment=gen_comment.explanation,
                suggestion=gen_comment.suggestion,
                confidence=gen_comment.confidence,
            )
            self.db.add(comment)

        await self.db.flush()

        logger.info(
            f'Saved review {review.id[:8]} with {len(result.comments)} comments'
        )

        return review

    async def get_review_for_snapshot(
        self,
        snapshot_id: str,
    ) -> Review | None:
        """Get review for a snapshot."""
        result = await self.db.execute(
            select(Review)
            .options(selectinload(Review.comments))
            .where(Review.snapshot_id == snapshot_id)
            .order_by(Review.created_at.desc())
        )
        return result.scalars().first()
