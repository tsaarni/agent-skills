---
name: runagent
description: Use when running applications, servers, or other long running commands in the background during debugging, testing, or development.
---

`runagent` is installed and available in your `PATH`. If not it can be run via `go run github.com/tsaarni/runagent/cmd/runagent@latest`

Example usage:

```console
$ runagent run -n webserver -- python3 -m http.server 9000
$ runagent ps webserver
# Shows process state, PID, command, uptime, CPU usage, memory usage, open file descriptors, disk I/O, etc.
$ runagent logs webserver
# Log line markers: │ = runagent control messages, ~ = resource stats, blank = process stdout/stderr. Stderr lines are shown in red when color is enabled. Timestamp format is customizable with --time-format.
# For more details use --json to see the full machine readable log records.
$ runagent ps
# Shows all managed processes in a compact table
$ runagent kill webserver
$ runagent delete webserver
```

Description of commands and flags

```json
[{"cmd":"runagent run <command>","desc":"Spawn a background process.","flags":["--name: Process name.","--env: Environment variable KEY=VALUE (repeatable).","--cwd: Working directory."]},{"cmd":"runagent ps [target]","desc":"List processes or show process details.","flags":["--all: Show details for all processes."]},{"cmd":"runagent logs <target>","desc":"Read process log output.","flags":["--type=start,log,stats,stop: Event types to show (comma-separated: start,log,stats,stop).","--time-range: Time range (FROM..TO). FROM/TO: -5m (ago), 21:00:00, 2026-06-24T21:00:00, +2m (relative to FROM).","--limit: Max events to return (first N from window).","--last: Return last N matching events.","--time-format=time: Timestamp format: time, datetime, none, or Go layout string.","--follow: Follow log output.","--stream: Filter by stream (stdout or stderr)."]},{"cmd":"runagent kill <target>","desc":"Send signal to process (default: SIGTERM).","flags":["--signal=SIGTERM: Signal to send."]},{"cmd":"runagent delete [target]","desc":"Remove process and logs.","flags":["--all: Delete all processes."]},{"cmd":"runagent wait <target>","desc":"Block until process exits.","flags":["--timeout: Timeout duration (e.g., 30s)."]},{"cmd":"runagent daemon status","desc":"Show daemon status."},{"cmd":"runagent daemon start","desc":"Start the daemon."},{"cmd":"runagent daemon stop","desc":"Stop the daemon."},{"cmd":"runagent daemon clean","desc":"Remove all runtime and state files."}]
```

Process States

- **Running** — process is alive
- **Exited** — process called exit() (check exit code for success/failure)
- **Killed** — terminated by signal

## Rules

- Use `-n <name>` when starting processes.
- If a dead process with the same name exists, `runagent run` auto-replaces it. If the process is still running, it will fail — use `runagent kill <name>` first or `runagent delete <name>`.
- After starting a process, check `runagent logs <name> --last 10` to verify it started correctly.
- Use `--json` when you need to parse output programmatically.
- Do not delete processes at the end of a test run unless asked. They might be useful if user asks further questions, or wants to continue testing.
