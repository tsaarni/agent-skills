"""Drive jdb from a pysess session. Usage: see the java-debugging SKILL.md.

jdb has no machine-readable protocol: a reader thread buffers its output, and each
command waits for the jdb prompt ("> " or "thread-name[N] ").
"""

import os
import re
import subprocess
import threading
import time

PROMPT = re.compile(r"(^|\n)(> |[^\[\n]+\[\d+\] )$")
INLINE_PROMPT = re.compile(r": (> |[^\[\n]+\[\d+\] )")
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

    def p(self, expr, release=None):
        """`print <expr>`, return only the value (or the jdb error text).

        A method call in expr leaves the whole VM suspended; release_others() undoes
        that. Pass release=False or set release_after_invoke = False to keep a
        suspend-all stop in place.
        """
        o = self.cmd("print " + expr).strip()
        if release is None:
            release = self.release_after_invoke
        if self.thread and release:
            self.release_others()
        lines = [l for l in o.splitlines() if not PROMPT.match(l + " ")]
        for line in lines:
            if " = " in line:
                return line.split(" = ", 1)[1]
        return lines[0] if lines else ""

    def release_others(self):
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
        """Wait for the next event, select its thread (stored in self.thread), return the event line.

        Scans all output, also what cmd() already returned, so an event printed
        during `cont` is not lost.
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
        line = m.group(0).strip()
        if not INLINE_PROMPT.search(line):
            self.out(timeout=1)  # consume the prompt that follows the event
        t = re.search(r'"thread=([^"]+)"', line)
        if t:
            self.select(t.group(1))
        return line

    def drain(self):
        """Forget events printed so far, so wait_hit() waits for a new one."""
        with self._lock:
            self._evpos = self._pos = len(self._buf)

    def cont(self):
        """Resume the thread stopped by the last event (by id: plain `cont` can answer "Nothing suspended")."""
        if getattr(self, "thread", None):
            o = self.cmd("resume " + self.thread[0])
            self.thread = None
            return o
        return self.cmd("cont")

    def where(self, depth=10):
        return "\n".join(self.cmd("where").splitlines()[:depth])

    def collect(self, exprs, count=10, timeout=30, keep=None):
        """At each of up to `count` events evaluate `exprs` and resume; stop early, suspended, when `keep(values)` is true."""
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
