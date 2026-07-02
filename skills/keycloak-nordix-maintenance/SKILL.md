---
name: keycloak-nordix-maintenance
description: 'Create Keycloak nordix fork releases with version-specific backports and security fixes. Use when creating new nordix branches (e.g., 26.2.14-nordix), cherry-picking backports from previous nordix versions, backporting patches like CVEs, managing merge conflicts in cherry-picks, and version tagging nordix releases.'
---

# Keycloak Nordix Release Skill

This skill handles the complete workflow for creating and maintaining nordix fork release branches of Keycloak, including backporting features, security fixes, and managing version control.

## When to Use This Skill

- **Creating new nordix release branches** for new upstream Keycloak versions (e.g., 26.2.14-nordix from 26.2.14 base)
- **Backporting features** from previous nordix release branches to newer
- **Backporting security fixes** and CVEs from newer upstream Keycloak versions to older nordix branches
- **Cherry-picking** commits while managing merge conflicts
- **Version tagging** and tracking nordix releases
- **Evaluating** which backports are needed vs. already in base release

## Prerequisites

- Access to Keycloak repository with nordix remote and upstream as origin configured

## Key Concepts

### Nordix Branching Pattern

Nordix maintains forked branches for specific Keycloak versions with strategic backports:

- **Base releases**: Official Keycloak versions (e.g., `26.2.14`)
- **Nordix branches**: Fork branches with additional features/fixes (e.g., `26.2.14-nordix`)
- **Version tags**: Track patch releases (e.g., `26.2.14-nordix-1`, `26.2.14-nordix-2`)

## Step-by-Step Workflow

### Phase 0: Preparation

1. **Fetch latest branches and tags** from both remotes
   ```bash
   git fetch nordix --tags --force
   git fetch origin --tags --force
   ```

### Phase 1: Planning & Analysis

1. **Identify versions** - ALWAYS execute this step first, before asking user for input
   - If user provided version in advance:
     1. Verify it exists in upstream releases: `git tag -l | grep "^{version}$"`
     2. Verify it does not have nordix release yet: `git tag -l | grep "^{version}-nordix-"`
     3. Proceed to step 2 if both checks pass
   - If user did not provide version:
     1. Execute version discovery command:
        ```bash
        echo "=== Upstream releases (latest 15) ===" && git tag -l --sort=-creatordate | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -15 && echo -e "\n=== Nordix releases (latest 15) ===" && git tag -l --sort=-creatordate | grep -E '^[0-9]+\.[0-9]+\.[0-9]+-nordix-[0-9]+$' | head -15
        ```
     2. Analyze results to find candidates:
        - Extract all upstream versions from the list (e.g., 26.5.5, 26.4.10, 26.2.14, 26.5.4, ...)
        - Extract all nordix versions from the list (e.g., 26.2.13-nordix-1, 26.5.2-nordix-1, ...)
        - For each upstream version, extract its major.minor part (e.g., 26.5 from 26.5.5)
        - Group upstream versions by major.minor series
        - For each series, find the highest patch version (latest in that series)
        - For each latest version in a series, check if ANY nordix release exists for that version (e.g., check if 26.5.5-nordix-* exists in nordix releases)
        - Keep only those latest versions that have NO nordix release yet
        - Sort these candidates by version descending (newest first)
     3. Present candidates using this template:
        ```
        Available candidates for new nordix fork:
        1) {latest-version-in-series-1} (latest in {series-1}, no nordix release yet)
        2) {latest-version-in-series-2} (latest in {series-2}, no nordix release yet)
        ...

        Select version number (1-N) or provide custom version:
        ```
     4. Wait for user to pick one of the suggested versions or provide their own

2. **Compare versions** - Check what changed between base versions and identify backports needed from previous nordix
   ```bash
    echo "=== Changes between {old-base} and {new-base} ===" && git --no-pager log {old-base}..{new-base} --oneline && echo -e "\n=== Backports in {old-nordix} (from {old-base}) ===" && git --no-pager log {old-base}..{old-nordix} --oneline
   ```

