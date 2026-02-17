---
name: contour-maintenance
description: Contour version bumps (Go, Envoy), dependency updates, and vulnerability checks for release branches
---

# Contour Maintenance Tasks

**Preconditions:**
- All tasks must be executed in the root directory of the Contour repository.
- Learn the GitHub username of the current user by running `gh api user --jq '.login'`
- Execute `git show main:versions.yaml | head -100` to read the latest supported Contour release tracks (e.g., `main`, `1.33`, `1.32`, etc.).

## Bump Go Version

- Read the latest supported Contour release tracks and tell the user the available release branches.
- Ask user for the target Contour release branch number.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<GO_VERSION>` with the detected Go version in all commands.
- By default the Go version is updated to the latest patch level of the same minor version, but the user can choose to update to a newer minor version if they want.
- At the end of the process, tell the user which Contour release branch we are in, what was the original Go version, and what is the new Go version.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
go run ./hack/actions/bump-go-version/main.go [<OPTIONAL_GO_RELEASE_TRACK>]
git status  # NOTE: Stop here if Go was already up to date, otherwise continue with the next commands.
git checkout -b chore/release-<CONTOUR_VERSION>/bump-go-<GO_VERSION>
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to go <GO_VERSION>"
```

## Bump Envoy Version

- Read the latest supported Contour release tracks and tell the user the available release branches.
- Ask user for the target Contour release branch number.
- Replace `<CONTOUR_VERSION>` with the Contour release number and `<ENVOY_VERSION>` with the detected Envoy version in all commands.
- By default the Envoy version is updated to the latest patch level of the same minor version, but the user can choose to update to a newer minor version if they want.
- At the end of the process, tell the user which Contour release branch we are in, what was the original Envoy version, and what is the new Envoy version.

```shell
git checkout release-<CONTOUR_VERSION>
git pull
go run ./hack/actions/bump-envoy-version/main.go [<OPTIONAL_ENVOY_RELEASE_TRACK>]
git status   # NOTE: Stop here if Envoy was already up to date, otherwise continue with the next commands.
git checkout -b chore/release-<CONTOUR_VERSION>/bump-envoy-<ENVOY_VERSION>
git add -u
git commit -sm "release-<CONTOUR_VERSION>: Bump to Envoy <ENVOY_VERSION>"
```

## Check for Known Vulnerabilities in Contour Dependencies and Go Version

Source code scanning:

- Get Go version from `Makefile` variable `BUILD_BASE_IMAGE`
- Create `osv-scanner.toml` with `GoVersionOverride = "<GO_VERSION>"`
- Run: `osv-scanner scan source -r . --format=json --call-analysis=go --output=osv-results.json`
- Read `osv-results.json` and summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Contour dependencies with Go <GO_VERSION>".
- Delete `osv-scanner.toml`

Container image scanning:

- Run scanner against published container image such as `osv-scanner scan image --format json ghcr.io/projectcontour/contour:<CONTOUR_VERSION>`
- Read the output and summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Contour <CONTOUR_VERSION>".

## Check for Known Vulnerabilities in Envoy

- Get Envoy version from `examples/contour/03-envoy.yaml`
- Use request `http --ignore-stdin -b POST https://api.osv.dev/v1/query package[name]=github.com/envoyproxy/envoy version=<ENVOY_VERSION>` to query the OSV API for known vulnerabilities in the detected Envoy version.
- Replace `<ENVOY_VERSION>` with the detected Envoy version in all commands.
- Summarize severity and most important details for any vulnerabilities found.
- If no vulnerabilities are found, report "No known vulnerabilities found for Envoy <ENVOY_VERSION>".

## Bump Kubernetes Version for E2E Tests

Updates Contour's E2E and upgrade test infrastructure to support a new Kubernetes version.
Execute these instructions on the `main` branch.

- Display the available release branches by reading the latest supported Contour release tracks.
- Retrieve the latest stable Kubernetes minor release track from https://dl.k8s.io/release/stable.txt.
- Check the latest Kind release and available node images at https://github.com/kubernetes-sigs/kind/releases.
- Verify whether the latest stable Kubernetes version is newer than what's currently supported in `main`. If the versions match, stop and report: "No new Kubernetes version to update to. Latest stable Kubernetes version is <K8S_VERSION> and current supported versions are <SUPPORTED_VERSIONS>".
- Update `.github/workflows/prbuild.yaml` to include the new Kubernetes version in the E2E and upgrade test matrices. Use the corresponding Kind node images. Add the new version as the latest supported version and remove the oldest version, maintaining the n, n-1, n-2 support pattern.
- Update the default Kind node image in `test/scripts/make-kind-cluster.sh` to use the new Kubernetes release tracks.
- Update Kubernetes version support in `versions.yaml` and `site/content/resources/compatibility-matrix.md` for the `main` release track.
- Update the `kind` and `kubectl` versions in `hack/actions/install-kubernetes-toolchain.sh`.
- Create a changelog entry at `changelogs/unreleased/RRRR-<author>-small.md` with: "Updates kind node image for e2e tests to Kubernetes <K8S_VERSION>. Supported/tested Kubernetes versions are now <SUPPORTED_VERSIONS>." Replace placeholders with the GitHub username, new Kubernetes version, and updated supported versions.

```shell
git checkout main
git pull
git checkout -b chore/main/update-k8s-version-<K8S_RELEASE_TRACK>
git add -u
git commit -sm "Update Kubernetes version for E2E tests to <K8S_RELEASE_TRACK>"
```
