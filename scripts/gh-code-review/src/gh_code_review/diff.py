from typing import List, Dict, Set
from unidiff import PatchSet
from gh_code_review.analyzers.base import LanguageAnalyzer


def parse_diff(
    diff_content: str, analyzers: List[LanguageAnalyzer]
) -> Dict[str, Set[int]]:
    """
    Parses a unified diff and returns a mapping of modified files to their changed line numbers.
    Only files supported by the provided analyzers are included.
    """
    patch_set = PatchSet(diff_content)
    changed_lines: Dict[str, Set[int]] = {}
    for patched_file in patch_set:
        if patched_file.is_removed_file:
            continue
        # Check if any analyzer supports this file
        if not any(analyzer.supports_file(patched_file.path) for analyzer in analyzers):
            continue

        # Extract target line numbers (after applying the diff)
        lines = {
            line.target_line_no
            for hunk in patched_file
            for line in hunk
            if line.is_added and line.target_line_no
        }
        if lines:
            changed_lines[patched_file.path] = lines

    return changed_lines
