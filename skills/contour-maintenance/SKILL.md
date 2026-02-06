---
name: contour-maintenance
description: Contour version bumps (Go, Envoy), dependency updates, and vulnerability checks for release branches
---

# Contour Maintenance Tasks

**Preconditions:**
- All tasks must be executed in the root directory of the Contour repository.

## Bump Go Version

- Check the latest supported Contour release tracks from `versions.yaml` in the Contour repository and tell the user the available release branches (e.g., "1.33", "1.32", etc.).
- Ask user for the target Contour release branch number.
- Execute `go run ./hack/actions/bump-go-version/main.go` and capture output to determine the new Go version.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<GO_VERSION>` with the detected Go version in all commands.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
git checkout -b bump-go-release-<CONTOUR_VERSION>-$(date +%Y%m%d)
go run ./hack/actions/bump-go-version/main.go
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to go <GO_VERSION>"
```

## Bump Envoy Version

- Check the latest supported Contour release tracks from `versions.yaml` in the Contour repository and tell the user the available release branches (e.g., "1.33", "1.32", etc.).
- Ask user for the target Contour release branch number.
- Execute `go run ./hack/actions/bump-envoy-version/main.go` and capture output to determine the new Envoy version.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<ENVOY_VERSION>` with the detected Envoy version in all commands.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
git checkout -b bump-envoy-release-<CONTOUR_VERSION>-$(date +%Y%m%d)
go run ./hack/actions/bump-envoy-version/main.go
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to Envoy <ENVOY_VERSION>"
```

## Check for Known Vulnerabilities in Contour Dependencies

- Get Go version from `Makefile` variable `BUILD_BASE_IMAGE`
- Create `osv-scanner.toml` with `GoVersionOverride = "<GO_VERSION>"`
- Run: `osv-scanner scan source -r . --format=json --call-analysis=go --output=osv-results.json`
- Read `osv-results.json` and summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Contour dependencies with Go <GO_VERSION>".
- Delete `osv-scanner.toml`

## Check for Known Vulnerabilities in Envoy

- Get Envoy version from `examples/contour/03-envoy.yaml`
- Run OSV scanner against the Envoy image
- Summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Envoy <ENVOY_VERSION>".

Use this command to query OSV API:

```shell
http --ignore-stdin -b POST https://api.osv.dev/v1/query package[name]=github.com/envoyproxy/envoy version=<ENVOY_VERSION>
```
