---
name: runagent
description: Use when running applications, servers, or other long running commands in the background during debugging, testing, or development.
---

`runagent` is installed and available in `PATH`. If not it can be run via `go run github.com/tsaarni/runagent/cmd/runagent@latest`

Description of commands and flags

```
Usage: runagent <command> [flags]

Flags:
  -h, --help    Show context-sensitive help.
      --json    Output in JSON format.

Commands:
  run <command>
    Spawn a background process.
    -n, --name=NAME            Process name.
    -e, --env=KEY=VALUE,...    Environment variable KEY=VALUE (repeatable).
        --cwd=PATH             Working directory.

  ps [target]
    List processes or show process details.
    -a, --all                  Show details for all processes.

  logs <target>
    Read process log output.
        --type=TYPES           Event types to show (comma-separated: start,log,stats,stop).
        --time-range=FROM..TO  Time range (FROM..TO). FROM/TO: -5m (ago), 21:00:00, 2026-06-24T21:00:00, +2m (relative to FROM).
        --limit=N              Max events to return (first N from window).
        --last=N               Return last N matching events.
        --time-format=FORMAT   Timestamp format: time, datetime, none, or Go layout string.
    -f, --follow               Follow log output.
        --stream=STREAM        Filter by stream (stdout or stderr).

  kill <target>
    Send signal to process (default: SIGTERM).
        --signal=SIGNAL        Signal to send.

  delete [target]
    Kill process (if running) and delete its logs.
        --all                  Delete all processes.

  wait <target>
    Block until process exits.
        --timeout=DURATION     Timeout duration (e.g., 30s).

  daemon status
    Show daemon status.

  daemon start
    Start the daemon.

  daemon stop
    Stop the daemon.

  daemon clean
    Remove all runtime and state files.

Examples:
  runagent run -n web -- python3 -m http.server 8080
  runagent ps
  runagent logs web --last 20
  runagent wait web
  runagent delete web
```

Process States

- **Running** — process is alive
- **Exited** — process called exit() (check exit code for success/failure)
- **Killed** — terminated by signal

`runagent ps` shows process state, PID, command, uptime, CPU usage, memory usage, open file descriptors, disk I/O, etc.

`runagent logs` log line markers: │ = runagent control messages, ~ = resource stats, blank = process stdout/stderr. Stderr lines are shown in red when color is enabled. Timestamp format is customizable with --time-format.


## Rules

- Use `-n <name>` when starting processes.
- Run processes under runagent without any log filtering (like `| grep ERROR`). Filtering can be done when using `runagent logs`.
- If a dead process with the same name exists, `runagent run` auto-replaces it. If the process is still running, it will fail — use `runagent kill <name>` first or `runagent delete <name>`.
- After starting a process, check `runagent logs <name> --last 10` to verify it started correctly.
- When waiting process to exit, use `runagent wait <name>` which will return immediately at process exists and avoids polling.
- Do not delete processes at the end of a test run unless asked. They might be useful if user asks further questions, or wants to continue testing.
