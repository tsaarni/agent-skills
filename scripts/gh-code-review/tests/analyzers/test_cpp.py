from gh_code_review.analyzers.cpp import CppAnalyzer


def test_cpp_analyzer_supports_file():
    analyzer = CppAnalyzer()
    # C files
    assert analyzer.supports_file("main.c") is True
    assert analyzer.supports_file("header.h") is True
    # C++ files
    assert analyzer.supports_file("main.cpp") is True
    assert analyzer.supports_file("main.cc") is True
    assert analyzer.supports_file("main.cxx") is True
    assert analyzer.supports_file("header.hpp") is True
    assert analyzer.supports_file("header.hxx") is True
    # Non-C/C++ files
    assert analyzer.supports_file("test.go") is False
    assert analyzer.supports_file("Main.java") is False
    assert analyzer.supports_file("script.py") is False


def test_cpp_analyzer_extract_context_c_function(tmp_path):
    analyzer = CppAnalyzer()
    c_content = """\
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

void greet(const char *name) {
    printf("Hello, %s\\n", name);
}

int main(int argc, char *argv[]) {
    int result = add(1, 2);
    greet("World");
    return 0;
}
"""
    c_file = tmp_path / "main.c"
    c_file.write_text(c_content)

    # Line 4 inside add
    context = analyzer.extract_context(str(c_file), {4})
    assert len(context.ranges) == 1
    assert context.ranges[0].name == "add"
    assert "return a + b" in context.ranges[0].content
    assert "add" in context.identifiers


def test_cpp_analyzer_extract_context_struct(tmp_path):
    analyzer = CppAnalyzer()
    c_content = """\
typedef struct {
    int x;
    int y;
} Point;

Point create_point(int x, int y) {
    Point p = {x, y};
    return p;
}
"""
    c_file = tmp_path / "point.c"
    c_file.write_text(c_content)

    # Line 7 inside create_point
    context = analyzer.extract_context(str(c_file), {7})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "create_point" in names
    assert "create_point" in context.identifiers


def test_cpp_analyzer_extract_context_cpp_class(tmp_path):
    analyzer = CppAnalyzer()
    cpp_content = """\
class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }

    int subtract(int a, int b) {
        return a - b;
    }
};
"""
    cpp_file = tmp_path / "calculator.cpp"
    cpp_file.write_text(cpp_content)

    # Line 4 inside add
    context = analyzer.extract_context(str(cpp_file), {4})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "add" in names
    assert "add" in context.identifiers


def test_cpp_analyzer_extract_context_cpp_class_declaration(tmp_path):
    analyzer = CppAnalyzer()
    cpp_content = """\
class MyService {
public:
    void doWork();
private:
    int counter;
};
"""
    cpp_file = tmp_path / "service.hpp"
    cpp_file.write_text(cpp_content)

    # Line 5: int counter;
    context = analyzer.extract_context(str(cpp_file), {5})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "counter" in names


def test_cpp_analyzer_extract_context_preproc_define(tmp_path):
    analyzer = CppAnalyzer()
    c_content = """\
#define MAX_SIZE 1024
#define MIN(a, b) ((a) < (b) ? (a) : (b))

int buffer[MAX_SIZE];
"""
    c_file = tmp_path / "defs.c"
    c_file.write_text(c_content)

    # Line 1: #define
    context = analyzer.extract_context(str(c_file), {1})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "MAX_SIZE" in names


def test_cpp_analyzer_extract_context_c_enum(tmp_path):
    analyzer = CppAnalyzer()
    c_content = "enum Color { RED, GREEN, BLUE };\n"
    c_file = tmp_path / "colors.c"
    c_file.write_text(c_content)

    context = analyzer.extract_context(str(c_file), {1})
    assert "Color" in context.identifiers


def test_cpp_analyzer_extract_context_c_typedef(tmp_path):
    analyzer = CppAnalyzer()
    c_content = "typedef unsigned long long uint64;\n"
    c_file = tmp_path / "types.c"
    c_file.write_text(c_content)

    context = analyzer.extract_context(str(c_file), {1})
    assert "uint64" in context.identifiers


def test_cpp_analyzer_extract_context_c_union(tmp_path):
    analyzer = CppAnalyzer()
    c_content = """\
union Data {
    int i;
    float f;
};
"""
    c_file = tmp_path / "data.c"
    c_file.write_text(c_content)

    # Line 1 is the union head
    context = analyzer.extract_context(str(c_file), {1})
    assert "Data" in context.identifiers


def test_cpp_analyzer_extract_context_c_multi_decl(tmp_path):
    analyzer = CppAnalyzer()
    c_content = "int a = 1, b = 2, c = 3;\n"
    c_file = tmp_path / "multi.c"
    c_file.write_text(c_content)

    context = analyzer.extract_context(str(c_file), {1})
    assert {"a", "b", "c"}.issubset(context.identifiers)
