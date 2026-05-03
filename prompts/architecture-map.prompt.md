---
description: "Create architecture map for a project"
name: "architecture-map"
---

Your task is to produce a compact, evidence-backed architecture overview of the current codebase, optimized for LLM consumption and security review.

Priorities, in order:

1. Maximize signal density.
2. Prefer code/config evidence over prose docs.
3. Surface trust boundaries, runtime entry points, privileged paths, and external dependencies.
4. Avoid filler, placeholder-like prose, and repeated caveats.

## Phase 1 - Gather evidence

Inspect the repository systematically, but stay selective: spend attention where architectural or security signal is likely.

Collect evidence for these topics:

1. Top-level structure and major subprojects.
2. Dependency manifests and lockfiles to determine languages, frameworks, pinned versions, and build tooling.
3. Top-level docs such as `README.md`, `CONTRIBUTING.md`, and architecture or operations docs.
4. Runtime entry points and execution surfaces such as CLIs, servers, workers, hooks, schedulers, and wrapper scripts.
5. Deployment and infra assets such as Dockerfiles, compose files, Kubernetes manifests, Helm charts, Terraform, CDK, and release scripts.
6. CI/CD workflows, publishing paths, permissions, token usage, and supply-chain controls.
7. Persistent state and data stores, including databases, queues, caches, object stores, local files, and control-plane state.
8. External integrations such as APIs, SDKs, webhooks, cloud services, SCM services, and messaging systems.
9. Authentication, authorization, identity transitions, and privilege boundaries.
10. Untrusted input flows from CLI args, env vars, files, network payloads, API responses, GitHub data, and repo content into parsing, templating, persistence, subprocesses, or outbound calls.
11. Security-relevant implementation details such as secret handling, TLS/crypto, temp files, archive extraction, dynamic execution, filesystem mutation, and panic/error exposure.
12. Test, lint, SAST, dependency scanning, and other quality/security tooling.

Language-specific overlay:

- For Go projects, also inspect module boundaries, `replace` directives, vendoring, `cmd/`, `internal/`, `pkg/`, generated code, router or interceptor registration, controller setup, worker lifecycles, `os/exec`, `filepath` handling, Kubernetes RBAC, and debug surfaces such as `/metrics`, `/healthz`, `/readyz`, and `pprof`.

Evidence rules:

- Prefer direct evidence from source, manifests, and pipeline files over documentation.
- If docs and code disagree, prefer executable/config evidence and call out the mismatch.
- Mark indirect conclusions as `Inferred`.
- If something cannot be determined, write `_Not determined from available evidence._`

## Phase 2 - Write the architecture document

Produce the document using exactly the template below.

Output rules:

- Keep the document compact and information-dense.
- Prefer tables over prose for inventories.
- Use short factual sentences, not explanatory narration.
- Do not repeat the same fact in multiple sections unless it changes meaning in context.
- Do not invent components to fit a template.
- If a section has no findings, use `N/A - [reason]` or `_Not determined from available evidence._` as directed.
- Add short evidence references at the end of substantive lines or cells, for example `(Evidence: src/main.py, .github/workflows/ci.yml)`.
- If a statement is based only on documentation, mark it `Docs-only`.

Diagram rules:

- Use Mermaid in Section 2.
- Choose the diagram type that best compresses the architecture:
- Use `flowchart` for runtime topology, trust zones, deployment shape, and component relationships.
- Use `sequenceDiagram` when request flow, auth flow, webhook flow, or data-ingest order matters more than topology.
- Use at most one Mermaid diagram.
- Model only evidence-backed components and boundaries.
- Prefer trust-zone subgraphs or explicit boundary labels over decorative detail.

---

# Architecture Overview

## 1. Executive Summary