3. **Check if any are already in the new base release**
   - Note which commits are already in the new base release (these will be omitted when creating the new nordix branch)

4. **Summarize findings**
   - Create a list of commits to cherry-pick, with commit id and description.
   - **IMPORTANT**: Reverse the order from git log output (oldest first for cherry-picking)

### Phase 2: Branch Creation

1. **Create new branch** from target base version tag
   ```bash
   git checkout -b {version}-nordix {version}
   git --no-pager log --oneline -3  # Verify
   ```

### Phase 3: Cherry-pick Backports

1. **Cherry-pick backports**
   ```bash
   git cherry-pick <commit1> <commit2> <commit3> ...
   ```
   - IMPORTANT: the commits must be in chronological order (oldest first)
   - NOTE: `git log` output is in reverse chronological order (newest first), so reverse the list from git log output when cherry-picking
   - Resolve any conflicts that arise (see Common Scenarios: Conflict Resolution)

### Phase 4: Backport Specific Fixes

This phase applies to any explicit backport request: a CVE/security fix, a commit hash given directly, or any other targeted patch.

1. **Locate the commit** to backport
   - If a commit hash is given directly, proceed directly to step 2
   - If a CVE ID is given, find the fix commit using these methods in order:

   **Method 1: Search GitHub advisories by CVE ID**
   ```bash
   gh api "advisories?cve_id={CVE-XXXX-XXXXX}"
   ```
   If results are returned, extract the commit ID from the advisory data.

   **Method 2: Search commit messages for CVE ID**
   ```bash
   git --no-pager log --all --oneline --grep="CVE-XXXX-XXXXX" origin/main
   ```

   **Method 3: Search upstream release notes**
   ```bash
   gh release view {upstream-version} --repo keycloak/keycloak
   ```
   Look for security fixes mentioned in the release notes, then search for related commits.

   **Method 4: Search by description derived from CVE details**
   - Look up the CVE to understand what the vulnerability is about
   - Search commits using keywords from the description:
     ```bash
     git --no-pager log --all --oneline --grep="keyword-from-cve-description" origin/main
     ```

   **Method 5: Find fix commit via GitHub issue → PR → release branch comparison**

   Many Keycloak CVEs are tracked as GitHub issues first and fixed in private PRs that are merged directly to release branches before public disclosure. The fix commit often does **not** contain the CVE ID in its message. Use this workflow:

   a. Find the GitHub issue number (usually referenced from the CVE advisory or NVD page):
      ```bash
      gh issue view {issue-number} --repo keycloak/keycloak
      ```

   b. Find the fix PR (check issue labels for `release/X.Y.Z` and look for linked PRs):
      ```bash
      gh pr list --repo keycloak/keycloak --state merged --base release/{X.Y} --search "CVE-XXXX-XXXXX" --json number,title,mergeCommit
      ```

   c. If not found by CVE ID, compare release tags to find commits in the target release:
      ```bash
      gh api repos/keycloak/keycloak/git/refs/tags | jq -r '.[] | select(.ref | test("X.Y.Z")) | .object.sha'
      # Then compare:
      gh api "repos/keycloak/keycloak/compare/{prev-tag}...{target-tag}" | \
        jq -r '.commits[] | "\(.sha[0:12])  \(.commit.message | split("\n")[0])"'
      ```

   d. Match commit descriptions to the CVE by understanding what the CVE is about. Examples:
      - Session fixation → look for "authSessionCookie" or "SessionCodeChecks"
      - Open redirect via wildcard → look for "Wildcards" or "authority cannot be parsed"
      - SAML DoS → look for "SAML11" or "parsing"
      - Implicit flow bypass → look for "redirect_uri from client_data"
      - XSS in org selection → look for "inline handlers" or "org buttons"

   **Important caveats:**
   - Keycloak CVE fix commits on release branches often come from private pre-release PRs with short PR numbers (e.g., `#603`, `#609`) that differ from the public GitHub issue numbers.
   - The commit message title may not mention CVE at all — the CVE ID only appears in the public issue/PR opened after the embargo lifts.
   - **Do not confuse CVEs that are backported "Not Before" fix together**: e.g., "fix not before validation" in Keycloak 26.6.2/26.4.12 is CVE-2026-8922 (token introspection notBefore), NOT CVE-2026-7571 (implicit flow bypass). Always verify by checking the closing issue number.
   - CVE-2026-37977 (CORS/UMA) was NOT included in 26.4.12 or 26.6.2 despite the issue having those release labels — it was backported later (26.4.13/26.6.3). Always verify by comparing release tags, not just issue labels.

