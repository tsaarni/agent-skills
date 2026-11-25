# gh-contributions

Fetch GitHub contributions grouped by repository, type, and state.

Requires [gh](https://cli.github.com/) CLI, authenticated.

## Usage

```bash
gh-contributions --period=q3
gh-contributions --period=2026-09 --repo=owner/repo
gh-contributions --period=q3 --author=<user>  # defaults to @me
gh-contributions --period=q3 --format=markdown
gh-contributions --period=q3 --include-bots   # include bot-authored PRs/issues
gh-contributions inspect https://github.com/owner/repo/pull/456 owner/repo#123
gh-contributions inspect --format=markdown https://github.com/owner/repo/pull/456
gh-contributions --help
```

Output is auto-detected: JSON when piped, text when interactive (pass `--format=markdown` for a compact LLM-friendly overview with full titles, labels, and links). Bot-authored PRs and issues are filtered out by default; pass `--include-bots` to include them.

## JSON schema

Each item in the output array:

```json
{
  "repo": "owner/repo",
  "contribution": "pr | issue",
  "role": "author | reviewer | commented",
  "state": "open | merged | closed",
  "author": "username",
  "labels": ["kind/bug"],
  "commentsCount": 2,
  "title": "...",
  "url": "https://github.com/...",
  "number": 123,
  "createdAt": "2026-09-01T00:00:00Z",
  "closedAt": "2026-09-15T00:00:00Z | null"
}
```

Roles:
- **author** — created by the user
- **reviewer** — PRs reviewed by the user (excluding own)
- **commented** — issues and PRs the user commented on (excluding authored)

## Inspecting pivotal items

`gh-contributions inspect` fetches rich details for specific PRs or issues (accepts `owner/repo#123`, full GitHub URLs, or one reference per line on `stdin`):

```bash
gh-contributions inspect projectcontour/contour#7803 keycloak/keycloak#52403 openbao/openbao#4486
# Compact markdown dossier (great for AI agents):
gh-contributions inspect --format=markdown https://github.com/projectcontour/contour/pull/7803
# For another user:
gh-contributions inspect --author=<user> owner/repo#123
```

Returns a JSON array (or compact Markdown with `--format=markdown`, `--max-body=N`) with:
- **PRs:** `title`, `state`, `author`, `labels`, `closes` (linked issue numbers), `files` (`path`, `additions`, `deletions`), `body`, plus `userReviews`, `userReviewComments`, and `userComments` filtered to the target user.
- **Issues:** `title`, `state`, `author`, `labels`, `commentsCount`, `body`, and `userComments` filtered to the target user.

## Behavior & Notes

- **Focus areas & sorting:** Text output starts with a `Focus areas` summary table and sorts repositories by contribution volume descending.
- **Role deduplication:** Items matching multiple queries keep the highest-priority role (`author` > `reviewer` > `commented`).
- **Limit warnings:** `--limit` (default `200`, max `1000`) applies per `gh search` query and prints a warning to `stderr` if results may be truncated.
- **Period filtering:** `--period` filters by item creation date (`created`), so reviews/comments on older items and direct commits outside PRs are not captured.

## AI Agent Instructions

See [SKILL.md](SKILL.md) for the agent workflow to synthesize a developer impact summary from `gh-contributions`.
