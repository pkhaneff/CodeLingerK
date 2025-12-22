"""
Health Routes - System health and status endpoints
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
async def root():
    """Root endpoint with service info"""
    return {
        "service": "CodeLingerK",
        "status": "running",
        "stage": "Moc 1 - The Skeleton",
        "capabilities": [
            "Parse code changes",
            "Detect modified functions/classes",
            "GitHub webhook integration"
        ]
    }


@router.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}
