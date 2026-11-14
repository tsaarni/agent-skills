#!/bin/bash
# Install skills, prompts, and APPEND_SYSTEM.md for Pi Coding Agent.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
skills=~/.agents/skills
prompts=~/.pi/agent/prompts
append_system=~/.pi/agent/APPEND_SYSTEM.md

mkdir -p ~/.agents ~/.pi/agent
rm -rf "$skills" "$prompts"
ln -sfn "$repo/skills" "$skills"
echo "Linked skills -> $skills"
ln -sfn "$repo/prompts" "$prompts"
echo "Linked prompts -> $prompts"
ln -sfn "$repo/pi-coding-agent/configs/agent/APPEND_SYSTEM.md" "$append_system"
echo "Linked APPEND_SYSTEM.md -> $append_system"
