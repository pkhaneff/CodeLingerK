"""
Webhook Routes - Define API endpoints
"""

from fastapi import APIRouter, HTTPException, Request

from api.models import GitHubPushPayload, GitHubPRPayload
from api.controllers import WebhookController

router = APIRouter(prefix="/webhook", tags=["webhooks"])
controller = WebhookController()


@router.post("/github/push")
async def github_push(payload: GitHubPushPayload):
    """Handle GitHub push event webhook"""
    return controller.handle_push(payload)


@router.post("/github/pull_request")
async def github_pull_request(payload: GitHubPRPayload):
    """Handle GitHub pull request event webhook"""
    return controller.handle_pull_request(payload)


@router.post("/github")
async def github_generic(request: Request):
    """Handle generic GitHub webhook events"""
    event_type = request.headers.get("X-GitHub-Event")

    if not event_type:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    return controller.handle_generic_event(event_type)
