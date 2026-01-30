.PHONY: install
install:
	@echo "Installing agent skills..."
	@mkdir -p ~/.copilot
	@rm -rf ~/.copilot/skills
	@ln -sfn $(PWD)/skills ~/.copilot/skills
	@echo "✓ Linked skills to ~/.copilot/skills"
	@mkdir -p ~/.gemini
	@rm -rf ~/.gemini/skills
	@ln -sfn $(PWD)/skills ~/.gemini/skills
	@echo "✓ Linked skills to ~/.gemini/skills"
	@echo "Done! Agent skills are now available."
