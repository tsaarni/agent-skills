---
name: contour-maintenance
description: Contour version bumps (Go, Envoy), dependency updates, and vulnerability / CVE checks for release branches and images
---

# Contour Maintenance Tasks

## Scope
- **Target**: Contour repository (github.com/projectcontour/contour)
- **Supported Branches**: `main` and release-X.Y branches only
- **Platform**: Linux/macOS only
- **Prerequisites**: Contour repository checked out locally, `gh` CLI, `git`, `go`, `httpie`, `osv-scanner` installed

## Global Preconditions (MUST validate before ANY task)

Execute these checks and STOP if any fail:

```bash
# 1. Verify current directory is Contour repo root
test -f Makefile && test -f versions.yaml && echo "✓ In Contour repo root" || echo "✗ NOT in Contour root"
```

STOP here if any check fails. Do not proceed to specific tasks.

## Critical Warnings

**NEVER:**
- Use `go.mod` to determine Go version → ONLY read from `Makefile` variable `BUILD_BASE_IMAGE`
- Use `versions.yaml` from release branches → ONLY read from `main` branch
- Create local branches without `chore/` prefix
- Skip git status checks between operations
- Assume tool versions without explicit verification

**ONLY:**
- Create branches with format: `chore/release-<VERSION>/bump-<TYPE>-<NEW_VERSION>`
- Use commands listed in task procedures exactly as written

---

## Task: Find out what the latest supported Contour releases are

**What this does**: Lists currently supported Contour releases.

MUST execute exactly:
```bash
echo "Latest available Contour releases are:"
git show main:versions.yaml | yq '.versions[] | select(.supported == "true") | .version'
```

---

## Task: Bump Go Version

**What this does**: Updates Go version in Makefile to latest patch level of current minor version (or user-specified major/minor track).

### Step 1: Gather Release Information (REQUIRED)

MUST execute exactly:
```bash
echo "Available Contour release tracks are:"
(echo main && git show main:versions.yaml | yq '.versions[] | select(.supported == "true") | .version' | sed 's/v//' | cut -d'.' -f1-2 | sed 's/^/release-/')
```

MUST capture the output and present it to user.
MUST ask user: "Which Contour release branch to update? (e.g., 1.33, 1.32, or 'main')"
STOP and WAIT for response before continuing.

### Step 2: Verify Current Go Version (REQUIRED)

Replace `<CONTOUR_VERSION>` with user's choice from Step 1.

MUST execute:
```bash
git checkout release-<CONTOUR_VERSION>
git pull
```

Expected: Branch successfully checked out and up-to-date.
STOP if git commands fail.

THEN execute:
```bash
OLD_GO_VERSION=$(grep "BUILD_BASE_IMAGE ?=" Makefile | sed 's/.*golang://' | sed 's/@.*//')
echo "Current Go version: ${OLD_GO_VERSION}"
```

MUST capture and report the OLD_GO_VERSION to user.

### Step 3: Execute Bump (NON-REVOCABLE)

MUST execute:
```bash
go run ./hack/actions/bump-go-version/main.go
```

MUST execute:
```bash
NEW_GO_VERSION=$(grep "BUILD_BASE_IMAGE ?=" Makefile | sed 's/.*golang://' | sed 's/@.*//')
echo "New Go version: ${NEW_GO_VERSION}"
```

Decision point:
- IF output shows same Go version as before: Report "Go version already at latest patch level ${OLD_GO_VERSION} for current track"
- STOP here, do not proceed to next steps
- ELSE IF changes detected: Continue to Step 4

### Step 4: Commit Changes (SEQUENTIAL)

DO NOT execute this step if Step 3 showed we're already at the latest Go version.

MUST execute:
```bash
git checkout -b chore/release-<CONTOUR_VERSION>/bump-go-${NEW_GO_VERSION}
```

MUST verify:
```bash
git branch --show-current
```
Expected: Branch name matches `chore/release-<CONTOUR_VERSION>/bump-go-<VERSION>`

THEN execute:
```bash
git add -u
git commit -m "release-<CONTOUR_VERSION>: Bump to go $NEW_GO_VERSION"
```

MUST verify commit created:
```bash
git log --oneline -1
```

### Step 5: Report Final State (REQUIRED)

