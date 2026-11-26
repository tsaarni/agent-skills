"""Drive jdb from a pysess session. Usage: see the java-debugging SKILL.md.

jdb has no machine-readable protocol: a reader thread buffers its output, and each
command waits for the jdb prompt ("> " or "thread-name[N] ").

Checks (run after editing):
    uvx ruff check --line-length 100 --select E,W,F,I,UP,B,SIM jdb_driver.py
    uvx ty check jdb_driver.py
"""

from __future__ import annotations

import builtins
import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Generator, Sequence
from types import TracebackType
from typing import IO, Literal, Self

_PROMPT_TOK = r"(?:> |[^\[\n]+\[\d+\] )"
PROMPT = re.compile(rf"(?:^|\n){_PROMPT_TOK}$")
INLINE_PROMPT = re.compile(rf": {_PROMPT_TOK}")
# Events that jdb prints asynchronously. The prompt can be printed in the middle
# of the line ('Breakpoint hit: worker-6[1] "thread=worker-6", ...').
# Field watchpoints format as:
#   Field (C.f) is 1000, will be 1001: "thread=...", ...
#   Field (C.f) access encountered: "thread=...", ...
EVENT = re.compile(
    r"(?:(?:Breakpoint hit|Exception occurred|Step completed|Method (?:entered|exited)): "
    r"|Field \([^)\n]+\) )[^\n]*\n"
)

_THREAD_STATUS = (
    r"(running|sleeping|cond\. waiting|waiting in a monitor|zombie|not started|unknown)"
    r"( \(at breakpoint\))?"
)
_THREAD_RE = re.compile(r"\s*\(\S+\)(\w+)\s+(.+?)\s+" + _THREAD_STATUS + r"\s*$")
_THREADS_UNTIL = re.compile(rf"Group .*{_PROMPT_TOK}$", re.S)

_ERR_PREFIX = (
    r"(?:Unable to |Cannot |Invalid |Not found:|Name unknown:|Internal exception:"
    r"|No (?:method|field|class|thread)\b"
    r"|com\.sun\.tools\.example\.debug\.expr\.ParseException)"
)
_JDB_ERR = re.compile(rf"^{_ERR_PREFIX}", re.M)
_PRINT_UNTIL = re.compile(rf"(?: = |Exception|{_ERR_PREFIX}).*\n{_PROMPT_TOK}$", re.S)


class _Text(str):
    """str subclass whose repr() renders multi-line text cleanly in pysess."""

    def __repr__(self) -> str:
        return str(self)


def _strip_prompt(text: str) -> str:
    """Remove trailing jdb prompt lines and interleaved async event lines."""
    cleaned = EVENT.sub("", text)
    lines = [line for line in cleaned.splitlines() if not PROMPT.match(line.rstrip() + " ")]
    return "\n".join(lines).strip()


def _check_err(cmd: str, out: str) -> str:
    """Strip prompts/events and raise RuntimeError if jdb reported a command failure."""
    cleaned = _strip_prompt(out)
    if _JDB_ERR.search(cleaned):
        raise RuntimeError(f"jdb `{cmd}` failed:\n{cleaned}")
    return cleaned


@contextlib.contextmanager
def _scoped(value: _Text, teardown: Callable[[], object]) -> Generator[_Text]:
    try:
        yield value
    finally:
        with contextlib.suppress(Exception):
            teardown()


