.PHONY: install

UNAME := $(shell uname)
ifeq ($(UNAME),Darwin)
	PROMPTS_DIR := $(HOME)/Library/Application Support/Code/User/prompts
else
	PROMPTS_DIR := $(HOME)/.config/Code/User/prompts
endif

PWD := $(shell pwd)

# Skills not installed for Claude Code, space separated
CLAUDE_SKIP_SKILLS :=

install:
	@echo "Installing agent customizations..."

	# Antigravity CLI skills
	@mkdir -p "$(HOME)/.gemini/config"
	@rm -rf "$(HOME)/.gemini/config/skills"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.gemini/config/skills"
	@echo "Linked skills -> $(HOME)/.gemini/config/skills"

	# Antigravity CLI commands
	@uv run gemini/convert-prompts.py prompts "$(HOME)/.gemini/commands"
	@echo "Converted prompts -> $(HOME)/.gemini/commands/"

	# Kiro CLI
	@mkdir -p "$(HOME)/.kiro"
	@rm -rf "$(HOME)/.kiro/skills" "$(HOME)/.kiro/prompts"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.kiro/skills"
	@ln -sfn "$(PWD)/prompts" "$(HOME)/.kiro/prompts"
	@echo "Linked prompts & skills -> $(HOME)/.kiro/"

	# Pi Agent
	@mkdir -p "$(HOME)/.agents" "$(HOME)/.pi/agent"
	@rm -rf "$(HOME)/.agents/skills" "$(HOME)/.pi/agent/prompts"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.agents/skills"
	@ln -sfn "$(PWD)/prompts" "$(HOME)/.pi/agent/prompts"
	@echo "Linked prompts & skills -> ~/.pi/agent/ & ~/.agents/"

	# Copilot prompts
	@rm -rf "$(PROMPTS_DIR)"
	@mkdir -p "$(PROMPTS_DIR)"
	@for f in prompts/*.md; do \
		cp "$$f" "$(PROMPTS_DIR)/$$(basename "$$f" .md).prompt.md"; \
	done
	@echo "Copied prompts -> $(PROMPTS_DIR)/"

	# Claude Code skills (linked one by one, ~/.claude/skills also holds skills from other sources)
	@mkdir -p "$(HOME)/.claude/skills"
	@find "$(HOME)/.claude/skills" -maxdepth 1 -type l -lname "$(PWD)/skills/*" -delete
	@for d in skills/*/; do \
		name=$$(basename "$$d"); \
		case " $(CLAUDE_SKIP_SKILLS) " in *" $$name "*) continue ;; esac; \
		ln -sfn "$(PWD)/$${d%/}" "$(HOME)/.claude/skills/$$name"; \
	done
	@echo "Linked skills -> $(HOME)/.claude/skills/"

	# Claude Code commands
	@mkdir -p "$(HOME)/.claude/commands"
	@for f in prompts/*.md; do \
		sed -E 's/\$$\{input:[^}]*\}/$$ARGUMENTS/g' "$$f" > "$(HOME)/.claude/commands/$$(basename "$$f")"; \
	done
	@echo "Copied prompts -> $(HOME)/.claude/commands/"

	@echo "Done!"
