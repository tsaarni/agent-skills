.PHONY: install backup

install:
	@gemini/install.sh
	@kiro/install.sh
	@pi-coding-agent/install.sh
	@copilot/install.sh
	@claude/install.sh

backup:
	cp ~/.kiro/agents/*.json kiro/agents/
	cp ~/.config/gravelpit/policies/* sandboxes/gravelpit/policies/
