---
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "[what the next session will be used for]"
---

Next session focus: $ARGUMENTS

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save it to `${TMPDIR:-/tmp}/agent-output/handoffs/<YYYYMMDD-HHMM>-<topic>.md`, not the current workspace. Tell the user the full file path after creating the file.

Reference content already captured in other artifacts (documents, issues, pull requests, commits, diffs) by path or URL instead of duplicating it.

If a focus is given, tailor the document to it.
