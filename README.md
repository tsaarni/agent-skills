# Agent Skills, Custom Instructions, and Commands

Personal repository and reference for AI agent configuration files.


## Installation

```
make install
```


## Skills

Skills are task-specific capabilities that agents load on-demand.

Activation behavior:

- Progressive loading: Agent loads skill metadata at startup, full content when needed
- Autonomous: Agent decides when to activate based on task
- Context injection: Full `SKILL.md` content injected after activation, files from `scripts/`, `references/`, `assets/` are read as needed

Directory structure:

```
skills/skill-name/
├── SKILL.md          # Required (frontmatter + instructions)
├── scripts/          # Optional (executable scripts)
├── references/       # Optional (additional docs)
└── assets/           # Optional (configs, templates)
```

Format: Markdown with frontmatter.
```yaml
---
name: <skill-name>                   # Must match directory name
description: <one-line description>  # When to use this skill
---

Detailed instructions in Markdown.
```

Directories used by different agents:

| Agent | Directories |
|-------|-------------|
| GitHub Copilot (VS Code & CLI) | `~/.copilot/skills/`, `~/.claude/skills/` (user)<br>`.github/skills/`, `.claude/skills/` (project) |
| Gemini CLI | `~/.gemini/skills/` (user)<br>`.gemini/skills/` (project) |
| Kiro CLI (Amazon Q) | Requires `skill://` resource in custom agent configuration file |

References:
- [Agent Skills Specification](https://agentskills.io/specification)
- GitHub Copilot (VS Code & CLI): [About Agent Skills - GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)
- Gemini CLI: [Agent Skills - Gemini CLI](https://geminicli.com/docs/cli/skills/)
- Kiro CLI (Amazon Q): [Custom Agents - Kiro CLI](https://kiro.dev/docs/cli/custom-agents/configuration-reference/)



## Custom Instructions

Instructions that are automatically injected into context.

Activation behavior:

- Always injected: Loaded automatically without user action

Format:
- Markdown
- Copilot instructions with [optional frontmatter](https://code.visualstudio.com/docs/copilot/customization/custom-instructions#_instructions-file-format) (`description`, `name`, `applyTo`)

Directories used by different agents:

| Agent | Directories |
|-------|-------------|
| GitHub Copilot (VS Code) | `~/.config/Code/User/profiles/Default/*.instructions.md`<sup>1</sup> (user)<br>`.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, `AGENTS.md` (project) |
| GitHub Copilot (CLI) | `~/.copilot/copilot-instructions.md` (user) |
| Gemini CLI | `~/.gemini/GEMINI.md` (user)<br>`GEMINI.md` (project, root and subdirectories) |
| Kiro CLI (Amazon Q) | `~/.kiro/steering/` (user)<br>`.kiro/steering/` (project) |

<sup>1</sup> On macOS, replace `~/.config/Code/User/` with `~/Library/Application Support/Code/User/`

References:
- [GitHub Copilot Repository Custom Instructions](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot)
- [GitHub Copilot Personal Instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-personal-instructions)
- [Use custom instructions in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)
- [Gemini CLI GEMINI.md](https://geminicli.com/docs/cli/gemini-md/)
- [Kiro Steering](https://kiro.dev/docs/steering/)

## Custom Agents

Specialized personas with specific tools and instructions.

Activation behavior:

- Explicit switching: User selects agent from UI or command
- Auto-delegation (Gemini CLI only): Main agent delegates tasks to sub-agents
- Workflow transitions (GitHub Copilot only): Handoffs between agents

| Agent | Locations | Format | Notes |
|-------|-----------|--------|-------|
| GitHub Copilot (VS Code) | `~/.config/Code/User/profiles/Default/agents/`<sup>1</sup> (user)<br>`.github/agents/*.agent.md` (project) | [Markdown with YAML frontmatter](https://code.visualstudio.com/docs/copilot/customization/custom-agents#_agent-file-format)<br>Properties: `name`, `description`, `tools`, `model`, `handoffs`, `target` | Supports handoffs for workflow transitions. |
| GitHub Copilot (CLI) | `~/.copilot/agents/` (user)<br>`.github/agents/*.agent.md` (project) | Same format | Access via `/agent` command or `--agent` flag. |
| Gemini CLI | `~/.gemini/agents/*.md` (user)<br>`.gemini/agents/*.md` (project) | [Markdown with YAML frontmatter](https://geminicli.com/docs/core/subagents/#agent-file-format)<br>Properties: `name`, `description`, `kind`, `tools`, `model`, `temperature`, `max_turns`, `timeout_mins` | Experimental feature. Requires `enableAgents: true` in settings. |
| Kiro CLI | `~/.kiro/agents/*.json` (user)<br>`.kiro/agents/*.json` (project) | [JSON](https://kiro.dev/docs/cli/custom-agents/configuration-reference/)<br>Properties: `name`, `description`, `prompt`, `tools`, `resources`, `mcpServers`, `model`, `keyboardShortcut`, `welcomeMessage` | Switch via `/agent swap <name>` or keyboard shortcuts. Supports `file://` URIs for prompt. Local agents override global with same name |

<sup>1</sup> On macOS, replace `~/.config/Code/User/` with `~/Library/Application Support/Code/User/`

References:
- GitHub Copilot: [About agents](https://docs.github.com/en/copilot/concepts/agents/about-agents), [Custom agents in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-agents)
- GitHub Copilot (GitHub.com & CLI): [Creating custom agents](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-custom-agents), [About Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli)
- Gemini CLI: [Sub-agents](https://geminicli.com/docs/core/subagents/)
- Kiro CLI: [Custom Agents](https://kiro.dev/docs/cli/custom-agents/configuration-reference/)


## Custom Commands

Reusable prompts invoked on-demand.

Activation behavior:

- Manual invocation: User runs command explicitly
- On-demand: Not automatically injected
- Parameterized: Accept arguments at invocation time

| Agent | Locations | Format | Notes |
|-------|-----------|--------|-------|
| GitHub Copilot (VS Code) | `~/.config/Code/User/profiles/Default/`<sup>1</sup> (user)<br>`.github/prompts/*.prompt.md` (project) | [Markdown with YAML frontmatter](https://code.visualstudio.com/docs/copilot/customization/prompt-files#_prompt-file-format)<br>Properties: `name`, `description`, `agent`, `tools`, `model`, `argument-hint` | Invocation: `/prompt-name` in Chat or Command Palette. Supports variables: `${workspaceFolder}`, `${file}`, `${selection}`, `${input:var:placeholder}`. Can reference other files with Markdown links |
| GitHub Copilot (CLI) | N/A | N/A | N/A |
| Gemini CLI | `~/.gemini/commands/*.toml` (user)<br>`.gemini/commands/*.toml` (project) | [TOML](https://geminicli.com/docs/cli/custom-commands/#toml-file-format-v1)<br>Properties: `description`, `prompt` | Invocation: `/command-name args`. Supports `{{args}}` placeholders, `!{shell}` execution, `@{file}` injection. Subdirectories create namespaced commands with `:` separator |
| Kiro CLI | N/A | N/A | N/A |

<sup>1</sup> On macOS, replace `~/.config/Code/User/` with `~/Library/Application Support/Code/User/`

References:
- GitHub Copilot (VS Code): [Prompt Files - GitHub Copilot](https://code.visualstudio.com/docs/copilot/customization/prompt-files)
- Gemini CLI: [Custom Commands - Gemini CLI](https://geminicli.com/docs/cli/custom-commands/)
