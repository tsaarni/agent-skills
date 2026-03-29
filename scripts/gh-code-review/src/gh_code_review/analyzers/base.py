import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass
class ExtractedRange:
    start_line: int
    end_line: int
    name: Optional[str] = None
    content: Optional[str] = None


@dataclass
class ExtractedContext:
    ranges: List[ExtractedRange]
    identifiers: Set[str]


@dataclass
class Usage:
    file_path: str
    line_number: int
    content: str


class LanguageAnalyzer(ABC):
    """Base class with common logic for language-specific analysis."""

    BUILTIN_TYPES: Set[str] = set()

    def __init__(self, parser=None, target_node_types=None):
        self.parser = parser
        self.target_node_types = target_node_types or set()

    @abstractmethod
    def supports_file(self, file_path: str) -> bool:
        """Returns True if the analyzer supports the given file path."""
        pass

    def _get_parser_and_types(self, file_path: str):
        """Helper to return parser and target types. Can be overridden (e.g. for C/C++)."""
        return self.parser, self.target_node_types

    def _find_enclosing_nodes(
        self,
        node,
        line_number: int,
        target_node_types: Optional[Set[str]],
        found_nodes: Set,
    ):
        """Finds all enclosing nodes of target types for a line number."""
        ts_line = line_number - 1
        if not (node.start_point[0] <= ts_line <= node.end_point[0]):
            return

        # Add current node if it matches target types
        if target_node_types is None or node.type in target_node_types:
            found_nodes.add((node.start_byte, node.end_byte, node))

        for child in node.children:
            self._find_enclosing_nodes(
                child, line_number, target_node_types, found_nodes
            )

    @abstractmethod
    def _extract_node_names(self, node) -> Set[str]:
        """Language-specific logic to extract names from a node."""
        pass

    def _extract_identifier(self, node, identifier_type: str) -> Optional[str]:
        """Utility helper to find an identifier child of a specific type."""
        for child in node.children:
            if child.type == identifier_type:
                return child.text.decode("utf8")
        return None

    def _recursive_extract_identifiers(
        self, node, names: Set[str], identifier_types: Set[str] = {"identifier"}
    ):
        """Recursively finds all identifiers of specified types in a node."""
        if node.type in identifier_types:
            names.add(node.text.decode("utf8"))
        for child in node.children:
            self._recursive_extract_identifiers(child, names, identifier_types)

    def extract_context(
        self, file_path: str, changed_lines: Set[int]
    ) -> ExtractedContext:
        """Generic context extraction with formatting and intelligent ellipses."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source_code = f.read()
        except (FileNotFoundError, IOError):
            return ExtractedContext(ranges=[], identifiers=set())

        parser, target_node_types = self._get_parser_and_types(file_path)
        if not parser:
            return ExtractedContext(ranges=[], identifiers=set())

        tree = parser.parse(bytes(source_code, "utf8"))
        context_nodes = set()
        for line_no in changed_lines:
            self._find_enclosing_nodes(
                tree.root_node, line_no, target_node_types, context_nodes
            )

        sorted_nodes = sorted(context_nodes, key=lambda x: x[0])
        extracted_ranges = []
        modified_identifiers = set()

        lines = source_code.splitlines()
        radius = 40
        min_hidden_lines = 20

        for _, _, node in sorted_nodes:
            names = self._extract_node_names(node)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            node_changed_lines = {
                line for line in changed_lines if start_line <= line <= end_line
            }

            if not node_changed_lines or (end_line - start_line) < 100:
                visible_lines = set(range(start_line, end_line + 1))
            else:
                visible_lines = set()
                for c in node_changed_lines:
                    visible_lines.update(range(c - radius, c + radius + 1))

            content_lines = []
            i = start_line
            while i <= end_line:
                if i in visible_lines:
                    marker = "*" if i in node_changed_lines else " "
                    # Format: 1234*| code
                    content_lines.append(f"{i:4d}{marker}| {lines[i - 1]}")
                    i += 1
                else:
                    hidden_start = i
                    while i <= end_line and i not in visible_lines:
                        i += 1
                    hidden_end = i - 1

                    num_hidden = hidden_end - hidden_start + 1
                    if num_hidden >= min_hidden_lines:
                        content_lines.append(
                            f"... [{num_hidden} unchanged lines hidden] ..."
                        )
                    else:
                        for j in range(hidden_start, hidden_end + 1):
                            marker = "*" if j in node_changed_lines else " "
                            content_lines.append(f"{j:4d}{marker}| {lines[j - 1]}")

            content = "\n".join(content_lines)
            extracted_ranges.append(
                ExtractedRange(
                    start_line=start_line,
                    end_line=end_line,
                    name=next(iter(names)) if names else None,
                    content=content,
                )
            )
            modified_identifiers.update(names)

        return ExtractedContext(
            ranges=extracted_ranges, identifiers=modified_identifiers
        )

    def scan_for_usages(
        self, repo_root: str, identifiers: Set[str], exclude_file: str
    ) -> Dict[str, List[Usage]]:
        """Common regex-based usage scanner across multiple files."""
        if not identifiers:
            return {}

        usages = {ident: [] for ident in identifiers}
        # Avoid scanning for common built-in types to reduce noise/performance hit
        scannable_idents = {i for i in identifiers if i not in self.BUILTIN_TYPES}
        if not scannable_idents:
            return usages

        patterns = {
            ident: re.compile(rf"\b{re.escape(ident)}\b") for ident in scannable_idents
        }

        for root, dirs, files in os.walk(repo_root):
            # Prune directories to skip.
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in self._get_excluded_dirs()
            ]
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_root)
                if self.supports_file(file) and rel_path != exclude_file:
                    self._process_file(file_path, rel_path, usages, patterns)

        return usages

    def _get_excluded_dirs(self) -> Set[str]:
        """Default set of directories to skip during scanning."""
        return {"vendor", "build", "node_modules"}

    def _process_file(
        self,
        file_path: str,
        rel_path: str,
        usages: Dict[str, List[Usage]],
        patterns: Dict[str, re.Pattern],
    ):
        """Inner loop for usage scanning in a single file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            for ident, pattern in patterns.items():
                for match in pattern.finditer(content):
                    start_offset = match.start()
                    line_no = content.count("\n", 0, start_offset) + 1

                    line_start = content.rfind("\n", 0, start_offset) + 1
                    line_end = content.find("\n", start_offset)
                    if line_end == -1:
                        line_end = len(content)

                    line_content = content[line_start:line_end].strip()
                    usages[ident].append(
                        Usage(
                            file_path=rel_path,
                            line_number=line_no,
                            content=line_content,
                        )
                    )
        except Exception:
            pass
