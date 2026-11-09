---
description: Review a PR, branch, commits, local changes or files for bugs and breaking changes.
argument-hint: "[PR number/URL | branch | commit range | path] [focus or extra instructions]"
---

# Review changes

Arguments: $ARGUMENTS

Find real defects in the change and report them with evidence. Don't narrate what the code does.

If the focus is `security`, run only angles 2, 3 and 5, and report only Confirmed security findings.

## 1. Get the code and context

Output directories, set them again in each shell command that uses them:
`R=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null | tr / _ | grep . || basename "$PWD"); O=$HOME/.local/state/agent-output/$R; S=/tmp/agent-output/$R`

- `$O` is kept across reboots. It holds `architecture.md`, the report, and in `$O/reviews/<target>/` the files needed to repeat any test you ran: scripts, manifests, test inputs, patches, and small captured output used as evidence.
- `$S` is scratch. It holds worktrees, built binaries, generated certificates and keys, downloaded logs, and anything over about 1 MB that is not evidence for a finding.
- Scripts take the scratch location from a variable instead of hardcoding it, and generate test certificates and keys at setup instead of storing them. Record the commit SHAs and build commands for any binary you build, so it can be rebuilt.

Use the target from the arguments. Without one, review committed and uncommitted changes against the default branch (`git rev-parse --abbrev-ref origin/HEAD`, else `main` or `master`). Use `-W` so each hunk includes its whole enclosing function. Drop `-W` if the diff gets too large.

- PR: `gh pr view N --json number,title,body,baseRefName,state,isDraft,url`. Check it out in a worktree so the user's checkout stays untouched:
  `WT=$S/worktrees/pr-N; git worktree add --detach "$WT" && cd "$WT" && gh pr checkout N --detach`. Then run `git diff -W $(git merge-base origin/<base> HEAD)`. When done, run `git worktree remove "$WT"`.
- Branch: `git diff -W $(git merge-base <base> <branch>) <branch>`. Commits: `git diff -W A..B` or `git show -W <sha>`.
- Local work: `git diff -W $(git merge-base <base> HEAD)`, plus the untracked files from `git status --short`, read whole. In a repo with no commits, use `git diff -W --cached` plus the untracked files.
- Paths, or no git: review the files as they are.

Never stash, reset or switch the user's branch.

Collect whatever context exists, and skip what doesn't:
- Intent: PR title and body, linked issues (`gh issue view M`), commit messages (`git log --format='%h %s%n%b' <base>..HEAD`).
- PR discussion: `gh pr view N --comments`, plus unresolved inline threads:
  ```bash
  gh api graphql -F owner='{owner}' -F repo='{repo}' -F pr=N -f query='
  query($owner:String!,$repo:String!,$pr:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$pr){
   reviewThreads(first:100){nodes{isResolved isOutdated path line comments(first:20){nodes{author{login} body}}}}}}}' \
   --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)'
  ```
- CI: `gh pr checks N`. For each failure, run `gh run view <run-id> --log-failed | tail -100` and decide whether the diff caused it.
- Project rules: AGENTS.md, CLAUDE.md, GEMINI.md and CONTRIBUTING.md, at the repo root and in ancestor directories of the changed files.
- If it is cheap and safe, build or test the touched packages.

## 2. Find and verify issues

Note for yourself what the change is meant to do. Then look at the change from each angle below. If you can, run the angles as parallel subagents. Give each one the diff command, the review root, the intent note and its angle. Otherwise go through the angles one after another, and say so in "Not checked". Keep the angles independent: if two angles flag the same line for different reasons, keep both. Pass on every candidate that has a nameable failure scenario, even a half-believed one. The verify step filters them. Dropping candidates early is the main cause of missed bugs.

1. Line by line: read every hunk and its enclosing function. A bug in an unchanged line of a touched function counts when the change relies on it or exposes it. Look for inverted conditions, off-by-one errors, nil dereference, copy-paste using the wrong variable, swallowed errors, a missing await, close or unlock, unescaped regex characters, and state left inconsistent on an error path.
2. Removed behavior: for each deleted or replaced line, name the invariant it enforced. Then find where the new code re-establishes it. A dropped guard, a narrower validation or a deleted test case counts as a candidate.
3. Callers and contracts: grep for the callers of each changed function or type and the uses of each changed field. Look for new preconditions, a changed return shape or error, and ordering assumptions. Check for changes to a serialized format, config, CLI or public API that break existing users. If a field is added to a shared type, check that it fits every parent type that embeds it. If the change adds or edits a wrapper (cache, proxy, decorator, adapter), check that each method calls the wrapped object rather than going back through a registry or global, and that it forwards every method its callers use.
4. Concurrency and resources: races, deadlocks, a lock scope that got smaller, leaks, unbounded growth that an input can drive.
5. Security: first find who controls what. Read the trust boundaries in `$O/architecture.md`, SECURITY.md or threat-model docs if they exist. Otherwise derive them: in a Kubernetes controller, for example, tenant-namespace resources are untrusted and cluster-admin config is trusted. Then trace each new or changed input to its sinks: query, path, shell command, template, deserializer, outbound URL host, redirect, log of secrets. Check authn/authz decisions and their bypasses, privilege transitions, TLS and certificate validation, crypto and randomness, secrets in code, and sensitive data in errors, logs or responses. Find the sanitizers, validators and auth helpers the codebase already uses, and flag new code that skips or re-implements them.
6. Efficiency: O(n²) or N+1 work on hot paths, repeated I/O or computation, independent operations run in sequence, blocking work added at startup, and long-lived closures that keep a large scope alive.
7. Language pitfalls: for example Go loop-variable capture or a nil-map write, Python mutable defaults or late-binding closures, JS falsy-zero or `==`, float equality, and timezone or DST handling.
8. Intent, tests, docs: does the code do what the description says, and is anything missing? Do the tests cover the new behavior, including failure paths? Do docs, changelog or migration notes need updating? Do the changes address the unresolved review threads? Did the diff cause a CI failure? Does the change break a project rule? If so, quote the rule.
9. Design and reuse: the change patches a symptom instead of the root cause, for example by adding special cases to shared code. It re-implements a helper the codebase already has: grep the shared and utility modules and the files next to the change, and name the helper. It adds redundant state, dead code, or near-copies of the same code. Or it is over-engineered.

