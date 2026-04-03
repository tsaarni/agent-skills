from dataclasses import dataclass
from typing import Dict, List, Optional
from unidiff import PatchSet

@dataclass
class ExtractedRange:
    start_line: int
    end_line: int
    name: Optional[str] = None
    content: Optional[str] = None

@dataclass
class ExtractedContext:
    ranges: List[ExtractedRange]

def extract_context_from_diff(diff_content: str) -> Dict[str, ExtractedContext]:
    patch_set = PatchSet(diff_content)
    contexts: Dict[str, ExtractedContext] = {}

    for patched_file in patch_set:
        if patched_file.is_removed_file:
            continue
        if patched_file.is_binary_file:
            continue

        ranges = []

        for hunk in patched_file:
            start_line = hunk.target_start
            end_line = hunk.target_start + hunk.target_length - 1
            
            # Reconstruct hunk content
            content_lines = []
            for line in hunk:
                marker = " "
                if line.is_added:
                    marker = "*"
                elif line.is_removed:
                    continue  # Only show the target file state
                
                if line.target_line_no:
                    content_lines.append(f"{line.target_line_no:4d}{marker}| {line.value.rstrip('\n')}")

            content = "\n".join(content_lines)
            
            hunk_header = hunk.section_header.strip() if hunk.section_header else None

            ranges.append(ExtractedRange(
                start_line=start_line,
                end_line=end_line,
                name=hunk_header,
                content=content
            ))
            
        if ranges:
            contexts[patched_file.path] = ExtractedContext(ranges=ranges)

    return contexts
