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

	# Copilot CLI skills
	@mkdir -p "$(HOME)/.copilot"
	@rm -rf "$(HOME)/.copilot/skills"
	@ln -sfn "$(PWD)/skills" "$(HOME)/.copilot/skills"
	@echo "Linked skills to $(HOME)/.copilot/skills"

	# Gemini CLI skills
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
	@echo "Done!"
