---
name: code-review
description:  Use this skill to review code. It supports both local changes (staged or working tree) and remote Pull Requests (by ID or URL). It focuses on correctness, maintainability, and adherence to project standards.
---

# Code Reviewer

This skill guides the agent in conducting professional and thorough code reviews for both local development and remote Pull Requests.

## Critical Constraints
- **Read-Only Operation**: Do NOT modify files, commit changes, or submit reviews automatically unless explicitly instructed by the user. Your primary goal is to analyze and report.
- **Focus on Signal**: Limit output to actionable, high-value findings. Avoid listing files without insights.
- **Mandatory File References**: Every finding **must** cite its location using the exact format `path/to/file:lineno` (single line) or `path/to/file:startline-endline` (range). Never mention a file or issue without a precise location reference.

## Tooling & Environment
This skill adapts to your environment. Use the tools available to you to accomplish the goals in each step:
- **GitHub Copilot / MCP Environments**: Prioritize dedicated tools like `github-pull-request_activePullRequest`, `get_changed_files`, or `github-pull-request_doSearch`.
- **Gemini CLI / Terminal Environments**: Utilize `run_shell_command` to execute standard CLI tools (e.g., `git`, `gh`) and built-in filesystem tools (`read_file`, `grep_search`, `glob`) to gather context.

## Step 0: Pre-Review Setup (Context Gathering)

Before beginning the review:

1. **Read project guidelines**: Check for `README.md`, `CONTRIBUTING.md`, or similar files to understand project standards, coding conventions, architecture, and changelog/release note requirements.
2. **Understand scope**: Clarify with the user what aspects are most important to review (e.g., "focus on security", "check API design", "verify test coverage").
3. **Identify technology stack**: Note the primary languages, frameworks, and tools involved to apply appropriate review criteria.

## Step 1: Determine Review Target

Identify what the user wants you to review by checking the local environment or user request.

1. **User specified a PR?** (e.g., "Review PR #1234" or a PR URL)
   - Fetch the details of the specified PR.
   - Proceed to Step 2 (Remote PR).

2. **No PR specified, but local changes exist?**
   - Check for staged or unstaged files.
   - If changes exist, proceed to Step 2 (Local Changes).

3. **No PR specified and no local changes?**
   - List available PRs to review and ask the user.
   - Ask the user which PR to review, then loop back to target that PR.

## Step 2: Gather Changed Files

- **For Remote PRs:** Retrieve the PR description, metadata, and the actual code diff.
- **For Local Changes:** Retrieve the diff of the unstaged and staged files.

## Step 3: Read Files and Understand Intent

1. **Read PR description** (remote PRs only):
   - Extract: what problem does this solve? What's the intended behavior?
   - Note any design decisions or tradeoffs mentioned.
   - Identify any known limitations or TODOs.

2. **Examine changed files**:
   - For larger diffs where context is missing, read full contents of the relevant files.
   - Build a mental model of: what changed, why, and what it affects.

3. **Check related files**:
   - If changes modify APIs/interfaces: find and read related implementations or tests.
   - If changes affect configuration: check if config docs/schemas are updated.
   - Look for dependent code that might be affected by the changes.

4. **Identify scope of changes**:
   - How many files changed? (broad vs focused)
   - Atomic change or mixed concerns?
   - Does it touch build/config/docs in addition to code?

5. **Study Existing Conventions**:
   - Briefly examine surrounding code or similar files to infer implicit coding conventions, formatting, and structural patterns.
   - Ensure you understand how things are "already done" in this specific project to enforce consistency.

## Step 4: In-Depth Analysis

Apply the following analysis pillars. For each, look for specific patterns and issues:

### Core Review Pillars

- **Project Consistency & Conventions**: Does the code match the established style of the repository?
  - Alignment with existing architectural patterns and file structures?
  - Naming conventions (e.g., camelCase, snake_case, specific prefixes)?
  - Idiomatic use of the language or framework as established in the surrounding codebase?
  - Does it introduce a new library/framework when an existing one is already used for the same purpose?

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
  - Flag with severity if breaking changes are intentional.

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

- **Documentation & Changelogs**:
  - Are README/API docs/comments updated to reflect changes?
  - Is the documentation written from a user's perspective and easily understandable?
  - Does the project have a convention for PRs to update a changelog or release notes? If so, does this change include those updates?
- **Dependencies**: Are new dependencies justified? Are they the latest available versions? Any concerns?
- **Scope**: Single feature/fix per PR, or mixed concerns?
- **Code Review History**: For remote PRs, check existing comments to avoid duplicates.

## Step 5: Provide Comprehensive Summary

Structure your findings with clear actionability and priority. Present this summary to the user.

### Format

**Summary**:
- Concise 2-3 sentence overview of what was changed and its purpose.
- Indicate overall quality level (e.g., "Well-structured implementation" or "Needs revision before merge").

**Findings**:

#### [Critical] Must fix before merge
- Bugs or logic errors that break functionality.
- Security vulnerabilities.
- Breaking changes without justification or migration path.
- Missing required tests.
- Data loss/corruption risks.
  - *Format: Always cite `path/to/file:lineno` or `path/to/file:startline-endline` for every finding. Include a short code snippet where helpful.*

#### [Improvements] Should consider
- Code quality issues (maintainability, duplication, clarity).
- Performance optimizations.
- Better error handling.
- API design improvements.
- Test coverage gaps.
  - *Format: Cite `path/to/file:lineno` or `path/to/file:startline-endline` for every suggestion. Actionable; good to fix but not blockers.*

#### [Nitpicks] Nice to have
- Minor formatting/style issues.
- Comment clarity.
- Naming suggestions.
- *Format: Cite `path/to/file:lineno` or `path/to/file:startline-endline`. Optional improvements; consider only if time/energy permits.*

**Backwards Compatibility Notes** (if applicable):
- Summary of any breaking changes.
- Migration path or deprecation timeline.

**Additional Context** (if applicable):
- Dependencies or follow-up work needed.
- Related files that might need updates.
- Questions for the author (if understanding is unclear).

### Conclusion & Recommendation

**Recommendation**:
- Clear statement of what needs to happen next (e.g., "Ready to merge", "Requires addressing critical issues", "Approve with feedback").
- If requesting changes, prioritize by severity (critical first).

---

## Best Practices for the Agent

### Do's:
- ✓ Read the entire PR description and commit message before reviewing code.
- ✓ Always cite exact locations using `path/to/file:lineno` or `path/to/file:startline-endline` — never reference a file without a precise line number or range.
- ✓ Suggest concrete improvements, not just "this is bad".
- ✓ Consider context: is this a quick fix or critical feature? Is the author junior/new to codebase?
- ✓ Acknowledge good patterns and practices you notice.
- ✓ Check if this change aligns with project architecture/philosophy.
- ✓ Verify test quality, not just coverage percentage.

### Don'ts:
- ✗ Don't nitpick style if there's an automated formatter available.
- ✗ Don't suggest refactoring unrelated code (keep review focused).
- ✗ Don't ignore related files that might need updates (migrations, docs, config).
- ✗ Don't miss test files—they're code too and need review.
- ✗ Don't assume obvious intent; ask if unclear.
- ✗ Don't focus solely on code—check for security, performance, maintainability as a whole.
