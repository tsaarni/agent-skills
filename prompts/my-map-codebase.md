---
description: Create an evidence-backed architecture overview of the current codebase
argument-hint: "[focus or subproject]"
---

Produce a compact, evidence-backed architecture overview of the current codebase, for LLM consumption and security review.

Focus: $ARGUMENTS (if empty, the whole repository)

Output directory, set it again in each shell command that uses it:
`O=${TMPDIR:-/tmp}/agent-output/$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null | tr / _ | grep . || basename "$PWD")`

Priorities: signal density; code/config evidence over prose docs; trust boundaries, runtime entry points, privileged paths and external dependencies; no filler or repeated caveats.

## 1. Gather evidence

Work outward from what runs, staying within the focus. Spend attention where architectural or security signal is likely.

1. Layout: `git ls-files | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -40`, top-level README and architecture/operations docs.
2. Manifests and lockfiles: languages, frameworks, pinned versions, build tooling.
3. Entry points: `main` packages, `cmd/`, CLIs, servers, workers, controllers, hooks, schedulers, wrapper scripts. Follow each into its wiring: routers, handlers, interceptors, reconcilers.
4. Deployment and release: Dockerfiles, compose, Kubernetes manifests, Helm, Terraform, release scripts.
5. CI/CD: workflows, permissions, token usage, publishing, supply-chain controls.
6. State: databases, queues, caches, object stores, local files, Kubernetes objects.
7. External integrations: APIs, SDKs, webhooks, cloud and SCM services, messaging.
8. Authn/authz, identity transitions, privilege boundaries.
9. Untrusted input (CLI args, env vars, files, network payloads, API responses, repo content) into parsing, templating, persistence, subprocesses, outbound calls.
10. Secrets, TLS/crypto, temp files, archive extraction, dynamic execution, filesystem mutation, error exposure.
11. Test, lint, SAST and dependency-scanning tooling.

Go: also module boundaries, `replace` directives, vendoring, `internal/` vs `pkg/`, generated code, controller setup, worker lifecycles, `os/exec`, `filepath` handling, Kubernetes RBAC, `/metrics`, `/healthz`, `/readyz`, `pprof`.

Evidence rules:
- Prefer source, manifests and pipelines over docs. If they disagree, trust the executable/config evidence and call out the mismatch.
- End substantive lines and cells with `(Evidence: path, path)`. Mark indirect conclusions `Inferred` and doc-only statements `Docs-only`.
- Write `_Not determined from available evidence._` when unknown, `N/A - reason` when a section doesn't apply. Never invent components to fill the template.

## 2. Write the document

Use the template below. Tables over prose, short factual sentences, no fact repeated across sections. One Mermaid diagram: `flowchart` for topology and trust zones, `sequenceDiagram` when request, auth or ingest order matters more. Only evidence-backed components; show trust zones as subgraphs or labeled boundaries.

Write it to `$O/architecture.md` (`$O/architecture-<focus>.md` when focused). Before finishing, check that every `Evidence:` path exists, and fix or drop claims whose path does not. Reply with the path and the Executive Summary only.

---

# Architecture Overview: <repo> [focus]

## 1. Executive Summary

| Field | Value |
|---|---|
| Project type | CLI / library / service / monorepo / operator / mixed |
| Primary runtime surfaces | |
| Highest-value trust boundaries | 1-3 |
| Highest-risk input paths | 1-3 |
| Primary data stores | or `None identified` |
| Primary external integrations | or `None identified` |

## 2. System Diagram

```mermaid
```

## 3. Project Structure

Only architecturally relevant paths, grouped by role.

```text
path/  # purpose
```

## 4. Runtime Components

For a library, list the exposed packages or entry points instead.

| Component | Kind | Responsibility | Key tech | Deployment/Execution | Trust level |
|---|---|---|---|---|---|

## 5. Data Stores and Durable State

| Store | Type | Purpose | Key schema/objects | Access path |
|---|---|---|---|---|

## 6. External Integrations

| Service | Purpose | Integration method | Authn/Authz | Direction |
|---|---|---|---|---|

## 7. Deployment and Operations

| Topic | Findings |
|---|---|
| Deployment model | |
| Build and release path | |
| CI/CD | notable permissions and controls |
| Runtime configuration | env vars, config files, secret injection |
| Monitoring and logging | |

## 8. Security Review Notes

### 8.1 Trust Boundaries

- Boundary and why it matters `(Evidence: ...)`

### 8.2 Untrusted Input Flows

| Source | Validation / parsing | Sensitive sinks | Notes |
|---|---|---|---|

### 8.3 Authentication and Authorization

### 8.4 Secrets, Crypto, and Sensitive Operations

Secret sources, TLS/crypto, subprocess execution, filesystem mutation, privileged operations.

### 8.5 Security Tooling and Gaps

| Area | Findings |
|---|---|
| Preventive controls | linting, policy, scanning, signed artifacts |
| Test coverage | security and boundary tests |
| Unknowns / gaps | |