Each candidate needs `file:line`, a one-line claim, and a failure scenario: the concrete input or state that leads to a wrong result, a crash, or a broken caller. For a non-bug, state the concrete cost instead. For a security finding, the scenario names the attacker, what they control, the entry point, and what they gain.

Merge candidates that share a root cause. Then check each one against the code again, with a fresh subagent if you can:
- Confirmed: you can name the trigger and the wrong outcome.
- Unconfirmed: the mechanism is real but the trigger depends on timing, config or environment. Keep it, and state what would confirm it.
- Refuted: quote the line that proves it. The code doesn't do that, a guard elsewhere handles it, it can't happen, a linter or compiler catches it, or it is only style. Also refute an issue that existed before this change, unless it sits in a touched function and the change relies on it or exposes it. Drop refuted findings.

Don't refute a finding only because it depends on runtime state, when that state is realistic: an error path, a cold cache, a missing optional field, a race.

Refute a security finding when the attacker would need to control something trusted: env vars, CLI flags, operator-owned config or files. Also refute log injection of non-secret data, regex built from input (unless it can be catastrophic backtracking on a hot path), SSRF that controls only the path, theoretical timing attacks, outdated dependencies, memory safety in a memory-safe language, missing hardening without a concrete exploit, and code that runs only in tests. Shell and workflow injection count only with a concrete path for untrusted input. Resource exhaustion that an untrusted party can drive is a real finding: keep it.

Finally, re-read the diff once more as a fresh reviewer, looking only for issues not already found. First passes often miss these: code that was moved or extracted and lost a guard; a config default that flipped; a predicate with side effects; a default value evaluated only once; non-deterministic hashing or ordering; setup and teardown that don't match in tests.

Sort by severity. Correctness bugs come before cleanup.
- Critical: a bug, security hole, data loss or breaking change. Must be fixed.
- Should fix: robustness, missing tests for new behavior, performance or maintainability.
- Minor: only if there are few of them and each is worth the reader's time.

A clean review is a valid result. Don't pad it. If more than about 15 findings remain, the chat list shows the most severe ones and the report holds them all.

## 3. Report

Writing style for everything the user reads:
- State facts directly. Use active voice and short sentences: "`parse()` returns nil when the header is empty. `handle()` dereferences it at line 88."
- Name the exact function, field, input and outcome. Write "can panic" only for an unconfirmed finding.
- No em dashes, no praise, no filler, no marketing tone. Don't restate the diff.
- Use backticks for identifiers and `path:line`. Quote at most a few lines of code.

Always write the full report to `$O/reviews/<target>.md`, where target is `pr-N`, the branch name (with `/` replaced by `_`), or `local`. It contains:
- Summary: what the change does, and the verdict (ready, needs changes, or blocked).
- Findings, grouped by severity. Each has `path:line`, the claim, the failure scenario, a code excerpt and a suggested fix.
- Compatibility: breaking API, config or format changes, or "None found".
- Context checked: the review threads (addressed or not), CI status, the rules files read, the tests run, and what was not checked.
- Reproduction: for each test you ran, the files in `$O/reviews/<target>/`, the commit SHAs and build commands, and how to run it again.

Then show a short version in chat:

```
<Verdict in one sentence>. <What the change does in one sentence.>

1. Critical: `path:line` <claim in one line>
2. Should fix: `path:line` <claim> (unconfirmed)
...

Compatibility: <one line>
Not checked: <one line, if anything>
Full report: <path>

Go through the findings one by one?
```

If the user says yes, present one finding per message, then stop and wait:

```
### 2 of 5. Should fix: <short title>
`path:line`
<3 to 8 lines of the relevant code>

<What goes wrong, in 1 to 3 sentences, with a concrete input or state.>

Fix: <one sentence, or a small code snippet>
```

End each finding with "next, ask a question, fix it, or skip?" Answer questions about the current finding and stay on it until the user moves on. Apply a fix only when asked, and only in the user's own checkout. In a PR worktree, offer a patch instead. After the last finding, list which findings the user fixed, skipped or left open.

Never write to GitHub. Use `gh` only to read.
