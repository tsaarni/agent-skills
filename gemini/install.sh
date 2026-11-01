#!/bin/bash
# Install skills and prompts for Antigravity CLI.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
skills=~/.gemini/config/skills
commands=~/.gemini/commands

mkdir -p "$(dirname "$skills")"
ln -sfn "$repo/skills" "$skills"
echo "Linked skills -> $skills"

# Prompts become TOML commands, $ARGUMENTS -> {{args}}.
mkdir -p "$commands"
rm -f "$commands"/my-*.toml
for f in "$repo"/prompts/*.md; do
    desc=$(awk 'NR > 1 && /^---$/ {exit} sub(/^description: */, "")' "$f" \
        | sed -E 's/^"(.*)"$/\1/; s/\\/\\\\/g; s/"/\\"/g')
    body=$(awk 'NR==1 && /^---$/ {fm=1; next} fm && /^---$/ {fm=0; next} !fm' "$f" \
        | sed -e '/./,$!d' -e 's/\\/\\\\/g' -e 's/"""/\\"\\"\\"/g' -e 's/\$ARGUMENTS/{{args}}/g')
    printf 'description = "%s"\nprompt = """\n%s\n"""\n' "$desc" "$body" > "$commands/$(basename "$f" .md).toml"
done
echo "Converted prompts -> $commands/"
