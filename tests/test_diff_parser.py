"""
Unit tests for DiffParser
"""

import pytest
from core.diff_parser import DiffParser, DiffHunk, ParsedDiff

class TestDiffParser:
    """Test suite for DiffParser"""

    def test_parse_simple_diff(self):
        """Test parsing a simple diff with one hunk"""
        diff_text = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 def hello():
-    print("old")
+    print("new")
+    return True
"""
        parser = DiffParser()
        parsed = parser.parse_diff(diff_text)

        assert parsed.file_path == "test.py"
        assert parsed.old_path == "a/test.py"
        assert parsed.new_path == "b/test.py"
        assert len(parsed.hunks) == 1

        hunk = parsed.hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 3
        assert hunk.new_start == 1
        assert hunk.new_count == 4

    def test_parse_new_file(self):
        """Test parsing diff for a new file"""
        diff_text = """new file mode 100644
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,3 @@
+def new_function():
+    pass
+
"""
        parser = DiffParser()
        parsed = parser.parse_diff(diff_text)

        assert parsed.is_new_file is True
        assert parsed.file_path == "newfile.py"
        assert len(parsed.hunks) == 1

    def test_parse_deleted_file(self):
        """Test parsing diff for a deleted file"""
        diff_text = """deleted file mode 100644
--- a/oldfile.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def old_function():
-    pass
-
"""
        parser = DiffParser()
        parsed = parser.parse_diff(diff_text)

        assert parsed.is_deleted_file is True
        assert parsed.file_path == "oldfile.py"

    def test_parse_renamed_file(self):
        """Test parsing diff for a renamed file"""
        diff_text = """rename from old_name.py
rename to new_name.py
"""
        parser = DiffParser()
        parsed = parser.parse_diff(diff_text)

        assert parsed.is_renamed is True
        assert parsed.old_path == "old_name.py"
        assert parsed.new_path == "new_name.py"
        assert parsed.file_path == "new_name.py"

    def test_hunk_get_added_lines(self):
        """Test extracting added lines from a hunk"""
        hunk = DiffHunk(
            old_start=1,
            old_count=2,
            new_start=1,
            new_count=3,
            header="@@ -1,2 +1,3 @@",
            lines=[
                " line1",
                "-old line",
                "+new line",
                "+added line"
            ]
        )

        added = hunk.get_added_lines()
        assert len(added) == 2
        assert added[0] == (2, "new line")
        assert added[1] == (3, "added line")

    def test_hunk_get_deleted_lines(self):
        """Test extracting deleted lines from a hunk"""
        hunk = DiffHunk(
            old_start=1,
            old_count=3,
            new_start=1,
            new_count=2,
            header="@@ -1,3 +1,2 @@",
            lines=[
                " line1",
                "-deleted line1",
                "-deleted line2",
                " line2"
            ]
        )

        deleted = hunk.get_deleted_lines()
        assert len(deleted) == 2
        assert deleted[0] == (2, "deleted line1")
        assert deleted[1] == (3, "deleted line2")

    def test_parse_multi_file_diff(self):
        """Test parsing diff with multiple files"""
        diff_text = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1 +1 @@
-old1
+new1
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-old2
+new2
"""
        parser = DiffParser()
        parsed_list = parser.parse_multi_file_diff(diff_text)

        assert len(parsed_list) == 2
        assert parsed_list[0].file_path == "file1.py"
        assert parsed_list[1].file_path == "file2.py"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
