from gh_code_review.analyzers.java import JavaAnalyzer


def test_java_analyzer_supports_file():
    analyzer = JavaAnalyzer()
    assert analyzer.supports_file("Main.java") is True
    assert analyzer.supports_file("test.go") is False
    assert analyzer.supports_file("script.py") is False


def test_java_analyzer_extract_context(tmp_path):
    analyzer = JavaAnalyzer()
    java_content = """\
public class Greeter {

    private String name;

    public Greeter(String name) {
        this.name = name;
    }

    public String greet() {
        return "Hello, " + name;
    }
}
"""
    java_file = tmp_path / "Greeter.java"
    java_file.write_text(java_content)

    # Line 9 is public String greet()
    context = analyzer.extract_context(str(java_file), {9})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "greet" in names
    # Find the greet range for deeper checks
    greet_range = next(r for r in context.ranges if r.name == "greet")
    assert "public String greet()" in greet_range.content
    assert "greet" in context.identifiers

    # Line 5: constructor, Line 3: field
    context = analyzer.extract_context(str(java_file), {5, 3})
    names = {r.name for r in context.ranges}
    assert "Greeter" in names
    assert "name" in names


def test_java_analyzer_extract_context_class(tmp_path):
    analyzer = JavaAnalyzer()
    java_content = """\
public class MyService {
    public void doWork() {
        System.out.println("working");
    }
}
"""
    java_file = tmp_path / "MyService.java"
    java_file.write_text(java_content)

    # Line 2: public void doWork()
    context = analyzer.extract_context(str(java_file), {2})
    assert len(context.ranges) >= 1
    names = {r.name for r in context.ranges}
    assert "doWork" in names
    assert "doWork" in context.identifiers
