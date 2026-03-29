from typing import Set
from tree_sitter import Language, Parser
import tree_sitter_go as tsgo
from gh_code_review.analyzers.base import LanguageAnalyzer


class GoAnalyzer(LanguageAnalyzer):
    BUILTIN_TYPES = {
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "float32",
        "float64",
        "complex64",
        "complex128",
        "bool",
        "string",
        "byte",
        "rune",
        "error",
        "any",
    }

    def __init__(self):
        lang = Language(tsgo.language())
        parser = Parser(lang)
        super().__init__(
            parser=parser,
            target_node_types={
                "function_declaration",
                "method_declaration",
                "type_spec",
                "field_declaration",
            },
        )

    def supports_file(self, file_path: str) -> bool:
        return file_path.endswith(".go")

    def _extract_node_names(self, node) -> Set[str]:
        names = set()
        if node.type in {"function_declaration", "method_declaration"}:
            name = self._extract_identifier(node, "identifier")
            if not name:
                # Handle receiver methods case where identifier might be deeper
                for child in node.children:
                    if child.type == "field_identifier":
                        name = child.text.decode("utf8")
                        break
            if name:
                names.add(name)
        elif node.type == "type_spec":
            name = self._extract_identifier(node, "type_identifier")
            if name:
                names.add(name)
        elif node.type == "field_declaration":
            # For named fields: 'Name string'
            name = self._extract_identifier(node, "field_identifier")
            if name:
                names.add(name)
            else:
                # For anonymous fields: 'OtherStruct', '*OtherStruct', 'pkg.OtherStruct'
                type_node = node.child_by_field_name("type")
                if type_node:
                    # Look for type_identifier inside (possibly nested in pointer_type or qualified_type)
                    self._recursive_extract_type_names(type_node, names)
        return names

    def _recursive_extract_type_names(self, node, names: Set[str]):
        if node.type == "type_identifier":
            name = node.text.decode("utf8")
            if name not in self.BUILTIN_TYPES:
                names.add(name)
        for child in node.children:
            self._recursive_extract_type_names(child, names)
