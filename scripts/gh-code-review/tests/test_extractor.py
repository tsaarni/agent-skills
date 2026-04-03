from gh_code_review.extractor import extract_context_from_diff

def test_extract_context_ranges():
    # A diff where the hunk header context is an import line
    diff_content = """diff --git a/src/github.py b/src/github.py
--- a/src/github.py
+++ b/src/github.py
@@ -6,2 +6,2 @@ import sys
 
-def old_func():
+def new_func():
     pass
"""
    contexts = extract_context_from_diff(diff_content)
    assert "src/github.py" in contexts
    ranges = contexts["src/github.py"].ranges
    assert len(ranges) == 1
    assert ranges[0].start_line == 6
    assert ranges[0].name == "import sys"
    assert "new_func" in ranges[0].content

def test_extract_context_with_function_header():
    # A diff where the hunk header actually contains a function definition
    diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -10,2 +10,3 @@ def main():
     print("hello")
+    print("world")
     return
"""
    contexts = extract_context_from_diff(diff_content)
    assert "src/main.py" in contexts
    ranges = contexts["src/main.py"].ranges
    assert len(ranges) == 1
    assert ranges[0].name == "def main():"
    assert "world" in ranges[0].content

def test_extract_context_all_text_files():
    # A diff with files previously ignored
    diff_content = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
 # Project
+More info
diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,1 +1,2 @@
 print("hello")
+print("world")
"""
    contexts = extract_context_from_diff(diff_content)
    assert "README.md" in contexts
    assert "src/main.py" in contexts

def test_extract_context_multiple_hunks():
    diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
-def a():
+def a_new():
     pass
@@ -10,2 +10,2 @@
-def b():
+def b_new():
     pass
"""
    contexts = extract_context_from_diff(diff_content)
    assert "src/main.py" in contexts
    ranges = contexts["src/main.py"].ranges
    assert len(ranges) == 2
    assert "a_new" in ranges[0].content
    assert "b_new" in ranges[1].content