MUST report to user:
```
✓ COMPLETED: Bump Go Version

Release Branch: release-<CONTOUR_VERSION>
Old Go Version: ${OLD_GO_VERSION}
New Go Version: ${NEW_GO_VERSION}
Branch Created: chore/release-<CONTOUR_VERSION>/bump-go-${NEW_GO_VERSION}

Next: Push branch and create pull request:
  git push origin chore/release-<CONTOUR_VERSION>/bump-go-${NEW_GO_VERSION}
```

---

## Task: Bump Envoy Version

**What this does**: Updates Envoy version in Makefile to latest patch level of current minor version (or user-specified major/minor track).

### Step 1: Gather Release Information (REQUIRED)

MUST execute exactly:
```bash
echo "Available Contour release tracks are:"
(echo main && git show main:versions.yaml | yq '.versions[] | select(.supported == "true") | .version' | sed 's/v//' | cut -d'.' -f1-2 | sed 's/^/release-/')
```

MUST capture the output and present it to user.
MUST ask user: "Which Contour release branch to update? (e.g., 1.33, 1.32, or 'main')"
STOP and WAIT for response before continuing.

### Step 2: Verify Current Envoy Version (REQUIRED)

Replace `<CONTOUR_VERSION>` with user's choice from Step 1.

MUST execute:
```bash
git checkout release-<CONTOUR_VERSION>
git pull
```

Expected: Branch successfully checked out and up-to-date.
STOP if git commands fail.

THEN execute:
```bash
OLD_ENVOY_VERSION=$(grep 'envoyproxy/envoy' examples/contour/03-envoy.yaml | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
echo "Current Envoy version: ${OLD_ENVOY_VERSION}"
```

MUST capture and report the OLD_ENVOY_VERSION to user.

### Step 3: Execute Bump (NON-REVOCABLE)

MUST execute:
```bash
go run ./hack/actions/bump-envoy-version/main.go
```

MUST execute:
```bash
NEW_ENVOY_VERSION=$(grep 'envoyproxy/envoy' examples/contour/03-envoy.yaml | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
echo "Current Envoy version: ${NEW_ENVOY_VERSION}"
```

Decision point:
- IF output shows same Envoy version as before: Report "Envoy version already at latest patch level ${OLD_ENVOY_VERSION} for current track"
- STOP here, do not proceed to next steps and ignore the instructions from the tool about updates needed in versions.yaml and compatibility matrix
- ELSE IF changes detected: Continue to Step 4

### Step 4: Commit Changes (SEQUENTIAL)

DO NOT execute this step if Step 3 showed we're already at the latest Envoy version.

MUST execute:
```bash
git checkout -b chore/release-<CONTOUR_VERSION>/bump-envoy-${NEW_ENVOY_VERSION}
```

MUST verify:
```bash
git branch --show-current
```
Expected: Branch name matches `chore/release-<CONTOUR_VERSION>/bump-envoy-<VERSION>`

THEN execute:
```bash
git add -u
git commit -m "release-<CONTOUR_VERSION>: Bump to Envoy ${NEW_ENVOY_VERSION}"
```

MUST verify commit created:
```bash
git log --oneline -1
```

### Step 5: Report Final State (REQUIRED)

MUST report to user:
```
✓ COMPLETED: Bump Envoy Version

Release Branch: release-<CONTOUR_VERSION>
Old Envoy Version: ${OLD_ENVOY_VERSION}
New Envoy Version: ${NEW_ENVOY_VERSION}
Branch Created: chore/release-<CONTOUR_VERSION>/bump-envoy-${NEW_ENVOY_VERSION}

Next: Push branch and create pull request:
  git push origin chore/release-<CONTOUR_VERSION>/bump-envoy-${NEW_ENVOY_VERSION}
```

---

## Task: Check for Known Vulnerabilities (Source Code Scan)

**What this does**: Scans Contour source code and dependencies for known CVEs/vulnerabilities using OSV Scanner.


### Step 1: Gather Release Information (REQUIRED)

MUST execute exactly:
```bash
echo "Available Contour release tracks are:"
(echo main && git show main:versions.yaml | yq '.versions[] | select(.supported == "true") | .version' | sed 's/v//' | cut -d'.' -f1-2 | sed 's/^/release-/')
```

MUST capture the output and present it to user.
MUST ask user: "Which Contour release branch to scan? (e.g., 1.33, 1.32, or 'main')"
STOP and WAIT for response before continuing.

