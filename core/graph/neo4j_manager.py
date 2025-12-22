
from typing import List, Dict, Any, Optional
from neo4j import GraphDatabase, Driver, Session
from contextlib import contextmanager

from core.logging_config import get_logger
from core.graph.schema import CoreSchema, PythonExtension, SchemaManager

logger = get_logger(__name__)


class Neo4jManager:
    """Manage Neo4j connection and CRUD operations"""

    def __init__(self, uri: str, user: str, password: str):
        """
        Initialize Neo4j connection

        Args:
            uri: Neo4j connection URI (e.g., 'bolt://localhost:7687')
            user: Database username
            password: Database password
        """
        self.uri = uri
        self.driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info(f"Connected to Neo4j at {uri}")

    def close(self):
        """Close database connection"""
        if self.driver:
            self.driver.close()
            logger.info("Neo4j connection closed")

    @contextmanager
    def session(self) -> Session:
        """Context manager for database session"""
        session = self.driver.session()
        try:
            yield session
        finally:
            session.close()

    def setup_schema(self):
        """
        Setup database schema (constraints and indexes)
        Should be called once on initialization
        """
        logger.info("Setting up Neo4j schema...")

        with self.session() as session:
            # Create constraints
            for constraint in SchemaManager.get_all_constraints():
                try:
                    session.run(constraint)
                    logger.debug(f"Created constraint: {constraint[:50]}...")
                except Exception as e:
                    logger.warning(f"Constraint already exists or error: {e}")

            # Create indexes
            for index in SchemaManager.get_all_indexes():
                try:
                    session.run(index)
                    logger.debug(f"Created index: {index[:50]}...")
                except Exception as e:
                    logger.warning(f"Index already exists or error: {e}")

        logger.info("Schema setup completed")

    # ========================================================================
    # Core Node Operations
    # ========================================================================

    def create_symbol_node(
        self,
        uid: str,
        name: str,
        symbol_type: str,
        body_hash: str,
        line_start: int,
        line_end: int,
        language: str = "python",
        **extra_props
    ) -> Dict[str, Any]:
        """
        Create a Symbol node

        Args:
            uid: Unique identifier (file_path:name:line_start)
            name: Symbol name
            symbol_type: function, class, method, variable
            body_hash: SHA256 hash of content
            line_start: Starting line number
            line_end: Ending line number
            language: Programming language
            **extra_props: Additional properties

        Returns:
            Created node properties
        """
        query = f"""
        MERGE (s:{CoreSchema.NODE_SYMBOL} {{uid: $uid}})
        ON CREATE SET
            s.name = $name,
            s.type = $symbol_type,
            s.body_hash = $body_hash,
            s.line_start = $line_start,
            s.line_end = $line_end,
            s.language = $language,
            s.created_at = datetime()
        ON MATCH SET
            s.body_hash = $body_hash,
            s.updated_at = datetime()
        RETURN s
        """

        params = {
            "uid": uid,
            "name": name,
            "symbol_type": symbol_type,
            "body_hash": body_hash,
            "line_start": line_start,
            "line_end": line_end,
            "language": language,
        }
        params.update(extra_props)

        with self.session() as session:
            result = session.run(query, params)
            record = result.single()
            return dict(record["s"]) if record else {}

    def create_container_node(
        self,
        path: str,
        container_type: str,
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a Container node (file or directory)

        Args:
            path: File or directory path
            container_type: 'file' or 'directory'
            language: Programming language (for files)

        Returns:
            Created node properties
        """
        query = f"""
        MERGE (c:{CoreSchema.NODE_CONTAINER} {{path: $path}})
        ON CREATE SET
            c.type = $container_type,
            c.language = $language,
            c.created_at = datetime()
        RETURN c
        """

        params = {
            "path": path,
            "container_type": container_type,
            "language": language,
        }

        with self.session() as session:
            result = session.run(query, params)
            record = result.single()
            return dict(record["c"]) if record else {}

    def create_version_node(
        self,
        version_id: str,
        version_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a Version node (PR, commit, branch)

        Args:
            version_id: Version identifier (e.g., 'PR-123', 'commit-abc')
            version_type: 'pr', 'commit', 'branch'
            metadata: Additional metadata

        Returns:
            Created node properties
        """
        query = f"""
        MERGE (v:{CoreSchema.NODE_VERSION} {{id: $version_id}})
        ON CREATE SET
            v.type = $version_type,
            v.created_at = datetime()
        RETURN v
        """

        params = {
            "version_id": version_id,
            "version_type": version_type,
        }

        if metadata:
            params.update(metadata)

        with self.session() as session:
            result = session.run(query, params)
            record = result.single()
            return dict(record["v"]) if record else {}

    # ========================================================================
    # Core Relationship Operations
    # ========================================================================

    def create_contains_relationship(
        self,
        parent_uid: str,
        child_uid: str,
        parent_label: str = CoreSchema.NODE_CONTAINER,
        child_label: str = CoreSchema.NODE_SYMBOL
    ):
        """
        Create CONTAINS relationship

        Args:
            parent_uid: Parent node identifier
            child_uid: Child node identifier
            parent_label: Parent node label
            child_label: Child node label
        """
        query = f"""
        MATCH (parent:{parent_label} {{uid: $parent_uid}})
        MATCH (child:{child_label} {{uid: $child_uid}})
        MERGE (parent)-[:{CoreSchema.REL_CONTAINS}]->(child)
        """

        # Handle Container nodes (use 'path' instead of 'uid')
        if parent_label == CoreSchema.NODE_CONTAINER:
            query = f"""
            MATCH (parent:{parent_label} {{path: $parent_uid}})
            MATCH (child:{child_label} {{uid: $child_uid}})
            MERGE (parent)-[:{CoreSchema.REL_CONTAINS}]->(child)
            """

        params = {
            "parent_uid": parent_uid,
            "child_uid": child_uid,
        }

        with self.session() as session:
            session.run(query, params)

    def create_references_relationship(
        self,
        from_uid: str,
        to_uid: str,
        **properties
    ):
        """
        Create REFERENCES relationship

        Args:
            from_uid: Source symbol UID
            to_uid: Target symbol UID
            **properties: Additional relationship properties
        """
        query = f"""
        MATCH (from:{CoreSchema.NODE_SYMBOL} {{uid: $from_uid}})
        MATCH (to:{CoreSchema.NODE_SYMBOL} {{uid: $to_uid}})
        MERGE (from)-[r:{CoreSchema.REL_REFERENCES}]->(to)
        SET r += $properties
        RETURN r
        """

        params = {
            "from_uid": from_uid,
            "to_uid": to_uid,
            "properties": properties,
        }

        with self.session() as session:
            session.run(query, params)

    def create_modified_in_relationship(
        self,
        symbol_uid: str,
        version_id: str,
        change_type: str
    ):
        """
        Create MODIFIED_IN relationship

        Args:
            symbol_uid: Symbol UID
            version_id: Version ID
            change_type: 'added', 'modified', 'deleted'
        """
        query = f"""
        MATCH (s:{CoreSchema.NODE_SYMBOL} {{uid: $symbol_uid}})
        MATCH (v:{CoreSchema.NODE_VERSION} {{id: $version_id}})
        MERGE (s)-[r:{CoreSchema.REL_MODIFIED_IN}]->(v)
        SET r.change_type = $change_type,
            r.timestamp = datetime()
        RETURN r
        """

        params = {
            "symbol_uid": symbol_uid,
            "version_id": version_id,
            "change_type": change_type,
        }

        with self.session() as session:
            session.run(query, params)

    # ========================================================================
    # Python Extension Operations
    # ========================================================================

    def create_calls_relationship(
        self,
        caller_uid: str,
        callee_uid: str,
        line: int,
        context: Optional[str] = None
    ):
        """
        Create CALLS relationship (Python-specific)

        Args:
            caller_uid: Caller function UID
            callee_uid: Callee function UID
            line: Line number where call happens
            context: Optional context
        """
        query = f"""
        MATCH (caller:{CoreSchema.NODE_SYMBOL} {{uid: $caller_uid}})
        MATCH (callee:{CoreSchema.NODE_SYMBOL} {{uid: $callee_uid}})
        MERGE (caller)-[r:{PythonExtension.REL_CALLS}]->(callee)
        SET r.line = $line,
            r.context = $context
        RETURN r
        """

        params = {
            "caller_uid": caller_uid,
            "callee_uid": callee_uid,
            "line": line,
            "context": context,
        }

        with self.session() as session:
            session.run(query, params)

    def create_variable_node(
        self,
        uid: str,
        name: str,
        is_global: bool = False,
        is_param: bool = False,
        scope: str = "local",
        **extra_props
    ) -> Dict[str, Any]:
        """
        Create a Variable node (Python-specific)

        Args:
            uid: Unique identifier
            name: Variable name
            is_global: Is global variable
            is_param: Is function parameter
            scope: Variable scope (global, local, nonlocal)
            **extra_props: Additional properties

        Returns:
            Created node properties
        """
        query = f"""
        MERGE (v:{CoreSchema.NODE_SYMBOL}:{PythonExtension.NODE_VARIABLE} {{uid: $uid}})
        ON CREATE SET
            v.name = $name,
            v.type = 'variable',
            v.is_global = $is_global,
            v.is_param = $is_param,
            v.scope = $scope,
            v.language = 'python',
            v.created_at = datetime()
        RETURN v
        """

        params = {
            "uid": uid,
            "name": name,
            "is_global": is_global,
            "is_param": is_param,
            "scope": scope,
        }
        params.update(extra_props)

        with self.session() as session:
            result = session.run(query, params)
            record = result.single()
            return dict(record["v"]) if record else {}

    # ========================================================================
    # Query Operations
    # ========================================================================

    def query(self, cypher: str, **params) -> List[Dict[str, Any]]:
        """
        Execute a Cypher query

        Args:
            cypher: Cypher query string
            **params: Query parameters

        Returns:
            List of result records as dictionaries
        """
        with self.session() as session:
            result = session.run(cypher, params)
            return [dict(record) for record in result]

    def get_symbol_by_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get symbol node by UID"""
        query = f"""
        MATCH (s:{CoreSchema.NODE_SYMBOL} {{uid: $uid}})
        RETURN s
        """

        with self.session() as session:
            result = session.run(query, uid=uid)
            record = result.single()
            return dict(record["s"]) if record else None

    def get_function_dependencies(self, function_uid: str) -> List[Dict[str, Any]]:
        """
        Get all functions called by a function

        Args:
            function_uid: Function UID

        Returns:
            List of called functions
        """
        query = f"""
        MATCH (f:{CoreSchema.NODE_SYMBOL} {{uid: $function_uid}})
              -[:{PythonExtension.REL_CALLS}]->(dep:{CoreSchema.NODE_SYMBOL})
        RETURN dep.uid as uid,
               dep.name as name,
               dep.type as type
        """

        return self.query(query, function_uid=function_uid)

    def get_pr_changes(self, pr_number: int) -> List[Dict[str, Any]]:
        """
        Get all symbols modified in a PR

        Args:
            pr_number: PR number

        Returns:
            List of modified symbols
        """
        version_id = f"PR-{pr_number}"

        query = f"""
        MATCH (s:{CoreSchema.NODE_SYMBOL})
              -[r:{CoreSchema.REL_MODIFIED_IN}]->
              (v:{CoreSchema.NODE_VERSION} {{id: $version_id}})
        RETURN s.uid as uid,
               s.name as name,
               s.type as type,
               r.change_type as change_type
        """

        return self.query(query, version_id=version_id)

    def get_pr_graph(self, pr_number: int) -> Dict[str, Any]:
        """
        Get full graph for PR visualization

        Args:
            pr_number: PR number

        Returns:
            Dict with nodes and edges
        """
        version_id = f"PR-{pr_number}"

        # Get nodes
        nodes_query = f"""
        MATCH (s:{CoreSchema.NODE_SYMBOL})
              -[:{CoreSchema.REL_MODIFIED_IN}]->
              (v:{CoreSchema.NODE_VERSION} {{id: $version_id}})
        RETURN s.uid as id,
               s.name as label,
               s.type as type
        """

        # Get relationships
        edges_query = f"""
        MATCH (s1:{CoreSchema.NODE_SYMBOL})
              -[:{CoreSchema.REL_MODIFIED_IN}]->
              (v:{CoreSchema.NODE_VERSION} {{id: $version_id}})
        MATCH (s1)-[r:{PythonExtension.REL_CALLS}]->(s2:{CoreSchema.NODE_SYMBOL})
        RETURN s1.uid as source,
               s2.uid as target,
               type(r) as relationship
        """

        nodes = self.query(nodes_query, version_id=version_id)
        edges = self.query(edges_query, version_id=version_id)

        return {
            "nodes": nodes,
            "edges": edges
        }

    # ========================================================================
    # Utility Operations
    # ========================================================================

    def clear_database(self):
        """Clear all nodes and relationships (use with caution!)"""
        query = "MATCH (n) DETACH DELETE n"

        with self.session() as session:
            session.run(query)

        logger.warning("Database cleared")

    def get_stats(self) -> Dict[str, int]:
        """Get database statistics"""
        query = """
        MATCH (n)
        RETURN labels(n)[0] as label, count(n) as count
        """

        with self.session() as session:
            result = session.run(query)
            stats = {record["label"]: record["count"] for record in result}

        return stats
