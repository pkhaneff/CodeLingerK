"""
Unit tests for PythonParser
"""

import pytest
from core.parser.python_parser import PythonParser

class TestPythonParser:
    """Test suite for PythonParser"""

    def test_parse_function(self):
        """Test parsing a simple function"""
        code = """
def hello_world():
    print("Hello, World!")
    return True
"""
        parser = PythonParser()
        symbols = parser.parse_file(code)

        assert len(symbols) == 1
        func = symbols[0]
        assert func.name == "hello_world"
        assert func.type == "Function"
        assert func.line_start == 2
        assert "def hello_world():" in func.content
        assert func.body_hash is not None

    def test_parse_class(self):
        """Test parsing a simple class"""
        code = """
class MyClass:
    def __init__(self):
        self.value = 42
"""
        parser = PythonParser()
        symbols = parser.parse_file(code)

        # Should find class and __init__ method
        assert len(symbols) >= 1
        class_symbol = [s for s in symbols if s.type == "Class"][0]
        assert class_symbol.name == "MyClass"
        assert class_symbol.type == "Class"

    def test_parse_multiple_functions(self):
        """Test parsing multiple functions"""
        code = """
def func1():
    pass

def func2():
    return 42

def func3(x, y):
    return x + y
"""
        parser = PythonParser()
        symbols = parser.parse_file(code)

        assert len(symbols) == 3
        names = [s.name for s in symbols]
        assert "func1" in names
        assert "func2" in names
        assert "func3" in names

    def test_hash_consistency(self):
        """Test that same content produces same hash"""
        code = """
def test():
    return True
"""
        parser = PythonParser()
        symbols1 = parser.parse_file(code)
        symbols2 = parser.parse_file(code)

        assert symbols1[0].body_hash == symbols2[0].body_hash

    def test_hash_difference(self):
        """Test that different content produces different hash"""
        code1 = """
def test():
    return True
"""
        code2 = """
def test():
    return False
"""
        parser = PythonParser()
        symbols1 = parser.parse_file(code1)
        symbols2 = parser.parse_file(code2)

        assert symbols1[0].body_hash != symbols2[0].body_hash

    def test_empty_file(self):
        """Test parsing empty file"""
        code = ""
        parser = PythonParser()
        symbols = parser.parse_file(code)

        assert len(symbols) == 0

    def test_line_numbers(self):
        """Test that line numbers are correct"""
        code = """# Comment
def first():
    pass

def second():
    pass
"""
        parser = PythonParser()
        symbols = parser.parse_file(code)

        first = [s for s in symbols if s.name == "first"][0]
        second = [s for s in symbols if s.name == "second"][0]

        assert first.line_start == 2
        assert second.line_start == 5

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
