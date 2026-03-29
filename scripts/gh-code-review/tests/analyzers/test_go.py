from gh_code_review.analyzers.go import GoAnalyzer


def test_go_analyzer_supports_file():
    analyzer = GoAnalyzer()
    assert analyzer.supports_file("test.go") is True
    assert analyzer.supports_file("test.py") is False


def test_go_analyzer_extract_context(tmp_path):
    analyzer = GoAnalyzer()
    go_content = """package main

import "fmt"

// Hello function
func Hello(name string) {
    fmt.Printf("Hello, %s\\n", name)
}

type MyStruct struct {
    ID   int
    Name string
}

func (s *MyStruct) GetName() string {
    return s.Name
}
"""
    go_file = tmp_path / "test.go"
    go_file.write_text(go_content)

    # Line 6 is func Hello
    context = analyzer.extract_context(str(go_file), {6})
    assert len(context.ranges) == 1
    assert context.ranges[0].name == "Hello"
    assert "func Hello(name string)" in context.ranges[0].content
    assert "Hello" in context.identifiers

    # Line 10: type MyStruct struct {
    # Line 15: func (s *MyStruct) GetName() string {
    context = analyzer.extract_context(str(go_file), {10, 15})
    names = {r.name for r in context.ranges}
    assert "MyStruct" in names
    assert "GetName" in names
    assert "MyStruct" in context.identifiers
    assert "GetName" in context.identifiers


def test_go_analyzer_extract_context_anonymous_field(tmp_path):
    analyzer = GoAnalyzer()
    go_content = """package main
type MyStruct struct {
    OtherStruct
    *AnotherStruct
    pkg.QualifiedStruct
    int
}
"""
    go_file = tmp_path / "test.go"
    go_file.write_text(go_content)

    context = analyzer.extract_context(str(go_file), {3})
    assert "OtherStruct" in context.identifiers

    context = analyzer.extract_context(str(go_file), {4})
    assert "AnotherStruct" in context.identifiers

    context = analyzer.extract_context(str(go_file), {5})
    assert "QualifiedStruct" in context.identifiers

    context = analyzer.extract_context(str(go_file), {6})
    assert "int" not in context.identifiers