class Jdb:
    """Interactive jdb wrapper driven over stdin/stdout pipes."""

    def __init__(self, address: str = "localhost:5005", jdb: str = "jdb") -> None:
        self.proc: subprocess.Popen[bytes] = subprocess.Popen(
            [jdb, "-attach", address],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("jdb subprocess pipes were not created")
        self._stdin: IO[bytes] = self.proc.stdin
        self._stdout: IO[bytes] = self.proc.stdout
        self._buf = ""
        self._pos = 0  # read position for command output
        self._evpos = 0  # read position for events, independent of _pos
        self.thread: tuple[str, str] | None = None
        self._suspend_all = False
        self._tid_cache: dict[str, str] = {}
        self.release_after_invoke = True
        self._cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()
        self.banner: str = self.out(timeout=15.0)
        if self.proc.poll() is not None:
            raise RuntimeError("jdb exited:\n" + self.banner)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self.cleanup()
        return False

    def close(self) -> None:
        """Alias for cleanup()."""
        self.cleanup()

    def _reader(self) -> None:
        fd = self._stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            with self._cond:
                self._buf += chunk.decode(errors="replace")
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def out(
        self,
        until: str | re.Pattern[str] = PROMPT,
        timeout: float = 10.0,
    ) -> str:
        """Return output since the last call. Stop when `until` matches, or at timeout."""
        pattern = re.compile(until) if isinstance(until, str) else until
        end = time.monotonic() + timeout
        with self._cond:
            while True:
                new = self._buf[self._pos :]
                rem = end - time.monotonic()
                if pattern.search(new) or rem <= 0 or self.proc.poll() is not None:
                    self._pos += len(new)
                    return new
                self._cond.wait(timeout=min(0.05, rem))

    def cmd(
        self,
        c: str,
        until: str | re.Pattern[str] = PROMPT,
        timeout: float = 10.0,
    ) -> _Text:
        """Send one jdb command and return its output."""
        with self._cond:
            self._pos = len(self._buf)
        self._stdin.write((c + "\n").encode())
        self._stdin.flush()
        return _Text(self.out(until=until, timeout=timeout))

    def _run(self, c: str, *, check: bool = False) -> _Text:
        out = self.cmd(c)
        return _Text(_check_err(c, out) if check else _strip_prompt(out))

    def p(self, expr: str, release: bool | None = None) -> str:
        """`print <expr>`, return only the value (or the jdb error text).

        A method call in expr leaves the whole VM suspended; release_others() undoes
        that for thread-only stops. On suspend-all events (catch/watch/step), other
        threads stay suspended by default; pass release=True/False to override.
        """
        o = _strip_prompt(self.cmd("print " + expr, until=_PRINT_UNTIL))
        if release is None:
            release = self.release_after_invoke and not self._suspend_all
        if self.thread and release:
            self.release_others()
        lines = o.splitlines()
        for line in lines:
            if " = " in line:
                return line.split(" = ", 1)[1]
        return lines[0] if lines else ""

    def set(self, target: str, value: object) -> _Text:
        """Assign `value` to a local, argument, or field (e.g. `j.set('C.field', 1000)`)."""
        return self._run(f"set {target} = {value}", check=True)

    def release_others(self) -> None:
        """Resume every thread except the current one.

        A method invoke leaves a VM-level suspend that also suspends threads started
        later; resuming threads one by one does not clear it. So suspend the current
        thread once more, resume the VM, and reselect the thread (`resume` forgets it).
        """
        if self.thread:
            self.cmd("suspend " + self.thread[0])
        self.cmd("resume")
        if self.thread:
            self.cmd("thread " + self.thread[0])

    def field_ref(self, obj: str, field: str) -> str | None:
        """Return 'instance of X(id=N)' for obj.field via `dump`, without running code."""
        m = re.search(rf"^\s*{re.escape(field)}: (.*)$", self.cmd("dump " + obj), re.M)
        return m.group(1).strip() if m else None

    def threads(self) -> dict[str, tuple[str, str]]:
        """{name: (thread_id, status)} for all threads."""
        res: dict[str, tuple[str, str]] = {}
        for line in self.cmd("threads", until=_THREADS_UNTIL).splitlines():
            m = _THREAD_RE.match(line)
            if m:
                res[m.group(2)] = (m.group(1), m.group(3) + (m.group(4) or ""))
        self._tid_cache = {name: tid for name, (tid, _) in res.items()}
        return res

    def select(self, name: str) -> str:
        """Make the thread with this name current (needed for print, locals, where, cont)."""
        tid = self._tid_cache.get(name)
        if tid is not None:
            out = self.cmd("thread " + tid)
            if not _JDB_ERR.search(_strip_prompt(out)) and "not a valid thread" not in out:
                self.thread = (tid, name)
                return tid
        all_threads = self.threads()
        if name not in all_threads:
            raise KeyError(f"thread {name!r} not found; available: {sorted(all_threads)}")
        tid = all_threads[name][0]
        self.cmd("thread " + tid)
        self.thread = (tid, name)
        return tid

    def wait_hit(self, timeout: float = 30.0) -> str:
        """Wait for the next event, select its thread (stored in self.thread), return the line.

        Scans all output, also what cmd() already returned, so an event printed
        during `cont` is not lost.
        """
        end = time.monotonic() + timeout
        with self._cond:
            while True:
                m = EVENT.search(self._buf, self._evpos)
                if m:
                    self._evpos = m.end()
                    self._pos = max(self._pos, m.end())
                    break
                rem = end - time.monotonic()
                if rem <= 0 or self.proc.poll() is not None:
                    tail = self._buf[self._evpos :]
                    raise TimeoutError(f"no event within {timeout}s:\n{tail}")
                self._cond.wait(timeout=min(0.05, rem))
        line = m.group(0).strip()
        self._suspend_all = line.startswith(("Exception occurred:", "Field (", "Step completed:"))
        if not INLINE_PROMPT.search(line):
            self.out(timeout=1.0)  # consume the prompt that follows the event
        t = re.search(r'"thread=([^"]+)"', line)
        if t:
            self.select(t.group(1))
        return line

    def drain(self) -> None:
        """Forget events printed so far and compact the internal output buffer."""
        with self._cond:
            self._buf = ""
            self._pos = 0
            self._evpos = 0

    def cont(self) -> _Text:
        """Resume the thread stopped by the last event (by id: plain `cont` can fail)."""
        self._suspend_all = False
        if self.thread is not None:
            o = self.cmd("resume " + self.thread[0])
            self.thread = None
            return o
        return self.cmd("cont")

    def resume(self) -> _Text:
        """Resume all threads (use after suspend-all events like catch, watch, or step)."""
        self.thread = None
        self._suspend_all = False
        return self.cmd("resume")

    def bp(
        self,
        loc: str,
        suspend: Literal["thread", "all", "go"] = "thread",
    ) -> _Text:
        """Set a breakpoint at `Class.method` or `Class:line`.

        suspend: 'thread' (default, stops only the hitting thread),
                 'all' (stops all threads), or 'go' (logs without stopping).
        """
        if suspend not in ("thread", "all", "go"):
            raise ValueError("suspend must be 'thread', 'all', or 'go'")
        kind = "at" if ":" in loc else "in"
        prefix = "stop" if suspend == "all" else f"stop {suspend}"
        try:
            return self._run(f"{prefix} {kind} {loc}", check=True)
        except RuntimeError:
            with contextlib.suppress(Exception):
                self.cmd("clear " + loc)
            raise

    def bps(self) -> builtins.list[str]:
        """List currently set breakpoint locations."""
        return [
            m.group(1)
            for line in self.cmd("clear").splitlines()
            if (m := re.search(r"breakpoint (\S+)", line))
        ]

    def clear(self, loc: str | None = None) -> _Text:
        """Clear one breakpoint (`loc`), or all breakpoints when `loc` is None."""
        if loc is not None:
            return self._run("clear " + loc, check=True)
        cleared = [self._run("clear " + spec) for spec in self.bps()]
        return _Text("\n".join(c for c in cleared if c))

    def catch(
        self,
        exc: str,
        mode: Literal["caught", "uncaught", "all"] = "caught",
    ) -> _Text:
        """Break when `exc` is thrown (`mode`: 'caught', 'uncaught', or 'all')."""
        if mode not in ("caught", "uncaught", "all"):
            raise ValueError("mode must be 'caught', 'uncaught', or 'all'")
        return self._run(f"catch {mode} {exc}", check=True)

    def ignore(
        self,
        exc: str,
        mode: Literal["caught", "uncaught", "all"] = "caught",
    ) -> _Text:
        """Remove an exception breakpoint set with `catch`."""
        if mode not in ("caught", "uncaught", "all"):
            raise ValueError("mode must be 'caught', 'uncaught', or 'all'")
        return self._run(f"ignore {mode} {exc}", check=True)

    def watch(
        self,
        field: str,
        mode: Literal["write", "access", "all"] = "write",
    ) -> _Text:
        """Set a field watchpoint (`mode`: 'write', 'access', or 'all')."""
        if mode not in ("write", "access", "all"):
            raise ValueError("mode must be 'write', 'access', or 'all'")
        c = f"watch {field}" if mode == "write" else f"watch {mode} {field}"
        return self._run(c, check=True)

    def unwatch(
        self,
        field: str,
        mode: Literal["write", "access", "all"] = "write",
    ) -> _Text:
        """Remove a field watchpoint (`mode`: 'write', 'access', or 'all')."""
        if mode not in ("write", "access", "all"):
            raise ValueError("mode must be 'write', 'access', or 'all'")
        c = f"unwatch {field}" if mode == "write" else f"unwatch {mode} {field}"
        return self._run(c, check=True)

    def temp_bp(
        self,
        loc: str,
        suspend: Literal["thread", "all", "go"] = "thread",
    ) -> contextlib.AbstractContextManager[_Text]:
        """Context manager that sets a breakpoint and always clears it on exit."""
        return _scoped(self.bp(loc, suspend=suspend), lambda: self.clear(loc))

    def temp_watch(
        self,
        field: str,
        mode: Literal["write", "access", "all"] = "write",
    ) -> contextlib.AbstractContextManager[_Text]:
        """Context manager that sets a field watchpoint and always removes it on exit."""
        return _scoped(self.watch(field, mode=mode), lambda: self.unwatch(field, mode=mode))

    def temp_catch(
        self,
        exc: str,
        mode: Literal["caught", "uncaught", "all"] = "caught",
    ) -> contextlib.AbstractContextManager[_Text]:
        """Context manager that sets an exception breakpoint and always ignores it on exit."""
        return _scoped(self.catch(exc, mode=mode), lambda: self.ignore(exc, mode=mode))

    def inspect(self, depth: int = 5) -> _Text:
        """One-shot summary of current thread, locals/args, and top `depth` stack frames."""
        parts: builtins.list[str] = []
        if self.thread is not None:
            mode = "suspend-all" if self._suspend_all else "suspend-thread"
            parts.append(f"thread: {self.thread[1]} (id={self.thread[0]}, {mode})")
        if loc := str(self.locals()):
            parts.append(loc)
        if stk := str(self.where(depth)):
            parts.append("stack:\n" + stk)
        return _Text("\n".join(parts))

    def where(self, depth: int = 10) -> _Text:
        """Top `depth` frames of the current thread's stack."""
        return _Text("\n".join(self._run("where").splitlines()[:depth]))

    def locals(self) -> _Text:
        """Local variables and arguments of the current frame."""
        return self._run("locals")

    def dump(self, expr: str) -> _Text:
        """Fields of `expr` via `dump` (does not call toString())."""
        return self._run("dump " + expr)

    def list(self) -> _Text:
        """Source lines around the current location (configure path with `use`)."""
        return self._run("list")

    def up(self, n: int = 1) -> _Text:
        """Move up `n` caller frames."""
        return self._run(f"up {n}")

    def down(self, n: int = 1) -> _Text:
        """Move down `n` callee frames."""
        return self._run(f"down {n}")

    def _step_cmd(self, c: str, timeout: float = 15.0) -> str:
        self.drain()
        self.cmd(c)
        return self.wait_hit(timeout=timeout)

    def next(self, timeout: float = 15.0) -> str:
        """Step over one source line and wait for the step event."""
        return self._step_cmd("next", timeout=timeout)

    def step(self, timeout: float = 15.0) -> str:
        """Step into the next call and wait for the step event."""
        return self._step_cmd("step", timeout=timeout)

    def step_up(self, timeout: float = 15.0) -> str:
        """Step out of the current method and wait for the step event."""
        return self._step_cmd("step up", timeout=timeout)

    def collect(
        self,
        exprs: Sequence[str],
        count: int = 10,
        timeout: float = 30.0,
        keep: Callable[[builtins.list[str]], bool] | None = None,
    ) -> builtins.list[builtins.list[str]]:
        """At up to `count` events evaluate `exprs` and resume; stop early when `keep(vals)` holds.

        `timeout` is a total deadline (seconds) across all hits; if it expires after at least
        one hit, returns the partial rows collected so far instead of discarding them.
        """
        deadline = time.monotonic() + timeout
        rows: builtins.list[builtins.list[str]] = []
        for _ in range(count):
            try:
                self.wait_hit(max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                if rows:
                    break
                raise
            vals = [self.p(e) for e in exprs]
            rows.append(vals)
            if keep and keep(vals):
                break
            self.cont()
        return rows

    def cleanup(self) -> None:
        """Clear all breakpoints, resume every thread, and detach. The target keeps running."""
        if self.proc.poll() is not None:
            return
        with contextlib.suppress(Exception):
            self.clear()
        with contextlib.suppress(Exception):
            self.resume()
        with contextlib.suppress(OSError):
            self._stdin.write(b"exit\n")
            self._stdin.flush()
        for action, wait_s in ((None, 5), (self.proc.terminate, 3), (self.proc.kill, 3)):
            if action is not None:
                action()
            try:
                self.proc.wait(timeout=wait_s)
                return
            except subprocess.TimeoutExpired:
                continue
