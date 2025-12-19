"""
CodeLingerK - AI-Powered Code Review System
Moc 1: The Skeleton - FastAPI Webhook Server
"""

from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
import uvicorn

from core.logging_config import setup_logging, get_logger
from core.change_extractor import ChangeExtractor
from api.models import (
    GitHubPushPayload,
    GitHubPRPayload,
    ReviewResponse,
    ChangeUnitResponse
)

setup_logging(level="INFO")
logger = get_logger(__name__)

app = FastAPI(
    title="CodeLingerK",
    description="AI-Powered Code Review System - Moc 1",
    version="0.1.0"
)


@app.get("/")
async def root():
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


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/webhook/github/push")
async def github_push(payload: GitHubPushPayload):
    logger.info(f"Push event: {payload.repository.full_name}, commits: {len(payload.commits)}")

    try:
        repo_path = Path(".")
        extractor = ChangeExtractor(str(repo_path))

        change_units = extractor.extract_changes(
            mode="commit",
            commit_sha=payload.after
        )

        changes = []
        for unit in change_units:
            symbol = unit.new_symbol or unit.old_symbol
            change = ChangeUnitResponse(
                file_path=unit.file_path,
                change_type=unit.change_type,
                symbol_name=symbol.name if symbol else None,
                symbol_type=symbol.type if symbol else None,
                lines=f"{symbol.line_start}-{symbol.line_end}" if symbol else None
            )
            changes.append(change)

            if symbol:
                logger.info(f"{unit.change_type.upper()}: {symbol.type} '{symbol.name}' in {unit.file_path}")

        return ReviewResponse(
            status="success",
            commit_sha=payload.after,
            changes_detected=len(change_units),
            changes=changes,
            message=f"Detected {len(change_units)} code changes"
        )

    except Exception as e:
        logger.error(f"Error processing push: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/github/pull_request")
async def github_pr(payload: GitHubPRPayload):
    logger.info(f"PR event: {payload.action} - PR #{payload.number}")

    if payload.action not in ["opened", "reopened", "synchronize"]:
        return {"status": "skipped", "reason": f"Action '{payload.action}' not processed"}

    try:
        repo_path = Path(".")
        extractor = ChangeExtractor(str(repo_path))

        base_branch = payload.pull_request.base["ref"]
        head_sha = payload.pull_request.head["sha"]

        change_units = extractor.extract_changes(
            mode="branch",
            base_branch=base_branch,
            compare_branch=head_sha
        )

        changes = []
        for unit in change_units:
            symbol = unit.new_symbol or unit.old_symbol
            change = ChangeUnitResponse(
                file_path=unit.file_path,
                change_type=unit.change_type,
                symbol_name=symbol.name if symbol else None,
                symbol_type=symbol.type if symbol else None,
                lines=f"{symbol.line_start}-{symbol.line_end}" if symbol else None
            )
            changes.append(change)

            if symbol:
                logger.info(f"{unit.change_type.upper()}: {symbol.type} '{symbol.name}' in {unit.file_path}")

        return ReviewResponse(
            status="success",
            commit_sha=head_sha,
            changes_detected=len(change_units),
            changes=changes,
            message=f"PR #{payload.number}: Detected {len(change_units)} changes"
        )

    except Exception as e:
        logger.error(f"Error processing PR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/github")
async def github_generic(request: Request):
    event_type = request.headers.get("X-GitHub-Event")

    if not event_type:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    logger.info(f"GitHub event: {event_type}")

    if event_type == "ping":
        return {"status": "pong"}

    return {
        "status": "received",
        "event_type": event_type,
        "message": f"Event '{event_type}' acknowledged"
    }


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("CodeLingerK Server - Moc 1")
    logger.info("Starting on http://0.0.0.0:8000")
    logger.info("API docs: http://localhost:8000/docs")
    logger.info("=" * 60)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
