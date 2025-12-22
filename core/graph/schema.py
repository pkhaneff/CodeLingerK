"""
Neo4j Graph Schema for CodeLingerK

Design Pattern: Core Schema + Language Extensions
- Core: Language-agnostic structure
- Extensions: Language-specific features (Python, JS, etc.)
"""

from typing import List


class CoreSchema:
    """
    Core Schema - Language-agnostic
    Applicable to all programming languages
    """

    # ========================================================================
    # Node Labels
    # ========================================================================

    NODE_SYMBOL = "Symbol"
    """
    Base node for all code entities
    Properties:
        - uid: str (unique identifier: file_path:name:line_start)
        - name: str
        - type: str (function, class, method, variable, etc.)
        - body_hash: str (SHA256 of content)
        - line_start: int
        - line_end: int
        - language: str (python, javascript, etc.)
    """

    NODE_CONTAINER = "Container"
    """
    Represents File or Directory
    Properties:
        - path: str
        - type: str (file, directory)
        - language: str (for files)
    """

    NODE_VERSION = "Version"
    """
    Version control snapshot (PR, commit, branch)
    Properties:
        - id: str (PR-123, commit-abc, branch-main)
        - type: str (pr, commit, branch)
        - created_at: datetime
    """

    # ========================================================================
    # Relationship Types
    # ========================================================================

    REL_CONTAINS = "CONTAINS"
    """
    Hierarchical containment
    Examples:
        (:Container {type: 'file'})-[:CONTAINS]->(:Symbol {type: 'function'})
        (:Container {type: 'directory'})-[:CONTAINS]->(:Container {type: 'file'})
        (:Symbol {type: 'class'})-[:CONTAINS]->(:Symbol {type: 'method'})
    """

    REL_REFERENCES = "REFERENCES"
    """
    Basic reference relationship (A uses/mentions B)
    Examples:
        (:Symbol {type: 'function'})-[:REFERENCES]->(:Symbol {type: 'function'})  # Function call
        (:Symbol)-[:REFERENCES]->(:Container)  # Import file
    """

    REL_MODIFIED_IN = "MODIFIED_IN"
    """
    Tracks which version a symbol was modified in
    Examples:
        (:Symbol)-[:MODIFIED_IN {change_type: 'added'}]->(:Version {id: 'PR-123'})
        (:Symbol)-[:MODIFIED_IN {change_type: 'modified'}]->(:Version {id: 'commit-abc'})
    Properties:
        - change_type: str (added, modified, deleted)
        - timestamp: datetime
    """

    # ========================================================================
    # Constraints
    # ========================================================================

    CONSTRAINTS: List[str] = [
        # Symbol uniqueness
        f"CREATE CONSTRAINT symbol_uid_unique IF NOT EXISTS "
        f"FOR (s:{NODE_SYMBOL}) REQUIRE s.uid IS UNIQUE",

        # Container uniqueness
        f"CREATE CONSTRAINT container_path_unique IF NOT EXISTS "
        f"FOR (c:{NODE_CONTAINER}) REQUIRE c.path IS UNIQUE",

        # Version uniqueness
        f"CREATE CONSTRAINT version_id_unique IF NOT EXISTS "
        f"FOR (v:{NODE_VERSION}) REQUIRE v.id IS UNIQUE",
    ]

    # ========================================================================
    # Indexes
    # ========================================================================

    INDEXES: List[str] = [
        f"CREATE INDEX symbol_name IF NOT EXISTS FOR (s:{NODE_SYMBOL}) ON (s.name)",
        f"CREATE INDEX symbol_type IF NOT EXISTS FOR (s:{NODE_SYMBOL}) ON (s.type)",
        f"CREATE INDEX symbol_hash IF NOT EXISTS FOR (s:{NODE_SYMBOL}) ON (s.body_hash)",
        f"CREATE INDEX container_type IF NOT EXISTS FOR (c:{NODE_CONTAINER}) ON (c.type)",
    ]


