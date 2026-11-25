---
name: gh-contributions
description: "Summarize a developer's GitHub contributions and engineering impact using gh-contributions."
---

# Synthesizing Developer Impact with `gh-contributions`

Use `gh-contributions` as a fast, deduplicated **index**, `gh-contributions inspect` to pull engineering evidence on pivotal items, and `gh pr diff` only when code-level detail is missing.

Both commands run GitHub queries in parallel and support `--format=markdown` so you can read their output directly without writing ad-hoc JSON parsing scripts.

## Phase 1: Build the activity index

```bash
gh-contributions --period=last-90-days --format=markdown > /tmp/contrib.md
# For another user or timeframe (e.g. q3, 2026-09, 2026):
# gh-contributions --author=<user> --period=q3 --format=markdown > /tmp/contrib.md
```

- Default to `--period=last-90-days` unless the user specifies another timeframe.
- Keep bot filtering on by default; record the `Bots filtered` count as a one-line maintenance footnote.
- If `stderr` warns `hit --limit=...`, re-run with `--limit=1000`, or split `--period` into smaller ranges using `--format=json` and merge (`jq -s 'add | unique_by(.url)' /tmp/part*.json`).
- Read `/tmp/contrib.md` to:
  - Separate **primary focus repos**, **upstream/external repos**, and **personal repos**.
  - Spot **multi-item arcs and cross-repo threads** (e.g. an issue filed in one repo and fixed upstream in another, coordinated multi-repo deprecations, release cycles, or recurring security/reliability themes).

## Phase 2: Inspect pivotal work in depth

Do not stop at titles. Pick the **10–20 highest-signal items** across each major arc (security/bug fixes, architectural features, substantive reviews, and upstream discussions — skipping routine lint/version/dependency bumps) and inspect them in one call:

```bash
gh-contributions inspect --format=markdown \
  https://github.com/owner/repo/pull/123 \
  https://github.com/owner/repo/issues/456 > /tmp/inspect.md
# Or pipe URLs on stdin; pass --author=<user> when analyzing someone other than @me.
# Adjust --max-body=800 (or 0 for unlimited) if key details are truncated.
```

- **Prefer full GitHub URLs** from `/tmp/contrib.md` over `owner/repo#123` — URLs identify PR vs. issue immediately and avoid a wasted fallback API call on issues.
- Use **Files** and **Closes** to identify which architectural subsystems were touched and connect authored issues to the PRs that resolved them.
- Use **Review**, **Inline**, and **Comment** entries to capture what the developer actually caught, verified, or shaped in code reviews and discussions.
- Only run `gh pr diff <NUM> --repo <OWNER/REPO>` when a pivotal PR has a sparse description.
- Follow cross-references (`Closes`, upstream links) to tell complete stories (diagnosis → upstream fix → downstream adoption) rather than disconnected tickets.

## Phase 3: Synthesize a compelling summary

- **Tailor the framing to the audience/goal** (e.g. performance review, maintainer nomination, manager update, or technical bio) rather than dumping a chronological list.
- **Organize by impact themes and arcs, not raw repo dumps:** Show *what problems were solved*, *why they mattered*, and *which architectural layers* the developer operated in.
- **Highlight engineering judgment & leverage:** Use concrete evidence from review comments and issue threads to show how the developer prevents bugs, verifies fixes across environments, maintains releases/governance, and influences upstream projects.
- **Keep signal clean:** Distinguish **shipped (`merged`)** from **in-progress (`open`)**, separate **upstream/org work** from **personal projects**, and summarize routine maintenance (like the filtered bot count) in a single line.
- **Back every major claim with clickable links** (`[owner/repo#123](url)`) and briefly note scope limits (`--period` filters by creation date `created`; reviews/comments on older items and direct commits outside PRs are not captured).