2. **Review the commit before applying**
   ```bash
   git show <commit-id>
   ```
   - Read the commit message to understand the intent
   - Review the diff to assess scope and any potential conflicts
   - Confirm the commit is appropriate for the target nordix branch

3. **Apply the commit**
   ```bash
   git cherry-pick <commit-id>
   ```
   - Resolve any conflicts carefully (see Common Scenarios: Conflict Resolution)
   - Continue cherry-pick after resolving: `git cherry-pick --continue`
   - Summarize the backport, including the commit reviewed, what it changes, and how any conflicts were resolved

### Phase 5: Finalization

1. **Create version tag**
    ```bash
    git tag {version}-nordix-1
    ```
2. **Verify branch structure**
    ```bash
    git --no-pager log {base-version}..{version}-nordix --oneline | wc -l
    git --no-pager log {version}-nordix --graph --oneline -20
    ```

### Phase 6: Create Release notes

1. **Create candidate Markdown release notes**
   - Read the release notes from previous nordix release using `gh release view {previous-nordix-tag} --repo Nordix/keycloak`
   - Use that as template to create release notes for the new nordix release


### Phase 7: Summarization

1. **Summarize what was done**
2. **Provide user with commands to execute** to push the new branch and tag to nordix remote (the agent will display these for reference only)
   ```bash
   git push nordix {version}-nordix
   git push nordix {version}-nordix-{patch}
   ```

## Evaluation Checklist

Before finalizing a nordix branch, verify:

- [ ] All old feature commits from previous nordix branch are included
- [ ] All backports are evaluated (not already in base)
- [ ] Original commit messages and author are preserved
- [ ] Merge conflicts properly resolved
- [ ] Branch has correct tag format: `{version}-nordix-{patch}`
- [ ] Log history is clean and chronological

## Conflict Resolution Strategy

### Content Conflict
- **HEAD (nordix branch)**: Usually has accumulated fixes
- **Incoming (from cherry-pick)**: Has the change to be backported
- **Resolution**: If both changes are valid, merge manually; if conflicting, keep security-critical change

### Delete/Modify Conflict
- **Deleted by us**: File was removed in nordix evolution
- **Modified by them**: Incoming commit wants to modify removed file
- **Resolution**: Evaluate if the incoming change is still relevant; add file if needed


## Common Scenarios

### Scenario 1: Creating New Version Branch
```bash
git checkout -b 26.2.14-nordix 26.2.14
git cherry-pick commit1 commit2 commit3
git tag 26.2.14-nordix-1
```

### Scenario 2: Conflict Resolution

1. **Try automatic merge strategies first**
   ```bash
   git cherry-pick -X ort <commit>
   ```

2. **For manual conflict resolution** (when automatic strategies fail)
   - Review conflict markers to understand changes from both sides
   - Prefer the incoming commit's logic but preserve any other changes from HEAD
     - Security fixes must be applied with their intended logic intact
     - Merge both sides intelligently: keep the security fix's core logic while preserving other HEAD changes

3. **Continue cherry-pick after resolving conflicts**
   ```bash
   git add <resolved-files>
   git rm <deleted-files>  # For delete/modify conflicts, remove the file if it was deleted in HEAD
   git cherry-pick --continue --no-edit
   ```

4. **Summarize conflict resolutions**
   - Describe how conflicts were resolved and why certain changes were kept or discarded
   - For security fixes: explain how the security fix logic was preserved while merging with other changes


## References

- [Nordix Keycloak Repository](https://github.com/Nordix/keycloak)
