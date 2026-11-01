#!/bin/bash
# Install skills and prompts for Claude Code.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
skills=~/.claude/skills
commands=~/.claude/commands

# Linked one by one, the directory also holds skills from other sources.
mkdir -p "$skills"
find "$skills" -maxdepth 1 -type l -lname "$repo/skills/*" -delete
for d in "$repo"/skills/*/; do
    ln -sfn "${d%/}" "$skills/$(basename "$d")"
done
echo "Linked skills -> $skills/"

# Prompts run only when invoked by the user.
mkdir -p "$commands"
rm -f "$commands"/my-*.md
for f in "$repo"/prompts/*.md; do
    sed '1s/^---$/---\ndisable-model-invocation: true/' "$f" > "$commands/$(basename "$f")"
done
echo "Converted prompts -> $commands/"
