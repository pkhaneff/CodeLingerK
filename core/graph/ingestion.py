"""
Graph Ingestion - Convert ChangeUnits to Neo4j graph
"""

from typing import List
from pathlib import Path

from core.logging_config import get_logger
from core.models import ChangeUnit, CodeSymbol
from core.graph.neo4j_manager import Neo4jManager
from core.analyzers import RelationshipAnalyzer, LayerClassifier

logger = get_logger(__name__)


class GraphIngestion:
    """Convert ChangeUnits to Neo4j graph with relationships"""

    def __init__(self, neo4j_manager: Neo4jManager):
        """
        Initialize ingestion pipeline

        Args:
            neo4j_manager: Neo4j connection manager
        """
        self.neo4j = neo4j_manager
        self.analyzer = RelationshipAnalyzer()
        self.classifier = LayerClassifier()

    def ingest_change_units(
        self,
        change_units: List[ChangeUnit],
        version_id: str,
        version_type: str = "pr"
    ):
        """
        Ingest change units into Neo4j graph

        Args:
            change_units: List of ChangeUnit from ChangeExtractor
            version_id: Version identifier (e.g., 'PR-123')
            version_type: Type of version ('pr', 'commit', 'branch')
        """
        logger.info(f"Ingesting {len(change_units)} change units for {version_id}")

        # Create version node
        self.neo4j.create_version_node(
            version_id=version_id,
            version_type=version_type
        )

        # Process each change unit
        for unit in change_units:
            try:
                self._ingest_change_unit(unit, version_id)
            except Exception as e:
                logger.error(f"Error ingesting change unit for {unit.file_path}: {e}", exc_info=True)

        logger.info(f"Ingestion completed for {version_id}")

    def _ingest_change_unit(self, unit: ChangeUnit, version_id: str):
        """
        Ingest a single change unit

        Args:
            unit: ChangeUnit to ingest
            version_id: Version identifier
        """
        # Create container node for file
        self._create_container(unit.file_path)

        # Ingest symbol
        symbol = unit.new_symbol or unit.old_symbol
        if symbol:
            symbol_uid = self._create_symbol(symbol, unit.file_path)

            # Link symbol to container
            self.neo4j.create_contains_relationship(
                parent_uid=unit.file_path,
                child_uid=symbol_uid,
                parent_label="Container",
                child_label="Symbol"
            )

            # Link symbol to version
            self.neo4j.create_modified_in_relationship(
                symbol_uid=symbol_uid,
                version_id=version_id,
                change_type=unit.change_type
            )

            # Extract and create relationships
            self._extract_relationships(unit, symbol_uid)

    def _create_container(self, file_path: str):
        """
        Create container node for file

        Args:
            file_path: Path to file
        """
        # Determine language from extension
        ext = Path(file_path).suffix
        language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.go': 'go',
        }
        language = language_map.get(ext)

        self.neo4j.create_container_node(
            path=file_path,
            container_type='file',
            language=language
        )

    def _create_symbol(self, symbol: CodeSymbol, file_path: str) -> str:
        """
        Create symbol node

        Args:
            symbol: CodeSymbol to create
            file_path: File containing the symbol

        Returns:
            Symbol UID
        """
        # Generate UID: file_path:name:line_start
        uid = f"{file_path}:{symbol.name}:{symbol.line_start}"

        # Classify layer
        layer = self.classifier.classify(file_path)

        # Create symbol node
        self.neo4j.create_symbol_node(
            uid=uid,
            name=symbol.name,
            symbol_type=symbol.type.lower(),
            body_hash=symbol.body_hash,
            line_start=symbol.line_start,
            line_end=symbol.line_end,
            language="python",
            layer=layer
        )

        return uid

    def _extract_relationships(self, unit: ChangeUnit, symbol_uid: str):
        """
        Extract and create relationships for a symbol

        Args:
            unit: ChangeUnit containing the symbol
            symbol_uid: Symbol UID
        """
        # Analyze relationships
        relationships = self.analyzer.analyze_change_unit(unit)

        # Create function call relationships
        for call in relationships.calls:
            # Create callee symbol UID (simplified - might not exist yet)
            # In production, this needs a symbol resolution mechanism
            callee_uid = f"{unit.file_path}:{call.callee}:0"

            try:
                self.neo4j.create_calls_relationship(
                    caller_uid=symbol_uid,
                    callee_uid=callee_uid,
                    line=call.line
                )
                logger.debug(f"Created CALLS: {call.caller} -> {call.callee}")
            except Exception as e:
                logger.debug(f"Could not create CALLS relationship: {e}")

        # Create variable nodes
        symbol = unit.new_symbol or unit.old_symbol
        if symbol:
            for var_name in relationships.variables:
                var_uid = f"{unit.file_path}:{symbol.name}:{var_name}"

                try:
                    self.neo4j.create_variable_node(
                        uid=var_uid,
                        name=var_name,
                        is_global=False,
                        is_param=False,
                        scope="local"
                    )
                except Exception:
                    pass  # Variable might already exist

    def ingest_pr(self, change_units: List[ChangeUnit], pr_number: int):
        """
        Convenience method to ingest a PR

        Args:
            change_units: List of changes in PR
            pr_number: PR number
        """
        version_id = f"PR-{pr_number}"
        self.ingest_change_units(change_units, version_id, version_type="pr")

    def ingest_commit(self, change_units: List[ChangeUnit], commit_sha: str):
        """
        Convenience method to ingest a commit

        Args:
            change_units: List of changes in commit
            commit_sha: Commit SHA
        """
        version_id = f"commit-{commit_sha[:7]}"
        self.ingest_change_units(change_units, version_id, version_type="commit")
