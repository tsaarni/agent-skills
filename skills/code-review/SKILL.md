---
name: code-review
description:  Use this skill to review code. It supports both local changes (staged or working tree) and remote Pull Requests (by ID or URL). It focuses on correctness, maintainability, and adherence to project standards.
---

# Code Reviewer

This skill guides the agent in conducting professional and thorough code reviews for both local development and remote Pull Requests.

## Step 0: Pre-Review Setup (Context Gathering)

Before beginning the review:

1. **Read project guidelines**: Check for `README.md`, `CONTRIBUTING.md`, or similar files to understand project standards, coding conventions, and architecture.
2. **Understand scope**: Clarify with the user what aspects are most important to review (e.g., "focus on security", "check API design", "verify test coverage").
3. **Identify technology stack**: Note the primary languages, frameworks, and tools involved to apply appropriate review criteria.

## Step 1: Determine Review Target

Execute:
```bash
git status
```

**Decision logic (check in this order):**

1. **User specified a PR?** (e.g., "Review PR #1234" or a PR URL)
   - Use tool: `github-pull-request_activePullRequest` or `github-pull-request_openPullRequest` to fetch PR details
   - Proceed to Step 2 (Remote PR)

2. **No PR specified, but local changes exist?** (staged or unstaged files shown in `git status`)
   - Proceed to Step 2 (Local Changes)

3. **No PR specified and no local changes?**
   - Use tool: `github-pull-request_formSearchQuery` with natural language "all open pull requests"
   - Use tool: `github-pull-request_doSearch` to execute the search
   - Display results using `github-pull-request_renderIssues`
   - Ask user which PR to review, then proceed to Step 1.1

If none of the above, clarify with the user what they want reviewed.


## Step 2: Gather Changed Files

**For Remote PRs:**
- Use tool: `github-pull-request_activePullRequest` to get PR metadata, description, review comments
- The PR object contains: title, description, changed files list, review status, CI/CD results
- Store this context for use in analysis

**For Local Changes:**
- Use tool: `get_changed_files` to retrieve staged and unstaged changes with diffs
- Alternatively execute: `git diff --name-only` (unstaged) and `git diff --cached --name-only` (staged)
- Get diffs with context: `git diff` (unstaged) or `git diff --cached` (staged)

## Step 3: Read Files and Understand Intent

1. **Read PR description** (remote PRs only):
   - Extract: what problem does this solve? What's the intended behavior?
   - Note any design decisions or tradeoffs mentioned
   - Identify any known limitations or TODOs

2. **Examine changed files**:
   - Use tool: `read_file` to read full contents of modified files (focus on actual changes, not entire files)
   - For large files, read only relevant sections around the changes
   - Build a mental model of: what changed, why, and what it affects

3. **Check related files**:
   - If changes modify APIs/interfaces: read related implementations or tests
   - If changes affect configuration: check if config docs/schemas are updated
   - Look for dependent code that might be affected by the changes

4. **Identify scope of changes**:
   - How many files changed? (broad vs focused)
   - Atomic change or mixed concerns?
   - Does it touch build/config/docs in addition to code?


## Step 4: In-Depth Analysis

Apply the following analysis pillars. For each, look for specific patterns and issues:

### Core Review Pillars

- **Correctness**: Does the code achieve its stated purpose?
  - Logic errors or off-by-one bugs?
  - Unhandled null/nil pointers?
  - Missing return statements or incomplete logic branches?
  - Type safety issues?

- **Maintainability**: Is the code clean and understandable?
  - Duplicate code that could be refactored?
  - Inconsistent variable/function naming?
  - Overly complex functions (consider recommending extracting smaller functions)?
  - Dead code or unused variables?

- **Readability**: Is the code easy to follow?
  - Complex logic without explanatory comments?
  - Non-obvious performance optimizations that need documentation?
  - Correct comment accuracy (verify comments match actual code)?

- **API Design**: If introducing or modifying public APIs/interfaces:
  - Consistent with existing patterns in the codebase?
  - Parameters in logical order?
  - Return types intuitive and consistent?
  - Clear error handling contract (what exceptions/errors returned)?

- **Backwards Compatibility & Breaking Changes**:
  - Removing or renaming public methods/constants?
  - Changing method signatures?
  - Modified return types or behavior changes?
  - Database migrations or data format changes need migration code?
  - Flag with severity if breaking changes are intentional

