"""
Script to clean up jobs with invalid UUIDs from Redis queues.

Usage:
    python scripts/cleanup_invalid_jobs.py
"""

import asyncio
import json
from uuid import UUID

from infra.redis_client import redis_client
from models.review_job import JobType


async def is_valid_uuid(value: str) -> bool:
    """Check if string is a valid UUID."""
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def cleanup_invalid_jobs():
    """Remove jobs with invalid snapshot_ids from all queues."""
    await redis_client.connect()

    removed_count = 0

    # Check all job type queues
    queues_to_check = [
        f'codelinger:queue:{job_type.value}' for job_type in JobType
    ] + [
        'codelinger:queue:processing',
        'codelinger:queue:dead_letter',
    ]

    for queue_key in queues_to_check:
        print(f'\nChecking queue: {queue_key}')

        job_ids = await redis_client._client.zrange(queue_key, 0, -1)
        print(f'  Found {len(job_ids)} jobs')

        for job_id in job_ids:
            job_key = f'codelinger:job:{job_id}'
            job_data_str = await redis_client._client.get(job_key)

            if job_data_str:
                try:
                    job_data = json.loads(job_data_str)
                    snapshot_id = job_data.get('snapshot_id', '')

                    if not await is_valid_uuid(snapshot_id):
                        print(f'  Removing invalid job: {job_id}')
                        print(f'    Invalid snapshot_id: {snapshot_id}')

                        # Remove from queue
                        await redis_client._client.zrem(queue_key, job_id)
                        # Remove job data
                        await redis_client._client.delete(job_key)
                        removed_count += 1
                except json.JSONDecodeError:
                    print(f'  Warning: Could not parse job data for {job_id}')
            else:
                print(f'  Warning: Job {job_id} has no data, removing from queue')
                await redis_client._client.zrem(queue_key, job_id)
                removed_count += 1

    print(f'\nCleanup complete. Removed {removed_count} invalid jobs.')


if __name__ == '__main__':
    asyncio.run(cleanup_invalid_jobs())
