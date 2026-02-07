---
name: contour-maintenance
description: Contour version bumps (Go, Envoy), dependency updates, and vulnerability checks for release branches
---

# Contour Maintenance Tasks

**Preconditions:**
- All tasks must be executed in the root directory of the Contour repository.

## Bump Go Version

- Execute `git show main:versions.yaml | head -100` to read the latest supported Contour release tracks and tell the user the available release branches (e.g., "1.33", "1.32", etc.).
- Ask user for the target Contour release branch number.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<GO_VERSION>` with the detected Go version in all commands.
- At the end of the process, tell the user which Contour release branch we are in, what was the original Go version, and what is the new Go version.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
go run ./hack/actions/bump-go-version/main.go
git status  # NOTE: Stop here if Go was already up to date, otherwise continue with the next commands.
git checkout -b chore/release-<CONTOUR_VERSION>/bump-go-<GO_VERSION>
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to go <GO_VERSION>"
```

## Bump Envoy Version

- Execute `git show main:versions.yaml | head -100` to read the latest supported Contour release tracks and tell the user the available release branches (e.g., "1.33", "1.32", etc.).
- Ask user for the target Contour release branch number.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<ENVOY_VERSION>` with the detected Envoy version in all commands.
- At the end of the process, tell the user which Contour release branch we are in, what was the original Envoy version, and what is the new Envoy version.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
go run ./hack/actions/bump-envoy-version/main.go
git status   # NOTE: Stop here if Envoy was already up to date, otherwise continue with the next commands.
git checkout -b chore/release-<CONTOUR_VERSION>/bump-envoy-<ENVOY_VERSION>
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to Envoy <ENVOY_VERSION>"
```

## Check for Known Vulnerabilities in Contour Dependencies and Go Version

- Get Go version from `Makefile` variable `BUILD_BASE_IMAGE`
- Create `osv-scanner.toml` with `GoVersionOverride = "<GO_VERSION>"`
- Run: `osv-scanner scan source -r . --format=json --call-analysis=go --output=osv-results.json`
- Read `osv-results.json` and summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Contour dependencies with Go <GO_VERSION>".
- Delete `osv-scanner.toml`

## Check for Known Vulnerabilities in Envoy

- Get Envoy version from `examples/contour/03-envoy.yaml`
- Use request `http --ignore-stdin -b POST https://api.osv.dev/v1/query package[name]=github.com/envoyproxy/envoy version=<ENVOY_VERSION>` to query the OSV API for known vulnerabilities in the detected Envoy version.
- Replace `<ENVOY_VERSION>` with the detected Envoy version in all commands.
- Summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Envoy <ENVOY_VERSION>".