### Step 2: Extract Go Version from Makefile (REQUIRED)

Replace `<CONTOUR_VERSION>` with user's choice from Step 1.

MUST execute:
```bash
git checkout release-<CONTOUR_VERSION>
git pull
```

Expected: Branch successfully checked out and up-to-date.
STOP if git commands fail.


MUST execute:
```bash
GO_VERSION=$(grep "BUILD_BASE_IMAGE = golang:" Makefile | sed 's/.*golang://' | sed 's/ .*//')
echo "Go version used for the scan: $GO_VERSION"
```

MUST capture GO_VERSION and verify it's not empty (format: X.Y.Z).
MUST report the Go version to the user: "Go version detected for this branch: $GO_VERSION"
STOP if extraction fails.

### Step 3: Create OSV Scanner Override File (REQUIRED)

MUST execute exactly:
```bash
printf "GoVersionOverride = \"%s\"\n" "$GO_VERSION" > osv-scanner.toml
```

THEN verify:
```bash
cat osv-scanner.toml
```

Expected: Single line containing `GoVersionOverride = "X.Y.Z"`

### Step 4: Execute Vulnerability Scan (NON-REVOCABLE)

MUST execute exactly:
```bash
osv-scanner scan source -r . \
  --format=json \
  --call-analysis=go \
  --output=osv-results.json 2>/dev/null
```

Expected: `osv-results.json` file created in current directory.
STOP if scan fails with error (not if vulnerabilities found).

### Step 5: Analyze Results (REQUIRED)

MUST extract vulnerabilities:

```bash
VULN_COUNT=$(jq '[.results[].vulnerabilities] | flatten | length' osv-results.json 2>/dev/null || echo "0")
echo "Total vulnerabilities found: $VULN_COUNT"

jq -r '.results[].vulnerabilities[] | "\(.id) - \(.severity) - \(.summary // .description)"' osv-results.json
```

MUST capture ALL vulnerability details for reporting.

### Step 6: Cleanup and Report (REQUIRED)

MUST execute:
```bash
rm osv-scanner.toml osv-results.json
```

MUST report to user:
```
✓ COMPLETED: Source Code Vulnerability Scan

Go Version: $GO_VERSION
Vulnerabilities Found: [count]
[List of vulnerabilities if any]

Status: [No known vulnerabilities found | Vulnerabilities detected - review above]
```

---

## Task: Check for Known Vulnerabilities (Container Image Scan)

**What this does**: Scans published Contour container images for vulnerabilities.

**Precondition**: OSV Scanner installed, network access to ghcr.io

### Step 1: Determine Target Version(s) (REQUIRED)

MUST ask user: "Scan single version, all supported versions?"

Decision:
- IF "single": Ask "Which version?" and capture input (e.g., "1.33.0", "main")
- IF "all supported versions": Execute and capture supported versions


For "single", execute:
```bash
VERSIONS=$(git show main:versions.yaml | yq '.versions | .[0:15] | [.[] | del(.dependencies)]' -o json)
echo "These are some most recent Contour versions to scan: $VERSIONS"
```
MUST capture version list and present to user and ask for selection.


For "all supported versions", execute:
```bash
VERSIONS=$(git show main:versions.yaml | yq '.versions[] | select(.supported == "true") | .version')
echo "These are supported Contour versions to scan: $VERSIONS"
```

MUST capture version list and present to user the list of versions that will be scanned.

### Step 2: Execute Image Scans (SEQUENTIAL)

For each version, MUST execute exactly:
```bash
osv-scanner scan image \
  --format json \
  ghcr.io/projectcontour/contour:<VERSION> 2>/dev/null > /tmp/osv-image-<VERSION>.json
```

Expected: JSON file created for each version.
REPORT progress after each scan.

### Step 3: Analyze Results for Each Image (REQUIRED)

For each scanned version, execute:
```bash
if ! jq empty /tmp/osv-image-<VERSION>.json 2>/dev/null; then
  echo "Invalid JSON in /tmp/osv-image-<VERSION>.json"
  exit 1
fi

VULN_COUNT=$(jq '.vulnerabilities | length' /tmp/osv-image-<VERSION>.json 2>/dev/null || echo "0")
echo "Version <VERSION>: $VULN_COUNT vulnerabilities"

jq -r '.vulnerabilities[] | "\(.id) - \(.severity) - \(.summary // "N/A")"' /tmp/osv-image-<VERSION>.json
```

