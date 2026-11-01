#!/bin/bash
# Install skills and prompts for Pi Coding Agent.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
skills=~/.agents/skills
prompts=~/.pi/agent/prompts

mkdir -p ~/.agents ~/.pi/agent
rm -rf "$skills" "$prompts"
ln -sfn "$repo/skills" "$skills"
echo "Linked skills -> $skills"
ln -sfn "$repo/prompts" "$prompts"
echo "Linked prompts -> $prompts"