- **Efficiency & Performance**:
  - Obvious algorithmic inefficiencies (O(n²) when O(n) is possible)?
  - Unnecessary loops, repeated computations in loops?
  - Memory leaks (unreleased resources, event listener cleanup)?
  - N+1 query problems in database code?
  - Large data structure copies that could use references?

- **Security**:
  - Injection risks: parameterized queries used e.g. SQL injection, shell escaping?
  - Hardcoded secrets, API keys, or credentials?
  - Insufficient input validation?
  - Privilege escalation paths?
  - Use of weak cryptographic functions?
  - Sensitive data logging?
  - CORS/CSRF protections if handling requests?

- **Edge Cases & Error Handling**:
  - Empty collections/arrays handled?
  - Negative numbers or boundary conditions?
  - Timeout handling for external calls?
  - Partial failures in batch operations?
  - Graceful degradation on errors?
  - User-facing error messages (helpful, not exposing internals)?

- **Tests & Test Coverage**:
  - Are new features covered by tests?
  - Happy path AND error cases tested?
  - Sufficient assertions (not just "did it run")?
  - Tests verify the fix itself, not just pre-requisites?
  - Test names describe what is being tested?
  - Mocks/stubs realistic and maintainable?

### Additional Considerations

- **Documentation**: Are README/API docs/comments updated to reflect changes?
- **Dependencies**: Are new dependencies justified? Are they the latest available versions? Any security concerns?
- **Scope**: Single feature/fix per PR, or mixed concerns?
- **Code Review History**: For remote PRs, check existing comments to avoid duplicates

## Step 5: Provide Comprehensive Summary

Structure your findings with clear actionability and priority:

### Format

**Summary**:
- Concise 2-3 sentence overview of what was changed and its purpose
- Indicate overall quality level (e.g., "Well-structured implementation" or "Needs revision before merge")

**Findings**:

**🔴 Critical (Must fix before merge):**
- Bugs or logic errors that break functionality
- Security vulnerabilities
- Breaking changes without justification or migration path
- Missing required tests
- Data loss/corruption risks
- Format: Include file paths, line numbers, and specific code references where possible

**🟡 Improvements (Should consider):**
- Code quality issues (maintainability, duplication, clarity)
- Performance optimizations
- Better error handling
- API design improvements
- Test coverage gaps
- Format: Actionable suggestions; good to fix but not blockers

**🔵 Nitpicks (Nice to have):**
- Minor formatting/style issues
- Comment clarity
- Naming suggestions
- Format: Optional improvements; consider only if time/energy permits

**Backwards Compatibility Notes** (if applicable):
- Summary of any breaking changes
- Migration path or deprecation timeline

**Additional Context** (if applicable):
- Dependencies or follow-up work needed
- Related files that might need updates
- Questions for the author (if understanding is unclear)

### Conclusion & Recommendation

**Status**: Choose one:
- ✅ **Approved**: Ready to merge
- ⏸️ **Request Changes**: Requires addressing critical/major issues before merge
- 📝 **Approve with Feedback**: Approved but authors should consider suggestions for next iteration
- ❓ **Needs More Info**: Missing context to review properly

**Recommendation**:
- Clear statement of what needs to happen next
- If requesting changes, prioritize by severity (critical first)

---

## Best Practices for the Agent

### Do's:
- ✓ Read the entire PR description and commit message before reviewing code
- ✓ Provide specific line numbers and file references when citing issues
- ✓ Suggest concrete improvements, not just "this is bad"
- ✓ Consider context: is this a quick fix or critical feature? Is the author junior/new to codebase?
- ✓ Acknowledge good patterns and practices you notice
- ✓ Check if this change aligns with project architecture/philosophy
- ✓ Verify test quality, not just coverage percentage

### Don'ts:
- ✗ Don't nitpick style if there's an automated formatter available
- ✗ Don't suggest refactoring unrelated code (keep review focused)
- ✗ Don't ignore related files that might need updates (migrations, docs, config)
- ✗ Don't miss test files—they're code too and need review
- ✗ Don't assume obvious intent; ask if unclear
- ✗ Don't focus solely on code—check for security, performance, maintainability as a whole

### Common Issues to Flag:
- ⚠️ Changes without corresponding test additions
- ⚠️ Error handling that silently fails (no logging, no exception re-throw)
- ⚠️ Commented-out code (should be removed or explained)
- ⚠️ Large commits mixing multiple concerns (suggest breaking into smaller PRs)
- ⚠️ Documentation that doesn't match implementation
- ⚠️ New dependencies without justification
