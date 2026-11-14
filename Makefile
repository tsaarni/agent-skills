.PHONY: install backup

install:
	@gemini/install.sh
	@kiro/install.sh
	@pi-coding-agent/install.sh
	@copilot/install.sh
	@claude/install.sh

backup:
	@gemini/backup.sh
	@pi-coding-agent/backup.sh
	@kiro/backup.sh
	@sandboxes/gravelpit/backup.sh
