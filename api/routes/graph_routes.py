"""
Graph Routes - API endpoints for graph queries and visualization
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import os

from api.controllers.graph_controller import GraphController
from core.graph import Neo4jManager

router = APIRouter(prefix="/graph", tags=["graph"])

# Initialize Neo4j (use environment variables or config)
neo4j = Neo4jManager(
    uri=os.getenv("NEO4J_URI"),
    user=os.getenv("NEO4J_USER"),
    password=os.getenv("NEO4J_PASSWORD")
)

controller = GraphController(neo4j)


@router.get("/pr/{pr_number}")
async def get_pr_graph(pr_number: int):
    """
    Get graph visualization data for a PR

    Returns nodes and edges for D3.js/Cytoscape visualization
    """
    try:
        return controller.get_pr_graph(pr_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pr/{pr_number}/changes")
async def get_pr_changes(pr_number: int):
    """Get all symbol changes in a PR"""
    try:
        changes = controller.get_pr_changes(pr_number)
        return {
            "pr_number": pr_number,
            "changes": changes,
            "total": len(changes)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/function/{function_name}/dependencies")
async def get_function_dependencies(
    function_name: str,
    file_path: Optional[str] = Query(None, description="File path to narrow search")
):
    """Get all functions called by a specific function"""
    try:
        return controller.get_function_dependencies(function_name, file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_stats():
    """Get database statistics"""
    try:
        return controller.get_database_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_symbols(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, description="Max results")
):
    """Search for symbols by name"""
    try:
        results = controller.search_symbols(q, limit)
        return {
            "query": q,
            "results": results,
            "total": len(results)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
