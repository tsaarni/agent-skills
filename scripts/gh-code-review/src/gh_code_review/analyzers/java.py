from typing import Set
from tree_sitter import Language, Parser
import tree_sitter_java as tsjava
from gh_code_review.analyzers.base import LanguageAnalyzer


class JavaAnalyzer(LanguageAnalyzer):
    def __init__(self):
        lang = Language(tsjava.language())
        parser = Parser(lang)
        super().__init__(
            parser=parser,
            target_node_types={
                "class_declaration",
                "method_declaration",
                "interface_declaration",
                "field_declaration",
                "constructor_declaration",
            },
        )

    def supports_file(self, file_path: str) -> bool:
        return file_path.endswith(".java")

    def _extract_node_names(self, node) -> Set[str]:
        names = set()
        if node.type in {
            "class_declaration",
            "method_declaration",
            "interface_declaration",
            "constructor_declaration",
        }:
            name = self._extract_identifier(node, "identifier")
            if name:
                names.add(name)
        elif node.type == "field_declaration":
            self._recursive_extract_identifiers(node, names)
        return names
