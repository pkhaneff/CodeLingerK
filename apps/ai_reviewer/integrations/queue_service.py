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

from core.logger import get_logger
from infra.redis_client import redis_client
from apps.ai_reviewer.models.review_job import ReviewJob, JobStatus, JobType
from core.exceptions import ConflictException, ErrorCode

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

        # Redis Lock & Queue Depth limits check on start of pipeline (JobType.CONTEXT)
        if job_type == JobType.CONTEXT and self.db:
            from sqlalchemy.orm import joinedload
            from apps.ai_reviewer.models.snapshot import Snapshot

            stmt = (
                select(Snapshot)
                .options(joinedload(Snapshot.pull_request))
                .where(Snapshot.id == snapshot_id)
            )
            res = await self.db.execute(stmt)
            snapshot = res.scalar_one_or_none()
            if snapshot and snapshot.pull_request:
                pr = snapshot.pull_request
                repo_id = str(pr.repository_id)
                pr_number = pr.pr_number
                head_sha = snapshot.commit_sha

                # Check Queue Depth Limit (CONTEXT queue only)
                waiting_count = await self._redis._client.zcard(self._queue_key(JobType.CONTEXT.value))
                if waiting_count >= 20:
                    logger.warning(f"Queue depth limit reached for CONTEXT queue: {waiting_count}/20")
                    raise ConflictException(
                        message="Hàng đợi của hệ thống hiện đang đầy, vui lòng thử lại sau."
                    )

                # Check Redis Lock
                lock_key = f"review_lock:repo:{repo_id}:pr:{pr_number}:sha:{head_sha}"
                acquired = await self._redis._client.set(
                    self._redis._key(lock_key),
                    "locked",
                    ex=1800,  # 30 minutes TTL
                    nx=True   # Set if not exists
                )
                if not acquired:
                    logger.warning(f"Duplicate job prevented. Lock key exists: {lock_key}")
                    raise ConflictException(
                        message="Một tiến trình review cho Commit SHA này của PR đang được xử lý, vui lòng chờ."
                    )

                if not metadata:
                    metadata = {}
                metadata['lock_key'] = lock_key
                metadata['repo_id'] = repo_id

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
        Get next job from queue.
        Implements concurrent limiting per repository for CONTEXT jobs.
        """
        queue_key = self._queue_key(job_type.value)
        now = datetime.utcnow()

        # Get top 10 items to find a ready job that satisfies concurrent limits
        result = await self._redis._client.zrange(
            queue_key,
            0,
            9,
            withscores=True,
        )

        job_id = None
        payload = None

        if result:
            for item in result:
                candidate_id, score = item
                
                # Check if job is delayed
                if score > self.MAX_PRIORITY:
                    scheduled_ts = score - (self.MAX_PRIORITY - 50)
                    if scheduled_ts > now.timestamp():
                        # Delayed job is not ready yet, skip it
                        continue

                # Load candidate payload
                candidate_payload_str = await self._redis._client.get(self._job_key(candidate_id))
                if not candidate_payload_str:
                    # Clean up orphaned job ID in queue
                    await self._redis._client.zrem(queue_key, candidate_id)
                    continue

                candidate_payload = json.loads(candidate_payload_str)
                
                # Apply concurrent limit ONLY to JobType.CONTEXT (start of pipeline)
                if job_type == JobType.CONTEXT:
                    repo_id = candidate_payload.get('metadata', {}).get('repo_id')
                    if repo_id:
                        running_key = self._redis._key(f"repo:{repo_id}:running_jobs")
                        running_count = await self._redis._client.scard(running_key)
                        
                        # Limit is 2 concurrent reviews per repo
                        if running_count >= 2:
                            # Skip this job for now (Fair scheduling)
                            continue
                            
                # If we get here, the job is ready and does not violate concurrent limits
                job_id = candidate_id
                payload = candidate_payload
                break

        # If no suitable job found in top 10, check if queue is completely empty
        if not job_id:
            # If there are items in the queue but all are blocked by limits, return None (don't block on bzpopmin!)
            queue_size = await self._redis._client.zcard(queue_key)
            if queue_size > 0:
                return None

            # Queue is empty, block waiting for new jobs
            blocking_result = await self._redis._client.bzpopmin(queue_key, timeout=timeout)
            if not blocking_result:
                return None
                
            # bzpopmin returns (queue_name, member, score)
            job_id = blocking_result[1]
            payload_str = await self._redis._client.get(self._job_key(job_id))
            if not payload_str:
                return None
            payload = json.loads(payload_str)

        # Move from queue to processing queue
        await self._redis._client.zrem(queue_key, job_id)
        await self._redis._client.zadd(self._processing_key(), {job_id: now.timestamp()})

        # Track active concurrent job
        repo_id = payload.get('metadata', {}).get('repo_id')
        snapshot_id = payload.get('snapshot_id')
        if repo_id and snapshot_id:
            running_key = self._redis._key(f"repo:{repo_id}:running_jobs")
            await self._redis._client.sadd(running_key, snapshot_id)
            await self._redis._client.expire(running_key, 1800)  # 30 mins TTL

        payload['attempt'] += 1

        # Update job status in database
        if self.db:
            await self._update_job_status(
                job_id,
                JobStatus.PROCESSING,
                started_at=now,
            )

        # Update payload in Redis
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

        # Release lock and running jobs set when final PUBLISH job completes
        payload = await self.get_job(job_id)
        if payload:
            metadata = payload.get('metadata', {})
            lock_key = metadata.get('lock_key')
            repo_id = metadata.get('repo_id')
            snapshot_id = payload.get('snapshot_id')
            job_type = payload.get('job_type')

            if job_type == JobType.PUBLISH.value:
                if lock_key:
                    await self._redis._client.delete(self._redis._key(lock_key))
                    logger.info(f"Released review lock: {lock_key} (Pipeline completed)")
                if repo_id and snapshot_id:
                    running_key = self._redis._key(f"repo:{repo_id}:running_jobs")
                    await self._redis._client.srem(running_key, snapshot_id)
                    logger.info(f"Removed snapshot {snapshot_id[:8]} from repo running jobs (Pipeline completed)")

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

        # Check snapshot existence in database to prevent foreign key violation on enqueue/retry
        snapshot_exists = True
        if self.db and is_valid_uuid:
            try:
                from apps.ai_reviewer.models.snapshot import Snapshot
                result = await self.db.execute(
                    select(Snapshot).where(Snapshot.id == snapshot_id)
                )
                snapshot_exists = result.scalar_one_or_none() is not None
                if not snapshot_exists:
                    logger.error(f"Snapshot {snapshot_id} not found in database. Disabling retry to prevent foreign key integrity error.")
                    retry = False
            except Exception as se:
                logger.error(f"Error checking snapshot existence for {snapshot_id}: {se}")

        if retry and attempt < max_attempts and is_valid_uuid and snapshot_exists:
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

            # Release lock and concurrent job set on permanent failure
            metadata = payload.get('metadata', {})
            lock_key = metadata.get('lock_key')
            repo_id = metadata.get('repo_id')
            snapshot_id = payload.get('snapshot_id')

            if lock_key:
                await self._redis._client.delete(self._redis._key(lock_key))
                logger.info(f"Released review lock on permanent failure: {lock_key}")
            if repo_id and snapshot_id:
                running_key = self._redis._key(f"repo:{repo_id}:running_jobs")
                await self._redis._client.srem(running_key, snapshot_id)
                logger.info(f"Removed snapshot {snapshot_id[:8]} from repo running jobs on permanent failure")

            # Update job status
            if self.db:
                await self._update_job_status(
                    job_id,
                    JobStatus.DEAD,
                    error_message=error,
                    completed_at=datetime.utcnow(),
                )
                
                # Mark corresponding snapshot as failed as well
                if is_valid_uuid:
                    try:
                        from apps.ai_reviewer.models.snapshot import Snapshot
                        snapshot_res = await self.db.execute(
                             select(Snapshot).where(Snapshot.id == snapshot_id)
                        )
                        snapshot = snapshot_res.scalar_one_or_none()
                        if snapshot:
                            snapshot.status = 'failed'
                            snapshot.error_message = error
                            await self.db.flush()
                            await self._redis.publish_snapshot_status(snapshot_id, 'failed', error)
                            logger.info(f"Marked snapshot {snapshot_id[:8]} as failed because job {job_id} is dead")
                    except Exception as e:
                        logger.error(f"Error marking snapshot {snapshot_id} as failed: {e}")

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
