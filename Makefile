.PHONY: install

UNAME := $(shell uname)
ifeq ($(UNAME),Darwin)
	PROMPTS_DIR := $(HOME)/Library/Application Support/Code/User/prompts
else
	PROMPTS_DIR := $(HOME)/.config/Code/User/prompts
endif

PWD := $(shell pwd)

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

	@echo "Done!"
