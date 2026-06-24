"""
Enhanced Python parser using tree-sitter.

Extracts:
- Functions and classes (symbols)
- Import statements
- Function calls
- Class inheritance
"""

import hashlib
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from core.parser.base_parser import (
    BaseParser,
    ParsedFile,
    ParsedSymbol,
    ParsedImport,
    ParsedCall,
    ParsedInheritance,
)


class EnhancedPythonParser(BaseParser):
    """Python parser with full AST extraction."""

    def __init__(self):
        self.lang = Language(tspython.language())
        self.parser = Parser(self.lang)

    @property
    def language(self) -> str:
        return 'python'

    @property
    def file_extensions(self) -> list[str]:
        return ['.py']

    def can_parse(self, file_path: str) -> bool:
        return Path(file_path).suffix in self.file_extensions

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _get_text(self, node, code: str) -> str:
        return code[node.start_byte : node.end_byte]

    def _get_docstring(self, node, code: str) -> str | None:
        """Extract docstring from function/class body."""
        body = node.child_by_field_name('body')
        if body and body.children:
            first_stmt = body.children[0]
            if first_stmt.type == 'expression_statement':
                expr = first_stmt.children[0] if first_stmt.children else None
                if expr and expr.type == 'string':
                    doc = self._get_text(expr, code)
                    # Remove quotes
                    if doc.startswith('"""') or doc.startswith("'''"):
                        return doc[3:-3].strip()
                    elif doc.startswith('"') or doc.startswith("'"):
                        return doc[1:-1].strip()
        return None

    def _get_function_signature(self, node, code: str) -> str:
        """Extract function signature (def name(params) -> return_type)."""
        name = node.child_by_field_name('name')
        params = node.child_by_field_name('parameters')
        return_type = node.child_by_field_name('return_type')

        sig = 'def '
        if name:
            sig += self._get_text(name, code)
        if params:
            sig += self._get_text(params, code)
        if return_type:
            sig += ' -> ' + self._get_text(return_type, code)

        return sig

    def _get_decorators(self, node, code: str) -> list[str]:
        """Extract decorators from a function/class."""
        decorators = []
        for child in node.children:
            if child.type == 'decorator':
                dec_text = self._get_text(child, code)
                # Remove @ prefix
                decorators.append(dec_text.lstrip('@'))
        return decorators

    def parse(self, file_path: str, content: str) -> ParsedFile:
        tree = self.parser.parse(bytes(content, 'utf8'))

        result = ParsedFile(
            file_path=file_path,
            language=self.language,
            content_hash=self._hash(content),
        )

        # Walk the tree
        self._extract_all(tree.root_node, content, result)

        return result

    def _extract_all(
        self,
        node,
        code: str,
        result: ParsedFile,
        current_class: str | None = None,
        current_function: str | None = None,
    ):
        """Recursively walk AST and extract all information."""

        # Import statements
        if node.type == 'import_statement':
            self._extract_import(node, code, result)

        elif node.type == 'import_from_statement':
            self._extract_from_import(node, code, result)

        # Class definitions
        elif node.type == 'class_definition':
            self._extract_class(node, code, result)
            return  # Don't recurse, _extract_class handles it

        # Function definitions
        elif node.type == 'function_definition':
            self._extract_function(node, code, result, current_class)
            return  # Don't recurse, _extract_function handles it

        # Function calls
        elif node.type == 'call':
            self._extract_call(node, code, result, current_function or current_class)

        # Recurse into children
        for child in node.children:
            self._extract_all(child, code, result, current_class, current_function)

    def _extract_import(self, node, code: str, result: ParsedFile):
        """Extract 'import X' statements."""
        for child in node.children:
            if child.type == 'dotted_name':
                module = self._get_text(child, code)
                result.imports.append(
                    ParsedImport(
                        module=module,
                        name=None,
                        alias=None,
                        is_relative=False,
                        line_number=node.start_point[0] + 1,
                    )
                )
            elif child.type == 'aliased_import':
                name_node = child.child_by_field_name('name')
                alias_node = child.child_by_field_name('alias')
                if name_node:
                    result.imports.append(
                        ParsedImport(
                            module=self._get_text(name_node, code),
                            name=None,
                            alias=self._get_text(alias_node, code) if alias_node else None,
                            is_relative=False,
                            line_number=node.start_point[0] + 1,
                        )
                    )

    def _extract_from_import(self, node, code: str, result: ParsedFile):
        """Extract 'from X import Y' statements."""
        module_node = node.child_by_field_name('module_name')
        module = self._get_text(module_node, code) if module_node else ''

        # Check for relative import (leading dots)
        is_relative = False
        for child in node.children:
            if child.type == 'relative_import':
                is_relative = True
                # Get the module part after dots
                for subchild in child.children:
                    if subchild.type == 'dotted_name':
                        module = self._get_text(subchild, code)
                break

        # Get imported names
        for child in node.children:
            if child.type == 'dotted_name' and child != module_node:
                result.imports.append(
                    ParsedImport(
                        module=module,
                        name=self._get_text(child, code),
                        alias=None,
                        is_relative=is_relative,
                        line_number=node.start_point[0] + 1,
                    )
                )
            elif child.type == 'aliased_import':
                name_node = child.child_by_field_name('name')
                alias_node = child.child_by_field_name('alias')
                if name_node:
                    result.imports.append(
                        ParsedImport(
                            module=module,
                            name=self._get_text(name_node, code),
                            alias=self._get_text(alias_node, code) if alias_node else None,
                            is_relative=is_relative,
                            line_number=node.start_point[0] + 1,
                        )
                    )

    def _extract_class(self, node, code: str, result: ParsedFile):
        """Extract class definition and its methods."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return

        class_name = self._get_text(name_node, code)
        content = self._get_text(node, code)

        # Extract base classes (inheritance)
        superclass = node.child_by_field_name('superclasses')
        if superclass:
            for child in superclass.children:
                if child.type == 'identifier':
                    parent = self._get_text(child, code)
                    result.inheritances.append(
                        ParsedInheritance(
                            child_class=class_name,
                            parent_class=parent,
                            line_number=node.start_point[0] + 1,
                        )
                    )
                elif child.type == 'attribute':
                    # Handle module.Class inheritance
                    parent = self._get_text(child, code)
                    result.inheritances.append(
                        ParsedInheritance(
                            child_class=class_name,
                            parent_class=parent,
                            line_number=node.start_point[0] + 1,
                        )
                    )

        # Create class symbol
        result.symbols.append(
            ParsedSymbol(
                name=class_name,
                type='class',
                file_path=result.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                content=content,
                content_hash=self._hash(content),
                docstring=self._get_docstring(node, code),
                decorators=self._get_decorators(node, code),
            )
        )

        # Extract methods inside the class
        body = node.child_by_field_name('body')
        if body:
            for child in body.children:
                self._extract_all(child, code, result, current_class=class_name)

    def _extract_function(
        self,
        node,
        code: str,
        result: ParsedFile,
        current_class: str | None,
    ):
        """Extract function/method definition."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return

        func_name = self._get_text(name_node, code)
        content = self._get_text(node, code)

        result.symbols.append(
            ParsedSymbol(
                name=func_name,
                type='method' if current_class else 'function',
                file_path=result.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                content=content,
                content_hash=self._hash(content),
                signature=self._get_function_signature(node, code),
                docstring=self._get_docstring(node, code),
                parent_class=current_class,
                decorators=self._get_decorators(node, code),
            )
        )

        # Extract calls inside the function
        body = node.child_by_field_name('body')
        if body:
            self._extract_calls_in_body(body, code, result, func_name)

    def _extract_calls_in_body(
        self,
        node,
        code: str,
        result: ParsedFile,
        caller: str,
    ):
        """Extract function calls within a function body."""
        if node.type == 'call':
            self._extract_call(node, code, result, caller)

        for child in node.children:
            # Skip nested function definitions
            if child.type not in ('function_definition', 'class_definition'):
                self._extract_calls_in_body(child, code, result, caller)

    def _extract_call(
        self,
        node,
        code: str,
        result: ParsedFile,
        caller: str | None,
    ):
        """Extract a function/method call."""
        if not caller:
            return

        func_node = node.child_by_field_name('function')
        if not func_node:
            return

        is_method = False
        receiver = None
        callee = ''

        if func_node.type == 'identifier':
            callee = self._get_text(func_node, code)
        elif func_node.type == 'attribute':
            # Method call: obj.method()
            callee = self._get_text(func_node, code)
            is_method = True
            # Get receiver (the object)
            obj_node = func_node.child_by_field_name('object')
            if obj_node:
                receiver = self._get_text(obj_node, code)

        if callee:
            result.calls.append(
                ParsedCall(
                    caller=caller,
                    callee=callee,
                    line_number=node.start_point[0] + 1,
                    is_method_call=is_method,
                    receiver=receiver,
                )
            )
