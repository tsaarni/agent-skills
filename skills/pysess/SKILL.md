---
name: pysess
description: Persistent Python REPL sessions that keep sockets, connections, imports, and variables alive across bash calls.
---

Use `pysess` instead of `python3 -c` when keeping Python state, sockets, or client connections alive across multiple bash calls, or when coordinating blocking operations (like a server `accept()` and a client request) across turns.

Source and documentation: https://github.com/tsaarni/pysess. If `pysess` is not installed, install it with `uv tool install git+https://github.com/tsaarni/pysess`.

```bash
# 1. Start a session named "web", bind a server socket, and get its URL synchronously
pysess new -n web <<'EOF'
import socket
srv = socket.create_server(("127.0.0.1", 0))
f"http://127.0.0.1:{srv.getsockname()[1]}"
EOF
# Output:
# [pysess: web]
# 'http://127.0.0.1:54321'

# 2. Run the blocking accept() step in the background (-d / --detach returns immediately)
pysess exec web -d <<'EOF'
conn, addr = srv.accept()
req = conn.recv(4096).decode()
conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
conn.close()
print("handled request from", addr, flush=True)
EOF
# Output:
# [pysess: web | running (0.0s) - use 'pysess wait web' or 'pysess cancel web']

# 3. Trigger the waiting server from another command
curl -s http://127.0.0.1:54321/hello

# 4. Come back to the session and inspect captured state (trailing expressions auto-print and bind to `_`)
#    Completed background output ("handled request from ...") is flushed automatically!
pysess exec web -c "req.splitlines()[0]"

# 5. Clean up when done
pysess kill web
```

## Rules
- Pass code to `new` or `exec` via `-c CODE`, a `FILE` path, or `stdin` (`new` with no code starts an empty session).
- Name sessions you will come back to (`pysess new -n dbg ...`, then `pysess exec dbg ...`) instead of copying random IDs between calls.
- Keep persistent resources (clients, connections, sockets, open files) in top-level variables instead of closing them in `with` blocks.
- Trailing expressions auto-print (`repr()` truncated at 32 KB; full value stays in `_`; `print()` is never truncated). End a step with an assignment or `None` if you do not need its value; a trailing call such as `proc.stdin.write(...)` or `conn.send(...)` otherwise prints its return value.
- A step that raises exits with code 1. Variables assigned before the error stay defined, so re-run only the failed part.
- A session runs one step at a time: `exec` on a session with a running step prints `busy`, does not run, and also exits with code 1.
- By default, `new` and `exec` block until the step finishes (or `-t`/`--timeout` expires). Use this when you need the result or setup output (like a bound port) right away.
- Pass `-d` / `--detach` when you do not want to wait for the step to finish (e.g., blocking `accept()` or a background task you will trigger or inspect later). Split setup that returns a value from the `-d` step.
- `-t SEC` / `--timeout SEC` accepts numeric seconds (e.g., `0.2`, `10`; default 10 seconds on `new`/`exec`, 30 seconds on `wait`); mutually exclusive with `-d`/`--detach`. If `-t` expires, the step keeps running in the background. Set `-t` above the expected duration to get the result in one call, and the timeout of your shell tool above `-t`.
- When a step is running in the background (`[pysess: <id> | running ...]`):
  - Once it finishes, the next `pysess exec <id>` flushes its unread `print()` output automatically. Its trailing value is not printed; read it with `_`, or `print()` it in the step.
  - While still running, use `pysess wait <id>` (`-t 0` to poll) or `pysess cancel <id>` (`KeyboardInterrupt`).
- Output that background processes or threads write after their step finished appears at the start of the next `exec` or `wait`. Redirect noisy background processes (e.g. `Popen(..., stdout=subprocess.DEVNULL)` or a log file) to keep later output clean.
- The working directory is fixed when the session is created; `cd` in the calling shell does not change it. Use absolute paths.
- Sessions exit after 1 hour without activity (`PYSESS_IDLE_TIMEOUT`), and all state is lost. See Patterns for recreating a session.
- Use `pysess ps [<id>]` to inspect sessions, blocked locations, and variables; use `pysess kill <id>` or `pysess kill --all` to clean up.

## Patterns

Put helpers and setup in a file and load it when you start the session. Recreate the session with the same command after `kill` or an idle exit:

```bash
pysess new -n dbg /tmp/work/setup.py
pysess exec dbg -c 'client = Client("localhost:5005")'
```

After you edit the file, load it again with `pysess exec dbg /tmp/work/setup.py`, then create objects again: old instances keep the old class. A reload also runs the file's top-level code again, so a file that starts servers or threads starts new ones while the old ones keep running. Guard that code, for example `if "srv" not in globals(): srv = ...`.

Keep the session free while a call blocks (for example, an HTTP request that waits at a debugger breakpoint): run it with `pysess.bg`, which calls it in a daemon thread and returns a `concurrent.futures.Future`. The `Future` keeps the call's exception, so it does not print as a traceback in a later, unrelated step:

```python
import pysess, urllib.request
req = pysess.bg(urllib.request.urlopen, url, timeout=120)
# later steps: inspect the blocked server, then
# req.done(), req.result(timeout=5) (re-raises the call's exception), req.exception()
```

`pysess kill` ends the session even while a `bg` call is blocked; `ThreadPoolExecutor` threads would keep the process alive until the call returns. A `Future` cannot stop a call that is running; give the call its own timeout.

Use `-d` instead when the step itself is the blocking part and you do not need the session until it finishes.

Drive an interactive CLI (debugger, database shell) through `subprocess.Popen` with pipes. Read stdout in a daemon thread into a buffer variable, and after each command wait until the buffer ends with the tool's prompt (a regex), not until output goes quiet. Do not call a blocking `read()` on the pipe in a step: the session stays busy until the CLI prints something. Keep the raw buffer, so you can print its tail when your parsing goes wrong.

`pysess kill` does not stop processes started with `subprocess.Popen`. A CLI that exits when its stdin closes (like `jdb`) goes away with the session; other processes keep running. Stop them first (`p.terminate()`, or exit the CLI cleanly), then `pysess kill`, then check with `pgrep -af <name>` that none are left.

## Installing packages
- Never `pip install` into the user's Python. Inside the session, run `import pysess; pysess.venv.install("pkg", ...)` and then import the package; it goes into a private venv deleted when the session ends. Pass `-t 60` or more, since downloads can take longer than the default timeout.
- To reuse packages across sessions, first call `pysess.venv.use("<name>")` (shared venv, kept until removed), then `install(...)`. Installing into or removing a shared venv affects every session using it.
- API (`help(pysess.venv)` for details):
  - `install(*packages, upgrade=False)`: install requirement specifiers or local paths into the attached venv (the private venv if none is attached).
  - `use(name=None)`: attach shared venv `name`, or the session's private venv if omitted; created if needed, and any other venv is detached.
  - `detach()`: detach the venv, undoing its `sys.path`, `PATH`, and `VIRTUAL_ENV` changes.
  - `current()`: the attached `Venv` (`name`, `private`, `path`, `packages()`) or `None`.
  - `list()` / `remove(name)`: list venvs, or delete a shared venv (or an orphaned private venv).
- Modules already imported from a venv stay loaded after `detach()` or switching venvs; prefer switching before importing.
- `sys.executable` stays the pysess interpreter; for subprocesses use `python` from `PATH`, which is the venv's.
- The session does not see packages of the system or user Python: `python3 -c "import pkg"` working in bash does not mean it imports in the session. Check inside the session, and install with `pysess.venv.install`.
- From bash, `pysess venv ls` lists venvs and `pysess venv rm <name>` removes a shared venv you no longer need.
