from gh_code_review.analyzers.python import PythonAnalyzer


def test_python_analyzer_supports_file():
    analyzer = PythonAnalyzer()
    assert analyzer.supports_file("test.py") is True
    assert analyzer.supports_file("test.go") is False


def test_python_analyzer_extract_context(tmp_path):
    analyzer = PythonAnalyzer()
    python_content = """def hello(name: str):
    print(f"Hello, {name}")

class MyClass:
    def __init__(self, val: int):
        self.val = val

    def get_val(self) -> int:
        return self.val

@decorator
def decorated_func():
    pass

MY_CONST = 123
x, y = 1, 2
"""
    python_file = tmp_path / "test.py"
    python_file.write_text(python_content)

    # Line 1: def hello
    context = analyzer.extract_context(str(python_file), {1})
    assert len(context.ranges) == 1
    assert context.ranges[0].name == "hello"
    assert "def hello(name: str):" in context.ranges[0].content
    assert "hello" in context.identifiers

    # Line 4: class MyClass
    context = analyzer.extract_context(str(python_file), {4})
    assert "MyClass" in context.identifiers

    # Line 8: def get_val
    context = analyzer.extract_context(str(python_file), {8})
    assert "get_val" in context.identifiers

    # Line 11: @decorator (decorated_definition)
    context = analyzer.extract_context(str(python_file), {11})
    assert "decorated_func" in context.identifiers

    # Line 12: def decorated_func (decorated_definition)
    context = analyzer.extract_context(str(python_file), {12})
    assert "decorated_func" in context.identifiers

    # Line 15: MY_CONST = 123
    context = analyzer.extract_context(str(python_file), {15})
    assert "MY_CONST" in context.identifiers

    # Line 16: x, y = 1, 2
    context = analyzer.extract_context(str(python_file), {16})
    assert "x" in context.identifiers
    assert "y" in context.identifiers