class PythonExtension:
    """
    Python-specific schema extensions
    Captures Python language features in detail
    """

    # ========================================================================
    # Extended Node Labels (inherit from Symbol)
    # ========================================================================

    NODE_VARIABLE = "Variable"
    """
    Python variable (extends Symbol)
    Additional Properties:
        - is_global: bool
        - is_param: bool (function parameter)
        - is_instance: bool (instance variable)
        - scope: str (global, local, nonlocal)
    """

    NODE_DECORATOR = "Decorator"
    """
    Python decorator
    Properties:
        - name: str
        - arguments: str (serialized)
    """

    NODE_IMPORT = "Import"
    """
    Import statement
    Properties:
        - module: str
        - names: List[str]
        - alias: str (optional)
    """

    # ========================================================================
    # Python-specific Relationships
    # ========================================================================

    REL_CALLS = "CALLS"
    """
    Function/method call (more specific than REFERENCES)
    (:Symbol {type: 'function'})-[:CALLS]->(:Symbol {type: 'function'})
    Properties:
        - line: int (where the call happens)
        - context: str (optional)
    """

    REL_ASSIGNED_FROM = "ASSIGNED_FROM"
    """
    Data flow tracking between variables
    (:Variable {name: 'x'})-[:ASSIGNED_FROM]->(:Variable {name: 'y'})
    (:Variable)-[:ASSIGNED_FROM]->(:Symbol {type: 'function'})  # x = foo()
    Properties:
        - line: int
        - operation: str (direct, call_result, etc.)
    """

    REL_DECORATED_BY = "DECORATED_BY"
    """
    Python decorator application
    (:Symbol {type: 'function'})-[:DECORATED_BY]->(:Decorator {name: '@staticmethod'})
    Properties:
        - order: int (decorator stack order)
    """

    REL_INHERITS = "INHERITS"
    """
    Class inheritance
    (:Symbol {type: 'class'})-[:INHERITS]->(:Symbol {type: 'class'})
    """

    REL_IMPORTS = "IMPORTS"
    """
    Import relationship
    (:Container)-[:IMPORTS]->(:Container)  # File imports module
    (:Symbol)-[:IMPORTS]->(:Symbol)  # from module import function
    Properties:
        - import_type: str (module, from_import, alias)
    """

    # ========================================================================
    # Layer Classification (Architecture Pattern)
    # ========================================================================

    NODE_LAYER = "Layer"
    """
    Architectural layer classification
    Properties:
        - name: str (Controller, Service, Repository, Model, Util)
        - pattern: str (MVC, Clean Architecture, etc.)
    """

    REL_BELONGS_TO_LAYER = "BELONGS_TO_LAYER"
    """
    (:Symbol)-[:BELONGS_TO_LAYER]->(:Layer {name: 'Service'})
    (:Container)-[:BELONGS_TO_LAYER]->(:Layer {name: 'Controller'})
    """


class SchemaManager:
    """Utility to setup schema in Neo4j"""

    @staticmethod
    def get_all_constraints() -> List[str]:
        """Get all constraints (core + extensions)"""
        return CoreSchema.CONSTRAINTS

    @staticmethod
    def get_all_indexes() -> List[str]:
        """Get all indexes (core + extensions)"""
        return CoreSchema.INDEXES

    @staticmethod
    def get_create_statements() -> List[str]:
        """Get all CREATE statements for schema setup"""
        return SchemaManager.get_all_constraints() + SchemaManager.get_all_indexes()


# ============================================================================
# Example Graph Structure
# ============================================================================

EXAMPLE_GRAPH = """
# Example: Function call in Python file

# Containers
(:Container {path: 'api/controllers/webhook_controller.py', type: 'file', language: 'python'})

# Symbols
(:Symbol:Function {
    uid: 'api/controllers/webhook_controller.py:handle_push:10',
    name: 'handle_push',
    type: 'function',
    body_hash: 'abc123...',
    line_start: 10,
    line_end: 50,
    language: 'python'
})

(:Symbol:Function {
    uid: 'core/change_extractor.py:extract_changes:20',
    name: 'extract_changes',
    type: 'function',
    language: 'python'
})

# Variables (Python Extension)
(:Symbol:Variable {
    uid: 'api/controllers/webhook_controller.py:handle_push:extractor',
    name: 'extractor',
    type: 'variable',
    is_global: false,
    is_param: false,
    scope: 'local'
})

# Version
(:Version {id: 'PR-123', type: 'pr', created_at: '2025-12-19'})

# Layer
(:Layer {name: 'Controller', pattern: 'MVC'})

# Relationships
(:Container)-[:CONTAINS]->(:Symbol:Function {name: 'handle_push'})
(:Symbol:Function {name: 'handle_push'})-[:CALLS]->(:Symbol:Function {name: 'extract_changes'})
(:Symbol:Function {name: 'handle_push'})-[:MODIFIED_IN {change_type: 'modified'}]->(:Version {id: 'PR-123'})
(:Symbol:Function {name: 'handle_push'})-[:BELONGS_TO_LAYER]->(:Layer {name: 'Controller'})

# Python-specific
(:Symbol:Function {name: 'handle_push'})-[:DECORATED_BY]->(:Decorator {name: '@async'})
(:Symbol:Variable {name: 'extractor'})-[:ASSIGNED_FROM {operation: 'call_result'}]->(:Symbol:Function {name: 'ChangeExtractor'})
"""
