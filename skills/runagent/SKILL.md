---
name: runagent
description: Use when running applications, servers, or other long running commands in the background during debugging, testing, or development.
---

`runagent` is installed and available in `PATH`. If not it can be run via `go run github.com/tsaarni/runagent/cmd/runagent@latest`

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
        --stopped              Delete all processes that are not running (exited or killed).

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

* Always name processes using `-n <name>`.
* For background jobs that complete on their own (such as long compilations), run: `runagent run -n <name> -- <compile-commands...>; runagent wait <name>; runagent logs <name> --last <num-lines>`.
* To wait for a process to finish, run `runagent wait <name>`. This returns immediately when the process exits, so you do not need to poll.
* Do not filter output when starting a process (avoid commands like `| grep ERROR`). Filter output later using `runagent logs`.
* `runagent run` automatically replaces dead processes that have the same name. If a process is still active, the command fails; stop or remove it first with `runagent kill <name>` or `runagent delete <name>`.
* When re-running recurring batch jobs prefer using same name in consequent runs, to avoid cluttering the `runagent ps` list with too many entries.
* After launching a process, run `runagent logs <name> --last 10` to confirm that it started properly.
* Keep processes alive after a test run unless instructed to remove them. They may be needed for debugging or follow-up testing.
