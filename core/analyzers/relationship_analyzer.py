"""
Relationship Analyzer - Extract code relationships from AST
"""

from typing import List, Dict, Any
from dataclasses import dataclass
from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from core.logging_config import get_logger
from core.models import ChangeUnit, CodeSymbol

logger = get_logger(__name__)


@dataclass
class FunctionCall:
    """Represents a function call"""
    caller: str  # Function name that makes the call
    callee: str  # Function name being called
    line: int    # Line number where call happens


@dataclass
class ImportStatement:
    """Represents an import statement"""
    module: str
    names: List[str]
    alias: str = None


@dataclass
class RelationshipData:
    """Container for extracted relationships"""
    calls: List[FunctionCall]
    imports: List[ImportStatement]
    variables: List[str]


class RelationshipAnalyzer:
    """Analyze code to extract relationships between symbols"""

    def __init__(self):
        self.lang = Language(tspython.language())
        self.parser = Parser(self.lang)

    def analyze_change_unit(self, unit: ChangeUnit) -> RelationshipData:
        """
        Analyze a ChangeUnit to extract relationships

        Args:
            unit: ChangeUnit to analyze

        Returns:
            RelationshipData with extracted relationships
        """
        symbol = unit.new_symbol or unit.old_symbol
        if not symbol:
            return RelationshipData(calls=[], imports=[], variables=[])

        # Extract relationships from symbol content
        calls = self._extract_function_calls(symbol)
        imports = []  # Will extract from file level, not symbol level
        variables = self._extract_variables(symbol)

        return RelationshipData(
            calls=calls,
            imports=imports,
            variables=variables
        )

    def analyze_file_imports(self, code: str) -> List[ImportStatement]:
        """
        Extract import statements from file

        Args:
            code: Full file content

        Returns:
            List of ImportStatement
        """
        tree = self.parser.parse(bytes(code, "utf8"))
        imports = []

        self._walk_for_imports(tree.root_node, code, imports)

        return imports

    def _extract_function_calls(self, symbol: CodeSymbol) -> List[FunctionCall]:
        """
        Extract function calls from a symbol's content

        Args:
            symbol: CodeSymbol to analyze

        Returns:
            List of FunctionCall
        """
        tree = self.parser.parse(bytes(symbol.content, "utf8"))
        calls = []

        self._walk_for_calls(tree.root_node, symbol.content, symbol.name, calls, symbol.line_start)

        return calls

    def _extract_variables(self, symbol: CodeSymbol) -> List[str]:
        """
        Extract variable names from symbol

        Args:
            symbol: CodeSymbol to analyze

        Returns:
            List of variable names
        """
        tree = self.parser.parse(bytes(symbol.content, "utf8"))
        variables = []

        self._walk_for_variables(tree.root_node, symbol.content, variables)

        return variables

    def _walk_for_calls(self, node, code: str, caller_name: str, calls: List[FunctionCall], base_line: int):
        """Recursively walk AST to find function calls"""
        if node.type == 'call':
            # Get function name being called
            function_node = node.child_by_field_name('function')
            if function_node:
                callee = code[function_node.start_byte:function_node.end_byte]

                # Handle method calls (obj.method) - extract just method name
                if '.' in callee:
                    callee = callee.split('.')[-1]

                line = node.start_point[0] + base_line

                calls.append(FunctionCall(
                    caller=caller_name,
                    callee=callee,
                    line=line
                ))

        # Recurse into children
        for child in node.children:
            self._walk_for_calls(child, code, caller_name, calls, base_line)

    def _walk_for_imports(self, node, code: str, imports: List[ImportStatement]):
        """Recursively walk AST to find import statements"""
        if node.type == 'import_statement':
            # import module
            name_node = node.child_by_field_name('name')
            if name_node:
                module = code[name_node.start_byte:name_node.end_byte]
                imports.append(ImportStatement(
                    module=module,
                    names=[module]
                ))

        elif node.type == 'import_from_statement':
            # from module import name1, name2
            module_node = node.child_by_field_name('module_name')
            module = code[module_node.start_byte:module_node.end_byte] if module_node else ''

            names = []
            for child in node.children:
                if child.type == 'dotted_name' or child.type == 'identifier':
                    name = code[child.start_byte:child.end_byte]
                    if name not in ['from', 'import', module]:
                        names.append(name)

            if names:
                imports.append(ImportStatement(
                    module=module,
                    names=names
                ))

        # Recurse into children
        for child in node.children:
            self._walk_for_imports(child, code, imports)

    def _walk_for_variables(self, node, code: str, variables: List[str]):
        """Recursively walk AST to find variable assignments"""
        if node.type == 'assignment':
            left = node.child_by_field_name('left')
            if left and left.type == 'identifier':
                var_name = code[left.start_byte:left.end_byte]
                if var_name not in variables:
                    variables.append(var_name)

        # Recurse into children
        for child in node.children:
            self._walk_for_variables(child, code, variables)
