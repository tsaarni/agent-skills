---
name: vault-nordix-maintenance
description: Use when a new upstream Vault release is out and a new Nordix fork release is needed.
---

# Vault Nordix Maintenance

- Remotes: `origin` = hashicorp/vault (no push), `nordix` = Nordix/vault.
- Branches are always `{upstream release}-nordix`, e.g. `v2.2.1-nordix` = upstream tag `v2.2.1` + Nordix cherry-picks.
- Tags are always `{upstream release}-nordix-{sequence number}`, e.g. `v2.2.1-nordix-1`. The sequence number starts at 1 and increments for each further release of the same branch.
- This skill covers only the first release (`-nordix-1`) of a new upstream version.
- Never push or create releases. Show those commands to the user.
- Never install into the user's environment (`~/go/bin`).

## Human in the loop

Before the decisions below, state the choice and the reason in 1-3 lines, then wait for the user's OK:

- Which upstream version to fork (step 1).
- Which commits to cherry-pick (step 2).

If a cherry-pick conflicts, stop. Explain the conflict to the user. Do not resolve it.

Do not explain routine steps.

## 1. Pick versions

```bash
git fetch origin --tags --force; git fetch nordix --tags --force
git tag -l --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -5   # upstream
git tag -l --sort=-v:refname '*-nordix-*' | head -3                           # nordix
OLD=v2.2.0; OLD_TAG=v2.2.0-nordix-1   # previous Nordix release
NEW=v2.2.1; NEW_TAG=v2.2.1-nordix-1    # new Nordix release
```

`NEW` is the upstream release to fork, `OLD` is the upstream release of the latest Nordix release. Propose both and wait for confirmation.

## 2. Branch and cherry-pick

```bash
git branch --list $NEW-nordix; git ls-remote --heads nordix $NEW-nordix   # must print nothing
git checkout -b $NEW-nordix $NEW
git log --reverse --oneline $OLD..nordix/$OLD-nordix   # Nordix commits, oldest first
git cherry-pick <sha> ...
```

If `$NEW-nordix` already exists locally or on `nordix`, stop and ask the user. Always cherry-pick from the previous Nordix release branch, not the tag. Show the commits to pick, wait for confirmation.

## 3. Test build

Do not use `make`: it installs into `$GOBIN` and enforces `.go-version`. Build the production binary directly, in the background with `runagent`:

```bash
runagent run -n vault-build -e CGO_ENABLED=0 -- nice -n 19 go build -p 4 -tags ui -o bin/vault ./command/server/cmd; runagent wait vault-build; runagent logs vault-build --last 30
runagent ps vault-build   # exit code 0 = success
bin/vault version
```

If the package path or tags fail, check `BUILD_SOURCE` and `BUILD_TAGS` in `scripts/build.sh`.

## 4. Release notes

Write to `/tmp/vault-nordix-release-notes.md`. Previous example: `gh release view $OLD_TAG --repo Nordix/vault`.

```markdown
This release is based on Vault [{NEW}](https://github.com/hashicorp/vault/releases/tag/{NEW}) with following additional changes:

* {commit subject} ([hashicorp#{N}](https://www.github.com/hashicorp/vault/issues/{N}))

**Full Changelog**: https://github.com/Nordix/vault/commits/{NEW_TAG}
```

One bullet per Nordix commit. `{N}` is the upstream issue or PR the change relates to. Ask the user if unknown.

## 5. Release (user runs)

Show these commands to the user. Do not run them.

```bash
git push nordix $NEW-nordix refs/tags/$NEW --no-verify   # branch + upstream tag (not --tags)
gh release create $NEW_TAG --repo Nordix/vault --draft --target $NEW-nordix --title $NEW_TAG --notes-file /tmp/vault-nordix-release-notes.md
```

The upstream tag `$NEW` is pushed to `nordix` so that compare and changelog links against it work. The release is a draft. The `$NEW_TAG` tag does not exist yet. GitHub creates it from `$NEW-nordix` (`--target`). `gh release create` prints the draft URL. To print it again:

```bash
gh api repos/Nordix/vault/releases --jq ".[] | select(.draft and .tag_name==\"$NEW_TAG\") | .html_url"
```

Tell the user to open that URL, review, and press Publish. Final release page: https://github.com/Nordix/vault/releases/tag/$NEW_TAG
