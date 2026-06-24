"""
Background worker for processing review pipeline jobs.

Processes jobs from Redis queues in sequence:
    CONTEXT → LAYER → REVIEW → PUBLISH

Run with:
    python worker.py --queues context,layer,review,publish

Environment variables:
    DATABASE_URL - PostgreSQL connection string
    REDIS_URL - Redis connection string
"""

import asyncio
import argparse
import signal
import sys
from typing import Any
from uuid import UUID

from core.logging_config import setup_logging, get_logger
from infra.database import get_db_context
from infra.redis_client import redis_client
from models.review_job import JobType
from models.snapshot import Snapshot, SnapshotStatus
from services.queue_service import QueueService

logger = get_logger(__name__)


class Worker:
    """
    Background worker for processing review jobs.

    Polls Redis queues for jobs and processes them sequentially.
    Each job type has a dedicated handler that processes the job
    and enqueues the next job in the pipeline.

    Pipeline flow:
        CONTEXT → LAYER → REVIEW → PUBLISH
    """

    def __init__(self, queues: list[str], concurrency: int = 1):
        """
        Initialize worker.

        Args:
            queues: List of queue names to process
            concurrency: Number of concurrent workers (not implemented yet)
        """
        self.queues = [JobType(q) for q in queues]
        self.concurrency = concurrency
        self.running = True
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Main worker loop."""
        logger.info(f'Worker starting, processing queues: {[q.value for q in self.queues]}')

        # Connect to Redis
        await redis_client.connect()

        try:
            while self.running:
                for job_type in self.queues:
                    try:
                        await self._poll_queue(job_type)
                    except Exception as e:
                        logger.error(f'Error polling {job_type.value} queue: {e}', exc_info=True)
                        await asyncio.sleep(5)  # Back off on errors

                # Small sleep between poll cycles
                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info('Worker cancelled')
        finally:
            await redis_client.close()
            logger.info('Worker stopped')

    async def _poll_queue(self, job_type: JobType) -> None:
        """Poll a single queue for jobs."""
        async with get_db_context() as db:
            queue_service = QueueService(db)

            # Non-blocking dequeue with short timeout
            job = await queue_service.dequeue(job_type, timeout=1)
            if not job:
                return

            try:
                await self._process_job(db, queue_service, job)
            except Exception as e:
                logger.error(f'Job {job["job_id"]} failed: {e}', exc_info=True)
                # Roll back the session first -- if the session is in a
                # failed state, calling `queue_service.fail` (which flushes
                # the DB) will raise a PendingRollbackError. Rollback before
                # reusing the session.
                await db.rollback()
                await queue_service.fail(job['job_id'], str(e))

    async def _process_job(
        self,
        db: Any,
        queue_service: QueueService,
        job: dict[str, Any],
    ) -> None:
        """
        Process a single job based on type.

        Args:
            db: Database session
            queue_service: Queue service for enqueuing next job
            job: Job payload from queue
        """
        job_id = job['job_id']
        job_type = job['job_type']
        snapshot_id = job['snapshot_id']

        # Validate snapshot_id is a valid UUID
        try:
            UUID(snapshot_id)
        except (ValueError, AttributeError, TypeError):
            logger.error(f'Invalid snapshot_id: {snapshot_id} is not a valid UUID')
            raise ValueError(f'Invalid snapshot_id: {snapshot_id} is not a valid UUID')

        logger.info(f'Processing job {job_id} ({job_type}) for snapshot {snapshot_id[:8]}')

        # Route to appropriate handler
        if job_type == JobType.CONTEXT.value:
            result = await self._process_context(db, snapshot_id)
            # Enqueue next job in pipeline
            await queue_service.enqueue(
                JobType.LAYER,
                snapshot_id,
                priority=job['priority'],
            )

        elif job_type == JobType.LAYER.value:
            result = await self._process_layer(db, snapshot_id)
            await queue_service.enqueue(
                JobType.REVIEW,
                snapshot_id,
                priority=job['priority'],
            )

        elif job_type == JobType.REVIEW.value:
            result = await self._process_review(db, snapshot_id)
            await queue_service.enqueue(
                JobType.PUBLISH,
                snapshot_id,
                priority=job['priority'],
            )

        elif job_type == JobType.PUBLISH.value:
            result = await self._process_publish(db, snapshot_id)
            # No next job - pipeline complete

        else:
            raise ValueError(f'Unknown job type: {job_type}')

        # Mark job complete
        await queue_service.complete(job_id, result)
        await db.commit()

        logger.info(f'Job {job_id} completed successfully')

    async def _process_context(self, db: Any, snapshot_id: str) -> dict:
        """
        Build context for snapshot.

        Uses ContextService to parse diff and build structured context.
        """
        from sqlalchemy import select
        from services.context_service import ContextService

        # Get snapshot
        result = await db.execute(
            select(Snapshot).where(Snapshot.id == snapshot_id)
        )
        snapshot = result.scalar_one_or_none()

        if not snapshot:
            raise ValueError(f'Snapshot not found: {snapshot_id}')

        # Update status to CONTEXT_BUILDING
        snapshot.status = SnapshotStatus.CONTEXT_BUILDING.value
        await db.flush()

        # Build context using ContextService
        context_service = ContextService(db)
        context = await context_service.build_context(snapshot)

        logger.info(
            f'Built context for snapshot {snapshot_id[:8]}: '
            f'{context.file_count} files, ~{context.estimated_tokens} tokens'
        )

        return {
            'status': 'context_built',
            'file_count': context.file_count,
            'estimated_tokens': context.estimated_tokens,
            'total_changes': context.total_changes,
        }

    async def _process_layer(self, db: Any, snapshot_id: str) -> dict:
        """
        Build functional layers for snapshot.

        Uses LayerService to classify files and create layer records.
        """
        from sqlalchemy import select
        from services.context_service import ContextService
        from services.layer_service import LayerService

        # Get snapshot
        result = await db.execute(
            select(Snapshot).where(Snapshot.id == snapshot_id)
        )
        snapshot = result.scalar_one_or_none()

        if not snapshot:
            raise ValueError(f'Snapshot not found: {snapshot_id}')

        # Update status to LAYERING
        snapshot.status = SnapshotStatus.LAYERING.value
        await db.flush()

        # Build context first (needed for layer classification)
        context_service = ContextService(db)
        context = await context_service.build_context(snapshot)

        # Build layers using LayerService
        layer_service = LayerService(db)
        layers = await layer_service.build_layers(snapshot, context)

        logger.info(
            f'Built {len(layers)} layers for snapshot {snapshot_id[:8]}'
        )

        return {
            'status': 'layers_built',
            'layer_count': len(layers),
            'layers': [
                {
                    'type': layer.layer_type,
                    'files_count': layer.files_count,
                    'risk_score': layer.risk_score,
                }
                for layer in layers
            ],
        }

    async def _process_review(self, db: Any, snapshot_id: str) -> dict:
        """
        Run AI review on snapshot.

        Uses AIReviewService to run 5-pass AI review pipeline.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from services.context_service import ContextService
        from services.layer_service import LayerService
        from services.ai_review_service import AIReviewService

        # Get snapshot with pull_request loaded
        result = await db.execute(
            select(Snapshot)
            .options(selectinload(Snapshot.pull_request))
            .where(Snapshot.id == snapshot_id)
        )
        snapshot = result.scalar_one_or_none()

        if not snapshot:
            raise ValueError(f'Snapshot not found: {snapshot_id}')

        # Update status to REVIEWING
        snapshot.status = SnapshotStatus.REVIEWING.value
        await db.flush()

        # Build context
        context_service = ContextService(db)
        context = await context_service.build_context(snapshot)

        # Get layers
        layer_service = LayerService(db)
        layers = await layer_service.get_layers_for_snapshot(snapshot_id)

        # Run AI review
        ai_service = AIReviewService(db)
        review_result = await ai_service.review_snapshot(snapshot, context, layers)

        # Save review to database
        review = await ai_service.save_review(snapshot, review_result)

        logger.info(
            f'Completed AI review for snapshot {snapshot_id[:8]}: '
            f'{len(review_result.comments)} comments, verdict={review_result.verdict}'
        )

        return {
            'status': 'review_complete',
            'review_id': review.id,
            'comment_count': len(review_result.comments),
            'verdict': review_result.verdict,
            'tokens_used': review_result.total_tokens,
        }

    async def _process_publish(self, db: Any, snapshot_id: str) -> dict:
        """
        Publish review to GitHub.

        Uses GitHubSyncService to post review and comments to GitHub.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from services.github_sync_service import GitHubSyncService
        from services.ai_review_service import AIReviewService

        # Get snapshot with pull_request loaded
        result = await db.execute(
            select(Snapshot)
            .options(selectinload(Snapshot.pull_request))
            .where(Snapshot.id == snapshot_id)
        )
        snapshot = result.scalar_one_or_none()

        if not snapshot:
            raise ValueError(f'Snapshot not found: {snapshot_id}')

        # Update status to PUBLISHING
        snapshot.status = SnapshotStatus.PUBLISHING.value
        await db.flush()

        # Get review for this snapshot
        ai_service = AIReviewService(db)
        review = await ai_service.get_review_for_snapshot(snapshot_id)

        if not review:
            logger.warning(f'No review found for snapshot {snapshot_id[:8]}, skipping publish')
            snapshot.mark_completed()
            await db.flush()
            return {'status': 'skipped', 'reason': 'no_review'}

        # Sync to GitHub
        sync_service = GitHubSyncService(db)
        sync_result = await sync_service.sync_review(review, snapshot)

        # Mark snapshot as completed
        snapshot.mark_completed()
        await db.flush()

        logger.info(
            f'Published review for snapshot {snapshot_id[:8]}: '
            f'{sync_result.get("comments_synced", 0)} comments'
        )

        return {
            'status': 'published',
            'github_review_id': sync_result.get('github_review_id'),
            'comments_synced': sync_result.get('comments_synced', 0),
        }

    def shutdown(self) -> None:
        """Signal worker to stop."""
        logger.info('Shutdown signal received')
        self.running = False
        self._shutdown_event.set()


async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    setup_logging(level=args.log_level)

    worker = Worker(
        queues=args.queues.split(','),
        concurrency=args.concurrency,
    )

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        worker.shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    await worker.run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Review pipeline worker')
    parser.add_argument(
        '--queues',
        default='context,layer,review,publish',
        help='Comma-separated list of queues to process',
    )
    parser.add_argument(
        '--concurrency',
        type=int,
        default=1,
        help='Number of concurrent workers',
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level',
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        logger.info('Worker interrupted')
        sys.exit(0)
