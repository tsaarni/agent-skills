from typing import Set, Optional
from tree_sitter import Language, Parser
import tree_sitter_cpp as tscpp
from gh_code_review.analyzers.base import LanguageAnalyzer


class CppAnalyzer(LanguageAnalyzer):
    def __init__(self):
        lang = Language(tscpp.language())
        parser = Parser(lang)
        super().__init__(
            parser=parser,
            target_node_types={
                "class_specifier",
                "function_definition",
                "type_definition",
                "preproc_def",
                "declaration",
                "field_declaration",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
            },
        )

    def supports_file(self, file_path: str) -> bool:
        return file_path.lower().endswith(
            (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx")
        )

    def _extract_node_names(self, node) -> Set[str]:
        names = set()
        if node.type in {
            "class_specifier",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
        }:
            name = self._extract_identifier(node, "type_identifier")
            if name:
                names.add(name)
        elif node.type == "function_definition":
            decl = node.child_by_field_name("declarator")
            if decl:
                name = self._find_function_name(decl)
                if name:
                    names.add(name)
        elif node.type == "type_definition":
            name = self._extract_identifier(node, "type_identifier")
            if name:
                names.add(name)
        elif node.type == "preproc_def":
            name = self._extract_identifier(node, "identifier")
            if name:
                names.add(name)
        elif node.type == "declaration":
            # Only extract as named range if at top level
            if node.parent and node.parent.type == "translation_unit":
                self._recursive_extract_identifiers(
                    node, names, {"identifier", "field_identifier", "type_identifier"}
                )
        elif node.type == "field_declaration":
            self._recursive_extract_identifiers(
                node, names, {"identifier", "field_identifier", "type_identifier"}
            )
        return names

    def _find_function_name(self, node) -> Optional[str]:
        if node.type in {"identifier", "field_identifier"}:
            return node.text.decode("utf8")
        for child in node.children:
            name = self._find_function_name(child)
            if name:
                return name
        return None
