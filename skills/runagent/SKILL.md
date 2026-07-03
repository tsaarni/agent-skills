---
name: runagent
description: Use when running applications, servers, or other long running commands in the background during debugging, testing, or development.
---

`runagent` is installed and available in your `PATH`. If not it can be run via `go run github.com/tsaarni/runagent/cmd/runagent@latest`

Example usage:

```console
$ runagent run -n webserver -- python3 -m http.server 9000
$ runagent status webserver
# Shows process state, PID, command, uptime, CPU usage, memory usage, open file descriptors, disk I/O, etc.
$ runagent logs webserver
# Log line markers: │ = runagent control messages, ~ = resource stats, blank = process stdout/stderr. Stderr lines are shown in red when color is enabled. Timestamp format is customizable with --time-format.
# For more details use --json to see the full machine readable log records.
$ runagent ps
# Shows all managed processes
$ runagent kill webserver
$ runagent delete webserver
```

Description of commands and flags

```json
[{"cmd":"runagent run \u003ccommand\u003e","desc":"Spawn a background process.","flags":["--name: Process name.","--env: Environment variable KEY=VALUE (repeatable).","--cwd: Working directory."]},{"cmd":"runagent list","desc":"List all managed processes."},{"cmd":"runagent status [target]","desc":"Show detailed process metrics."},{"cmd":"runagent logs \u003ctarget\u003e","desc":"Read process log output.","flags":["--type=start,log,stats,stop: Event types to show (comma-separated: start,log,stats,stop).","--time-range: Time range (FROM..TO). FROM/TO: -5m (ago), 21:00:00, 2026-06-24T21:00:00, +2m (relative to FROM).","--limit: Max events to return (first N from window).","--last: Return last N matching events.","--time-format=time: Timestamp format: time, datetime, none, or Go layout string.","--follow: Follow log output.","--stream: Filter by stream (stdout or stderr)."]},{"cmd":"runagent kill \u003ctarget\u003e","desc":"Send signal to process (default: SIGTERM).","flags":["--signal=SIGTERM: Signal to send."]},{"cmd":"runagent delete [target]","desc":"Remove process and logs.","flags":["--all: Delete all non-running processes.","--force: Kill running process and delete."]},{"cmd":"runagent wait \u003ctarget\u003e","desc":"Block until process exits.","flags":["--timeout: Timeout duration (e.g., 30s)."]},{"cmd":"runagent shutdown","desc":"Gracefully stop daemon and all processes."},{"cmd":"runagent daemon status","desc":"Show daemon status."},{"cmd":"runagent daemon start","desc":"Start the daemon."},{"cmd":"runagent daemon stop","desc":"Stop the daemon."},{"cmd":"runagent daemon clean","desc":"Remove all runtime and state files."}]
```

Process States

- **Running** — process is alive
- **Exited** — exit code 0
- **Crashed** — non-zero exit or killed by signal

## Rules

- Use `-n <name>` when starting processes.
- If process is already running e.g. from previous session, `runagent run` will fail. Use `runagent delete <name> --force` to remove it or clean up with `runagent delete --all --force`.
- After starting a process, check `runagent logs <name> --last 10` to verify it started correctly.
- Use `--json` when you need to parse output programmatically.
- Do not delete processes at the end of a test run unless asked. They might be useful if user asks further questions, or wants to continue testing.
