"""Drive an interactive jdb session from Python (meant to be loaded into a pysess session).

    exec(open("<skill>/scripts/jdb_driver.py").read())
    j = Jdb("localhost:5005")
    j.cmd("stop thread in org.example.Foo.bar")
    ... trigger the code path from another thread ...
    j.wait_hit()           # waits for the event, selects the stopped thread
    j.p("this.field")      # value of one expression
    j.cont()               # resume the stopped thread
    j.cleanup()            # clear breakpoints, resume, detach

jdb has no machine-readable protocol. This wrapper reads its output in a background
thread and waits for a jdb prompt ("> " or "thread-name[N] ") after each command, so
commands and output stay in sync.
"""

import os
import re
import subprocess
import threading
import time

PROMPT = re.compile(r"(^|\n)(> |\S+\[\d+\] )$")
# Events that jdb prints asynchronously. The prompt can be printed in the middle
# of the line ('Breakpoint hit: worker-6[1] "thread=worker-6", ...').
EVENT = re.compile(r"(Breakpoint hit|Exception occurred|Field \(|Step completed|Method (entered|exited)): [^\n]*\n")


class Jdb:
    def __init__(self, address="localhost:5005", jdb="jdb"):
        self.proc = subprocess.Popen(
            [jdb, "-attach", address],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._buf = ""
        self._pos = 0     # read position for command output
        self._evpos = 0   # read position for events, independent of _pos
        self.thread = None
        self.release_after_invoke = True
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        self.banner = self.out(timeout=15)
        if self.proc.poll() is not None:
            raise RuntimeError("jdb exited:\n" + self.banner)

    def _reader(self):
        while True:
            b = os.read(self.proc.stdout.fileno(), 4096)
            if not b:
                break
            with self._lock:
                self._buf += b.decode(errors="replace")

    def out(self, until=PROMPT, timeout=10):
        """Return output since the last call. Stop when `until` matches, or at timeout."""
        until = re.compile(until) if isinstance(until, str) else until
        end = time.time() + timeout
        while True:
            with self._lock:
                new = self._buf[self._pos:]
            if until.search(new) or time.time() > end or self.proc.poll() is not None:
                with self._lock:
                    self._pos += len(new)
                return new
            time.sleep(0.05)

    def cmd(self, c, **kw):
        """Send one jdb command and return its output."""
        self.proc.stdin.write((c + "\n").encode())
        self.proc.stdin.flush()
        return self.out(**kw)

    def p(self, expr):
        """Evaluate `print <expr>` and return only the value (or the jdb error text).

        If the expression invokes a method (also `print obj`, which calls toString()),
        jdb leaves ALL threads suspended afterwards, even after a `stop thread`
        breakpoint. With release_after_invoke (default), every other thread is resumed
        again, so the target keeps serving. Set j.release_after_invoke = False when
        you stopped with a suspend-all breakpoint and want everything to stay stopped.
        """
        o = self.cmd("print " + expr).strip()
        if self.thread and self.release_after_invoke:
            self.release_others()
        line = o.splitlines()[0] if o else ""
        return line.split(" = ", 1)[1] if " = " in line else line

    def release_others(self):
        """Resume every thread except the current one."""
        for tid, _ in self.threads().values():
            if not self.thread or tid != self.thread[0]:
                self.cmd("resume " + tid)

    def field_ref(self, obj, field):
        """Return 'instance of X(id=N)' for obj.field via `dump`, without running code in the target."""
        m = re.search(rf"^\s*{re.escape(field)}: (.*)$", self.cmd("dump " + obj), re.M)
        return m.group(1).strip() if m else None

    def threads(self):
        """{name: (thread_id, status)} for all threads."""
        res = {}
        status = r"(running|sleeping|cond\. waiting|waiting in a monitor|zombie|not started|unknown)( \(at breakpoint\))?"
        for line in self.cmd("threads").splitlines():
            m = re.match(r"\s*\(\S+\)(\w+)\s+(.+?)\s+" + status + r"\s*$", line)
            if m:
                res[m.group(2)] = (m.group(1), m.group(3) + (m.group(4) or ""))
        return res

    def select(self, name):
        """Make the thread with this name current (needed for print, locals, where, cont)."""
        tid = self.threads()[name][0]
        self.cmd("thread " + tid)
        self.thread = (tid, name)
        return tid

    def wait_hit(self, timeout=30):
        """Wait for the next breakpoint, watchpoint, exception or step event; select its thread.

        Returns the event line. Events are found in all jdb output, also output
        already returned by cmd(), so an event printed during `cont` is not lost.
        `stop thread in` does not make the stopped thread current, so the thread is
        selected by the name in the event line and stored in self.thread (`threads`
        stops showing "at breakpoint" once the breakpoint is cleared).
        """
        end = time.time() + timeout
        while True:
            with self._lock:
                m = EVENT.search(self._buf, self._evpos)
                if m:
                    self._evpos = m.end()
                    self._pos = max(self._pos, m.end())
            if m:
                break
            if time.time() > end or self.proc.poll() is not None:
                raise TimeoutError("no event within %ss:\n%s" % (timeout, self._buf[self._evpos:]))
            time.sleep(0.05)
        self.out(timeout=1)  # consume the prompt that follows the event
        line = m.group(0).strip()
        t = re.search(r'"thread=([^"]+)"', line)
        if t:
            self.select(t.group(1))
        return line

    def drain(self):
        """Forget events printed so far, so wait_hit() waits for a new one."""
        with self._lock:
            self._evpos = self._pos = len(self._buf)

    def cont(self):
        """Resume the thread stopped by the last event.

        Plain `cont` answers "Nothing suspended" when only one thread was suspended
        and it is not the current thread, so resume that thread by id.
        """
        if getattr(self, "thread", None):
            o = self.cmd("resume " + self.thread[0])
            self.thread = None
            return o
        return self.cmd("cont")

    def where(self, depth=10):
        return "\n".join(self.cmd("where").splitlines()[:depth])

    def collect(self, exprs, count=10, timeout=30, keep=None):
        """Logging or conditional breakpoint, done in the driver (jdb has neither).

        For each event: evaluate `exprs`, resume the thread, repeat `count` times.
        If `keep(values)` is given and returns True, stop there and leave the thread
        suspended for inspection. Returns the list of value lists.
        """
        rows = []
        for _ in range(count):
            self.wait_hit(timeout)
            vals = [self.p(e) for e in exprs]
            rows.append(vals)
            if keep and keep(vals):
                break
            self.cont()
        return rows

    def cleanup(self):
        """Clear all breakpoints, resume every thread, and detach. The target keeps running."""
        o = self.cmd("clear")
        for line in o.splitlines():
            m = re.search(r"breakpoint (\S+)", line)
            if m:
                self.cmd("clear " + m.group(1))
        self.cmd("resume")
        self.proc.stdin.write(b"exit\n")
        self.proc.stdin.flush()
        self.proc.wait(10)
