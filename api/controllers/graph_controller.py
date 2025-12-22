"""
Graph Controller - Handle graph queries and visualization
"""

from typing import Dict, Any, List, Optional

from core.logging_config import get_logger
from core.graph import Neo4jManager

logger = get_logger(__name__)


class GraphController:
    """Controller for graph operations and visualization"""

    def __init__(self, neo4j_manager: Neo4jManager):
        """
        Initialize graph controller

        Args:
            neo4j_manager: Neo4j connection manager
        """
        self.neo4j = neo4j_manager

    def get_pr_graph(self, pr_number: int) -> Dict[str, Any]:
        """
        Get graph visualization data for a PR

        Args:
            pr_number: PR number

        Returns:
            Dict with nodes and edges for visualization
        """
        logger.info(f"Fetching graph for PR #{pr_number}")

        graph_data = self.neo4j.get_pr_graph(pr_number)

        return {
            "pr_number": pr_number,
            "nodes": graph_data["nodes"],
            "edges": graph_data["edges"],
            "stats": {
                "total_nodes": len(graph_data["nodes"]),
                "total_edges": len(graph_data["edges"])
            }
        }

    def get_pr_changes(self, pr_number: int) -> List[Dict[str, Any]]:
        """
        Get all changes in a PR

        Args:
            pr_number: PR number

        Returns:
            List of changed symbols
        """
        logger.info(f"Fetching changes for PR #{pr_number}")

        changes = self.neo4j.get_pr_changes(pr_number)

        return changes

    def get_function_dependencies(self, function_name: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Get dependencies for a function

        Args:
            function_name: Function name
            file_path: Optional file path to narrow search

        Returns:
            Dict with function info and dependencies
        """
        logger.info(f"Fetching dependencies for function: {function_name}")

        # Search for function UID
        # In production, this needs better symbol resolution
        if file_path:
            function_uid = f"{file_path}:{function_name}:0"
        else:
            # Search by name
            query = """
            MATCH (f:Symbol {name: $name, type: 'function'})
            RETURN f.uid as uid
            LIMIT 1
            """
            results = self.neo4j.query(query, name=function_name)
            if not results:
                return {"error": f"Function '{function_name}' not found"}

            function_uid = results[0]["uid"]

        # Get dependencies
        deps = self.neo4j.get_function_dependencies(function_uid)

        return {
            "function": function_name,
            "uid": function_uid,
            "dependencies": deps,
            "dependency_count": len(deps)
        }

    def get_database_stats(self) -> Dict[str, Any]:
        """
        Get database statistics

        Returns:
            Dict with stats
        """
        stats = self.neo4j.get_stats()

        return {
            "stats": stats,
            "total_nodes": sum(stats.values())
        }

    def search_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search for symbols by name

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of matching symbols
        """
        cypher = """
        MATCH (s:Symbol)
        WHERE s.name CONTAINS $query
        RETURN s.uid as uid,
               s.name as name,
               s.type as type,
               s.layer as layer
        LIMIT $limit
        """

        results = self.neo4j.query(cypher, query=query, limit=limit)

        return results
