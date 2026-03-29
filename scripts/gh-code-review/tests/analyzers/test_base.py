from gh_code_review.analyzers.base import LanguageAnalyzer
from typing import Set


class MockAnalyzer(LanguageAnalyzer):
    """Concrete analyzer for testing base logic."""

    def supports_file(self, file_path: str) -> bool:
        return file_path.endswith(".mock")

    def _extract_node_names(self, node) -> Set[str]:
        return {"MockNode"}


def test_base_analyzer_intelligent_ellipses(tmp_path):
    from gh_code_review.analyzers.go import GoAnalyzer

    analyzer = GoAnalyzer()

    lines = ["package main", "func LargeFunction() {"]
    for i in range(120):
        lines.append(f"    // line {i}")
    lines.append("    // CHANGED LINE")
    for i in range(120, 150):
        lines.append(f"    // line {i}")
    lines.append("}")

    go_content = "\n".join(lines)
    go_file = tmp_path / "test.go"
    go_file.write_text(go_content)

    context = analyzer.extract_context(str(go_file), {123})
    assert len(context.ranges) == 1

    content = context.ranges[0].content
    assert "CHANGED LINE" in content
    assert "*|" in content
    assert "... [" in content
    assert "unchanged lines hidden] ..." in content


def test_base_analyzer_scan_for_usages(tmp_path):
    from gh_code_review.analyzers.go import GoAnalyzer

    analyzer = GoAnalyzer()

    file1 = tmp_path / "file1.go"
    file1.write_text("func main() { Hello() }")

    file2 = tmp_path / "file2.go"
    file2.write_text("func Hello() {}")

    usages = analyzer.scan_for_usages(str(tmp_path), {"Hello"}, exclude_file="file2.go")

    assert "Hello" in usages
    assert len(usages["Hello"]) == 1
    assert usages["Hello"][0].file_path == "file1.go"
    assert "Hello()" in usages["Hello"][0].content
