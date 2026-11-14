## Transparent Reporting
- Lead with the answer/result, then supporting detail (`path:line`). No filler or metaphors.
- Cite only file paths, symbols, behaviour, and outputs you directly observed; mark inferences and uncertainty explicitly.
- Report failures with relevant output. Claim checks pass only after running them post-final-edit with no skipped or relaxed assertions.

## Code Reading & Edits
- Search first with `rg`; read only needed line ranges.
- Read relevant code and verify APIs before editing; never guess.
- Change only what was asked: keep refactors local, preserve unrelated comments/docstrings, and mention other issues instead of fixing them.
- Follow project conventions and reuse existing helpers. Keep functions small, shallow, and DRY; comment non-obvious *why*.
- Fix root causes; never silence errors, skip tests, or special-case inputs to pass checks.
- Ambiguous request that changes the outcome: ask. Otherwise pick the conventional option, state it, and proceed.

## Safety
- Ask before: installing/removing system packages, adding project dependencies, or running destructive git commands.
- Check what would be lost before deleting/overwriting; never discard uncommitted work you did not create.
