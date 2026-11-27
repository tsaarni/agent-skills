---
name: runagent
description: "Run servers and long-running commands in the background while debugging or testing."
---

`runagent` is installed and available in `PATH`. If not it can be run via `go run github.com/tsaarni/runagent/cmd/runagent@latest`

## Commands

Run `runagent <command> --help` for all flags. `<name>` also accepts a process ID.

| Command | Purpose | Useful flags |
|---|---|---|
| `run -n <name> -- <cmd...>` | Start a background process | `-e KEY=VALUE` (repeatable), `--cwd PATH` |
| `ps [name]` | List processes, or show details for one | `-a` details for all |
| `logs <name>` | Read output | `--last N`, `--stream stderr`, `--time-range -5m..` or `15:05:00..+2m` |
| `wait <name>` | Block until exit | `--timeout 600s` |
| `kill <name>` | Send signal (default SIGTERM) | `--signal SIGINT` |
| `delete [name]` | Kill if running and delete logs | `--stopped`, `--all` |
| `daemon status\|start\|stop\|clean` | Manage the daemon | |

Global flag `--json` gives machine-readable output. Avoid `logs -f`: it blocks until interrupted.

## Process states

- **Running**: process is alive.
- **Exited**: process called exit(). `runagent wait` prints the exit code and exits with it, so `runagent wait <name> && ...` works.
- **Killed**: terminated by signal. `runagent wait` exits with 128 + signal number.

`runagent ps` shows state, PID, command, uptime, CPU, memory, open file descriptors and disk I/O.

`runagent logs` line markers: │ = runagent control messages, ~ = resource stats, blank = process stdout/stderr.

## Workflows

Server (runs until stopped):

```bash
runagent run -n <name> -- <command...>
runagent logs <name> --last 10    # confirm it started
```

Batch process (exits on its own, e.g. build or test suite):

```bash
runagent run -n <name> -- <command...>; runagent wait <name>; runagent logs <name> --last <N>
runagent delete <name>            # after a successful build; keep failed builds and test runs
```

## Rules

* Always name processes with `-n <name>`. Reuse the same name for repeated runs of the same build or test; do not invent new names per attempt.
* Use `runagent wait <name>` instead of polling. It returns as soon as the process exits.
* Do not filter output at start (no `| grep ERROR`). Filter later with `runagent logs`.
* `runagent run` replaces an exited or killed process with the same name, but fails if it is still running. Stop it first with `runagent kill <name>` or `runagent delete <name>`.
* Keep exited processes by default; their logs may be needed for debugging or follow-up testing. Exception: delete a build process once the build has succeeded and its output has been checked.