MUST capture and organize output by version.

### Step 4: Cleanup and Report (REQUIRED)

MUST execute:
```bash
rm /tmp/osv-image-*.json
```

MUST report to user:
```
✓ COMPLETED: Container Image Vulnerability Scan

Version(s) scanned: [list]
[Results organized by version]

Status: [No known vulnerabilities found | Vulnerabilities detected - review above]
```

---

## Task: Check for Known Vulnerabilities (Envoy)

**What this does**: Queries OSV API for known vulnerabilities in Envoy version.


### Step 1: Determine Target Version(s) (REQUIRED)

MUST ask user: "Scan single version, all supported versions?"

Decision:
- IF "single": Ask "Which version?" and capture input (e.g., "1.33.0", "main")
- IF "all supported versions": Execute and capture supported versions


For "single", execute:
```bash
VERSIONS=$(git show main:versions.yaml | yq '.versions | .[0:15] | [.[] | del(.dependencies)]' -o json)
echo "These are some most recent Contour versions to scan: $VERSIONS"
```
MUST capture version list and present to user and ask for selection.


### Step 2: Extract Envoy Version (REQUIRED)

MUST execute:
```bash
ENVOY_VERSION=$(git show <CONTOUR_VERSION>:examples/contour/03-envoy.yaml | grep "image.*envoyproxy/envoy" | sed 's/.*envoy://' | sed 's/".*//')
echo "Envoy version found: $ENVOY_VERSION"
```

MUST capture ENVOY_VERSION and verify it's not empty (format: vX.Y.Z).
STOP if extraction fails.

### Step 3: Query OSV API (NON-REVOCABLE)

MUST execute exactly:
```bash
curl -s -X POST https://api.osv.dev/v1/query \
  -H "Content-Type: application/json" \
  -d "{\"package\":{\"name\":\"github.com/envoyproxy/envoy\"},\"version\":\"$ENVOY_VERSION\"}"
```

Expected: JSON response returned to stdout.
STOP if HTTP request fails with error.

### Step 4: Parse and Analyze Results (REQUIRED)

From the OSV API response, MUST extract:
```bash
# Assuming response stored in variable or file
VULN_COUNT=$(echo "$RESPONSE" | jq '.vulnerabilities | length' 2>/dev/null)
echo "Vulnerabilities found: $VULN_COUNT"

# Extract details
echo "$RESPONSE" | jq -r '.vulnerabilities[] | "\(.id) - \(.severity // "N/A") - \(.summary // .description)"'
```

MUST capture all vulnerability details for reporting.

Decision:
- IF VULN_COUNT is 0: Report clean status
- IF VULN_COUNT > 0: Report each vulnerability with severity

### Step 5: Report Final State (REQUIRED)

MUST report to user:
```
✓ COMPLETED: Envoy Vulnerability Check

Envoy Version: $ENVOY_VERSION
Vulnerabilities Found: [count]
[List of vulnerabilities if any]

Status: [No known vulnerabilities found | Vulnerabilities detected - review above]
```

---

## Task: Bump Kubernetes Version for E2E Tests

**What this does**: Updates E2E test infrastructure to support new Kubernetes version. ONLY run on `main` branch.

**Precondition**: Currently on `main` branch (`git rev-parse --abbrev-ref HEAD` MUST output `main`)

### Step 1: Verify Current State (REQUIRED)

MUST execute:
```bash
git checkout main
git pull
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "✗ NOT on main branch"; exit 1
fi
echo "✓ On main branch"
```

### Step 2: Gather Kubernetes Version Information (REQUIRED)

MUST execute:

```bash
# Get currently supported Kubernetes tracks
SUPPORTED=$(git show main:versions.yaml | yq '.versions[] | select(.version == "main") | .dependencies.kubernetes | .[]')
echo "Currently supported K8s versions:"
echo "$SUPPORTED"

# Get latest stable Kubernetes version
LATEST_K8S=$(curl -s https://dl.k8s.io/release/stable.txt | sed 's/v//')
echo "Latest stable Kubernetes version: $LATEST_K8S"

# Get latest Kind release notes
gh release view --repo kubernetes-sigs/kind
```

MUST show both current and latest versions to user.
MUST check if Kind node images are available for the latest Kubernetes version (check Kind release notes for supported versions).
MUST ask: "Should we update to Kubernetes $LATEST_K8S? (yes/no)"
STOP and WAIT for response.

