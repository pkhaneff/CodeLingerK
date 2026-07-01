"""
QueueService - Redis-backed job queue for async review processing.

Uses sorted sets for priority ordering and provides:
- Priority-based job processing
- Exponential backoff retry
- Dead letter queue for failed jobs
- Job status tracking
"""

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4, UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from infra.redis_client import redis_client
from apps.ai_reviewer.models.review_job import ReviewJob, JobStatus, JobType

logger = get_logger(__name__)


class QueueService:
    """
    Redis-backed job queue for review pipeline.

    Queue structure (sorted sets for priority):
        codelingerk:v1:queue:{job_type} - Active jobs (score = priority inverted)
        codelingerk:v1:queue:processing - Jobs being processed
        codelingerk:v1:queue:dead_letter - Failed jobs (max retries exceeded)

    Job payload stored in hash:
        codelingerk:v1:job:{job_id} - Job details as JSON
    """

    # Queue name patterns
    QUEUE_PREFIX = 'queue'
    JOB_PREFIX = 'job'
    PROCESSING_QUEUE = 'processing'
    DEAD_LETTER_QUEUE = 'dead_letter'

    # Priority inversion (higher priority = lower score = dequeued first)
    MAX_PRIORITY = 100

    def __init__(self, db: AsyncSession | None = None):
        """
        Initialize queue service.

        Args:
            db: Optional database session for job tracking
        """
        self.db = db
        self._redis = redis_client

    def _queue_key(self, job_type: str) -> str:
        """Build queue key for job type."""
        return self._redis._key(self.QUEUE_PREFIX, job_type)

    def _job_key(self, job_id: str) -> str:
        """Build job data key."""
        return self._redis._key(self.JOB_PREFIX, job_id)

    def _processing_key(self) -> str:
        """Build processing queue key."""
        return self._redis._key(self.QUEUE_PREFIX, self.PROCESSING_QUEUE)

    def _dead_letter_key(self) -> str:
        """Build dead letter queue key."""
        return self._redis._key(self.QUEUE_PREFIX, self.DEAD_LETTER_QUEUE)

    async def enqueue(
        self,
        job_type: JobType,
        snapshot_id: str,
        priority: int = 50,
        delay_seconds: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add job to queue.

        Args:
            job_type: Type of job (context, layer, review, publish)
            snapshot_id: Snapshot being processed
            priority: 0-100, higher = process first
            delay_seconds: Delay before job becomes visible
            metadata: Additional job metadata

        Returns:
            job_id: Unique job identifier

        Raises:
            ValueError: If snapshot_id is not a valid UUID
        """
        # Validate snapshot_id is a valid UUID
        try:
            UUID(snapshot_id)
        except (ValueError, AttributeError) as e:
            raise ValueError(f'Invalid snapshot_id: {snapshot_id} is not a valid UUID') from e

        job_id = str(uuid4())
        now = datetime.utcnow()

        # Calculate when job should be processed
        scheduled_for = now + timedelta(seconds=delay_seconds) if delay_seconds > 0 else now

        # Create job record in database if db session available
        if self.db:
            job = ReviewJob(
                id=job_id,
                snapshot_id=snapshot_id,
                job_type=job_type.value,
                status=JobStatus.QUEUED.value,
                priority=priority,
                scheduled_for=scheduled_for if delay_seconds > 0 else None,
            )
            self.db.add(job)
            await self.db.flush()

        # Prepare job payload
        payload = {
            'job_id': job_id,
            'snapshot_id': snapshot_id,
            'job_type': job_type.value,
            'priority': priority,
            'attempt': 0,
            'created_at': now.isoformat(),
            'scheduled_for': scheduled_for.isoformat(),
            'metadata': metadata or {},
        }

        # Store job data
        await self._redis._client.set(self._job_key(job_id), json.dumps(payload))

        # Add to queue with inverted priority as score (lower score = higher priority)
        # For delayed jobs, add timestamp component to score
        score = self.MAX_PRIORITY - priority
        if delay_seconds > 0:
            # Delayed jobs get a future timestamp added to score
            score += scheduled_for.timestamp()

        queue_key = self._queue_key(job_type.value)
        await self._redis._client.zadd(queue_key, {job_id: score})

        logger.info(
            f'Enqueued job {job_id} ({job_type.value}) '
            f'for snapshot {snapshot_id[:8]} with priority {priority}'
        )

        return job_id

    async def dequeue(
        self,
        job_type: JobType,
        timeout: int = 30,
    ) -> dict[str, Any] | None:
        """
        Get next job from queue (blocking).

        Uses BZPOPMIN to atomically pop the highest priority job.
        Jobs with delay are skipped until their scheduled time.

        Args:
            job_type: Which queue to pop from
            timeout: Seconds to block waiting for job

        Returns:
            Job payload dict or None if timeout
        """
        queue_key = self._queue_key(job_type.value)
        now = datetime.utcnow()

        # Non-blocking check for ready jobs
        # Get jobs with score <= MAX_PRIORITY (non-delayed) or scheduled time passed
        result = await self._redis._client.zrange(
            queue_key,
            0,
            0,
            withscores=True,
        )

        if not result:
            # No jobs, do blocking wait
            result = await self._redis._client.bzpopmin(queue_key, timeout=timeout)
            if not result:
                return None
            # bzpopmin returns (queue_name, member, score)
            job_id = result[1]
        else:
            job_id, score = result[0]

            # Check if job is delayed
            if score > self.MAX_PRIORITY:
                # Score contains timestamp component
                scheduled_ts = score - (self.MAX_PRIORITY - 50)  # Approximate
                if scheduled_ts > now.timestamp():
                    # Job not ready yet, wait
                    await self._redis._client.close()
                    return None

            # Remove from queue
            await self._redis._client.zrem(queue_key, job_id)

        # Move to processing queue
        await self._redis._client.zadd(self._processing_key(), {job_id: now.timestamp()})

        # Get job payload
        payload_str = await self._redis._client.get(self._job_key(job_id))
        if not payload_str:
            logger.warning(f'Job {job_id} payload not found')
            return None

        payload = json.loads(payload_str)
        payload['attempt'] += 1

        # Update job status in database
        if self.db:
            await self._update_job_status(
                job_id,
                JobStatus.PROCESSING,
                started_at=now,
            )

        # Update payload with new attempt
        await self._redis._client.set(self._job_key(job_id), json.dumps(payload))

        logger.info(f'Dequeued job {job_id} ({job_type.value}), attempt {payload["attempt"]}')

        return payload

    async def complete(
        self,
        job_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark job as completed.

        Removes from processing queue and cleans up job data.

        Args:
            job_id: Job to complete
            result: Optional result data to store
        """
        # Remove from processing queue
        await self._redis._client.zrem(self._processing_key(), job_id)

        # Update database
        if self.db:
            await self._update_job_status(
                job_id,
                JobStatus.COMPLETED,
                completed_at=datetime.utcnow(),
                result_data=result,
            )

        # Clean up job payload (after a delay for debugging)
        # In production, you might want to keep this longer
        await self._redis._client.expire(self._job_key(job_id), 3600)  # 1 hour

        logger.info(f'Job {job_id} completed')

    async def fail(
        self,
        job_id: str,
        error: str,
        retry: bool = True,
    ) -> None:
        """
        Mark job as failed.

        If retry=True and attempts < max, re-queues with exponential backoff.
        Otherwise, moves to dead letter queue.

        Args:
            job_id: Job that failed
            error: Error message
            retry: Whether to attempt retry
        """
        # Remove from processing queue
        await self._redis._client.zrem(self._processing_key(), job_id)

        # Get job payload
        payload_str = await self._redis._client.get(self._job_key(job_id))
        if not payload_str:
            logger.warning(f'Job {job_id} payload not found for failure handling')
            return

        payload = json.loads(payload_str)
        attempt = payload.get('attempt', 1)
        max_attempts = 3  # Default

        # Validate snapshot_id before retry - don't retry if UUID is invalid
        snapshot_id = payload.get('snapshot_id', '')
        try:
            UUID(snapshot_id)
            is_valid_uuid = True
        except (ValueError, AttributeError, TypeError):
            is_valid_uuid = False
            logger.error(f'Job {job_id} has invalid snapshot_id: {snapshot_id}, skipping retry')

        if retry and attempt < max_attempts and is_valid_uuid:
            # Calculate backoff delay
            delay = ReviewJob.calculate_backoff(attempt)

            # Re-queue with delay
            job_type = JobType(payload['job_type'])
            await self.enqueue(
                job_type=job_type,
                snapshot_id=payload['snapshot_id'],
                priority=payload['priority'],
                delay_seconds=delay,
                metadata=payload.get('metadata'),
            )

            # Update job status
            if self.db:
                await self._update_job_status(
                    job_id,
                    JobStatus.RETRYING,
                    error_message=error,
                )

            logger.warning(
                f'Job {job_id} failed (attempt {attempt}), '
                f'retrying in {delay}s: {error}'
            )
        else:
            # Move to dead letter queue
            await self._redis._client.zadd(
                self._dead_letter_key(),
                {job_id: datetime.utcnow().timestamp()},
            )

            # Update job status
            if self.db:
                await self._update_job_status(
                    job_id,
                    JobStatus.DEAD,
                    error_message=error,
                    completed_at=datetime.utcnow(),
                )

            logger.error(f'Job {job_id} moved to dead letter queue: {error}')

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get job payload by ID."""
        payload_str = await self._redis._client.get(self._job_key(job_id))
        if payload_str:
            return json.loads(payload_str)
        return None

    async def get_queue_stats(self) -> dict[str, Any]:
        """
        Get statistics for all queues.

        Returns:
            Dict with queue sizes and processing count
        """
        stats = {
            'queues': {},
            'processing': 0,
            'dead_letter': 0,
        }

        # Count each job type queue
        for job_type in JobType:
            queue_key = self._queue_key(job_type.value)
            count = await self._redis._client.zcard(queue_key)
            stats['queues'][job_type.value] = count

        # Count processing queue
        stats['processing'] = await self._redis._client.zcard(self._processing_key())

        # Count dead letter queue
        stats['dead_letter'] = await self._redis._client.zcard(self._dead_letter_key())

        return stats

    async def get_dead_letter_jobs(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get jobs from dead letter queue.

        Args:
            limit: Maximum number of jobs to return

        Returns:
            List of failed job payloads
        """
        job_ids = await self._redis._client.zrange(
            self._dead_letter_key(),
            0,
            limit - 1,
        )

        jobs = []
        for job_id in job_ids:
            payload = await self.get_job(job_id)
            if payload:
                jobs.append(payload)

        return jobs

    async def retry_dead_letter_job(self, job_id: str) -> str | None:
        """
        Retry a job from dead letter queue.

        Args:
            job_id: Job to retry

        Returns:
            New job_id if successful, None otherwise
        """
        # Get job payload
        payload = await self.get_job(job_id)
        if not payload:
            return None

        # Remove from dead letter queue
        await self._redis._client.zrem(self._dead_letter_key(), job_id)

        # Re-enqueue with high priority
        new_job_id = await self.enqueue(
            job_type=JobType(payload['job_type']),
            snapshot_id=payload['snapshot_id'],
            priority=90,  # High priority for retry
            metadata=payload.get('metadata'),
        )

        logger.info(f'Retrying dead letter job {job_id} as {new_job_id}')
        return new_job_id

    async def _update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        error_message: str | None = None,
        result_data: dict | None = None,
    ) -> None:
        """Update job status in database."""
        if not self.db:
            return

        result = await self.db.execute(
            select(ReviewJob).where(ReviewJob.id == job_id)
        )
        job = result.scalar_one_or_none()

        if job:
            job.status = status.value
            if started_at:
                job.started_at = started_at
            if completed_at:
                job.completed_at = completed_at
            if error_message:
                job.error_message = error_message
            if result_data:
                job.result_data = result_data

            await self.db.flush()

    async def cleanup_stale_processing(
        self,
        timeout_minutes: int = 30,
    ) -> int:
        """
        Move stale processing jobs back to queue.

        Jobs stuck in processing longer than timeout are considered stale.

        Args:
            timeout_minutes: How long before a job is considered stale

        Returns:
            Number of jobs requeued
        """
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        cutoff_score = cutoff.timestamp()

        # Get stale jobs
        stale_job_ids = await self._redis._client.zrangebyscore(
            self._processing_key(),
            '-inf',
            cutoff_score,
        )

        count = 0
        for job_id in stale_job_ids:
            payload = await self.get_job(job_id)
            if payload:
                # Remove from processing
                await self._redis._client.zrem(self._processing_key(), job_id)

                # Re-enqueue
                job_type = JobType(payload['job_type'])
                await self.enqueue(
                    job_type=job_type,
                    snapshot_id=payload['snapshot_id'],
                    priority=payload['priority'],
                    metadata=payload.get('metadata'),
                )
                count += 1

                logger.warning(f'Requeued stale job {job_id}')

        return count
