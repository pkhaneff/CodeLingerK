import hashlib
from typing import List
from tree_sitter import Language, Parser
import tree_sitter_python as tspython
from core.models import CodeSymbol

class PythonParser:
    def __init__(self):
        self.lang = Language(tspython.language())
        self.parser = Parser(self.lang)

    def _get_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def parse_file(self, code: str) -> List[CodeSymbol]:
        tree = self.parser.parse(bytes(code, "utf8"))
        symbols = []
        
        query = self.lang.query("""
            (function_definition
                name: (identifier) @name
                body: (block) @body) @func
            (class_definition
                name: (identifier) @name) @class
        """)
        
        captures = query.captures(tree.root_node)
        
        for node, tag in captures:
            if tag in ['func', 'class']:
                name_node = node.child_by_field_name('name')
                name = code[name_node.start_byte:name_node.end_byte]
                content = code[node.start_byte:node.end_byte]
                
                symbols.append(CodeSymbol(
                    name=name,
                    type="Function" if tag == 'func' else "Class",
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    column_start=node.start_point[1],
                    content=content,
                    body_hash=self._get_hash(content)
                ))
        return symbols