Decision:
- IF user says "no": STOP and report "Update skipped"
- IF versions already match: Stop with "No new Kubernetes version to update to"
- IF user says "yes": Continue to Step 3

### Step 3: Create Feature Branch (NON-REVOCABLE)

Extract version number: `K8S_RELEASE_TRACK=$(echo "$LATEST_K8S" | cut -d. -f1-2)`

MUST execute:
```bash
git checkout -b chore/main/update-k8s-version-${K8S_RELEASE_TRACK}
```

Verify:
```bash
git branch --show-current
```
Expected: `chore/main/update-k8s-version-X.Y`

### Step 4: Update GitHub Workflows (REQUIRED)

MUST edit: `.github/workflows/prbuild.yaml`

Actions:
1. Add new K8s version to E2E and upgrade test matrices
2. Use corresponding Kind node images
3. Add as latest supported version
4. Remove oldest version (maintain n, n-1, n-2 pattern)

MUST execute verification:
```bash
git diff .github/workflows/prbuild.yaml | head -50
```

REPORT changes to user.

### Step 5: Update Kind Cluster Script (REQUIRED)

MUST edit: `test/scripts/make-kind-cluster.sh`

Action: Update default Kind node image to new K8s version.

MUST verify:
```bash
git diff test/scripts/make-kind-cluster.sh
```

REPORT changes to user.

### Step 6: Update versions.yaml (REQUIRED)

MUST edit: `versions.yaml`

Action: Update Kubernetes version support for `main` release track.

MUST verify:
```bash
git diff versions.yaml
```

REPORT changes to user.

### Step 7: Update Compatibility Matrix (REQUIRED)

MUST edit: `site/content/resources/compatibility-matrix.md`

Action: Update Kubernetes version support information.

MUST verify:
```bash
git diff site/content/resources/compatibility-matrix.md | head -30
```

REPORT changes to user.

### Step 8: Update Kubernetes Toolchain (REQUIRED)

MUST edit: `hack/actions/install-kubernetes-toolchain.sh`

Action: Update `kind` and `kubectl` versions to match new release.

MUST verify:
```bash
git diff hack/actions/install-kubernetes-toolchain.sh
```

REPORT changes to user.

### Step 9: Create Changelog Entry (REQUIRED)

Extract GitHub user if not already known:
```bash
GH_USER=$(gh api user --jq '.login')
echo "GitHub user: $GH_USER"
```

MUST create file: `changelogs/unreleased/RRRR-${GH_USER}-small.md`

MUST execute:
```bash
cat <<EOF > changelogs/unreleased/RRRR-${GH_USER}-small.md
Updates kind node image for e2e tests to Kubernetes ${K8S_RELEASE_TRACK}.
Supported/tested Kubernetes versions are now [LIST SUPPORTED VERSIONS].
EOF
```

MUST verify file created:
```bash
cat changelogs/unreleased/RRRR-${GH_USER}-small.md
```

### Step 10: Commit Changes and Report (NON-REVOCABLE)

MUST verify all files changed:
```bash
git status
```

Expected: All files from Steps 4-9 showing as modified.

MUST execute:
```bash
git add -u
git commit -m "Update Kubernetes version for E2E tests to ${K8S_RELEASE_TRACK}"
```

Verify:
```bash
git log --oneline -1
```

MUST report to user:
```
✓ COMPLETED: Bump Kubernetes Version

Branch: chore/main/update-k8s-version-${K8S_RELEASE_TRACK}
Updated K8s Version: ${K8S_RELEASE_TRACK}
Previous Supported Versions: [list]
New Supported Versions: [list]

Modified Files:
  - .github/workflows/prbuild.yaml
  - test/scripts/make-kind-cluster.sh
  - versions.yaml
  - site/content/resources/compatibility-matrix.md
  - hack/actions/install-kubernetes-toolchain.sh
  - changelogs/unreleased/RRRR-${GH_USER}-small.md

Next: Push branch and create pull request:
  git push origin chore/main/update-k8s-version-${K8S_RELEASE_TRACK}
```

---

## Task: Release Process Instructions

MUST refer to: `site/content/resources/release-process.md`

STOP and read that file first if you are executing a release.
These skill instructions do NOT cover release process — it is documented separately.

---
