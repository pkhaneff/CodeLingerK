"""
Activity API routes - endpoints for fetching activities.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.auth.api.middleware import get_current_user
from apps.auth.models.user import User
from infra.database import get_db
from apps.activities.services.activity_service import activity_service

router = APIRouter(tags=['Activities'])


# ─────────────────────────────────────────────────────────────
# Response Schemas
# ─────────────────────────────────────────────────────────────

class ActivityResponseItem(BaseModel):
    """Activity response item model."""
    id: str
    type: str
    status: str
    title: str
    description: str
    repo_name: str | None
    created_at: str | None
    action_url: str | None

    class Config:
        from_attributes = True


class PaginationInfo(BaseModel):
    """Pagination metadata model."""
    total: int
    page: int
    limit: int
    has_more: bool


class ActivityFeedResponse(BaseModel):
    """Paginated activity feed response model."""
    success: bool
    data: list[ActivityResponseItem]
    pagination: PaginationInfo


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@router.get('', response_model=ActivityFeedResponse)
async def get_activities(
    page: int = Query(1, ge=1, description='Page number (1-indexed)'),
    limit: int = Query(10, ge=1, le=100, description='Records per page'),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get user's recent activity feed.

    Only retrieves activities from active repositories (is_active = True).
    """
    activities, total = await activity_service.list_activities(
        db=db,
        owner_id=user.id,
        page=page,
        limit=limit,
    )

    data = [
        ActivityResponseItem(
            id=act.id,
            type=act.type,
            status=act.status,
            title=act.title,
            description=act.description,
            repo_name=act.repository.name if act.repository else None,
            created_at=act.created_at.isoformat() if act.created_at else None,
            action_url=act.action_url,
        )
        for act in activities
    ]

    has_more = total > (page * limit)

    return ActivityFeedResponse(
        success=True,
        data=data,
        pagination=PaginationInfo(
            total=total,
            page=page,
            limit=limit,
            has_more=has_more,
        ),
    )
