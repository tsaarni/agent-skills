# Agent Skills, Custom Instructions, and Commands

Personal reference for AI agent configuration files.


## Installing

```
make install
```

## Skills

Skills are task-specific capabilities that agents load on-demand.

### Activation

- **Progressive loading**: Agent loads skill metadata at startup, full content when needed
- **Autonomous**: Agent decides when to activate based on task
- **User consent**: Activation requires confirmation
- **Context injection**: Full `SKILL.md` content injected after activation

### File Format

Directory structure:

```
skills/skill-name/
├── SKILL.md          # Required (frontmatter + instructions)
├── scripts/          # Optional (executable scripts)
├── references/       # Optional (additional docs)
└── assets/           # Optional (configs, templates)
```

YAML frontmatter in `SKILL.md`:

```yaml
name: <skill-name>                   # Must match directory name
description: <one-line description>  # When to use this skill
```

Reference: https://agentskills.io/


## Custom Instructions

Instructions that are automatically injected into agent context.

### Activation

- **Always injected**: Loaded automatically without user action
- **Persistent**: Included with every prompt
- **Override**: Direct chat prompts override file instructions

### File Formats

#### AGENTS.md (Project-Specific)

Project context for AI agents. Works with GitHub Copilot (VS Code) and Gemini CLI.

Location:
```
project-root/
├── AGENTS.md                  # Project-wide
└── subdirectory/
    └── AGENTS.md              # Package-specific (takes precedence)
```

Format: Markdown, no frontmatter required.

Common sections: project overview, build/test commands, code style, testing instructions.

Configuration:
- **Gemini CLI:** `.gemini/settings.json`: `{ "context": { "fileName": "AGENTS.md" } }`
- **GitHub Copilot:** Auto-detected

Note: For global settings, use tool-specific formats below.

Reference: https://agents.md/

#### GEMINI.md (Gemini CLI - Global)

Global instructions for all Gemini CLI projects.

Location:
```
~/.gemini/
└── GEMINI.md
```

Format: Markdown. Supports `@file.md` imports for modularization.

Reference: https://geminicli.com/docs/cli/gemini-md/

#### Personal Instructions (GitHub Copilot - Global)

Global instructions for GitHub Copilot on GitHub.com.

Location: Web interface only at github.com/copilot (no local file).

Takes precedence over repository and organization instructions. Not available for VS Code.

Reference: https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-personal-instructions


## Custom Agents

Specialized AI personas with specific tools and instructions.

### Activation

- **Explicit switching**: User selects agent from UI or command
- **Auto-delegation** (Gemini CLI only): Main agent delegates tasks to sub-agents
- **Isolated context**: Each agent has its own configuration
- **Workflow transitions**: Handoffs between agents (GitHub Copilot only)

### File Formats

#### GitHub Copilot (VS Code)

Location:
```
.github/agents/                # Project-level
└── agent-name.agent.md

~/.vscode/User/profiles/<profile>/agents/    # User-level
└── agent-name.agent.md
```

Format (`.agent.md`):
```yaml
---
description: "Brief description"
name: "agent-name"                    # Optional: defaults to filename
tools: ["read", "write"]              # Optional
model: "Claude Sonnet 4"              # Optional
handoffs:                             # Optional
  - label: "Next Step"
    agent: "target-agent"
    prompt: "Suggested prompt"
    send: false
---

Instructions in Markdown.
Reference tools: #tool:toolName
```

Activation: Select from agent dropdown, use handoff buttons for transitions.

Reference: https://code.visualstudio.com/docs/copilot/customization/custom-agents

#### Gemini CLI Sub-agents

Requires: `~/.gemini/settings.json`: `{ "experimental": { "enableAgents": true } }`

Location:
```
.gemini/agents/                # Project-level
└── agent-name.md

~/.gemini/agents/              # User-level
└── agent-name.md
```

Format (`.md`):
```yaml
---
name: agent-name               # Unique identifier
description: "What this does"  # Used by main agent to decide when to call
kind: local                    # Optional: local (default) or remote
tools: ["read_file"]           # Optional
model: gemini-2.5-pro          # Optional
temperature: 0.2               # Optional: 0.0-2.0
max_turns: 10                  # Optional
---

System prompt in Markdown.
```

Activation: Main agent autonomously delegates tasks based on description. Sub-agent operates in separate context loop.

Warning: Experimental. May execute tools without confirmation.

Reference: https://geminicli.com/docs/core/subagents/

#### Kiro CLI

Location:
```
~/.kiro/agents/
└── agent-name.json
```

Format (JSON):
```json
{
  "name": "agent-name",
  "description": "What this does",
  "prompt": "System prompt" or "file://./prompt.md",
  "tools": ["read", "write", "@git"],
  "resources": [
    "file://README.md",
    "skill://.kiro/skills/**/SKILL.md"
  ]
}
```

Activation: `/agent swap <name>` or keyboard shortcuts.

Reference: https://kiro.dev/docs/cli/custom-agents/configuration-reference/


## Custom Commands

Reusable prompts invoked on-demand.

### Activation

- **Manual invocation**: User runs command explicitly
- **On-demand**: Not automatically injected
- **Parameterized**: Accept arguments at invocation time

### File Formats

#### GitHub Copilot Prompt Files

Location:
```
~/.vscode/User/globalStorage/github.copilot-chat/prompts/
└── prompt-name.prompt.md
```

Note: Synced via Settings Sync.

Format (`.prompt.md`):
```yaml
---
description: "Short description"
name: "prompt-name"                    # Optional: defaults to filename
agent: "agent"                         # Optional: ask, edit, agent, custom
tools: ["read", "write"]               # Optional
argument-hint: "Hint text"             # Optional
---

Instructions in Markdown.

Variables:
- ${workspaceFolder}, ${file}, ${selection}
- ${input:varName:placeholder}

Reference tools: #tool:toolName
```

Invocation: `/prompt-name` in Copilot Chat or Command Palette.

Reference: https://code.visualstudio.com/docs/copilot/customization/prompt-files

#### Gemini CLI Custom Commands

Location:
```
~/.gemini/commands/
└── command.toml
    subdir/
    └── namespaced.toml        # Creates /subdir:namespaced
```

Format (TOML):
```toml
description = "What this does"
prompt = """
Instructions here.
{{args}} = user arguments
!{command} = shell execution
@{file.md} = file injection
"""
```

Invocation: `/command-name arg1 arg2`

Reference: https://geminicli.com/docs/cli/custom-commands/


## References

- [Agent Skills](https://agentskills.io/)
- [Agent Skills Specification](https://agentskills.io/specification)
- [About Agent Skills - GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)
- [AGENTS.md](https://agents.md/)
- [GEMINI.md - Gemini CLI](https://geminicli.com/docs/cli/gemini-md/)
- [Personal Instructions - GitHub Copilot](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-personal-instructions)
- [Agent Skills - Gemini CLI](https://geminicli.com/docs/cli/skills/)
- [Prompt Files - GitHub Copilot](https://code.visualstudio.com/docs/copilot/customization/prompt-files)
- [Custom Commands - Gemini CLI](https://geminicli.com/docs/cli/custom-commands/)
- [Custom Agents - GitHub Copilot](https://code.visualstudio.com/docs/copilot/customization/custom-agents)
- [Sub-agents - Gemini CLI](https://geminicli.com/docs/core/subagents/)
- [Custom Agents - Kiro CLI](https://kiro.dev/docs/cli/custom-agents/configuration-reference/)
