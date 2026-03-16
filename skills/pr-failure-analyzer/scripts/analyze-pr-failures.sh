#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <owner/repo> <pr-number> [dest-dir]"
    echo "  Analyzes failed CI workflows for a GitHub PR."
    echo "  Results are collected into dest-dir/<pr-number>/"
    echo "  (default dest-dir: /tmp/pr-analysis)"
    exit 1
}

[[ $# -lt 2 ]] && usage

REPO="$1"
PR="$2"
WORKDIR="${3:-/tmp/pr-analysis}"
PRDIR="${WORKDIR}/${PR}"
SKILL_DIR="$(dirname "$(realpath "$0")")/.."

rm -rf "$PRDIR"
mkdir -p "$PRDIR"

# --- Collect PR metadata ---

echo "==> Fetching PR info for ${REPO}#${PR}"
gh pr view "$PR" --repo "$REPO" --json headRefName,baseRefName,headRefOid,title,body,files \
    > "${PRDIR}/pr-info.json"

PR_BRANCH=$(jq -r .headRefName "${PRDIR}/pr-info.json")
BASE_BRANCH=$(jq -r .baseRefName "${PRDIR}/pr-info.json")
echo "    PR branch: ${PR_BRANCH}, base: ${BASE_BRANCH}"

echo "==> Fetching PR diff"
gh pr diff "$PR" --repo "$REPO" > "${PRDIR}/pr.diff" 2>/dev/null || true

echo "==> Fetching check results"
gh pr checks "$PR" --repo "$REPO" --json name,state,link,bucket \
    > "${PRDIR}/checks.json" 2>/dev/null || echo "[]" > "${PRDIR}/checks.json"

echo "==> Checking base branch failure history"
gh run list --repo "$REPO" --branch "$BASE_BRANCH" --status failure \
    --json databaseId,name,conclusion,createdAt --limit 15 \
    > "${PRDIR}/base-branch-failures.json" 2>/dev/null || echo "[]" > "${PRDIR}/base-branch-failures.json"

# --- Discover all failed run IDs ---

echo "==> Discovering failed workflow runs"

# Method 1: direct failed runs on the PR branch
gh run list --repo "$REPO" --branch "$PR_BRANCH" --status failure \
    --json databaseId,name,conclusion,headSha --limit 20 \
    > "${PRDIR}/failed-runs.json"

# Collect run IDs into an associative array: run_id -> workflow_name
declare -A RUN_MAP
while IFS=$'\t' read -r id name; do
    RUN_MAP["$id"]="$name"
done < <(jq -r '.[] | [.databaseId, .name] | @tsv' "${PRDIR}/failed-runs.json")

# Method 2: delegated/child workflows via check-runs API
HEAD_SHA=$(jq -r '.headRefOid // empty' "${PRDIR}/pr-info.json")
if [[ -z "$HEAD_SHA" ]]; then
    HEAD_SHA=$(gh api "repos/${REPO}/pulls/${PR}" --jq '.head.sha')
fi

echo "    HEAD SHA: ${HEAD_SHA}"
echo "==> Discovering delegated/child workflow runs via check-runs API"

gh api "repos/${REPO}/commits/${HEAD_SHA}/check-runs" --paginate \
    --jq '.check_runs[] | select(.conclusion == "failure") | [.id, .name] | @tsv' \
    > "${PRDIR}/failed-check-runs.tsv" 2>/dev/null || true

while IFS=$'\t' read -r check_run_id check_name; do
    [[ -z "$check_run_id" ]] && continue
    run_ids=$(gh api "repos/${REPO}/check-runs/${check_run_id}" --jq '.output.text // ""' 2>/dev/null \
        | grep -oP 'actions/runs/\K[0-9]+' || true)
    if [[ -n "$run_ids" ]]; then
        for rid in $run_ids; do
            if [[ -z "${RUN_MAP[$rid]:-}" ]]; then
                RUN_MAP["$rid"]="$check_name (delegated)"
                echo "    Found delegated run ${rid} from check '${check_name}'"
            fi
        done
    else
        details_url=$(gh api "repos/${REPO}/check-runs/${check_run_id}" --jq '.details_url // ""' 2>/dev/null || true)
        rid=$(echo "$details_url" | grep -oP 'actions/runs/\K[0-9]+' || true)
        if [[ -n "$rid" && -z "${RUN_MAP[$rid]:-}" ]]; then
            RUN_MAP["$rid"]="$check_name (delegated)"
            echo "    Found delegated run ${rid} from check '${check_name}' details_url"
        fi
    fi
done < "${PRDIR}/failed-check-runs.tsv"

if [[ ${#RUN_MAP[@]} -eq 0 ]]; then
    echo "No failed runs found."
    exit 0
fi

echo "==> Total failed runs to process: ${#RUN_MAP[@]}"
for rid in "${!RUN_MAP[@]}"; do
    echo "    ${rid}: ${RUN_MAP[$rid]}"
done

# --- Download per-job logs ---

echo "==> Downloading per-job logs"
for run_id in "${!RUN_MAP[@]}"; do
    RUN_LOGDIR="${PRDIR}/${run_id}"
    mkdir -p "$RUN_LOGDIR"

    jobs_tsv=$(gh run view "$run_id" --repo "$REPO" --json jobs \
        --jq '.jobs[] | select(.conclusion == "failure") | [.databaseId, .name] | @tsv' 2>/dev/null || true)

    if [[ -z "$jobs_tsv" ]]; then
        echo "    Run ${run_id}: no failed jobs, skipping (likely a dispatcher workflow)"
        continue
    fi

    while IFS=$'\t' read -r job_id job_name; do
        [[ -z "$job_id" ]] && continue
        safe_name=$(echo "$job_name" | tr '/:${}[] ' '________' | tr -cd '[:alnum:]_.-')
        log_file="${RUN_LOGDIR}/${safe_name}.txt"
        echo "    Run ${run_id}, job '${job_name}' (${job_id})"
        gh run view "$run_id" --repo "$REPO" --job "$job_id" --log-failed \
            > "$log_file" 2>&1 || true
    done <<< "$jobs_tsv"
done

# --- Build context.md ---

PR_TITLE=$(jq -r .title "${PRDIR}/pr-info.json")
PR_BODY=$(jq -r .body "${PRDIR}/pr-info.json")

cat > "${PRDIR}/context.md" <<EOF
# CI Failure Analysis Context

Repository: ${REPO}
PR: #${PR} — ${PR_TITLE}
PR Branch: ${PR_BRANCH}
Base Branch: ${BASE_BRANCH}

## PR Description

${PR_BODY}

## Changed Files
$(jq -r '.files[] | "- \(.path) (+\(.additions) -\(.deletions))"' "${PRDIR}/pr-info.json")

## Failed Runs

Read the listed log files to analyze each failure.

EOF

for run_id in "${!RUN_MAP[@]}"; do
    workflow="${RUN_MAP[$run_id]}"
    RUN_LOGDIR="${PRDIR}/${run_id}"
    echo "### Run ${run_id}: ${workflow}" >> "${PRDIR}/context.md"
    if [[ ! -d "$RUN_LOGDIR" ]]; then
        echo "No logs downloaded." >> "${PRDIR}/context.md"
        echo "" >> "${PRDIR}/context.md"
        continue
    fi
    has_logs=false
    while IFS= read -r -d '' f; do
        [[ -s "$f" ]] || continue
        has_logs=true
        size=$(stat --printf='%s' "$f")
        echo "- \`${f}\` ($(( size / 1024 ))KB)" >> "${PRDIR}/context.md"
    done < <(find "$RUN_LOGDIR" -type f -name '*.txt' -print0 | sort -z)
    if [[ "$has_logs" == false ]]; then
        echo "No logs downloaded." >> "${PRDIR}/context.md"
    fi
    echo "" >> "${PRDIR}/context.md"
done

cat >> "${PRDIR}/context.md" <<EOF
## Base Branch Recent Failures
\`\`\`json
$(cat "${PRDIR}/base-branch-failures.json")
\`\`\`
EOF

cat >> "${PRDIR}/context.md" <<EOF

## PR Diff
The full diff is at: \`${PRDIR}/pr.diff\`
EOF

echo ""
echo "==> To analyze with kiro-cli:"
echo "  kiro-cli chat \"Follow the pr-failure-analyzer skill to analyze these CI failures: \$(cat ${PRDIR}/context.md)\""
echo ""
echo "Or manually review the collected data in ${PRDIR}/"
