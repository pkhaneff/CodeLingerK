"""
Webhook Controller - Business logic for GitHub webhooks
"""

from pathlib import Path
from typing import Dict, Any
from fastapi import HTTPException
import os

from core.logging_config import get_logger
from core.change_extractor import ChangeExtractor
from core.graph import Neo4jManager
from core.graph.ingestion import GraphIngestion
from api.models import (
    GitHubPushPayload,
    GitHubPRPayload,
    ReviewResponse,
    ChangeUnitResponse
)

logger = get_logger(__name__)

class WebhookController:
    """Controller for handling GitHub webhook events"""

    def __init__(self, repo_path: str = "."):
        self.repo_path = Path(repo_path)

        # Initialize Neo4j
        self.neo4j = Neo4jManager(
            uri=os.getenv("NEO4J_URI"),
            user=os.getenv("NEO4J_USERNAME"),
            password=os.getenv("NEO4J_PASSWORD")
        )

        # Initialize ingestion pipeline
        self.ingestion = GraphIngestion(self.neo4j)

    def handle_push(self, payload: GitHubPushPayload) -> ReviewResponse:
        """
        Handle GitHub push event

        Args:
            payload: GitHub push webhook payload

        Returns:
            ReviewResponse with detected changes
        """
        logger.info(f"Push event: {payload.repository.full_name}, commits: {len(payload.commits)}")

        try:
            extractor = ChangeExtractor(str(self.repo_path))

            change_units = extractor.extract_changes(
                mode="commit",
                commit_sha=payload.after
            )

            # Ingest into Neo4j
            self.ingestion.ingest_commit(change_units, payload.after)

            changes = self._convert_to_response(change_units)

            # Log detected changes
            for unit in change_units:
                symbol = unit.new_symbol or unit.old_symbol
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

    def handle_pull_request(self, payload: GitHubPRPayload) -> Dict[str, Any] | ReviewResponse:
        """
        Handle GitHub pull request event

        Args:
            payload: GitHub PR webhook payload

        Returns:
            ReviewResponse with detected changes or skip message
        """
        logger.info(f"PR event: {payload.action} - PR #{payload.number}")

        # Only process specific actions
        if payload.action not in ["opened", "reopened", "synchronize"]:
            return {"status": "skipped", "reason": f"Action '{payload.action}' not processed"}

        try:
            extractor = ChangeExtractor(str(self.repo_path))

            base_branch = payload.pull_request.base["ref"]
            head_sha = payload.pull_request.head["sha"]

            change_units = extractor.extract_changes(
                mode="branch",
                base_branch=base_branch,
                compare_branch=head_sha
            )

            # Ingest into Neo4j
            self.ingestion.ingest_pr(change_units, payload.number)

            changes = self._convert_to_response(change_units)

            # Log detected changes
            for unit in change_units:
                symbol = unit.new_symbol or unit.old_symbol
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

    def handle_generic_event(self, event_type: str) -> Dict[str, str]:
        """
        Handle generic GitHub events

        Args:
            event_type: Type of GitHub event

        Returns:
            Response dict
        """
        logger.info(f"GitHub event: {event_type}")

        if event_type == "ping":
            return {"status": "pong"}

        return {
            "status": "received",
            "event_type": event_type,
            "message": f"Event '{event_type}' acknowledged"
        }

    def _convert_to_response(self, change_units) -> list[ChangeUnitResponse]:
        """
        Convert ChangeUnits to API response format

        Args:
            change_units: List of ChangeUnit domain models

        Returns:
            List of ChangeUnitResponse DTOs
        """
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
        return changes
