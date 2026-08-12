.PHONY: install

UNAME := $(shell uname)
ifeq ($(UNAME),Darwin)
	PROMPTS_DIR := $(HOME)/Library/Application Support/Code/User/prompts
else
	PROMPTS_DIR := $(HOME)/.config/Code/User/prompts
endif

PWD := $(shell pwd)

install:
	@echo "Installing agent skills..."

	# Antigravity CLI skills
	@mkdir -p "$(HOME)/.gemini"
	@rm -rf "$(HOME)/.gemini/skills"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.gemini/skills"
	@echo "Linked skills to $(HOME)/.gemini/skills"

	# Copilot prompts and instructions
	@mkdir -p "$(PROMPTS_DIR)"
	@cp -r prompts/*prompt.md "$(PROMPTS_DIR)/"
	@echo "Copied prompts to $(PROMPTS_DIR)/"
	@mkdir -p "$(HOME)/.copilot/"
	@cp instructions/copilot-instructions.md "$(HOME)/.copilot/copilot-instructions.md"
	@echo "Copied Copilot instructions to $(HOME)/.copilot/copilot-instructions.md"

	# Kiro CLI skills
	@mkdir -p "$(HOME)/.kiro"
	@rm -rf "$(HOME)/.kiro/skills"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.kiro/skills"
	@echo "Linked skills to $(HOME)/.kiro/skills"

	# Agents skills (github copilot, pi)
	@mkdir -p "$(HOME)/.agents"
	@rm -rf "$(HOME)/.agents/skills"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.agents/skills"
	@echo "Linked skills to $(HOME)/.agents/skills"

	# Pi Agent
	@mkdir -p "$(HOME)/.pi/agent/prompts"
	@cp -r prompts/*prompt.md "$(HOME)/.pi/agent/prompts/"

	@echo "Done!"
