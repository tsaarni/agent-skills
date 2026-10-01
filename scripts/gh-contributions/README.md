# gh-contributions

Fetch GitHub contributions grouped by repository, type, and state.

Requires [gh](https://cli.github.com/) CLI, authenticated.

## Usage

```bash
gh-contributions --period=q3
gh-contributions --period=2026-09 --repo=owner/repo
gh-contributions --period=q3 --author=<user>  # defaults to @me
gh-contributions --help
```

Output is auto-detected: JSON when piped, text when interactive.

## JSON schema

Each item in the output array:

```json
{
  "repo": "owner/repo",
  "contribution": "pr | issue",
  "role": "author | reviewer | involved",
  "state": "open | merged | closed",
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
- **involved** — participated in but not authored (comments, mentions, assignments)
