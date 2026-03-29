from typing import Set
from tree_sitter import Language, Parser
import tree_sitter_python as tspython
from gh_code_review.analyzers.base import LanguageAnalyzer


class PythonAnalyzer(LanguageAnalyzer):
    def __init__(self):
        lang = Language(tspython.language())
        parser = Parser(lang)
        super().__init__(
            parser=parser,
            target_node_types={
                "class_definition",
                "function_definition",
                "decorated_definition",
                "assignment",
            },
        )

    def supports_file(self, file_path: str) -> bool:
        return file_path.endswith(".py")

    def _extract_node_names(self, node) -> Set[str]:
        names = set()
        if node.type in {"class_definition", "function_definition"}:
            name = self._extract_identifier(node, "identifier")
            if name:
                names.add(name)
        elif node.type == "decorated_definition":
            for child in node.children:
                if child.type in {"class_definition", "function_definition"}:
                    name = self._extract_identifier(child, "identifier")
                    if name:
                        names.add(name)
        elif node.type == "assignment":
            # Only extract as a "named range" if it's at the top level
            # module -> expression_statement -> assignment
            if (
                node.parent
                and node.parent.type == "expression_statement"
                and node.parent.parent
                and node.parent.parent.type == "module"
            ):
                left_node = node.child_by_field_name("left")
                if left_node:
                    self._recursive_extract_identifiers(left_node, names)
        return names
