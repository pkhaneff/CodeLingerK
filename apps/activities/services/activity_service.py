"""
Activity service - Handles creating, logging, and listing activities.
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from apps.activities.models.activity import Activity
from apps.repositories.models.repository import Repository


class ActivityService:
    """Service class for managing activities."""

    async def create_activity(
        self,
        db: AsyncSession,
        owner_id: str,
        repo_id: str,
        type: str,
        status: str,
        title: str,
        description: str,
        action_url: str | None = None,
    ) -> Activity:
        """
        Create and persist a new activity log.

        Args:
            db: Database session
            owner_id: User ID owning the activity
            repo_id: Repository ID where the event occurred
            type: enum type string (repo_connected, webhook_added, security_scan, pr_review)
            status: status string (success, warning, error, info)
            title: title of the activity
            description: details about the activity
            action_url: redirect link for frontend redirection

        Returns:
            The created Activity model
        """
        activity = Activity(
            owner_id=owner_id,
            repo_id=repo_id,
            type=type,
            status=status,
            title=title,
            description=description,
            action_url=action_url,
        )
        db.add(activity)
        await db.commit()
        await db.refresh(activity)
        return activity

    async def list_activities(
        self,
        db: AsyncSession,
        owner_id: str,
        page: int = 1,
        limit: int = 10,
    ) -> tuple[list[Activity], int]:
        """
        List paginated activities for the user, only returning activities
        associated with currently active repositories (is_active = True).

        Args:
            db: Database session
            owner_id: User ID
            page: page number (1-indexed)
            limit: items per page

        Returns:
            A tuple of (list of activities, total count)
        """
        offset = (page - 1) * limit

        # Filter by owner_id and ensure joined repository is currently active
        base_query = (
            select(Activity)
            .join(Repository, Activity.repo_id == Repository.id)
            .where(
                Activity.owner_id == owner_id,
                Repository.is_active == True,
            )
            .options(joinedload(Activity.repository))
        )

        # Get total count of matching activities
        count_query = select(func.count()).select_from(base_query.subquery())
        count_result = await db.execute(count_query)
        total = count_result.scalar_one()

        # Get activities ordered by newest first
        query = (
            base_query.order_by(Activity.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(query)
        activities = list(result.scalars().all())

        return activities, total


# Singleton instance
activity_service = ActivityService()
