#!/usr/bin/env python3
"""Convert markdown prompt files to Antigravity / Gemini CLI TOML commands."""

import argparse
import glob
import os
import re
import sys
from pathlib import Path


def extract_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse simple YAML frontmatter and return (metadata, body)."""
    metadata: dict[str, str] = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
            for line in fm_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*(.*)$", line)
                if match:
                    key = match.group(1).strip()
                    val = match.group(2).strip().strip("\"'")
                    metadata[key] = val

    return metadata, body.strip()


def convert_prompt_body(body: str) -> str:
    """Map prompt placeholders to Antigravity CLI's {{args}} syntax."""
    # Replace VS Code input variables: ${input:var:placeholder} or ${input:var}
    body = re.sub(r"\$\{input:[^}]*\}", "{{args}}", body)
    # Replace Pi / bash argument placeholders: $@, $ARGUMENTS, $1, ${1:-...}
    body = re.sub(r"\$(@|ARGUMENTS|1\b|\{[^}]*\})", "{{args}}", body)
    return body


def toml_escape(text: str) -> str:
    """Escape text for TOML multi-line string."""
    # Escape triple double-quotes
    return text.replace('"""', r'\"\"\"')


def convert_file(md_path: Path, output_dir: Path) -> Path:
    """Convert a single markdown prompt file to a TOML command file."""
    content = md_path.read_text(encoding="utf-8")
    metadata, body = extract_frontmatter(content)

    name = md_path.stem
    desc = metadata.get("description", "")

    if not desc:
        # Fallback to the first non-empty line
        for line in body.splitlines():
            cleaned = line.strip().lstrip("#").strip()
            if cleaned:
                desc = cleaned[:80]
                break
        if not desc:
            desc = name

    body = convert_prompt_body(body)

    desc_escaped = desc.replace("\\", "\\\\").replace('"', '\\"')
    body_escaped = toml_escape(body)

    toml_content = f'description = "{desc_escaped}"\nprompt = """\n{body_escaped}\n"""\n'

    out_file = output_dir / f"{name}.toml"
    out_file.write_text(toml_content, encoding="utf-8")
    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert markdown prompts to Antigravity CLI TOML commands."
    )
    parser.add_argument(
        "source_dir",
        type=Path,
        nargs="?",
        default=Path("prompts"),
        help="Directory containing markdown prompt files (default: prompts)",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        nargs="?",
        default=Path.home() / ".gemini" / "commands",
        help="Target directory for TOML files (default: ~/.gemini/commands)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed file conversion output",
    )
    args = parser.parse_args()

    source_dir: Path = args.source_dir.resolve()
    output_dir: Path = args.output_dir.expanduser().resolve()

    if not source_dir.is_dir():
        print(f"Error: source directory '{source_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clean overwrite: remove existing *.toml files in target directory
    for existing in output_dir.glob("*.toml"):
        existing.unlink()

    md_files = sorted(source_dir.glob("*.md"))
    if not md_files:
        if args.verbose:
            print(f"No .md files found in '{source_dir}'.")
        return

    count = 0
    for md_file in md_files:
        out_file = convert_file(md_file, output_dir)
        count += 1
        if args.verbose:
            print(f"  {md_file.name} -> {out_file.name}")

    if args.verbose:
        print(f"Converted {count} prompt(s) to '{output_dir}'.")


if __name__ == "__main__":
    main()