| Field | Value |
|---|---|
| Project type | [CLI / library / service / monorepo / operator / mixed] |
| Primary runtime surfaces | [Main binaries, CLIs, services, workers] |
| Highest-value trust boundaries | [1-3 boundary summaries] |
| Highest-risk input paths | [1-3 concise summaries] |
| Primary data stores | [Datastores or `None identified`] |
| Primary external integrations | [Key integrations or `None identified`] |

## 2. System Diagram

Use one Mermaid diagram that best captures the most important runtime structure or request/data flow.

```mermaid
flowchart LR
    %% replace with an evidence-backed diagram
```

## 3. Project Structure

Provide only the directories and files that matter architecturally. Group by role, not by exhaustive filesystem listing.

```text
[Project Root]/
├── path/  # purpose
└── path/  # purpose
```

## 4. Runtime Components

List the concrete runtime components that actually exist. If the repository is primarily a library, say so and describe the exposed packages or entry points instead.

| Component | Kind | Responsibility | Key tech | Deployment/Execution | Trust level |
|---|---|---|---|---|---|
| [Name] | [CLI / API / worker / controller / library] | [What it does] | [Language/frameworks] | [How it runs] | [User / internal / privileged] |

## 5. Data Stores and Durable State

Include durable state only. If none is evident, say `N/A - no durable state identified in this repository.`

| Store | Type | Purpose | Key schema/objects | Access path |
|---|---|---|---|---|
| [Name] | [PostgreSQL / Redis / S3 / files / K8s objects] | [Why it exists] | [Important tables, collections, object kinds, or paths] | [Which component uses it] |

## 6. External Integrations

| Service | Purpose | Integration method | Authn/Authz method | Direction |
|---|---|---|---|---|
| [Name] | [Why used] | [REST / SDK / webhook / git / CLI] | [Token / key / OAuth / none evident] | [Inbound / outbound / both] |

## 7. Deployment and Operations

| Topic | Findings |
|---|---|
| Deployment model | [Local-only / containerized / Kubernetes / serverless / mixed] |
| Build and release path | [How artifacts are built and published] |
| CI/CD | [Pipeline system and notable permissions or controls] |
| Runtime configuration | [Env vars, config files, secret injection patterns] |
| Monitoring and logging | [What is evident, or `Not determined`] |

## 8. Security Review Notes

### 8.1 Trust Boundaries

- [Boundary and why it matters] `(Evidence: ...)`

### 8.2 Untrusted Input Flows

| Source | Validation / parsing | Sensitive sinks | Notes |
|---|---|---|---|
| [CLI args / webhook / file / env var / API response] | [How handled] | [exec / file write / template / DB / outbound call] | [Risk-relevant detail] |

### 8.3 Authentication and Authorization

[Concise findings, or `N/A - no authn/authz mechanisms evident in this repository.`]

### 8.4 Secrets, Crypto, and Sensitive Operations

[Concise findings covering secret sources, TLS/crypto, subprocess execution, filesystem mutation, privileged operations, and other material security mechanisms.]

### 8.5 Security Tooling and Gaps

| Area | Findings |
|---|---|
| Preventive controls | [Linting, policy, scanning, signed artifacts, etc.] |
| Test coverage | [Relevant security or boundary coverage] |
| Unknowns / gaps | [Important unanswered questions] |

## 9. Development and Testing

| Topic | Findings |
|---|---|
| Local setup | [Brief steps or doc references] |
| Test frameworks | [Pytest / Go test / Jest / etc.] |
| Quality tools | [Linters, formatters, type-checkers, SAST] |
| Notable test strategy | [Integration tests, ephemeral envs, fuzzing, race detection, etc.] |

## 10. Future Considerations

- [Architectural debt, planned change, FIXME, or `_Not determined from available evidence._`]

## 11. Project Identification

| Field | Value |
|---|---|
| Project name | [Name] |
| Repository URL | [URL] |
| Primary contact/team | [Name or `_Not determined from available evidence._`] |
| Last updated | [YYYY-MM-DD] |

## 12. Glossary

| Term / Acronym | Definition |
|---|---|
| [Term] | [Meaning] |
