"""Drive headless Delve (dlv --headless --api-version=2) via JSON-RPC from a pysess session.

Usage: see go-debugging SKILL.md.
Delve's API v2 speaks newline-delimited JSON-RPC 1.0 over TCP, so responses are
structured JSON rather than scraped terminal prompts.

Checks (run after editing):
    uvx ruff check --line-length 100 --select E,W,F,I,UP,B,SIM dlv_driver.py
    uvx ty check dlv_driver.py
"""

from __future__ import annotations

import builtins
import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable, Generator, Iterable, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self, overload

# Delve api.LoadConfig for readable variable inspection without huge dumps.
DEFAULT_LOAD: dict[str, Any] = {
    "FollowPointers": True,
    "MaxVariableRecurse": 1,
    "MaxStringLen": 256,
    "MaxArrayValues": 32,
    "MaxStructFields": 24,
}

WATCH_TYPES: dict[str, int] = {"read": 1, "write": 2, "rw": 3}


class _Text(str):
    """str subclass whose repr() renders multi-line text cleanly in pysess."""

    def __repr__(self) -> str:
        return str(self)


@dataclass
class Stop:
    """Compact summary of a Delve DebuggerState."""

    reason: str
    goroutine: int
    func: str
    loc: str
    bp_id: int | None = None
    bp_name: str = ""
    exited: bool = False
    raw: dict[str, Any] | None = None

    def __repr__(self) -> str:
        if self.exited:
            status = self.raw.get("exitStatus", 0) if self.raw else 0
            return f"Stop(exited, status={status})"
        bp = ""
        if self.bp_id is not None:
            label = f" {self.bp_name!r}" if self.bp_name else ""
            bp = f"{self.reason}={self.bp_id}{label}, "
        elif self.reason:
            bp = f"{self.reason}, "
        where = f"{self.func} at {self.loc}" if self.func else self.loc
        suffix = f", {where}" if where else ""
        return f"Stop({bp}g={self.goroutine}{suffix})"


def _make_stop(st: dict[str, Any], fallback_reason: str = "stopped") -> Stop:
    """Convert a raw Delve DebuggerState dict into a compact Stop summary."""
    if st.get("exited"):
        return Stop(reason="exited", goroutine=-1, func="", loc="", exited=True, raw=st)

    g: dict[str, Any] = st.get("selectedGoroutine") or {}
    t: dict[str, Any] = st.get("currentThread") or {}
    gid: int = g.get("id", t.get("goroutineID", -1))

    loc_obj: dict[str, Any] = g.get("currentLoc") or {}
    fn: str = (loc_obj.get("function") or {}).get("name") or (
        t.get("function") or {}
    ).get("name", "")
    file_: str = loc_obj.get("file") or t.get("file", "")
    line: int = loc_obj.get("line") or t.get("line", 0)
    loc = f"{file_}:{line}" if file_ else ""

    bp = t.get("breakPoint") or t.get("breakpoint")
    if bp:
        reason = "watchpoint" if bp.get("WatchExpr") else "bp"
        return Stop(
            reason=reason,
            goroutine=gid,
            func=fn,
            loc=loc,
            bp_id=bp.get("id"),
            bp_name=bp.get("name", ""),
            raw=st,
        )

    return Stop(reason=fallback_reason, goroutine=gid, func=fn, loc=loc, raw=st)


def _fmt_var(v: dict[str, Any]) -> str:
    """Format a Delve api.Variable dict into a compact, readable string."""
    # Go reflect.Kind values: chan=18, func=19, interface=20, map=21, ptr=22, slice=23, string=24
    nilable_kinds = {18, 19, 20, 21, 22, 23}

    if v.get("unreadable"):
        return f"<unreadable: {v['unreadable']}>"
    val = v.get("value")
    children: list[dict[str, Any]] = v.get("children") or []
    typ: str = v.get("type", "")
    kind: int = v.get("kind", 0)
    length: int = v.get("len", 0)

    if (kind == 24 or typ == "string") and val is not None:
        s = json.dumps(val)
        if length > len(val):
            return f"{s}...(len={length})"
        return s

    if val != "" and val is not None:
        return str(val)

    if kind == 21 or typ.startswith("map["):
        if not children and v.get("addr", 0) == 0 and length == 0:
            return f"nil ({typ})"
        pairs: list[str] = []
        limit = min(len(children), 32)
        for i in range(0, limit - 1, 2):
            pairs.append(f"{_fmt_var(children[i])}: {_fmt_var(children[i + 1])}")
        suffix = f", ... (len={length})" if length > len(pairs) else ""
        return "{" + ", ".join(pairs) + suffix + "}"

    if children:
        parts: list[str] = []
        for c in children[:16]:
            name = c.get("name", "")
            cv = _fmt_var(c)
            parts.append(f"{name}: {cv}" if name else cv)
        total = max(length, len(children))
        suffix = f", ... (len={total})" if total > 16 else ""
        return "{" + ", ".join(parts) + suffix + "}"

    if kind in nilable_kinds and v.get("addr", 0) == 0:
        return f"nil ({typ})" if typ else "<nil>"
    if kind == 23 or typ.startswith("[]"):
        return f"{typ}{{}}"
    return f"({typ})" if typ else "<nil>"


def _load_cfg(cfg: dict[str, Any] | None = None, depth: int | None = None) -> dict[str, Any]:
    base = dict(cfg or DEFAULT_LOAD)
    if depth is not None:
        base["MaxVariableRecurse"] = depth
    return base


class Dlv:
    """JSON-RPC client for a headless Delve server (--api-version=2)."""

    def __init__(self, address: str = "127.0.0.1:2345", timeout: float = 30.0) -> None:
        host, port = address.rsplit(":", 1)
        self.host = host.strip("[]")
        self.port = int(port)
        self._id = 0
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self._sock.settimeout(None)
        self._rfile = self._sock.makefile("r", encoding="utf-8", errors="replace")
        self.goroutine = -1
        self.frame = 0
        self._cont_fut: Future[Stop] | None = None
        with contextlib.suppress(Exception):
            st = self.rpc("State", {"NonBlocking": True})["State"]
            if not st.get("Running", False):
                self._sync_state(st)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False

    def close(self) -> None:
        try:
            self._rfile.close()
        finally:
            self._sock.close()

    def _check_not_running(self) -> None:
        if self._cont_fut is not None:
            if not self._cont_fut.done():
                raise RuntimeError(
                    "target is running (resumed); call d.halt() or d.wait() first"
                )
            fut = self._cont_fut
            self._cont_fut = None
            fut.result()

    def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        _allow_running: bool = False,
    ) -> Any:
        """Send one JSON-RPC request to RPCServer.<method> and return result."""
        if not _allow_running:
            self._check_not_running()
        self._id += 1
        req_id = self._id
        req = {
            "id": req_id,
            "method": f"RPCServer.{method}",
            "params": [params if params is not None else {}],
        }
        old_to = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            self._sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
            line = self._rfile.readline()
            if not line:
                raise ConnectionError("delve closed connection")
            resp = json.loads(line)
        finally:
            self._sock.settimeout(old_to)
        if resp.get("id") != req_id:
            raise RuntimeError(
                f"dlv {method}: out-of-sync response id {resp.get('id')} != {req_id}"
            )
        if resp.get("error"):
            raise RuntimeError(f"dlv {method}: {resp['error']}")
        return resp.get("result")

    def _drain_pending_reply(self, timeout: float = 2.0) -> None:
        """Drain one pending JSON-RPC reply after interrupting a blocking continue."""
        old_to = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            with contextlib.suppress(Exception):
                line = self._rfile.readline()
                if line:
                    resp = json.loads(line)
                    st = (resp.get("result") or {}).get("State")
                    if st:
                        self._sync_state(st)
        finally:
            self._sock.settimeout(old_to)

    def _scope(self, goroutine: int | None = None, frame: int | None = None) -> dict[str, int]:
        return {
            "GoroutineID": self.goroutine if goroutine is None else goroutine,
            "Frame": self.frame if frame is None else frame,
        }

    def state(self, non_blocking: bool = False) -> Stop:
        """Return current Stop summary."""
        res = self.rpc("State", {"NonBlocking": non_blocking})
        st: dict[str, Any] = res["State"]
        self._sync_state(st)
        return _make_stop(st)

    def _sync_state(self, st: dict[str, Any]) -> None:
        g = st.get("selectedGoroutine") or {}
        t = st.get("currentThread") or {}
        gid = g.get("id")
        if gid is None or gid < 0:
            gid = t.get("goroutineID", -1)
        if gid is not None and gid >= 0:
            self.goroutine = gid
        self.frame = 0

    def _cmd(
        self,
        name: str,
        fallback_reason: str | None = None,
        _allow_running: bool = False,
        **extra: Any,
    ) -> Stop:
        payload: dict[str, Any] = {"name": name}
        payload.update(extra)
        res = self.rpc("Command", payload, _allow_running=_allow_running)
        st: dict[str, Any] = res["State"]
        self._sync_state(st)
        return _make_stop(st, fallback_reason=fallback_reason or name)

    def _halt_raw(self) -> dict[str, Any]:
        """Send halt over a separate connection (requires --accept-multiclient)."""
        s = socket.create_connection((self.host, self.port), timeout=5.0)
        try:
            req = {"id": 1, "method": "RPCServer.Command", "params": [{"name": "halt"}]}
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            with s.makefile("r", encoding="utf-8", errors="replace") as f:
                line = f.readline()
            if not line:
                raise ConnectionError("delve closed connection during halt")
            resp = json.loads(line)
            if resp.get("error"):
                raise RuntimeError(f"dlv halt: {resp['error']}")
            return resp["result"]["State"]
        finally:
            s.close()

    def cont(self, timeout: float | None = None) -> Stop:
        """Continue until next breakpoint, watchpoint, halt, or exit.

        If timeout (seconds) expires first, halts the target cleanly and raises TimeoutError.
        Safe to interrupt with KeyboardInterrupt (e.g. `pysess cancel`): halts Delve and
        drains the pending reply so the connection stays in sync.
        """
        timed_out = threading.Event()
        timer: threading.Timer | None = None
        if timeout is not None:

            def _on_timeout() -> None:
                timed_out.set()
                with contextlib.suppress(Exception):
                    self._halt_raw()

            timer = threading.Timer(timeout, _on_timeout)
            timer.daemon = True
            timer.start()
        try:
            stop = self._cmd("continue", fallback_reason="halted")
        except KeyboardInterrupt:
            with contextlib.suppress(Exception):
                self._halt_raw()
            self._drain_pending_reply()
            raise
        finally:
            if timer is not None:
                timer.cancel()

        if timed_out.is_set() and stop.bp_id is None and not stop.exited:
            raise TimeoutError(
                f"cont timed out after {timeout}s (target halted at {stop.loc})"
            )
        return stop

    def resume(self) -> Future[Stop]:
        """Resume the target in a daemon thread and return a Future[Stop].

        Keeps the pysess session free across turns. Call d.halt() to pause or
        d.wait(timeout=...) to wait for a breakpoint hit.
        """
        self._check_not_running()
        fut: Future[Stop] = Future()
        fut.set_running_or_notify_cancel()
        self._cont_fut = fut

        def _runner() -> None:
            try:
                stop = self._cmd("continue", fallback_reason="halted", _allow_running=True)
            except BaseException as exc:
                fut.set_exception(exc)
            else:
                fut.set_result(stop)

        threading.Thread(target=_runner, name="dlv.resume", daemon=True).start()
        return fut

    def wait(self, timeout: float | None = None) -> Stop:
        """Wait for a d.resume() run to stop; if timeout expires, halts and raises TimeoutError."""
        if self._cont_fut is None:
            return self.state()
        fut = self._cont_fut
        try:
            stop = fut.result(timeout=timeout)
        except FutureTimeoutError:
            self.halt()
            raise TimeoutError(
                f"wait timed out after {timeout}s (target halted)"
            ) from None
        self._cont_fut = None
        return stop

    @contextlib.contextmanager
    def running(self) -> Generator[Future[Stop]]:
        """Keep the target running during the with-block, then halt and join cleanly."""
        fut = self.resume()
        try:
            yield fut
        finally:
            if not fut.done():
                self.halt()
            else:
                self._cont_fut = None
                fut.result()

    def next(self) -> Stop:
        return self._cmd("next", fallback_reason="step")

    def step(self) -> Stop:
        return self._cmd("step", fallback_reason="step")

    def stepout(self) -> Stop:
        return self._cmd("stepOut", fallback_reason="step")

    def halt(self) -> Stop:
        """Pause a running target (idempotent if already paused; requires --accept-multiclient)."""
        if self._cont_fut is not None:
            fut = self._cont_fut
            self._cont_fut = None
            if not fut.done():
                self._halt_raw()
            return fut.result(timeout=5.0)
        st: dict[str, Any] = self.rpc("State", {"NonBlocking": True})["State"]
        if not st.get("Running", False):
            self._sync_state(st)
            return _make_stop(st, fallback_reason="halted")
        st = self._halt_raw()
        self._sync_state(st)
        return _make_stop(st, fallback_reason="halted")

    def find_loc(self, loc: str) -> builtins.list[dict[str, Any]]:
        """Resolve a Delve location spec (e.g. 'main.visit' or 'file.go:42')."""
        res = self.rpc("FindLocation", {"Scope": self._scope(), "Loc": loc})
        return res.get("Locations") or []

    @overload
    def bp(
        self,
        loc: str | int,
        cond: str | None = None,
        trace: bool = False,
        hit_cond: str | None = None,
        name: str | None = None,
        vars: Iterable[str] | None = None,
        goroutine: bool = False,
        all_locs: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    def bp(
        self,
        loc: str | int,
        cond: str | None = None,
        trace: bool = False,
        hit_cond: str | None = None,
        name: str | None = None,
        vars: Iterable[str] | None = None,
        goroutine: bool = False,
        *,
        all_locs: Literal[True],
    ) -> builtins.list[dict[str, Any]]: ...

    @overload
    def bp(
        self,
        loc: str | int,
        cond: str | None = None,
        trace: bool = False,
        hit_cond: str | None = None,
        name: str | None = None,
        vars: Iterable[str] | None = None,
        goroutine: bool = False,
        all_locs: bool = False,
    ) -> dict[str, Any] | builtins.list[dict[str, Any]]: ...

    def bp(
        self,
        loc: str | int,
        cond: str | None = None,
        trace: bool = False,
        hit_cond: str | None = None,
        name: str | None = None,
        vars: Iterable[str] | None = None,
        goroutine: bool = False,
        all_locs: bool = False,
    ) -> dict[str, Any] | builtins.list[dict[str, Any]]:
        """Set a breakpoint (or non-stopping tracepoint when trace=True).

        loc: function name ('main.visit'), 'file.go:line', or integer PC.
        cond: Go boolean expression (e.g. 'k == "k5"').
        all_locs: if loc resolves to multiple PCs, set breakpoints on all of them.
        """
        spec: dict[str, Any] = {"tracepoint": trace, "goroutine": goroutine}
        if cond:
            spec["Cond"] = cond
        if hit_cond:
            spec["HitCond"] = hit_cond
        if name:
            spec["name"] = name
        if vars:
            spec["variables"] = list(vars)

        if isinstance(loc, int):
            spec["addr"] = loc
            res = self.rpc("CreateBreakpoint", {"Breakpoint": spec})
            return res["Breakpoint"]

        locs = self.find_loc(loc)
        if not locs:
            raise ValueError(f"location not found: {loc}")
        if len(locs) > 1:
            if not all_locs:
                cands = ", ".join(
                    f"{(item.get('function') or {}).get('name', '?')} "
                    f"at {item.get('file', '?')}:{item.get('line', 0)}"
                    for item in locs[:5]
                )
                more = f" (+{len(locs) - 5} more)" if len(locs) > 5 else ""
                raise ValueError(
                    f"ambiguous location {loc!r} matched {len(locs)} locations: "
                    f"{cands}{more}; use a more specific spec or pass all_locs=True"
                )
            bps: list[dict[str, Any]] = []
            for idx, item in enumerate(locs):
                s = dict(spec)
                s["addr"] = item["pc"]
                if name:
                    s["name"] = f"{name}{idx}"
                bps.append(self.rpc("CreateBreakpoint", {"Breakpoint": s})["Breakpoint"])
            return bps

        spec["addr"] = locs[0]["pc"]
        res = self.rpc("CreateBreakpoint", {"Breakpoint": spec})
        return res["Breakpoint"]

    def tracepoint(
        self,
        loc: str | int,
        vars: Iterable[str] = (),
        cond: str | None = None,
        name: str | None = None,
        goroutine: bool = False,
        all_locs: bool = False,
    ) -> dict[str, Any] | builtins.list[dict[str, Any]]:
        """Set a non-stopping tracepoint that logs `vars` to Delve's output on each hit."""
        return self.bp(
            loc,
            cond=cond,
            trace=True,
            name=name,
            vars=list(vars),
            goroutine=goroutine,
            all_locs=all_locs,
        )

    def watch(self, expr: str, wtype: str = "write") -> dict[str, Any]:
        """Set a hardware watchpoint on expr ('read', 'write', or 'rw')."""
        if wtype not in WATCH_TYPES:
            raise ValueError(f"wtype must be one of {list(WATCH_TYPES)}")
        res = self.rpc(
            "CreateWatchpoint",
            {
                "Scope": self._scope(),
                "Expr": expr,
                "Type": WATCH_TYPES[wtype],
            },
        )
        return res.get("Breakpoint", res) if isinstance(res, dict) else res

    @contextlib.contextmanager
    def temp_bp(
        self,
        loc: str | int,
        cond: str | None = None,
        hit_cond: str | None = None,
        name: str | None = None,
    ) -> Generator[dict[str, Any]]:
        """Context manager that sets a breakpoint and always clears it on exit."""
        bp = self.bp(loc, cond=cond, hit_cond=hit_cond, name=name)
        try:
            yield bp
        finally:
            with contextlib.suppress(Exception):
                self.clear(bp["id"])

    @contextlib.contextmanager
    def temp_watch(self, expr: str, wtype: str = "write") -> Generator[dict[str, Any]]:
        """Context manager that sets a watchpoint and always clears it on exit."""
        wp = self.watch(expr, wtype=wtype)
        try:
            yield wp
        finally:
            with contextlib.suppress(Exception):
                self.clear(wp["id"])

    def bps(self) -> builtins.list[dict[str, Any]]:
        """List all user breakpoints (id, name, file:line, cond, watch, hits, trace)."""
        res = self.rpc("ListBreakpoints", {"All": False})
        out: list[dict[str, Any]] = []
        for b in res.get("Breakpoints") or []:
            if b.get("id", 0) < 0:
                continue
            out.append(
                {
                    "id": b["id"],
                    "name": b.get("name", ""),
                    "loc": f"{b['file']}:{b.get('line', 0)}" if b.get("file") else "",
                    "func": b.get("functionName", ""),
                    "cond": b.get("Cond", ""),
                    "watch": b.get("WatchExpr", ""),
                    "hits": b.get("totalHitCount", 0),
                    "trace": b.get("tracepoint", False),
                }
            )
        return out

    def clear(self, bp_id: int | str) -> dict[str, Any]:
        """Clear breakpoint by numeric ID or name."""
        params: dict[str, Any] = {"Id": bp_id} if isinstance(bp_id, int) else {"Name": bp_id}
        return self.rpc("ClearBreakpoint", params)["Breakpoint"]

    def clear_all(self) -> None:
        """Clear all user breakpoints."""
        for b in self.bps():
            self.clear(b["id"])

    def eval(
        self,
        expr: str,
        frame: int | None = None,
        goroutine: int | None = None,
        depth: int | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> str:
        """Evaluate a Go expression and return a formatted string."""
        res = self.rpc(
            "Eval",
            {
                "Scope": self._scope(goroutine=goroutine, frame=frame),
                "Expr": expr,
                "Cfg": _load_cfg(cfg, depth),
            },
        )
        return _fmt_var(res["Variable"])

    def eval_raw(
        self,
        expr: str,
        frame: int | None = None,
        goroutine: int | None = None,
        depth: int | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a Go expression and return the raw api.Variable dict."""
        return self.rpc(
            "Eval",
            {
                "Scope": self._scope(goroutine=goroutine, frame=frame),
                "Expr": expr,
                "Cfg": _load_cfg(cfg, depth),
            },
        )["Variable"]

    def set(
        self,
        symbol: str,
        value: Any,
        frame: int | None = None,
        goroutine: int | None = None,
    ) -> None:
        """Assign a new value to a variable (e.g. d.set('n', '10'))."""
        self.rpc(
            "Set",
            {
                "Scope": self._scope(goroutine=goroutine, frame=frame),
                "Symbol": symbol,
                "Value": str(value),
            },
        )

    def call(
        self,
        expr: str,
        depth: int | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> str | builtins.list[str] | None:
        """Call a Go function in the target (e.g. d.call('fib(5)')) and return result(s)."""
        res = self.rpc(
            "Command",
            {
                "name": "call",
                "goroutineID": self.goroutine,
                "expr": expr,
                "unsafe": False,
                "returnInfoLoadConfig": _load_cfg(cfg, depth),
            },
        )
        st: dict[str, Any] = res["State"]
        self._sync_state(st)
        t = st.get("currentThread") or {}
        ret = t.get("ReturnValues") or t.get("returnValues") or []
        vals = [_fmt_var(v) for v in ret]
        if not vals:
            return None
        return vals[0] if len(vals) == 1 else vals

    def locals(
        self,
        frame: int | None = None,
        goroutine: int | None = None,
        depth: int | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Return {name: value} for local variables in the current (or given) frame."""
        res = self.rpc(
            "ListLocalVars",
            {
                "Scope": self._scope(goroutine=goroutine, frame=frame),
                "Cfg": _load_cfg(cfg, depth),
            },
        )
        return {v["name"]: _fmt_var(v) for v in (res.get("Variables") or [])}

    def args(
        self,
        frame: int | None = None,
        goroutine: int | None = None,
        depth: int | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Return {name: value} for function arguments in the current (or given) frame."""
        res = self.rpc(
            "ListFunctionArgs",
            {
                "Scope": self._scope(goroutine=goroutine, frame=frame),
                "Cfg": _load_cfg(cfg, depth),
            },
        )
        return {v["name"]: _fmt_var(v) for v in (res.get("Args") or [])}

    def stack(self, depth: int = 10, goroutine: int | None = None) -> _Text:
        """Return formatted stack frames for the current (or specified) goroutine."""
        gid = self.goroutine if goroutine is None else goroutine
        res = self.rpc(
            "Stacktrace",
            {
                "Id": gid,
                "Depth": depth,
                "Full": False,
            },
        )
        return self._format_stack(res.get("Locations") or [], gid)

    def _format_source(
        self, file_path: str | None, line: int, context: int = 4
    ) -> _Text:
        if not file_path or line <= 0:
            return _Text("")
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError:
            return _Text(f"<source unavailable: {file_path}:{line}>")
        start = max(1, line - context)
        end = min(len(all_lines), line + context)
        width = len(str(end))
        out: list[str] = []
        for lineno in range(start, end + 1):
            marker = ">" if lineno == line else " "
            out.append(f"{marker} {lineno:>{width}}: {all_lines[lineno - 1].rstrip()}")
        return _Text("\n".join(out))

    def _format_stack(self, locs: Sequence[dict[str, Any]], gid: int) -> _Text:
        lines: list[str] = []
        for i, f in enumerate(locs):
            fn = (f.get("function") or {}).get("name", "?")
            marker = "*" if (gid == self.goroutine and i == self.frame) else " "
            lines.append(f"{marker}#{i} {fn} at {f.get('file', '?')}:{f.get('line', 0)}")
        return _Text("\n".join(lines))

    def list(
        self,
        context: int = 4,
        frame: int | None = None,
        goroutine: int | None = None,
    ) -> _Text:
        """Show source lines around the current (or given) frame with '>' on the current line."""
        gid = self.goroutine if goroutine is None else goroutine
        frm = self.frame if frame is None else frame
        res = self.rpc("Stacktrace", {"Id": gid, "Depth": frm + 1, "Full": False})
        locs = res.get("Locations") or []
        if frm >= len(locs):
            return _Text("")
        loc = locs[frm]
        return self._format_source(loc.get("file") or None, loc.get("line", 0), context=context)

    def where(
        self,
        stack: int = 5,
        depth: int | None = None,
        source: int | None = 1,
    ) -> _Text:
        """One-shot summary of goroutine, frame, source context, args, locals, and stack."""
        parts = [f"goroutine {self.goroutine}, frame #{self.frame}"]
        need_locs = (source is not None and source >= 0) or stack > 0
        locs: list[dict[str, Any]] = []
        if need_locs:
            req_depth = max(stack, self.frame + 1)
            res = self.rpc(
                "Stacktrace", {"Id": self.goroutine, "Depth": req_depth, "Full": False}
            )
            locs = res.get("Locations") or []
        if source is not None and source >= 0 and self.frame < len(locs):
            loc = locs[self.frame]
            src = self._format_source(
                loc.get("file") or None, loc.get("line", 0), context=source
            )
            if src and not src.startswith("<source unavailable"):
                parts.append(str(src))
        a = self.args(depth=depth)
        if a:
            parts.append("args:   " + ", ".join(f"{k}={v}" for k, v in a.items()))
        loc_vars = self.locals(depth=depth)
        if loc_vars:
            parts.append("locals: " + ", ".join(f"{k}={v}" for k, v in loc_vars.items()))
        if stack > 0 and locs:
            parts.append(self._format_stack(locs, self.goroutine))
        return _Text("\n".join(parts))

    def switch_frame(self, frame: int) -> None:
        """Select stack frame index (0 = innermost)."""
        if frame < 0:
            raise ValueError("frame must be >= 0")
        self.frame = frame

    def up(self, n: int = 1) -> None:
        """Move up n caller frames."""
        self.frame += n

    def down(self, n: int = 1) -> None:
        """Move down n callee frames (clamped at 0)."""
        self.frame = max(0, self.frame - n)

    def goroutines(
        self, match: str | None = None, limit: int = 50
    ) -> builtins.list[dict[str, Any]]:
        """List goroutines as compact dicts; filter by substring in user func/file.

        limit: max entries returned (default 50; pass 0 for all).
        """
        count = limit if (match is None and limit > 0) else 0
        res = self.rpc("ListGoroutines", {"Start": 0, "Count": count})
        out: list[dict[str, Any]] = []
        for g in res.get("Goroutines") or []:
            uloc = g.get("userCurrentLoc") or {}
            ufn = (uloc.get("function") or {}).get("name", "")
            ufile = f"{uloc.get('file', '')}:{uloc.get('line', 0)}"
            if match is not None and match not in ufn and match not in ufile:
                continue
            item: dict[str, Any] = {"id": g["id"], "func": ufn, "loc": ufile}
            if g.get("waitReason"):
                item["wait_reason"] = g["waitReason"]
            if g.get("labels"):
                item["labels"] = g["labels"]
            out.append(item)
            if limit > 0 and len(out) >= limit:
                break
        return out

    def switch_goroutine(self, gid: int) -> None:
        """Select goroutine gid as current (resets frame to 0)."""
        self.rpc("Command", {"name": "switchGoroutine", "goroutineID": gid})
        self.goroutine = gid
        self.frame = 0

    def collect(
        self,
        loc: str | int,
        exprs: Sequence[str],
        count: int = 10,
        cond: str | None = None,
        until: Callable[[dict[str, str]], bool] | None = None,
        keep: Callable[[dict[str, str]], bool] | None = None,
        timeout: float | None = None,
    ) -> builtins.list[dict[str, str]]:
        """Hit a temporary breakpoint at loc up to `count` times, returning [{expr: val}, ...].

        Stops early (leaving target paused at the hit) when until(row) is True.
        `timeout` is a total deadline (seconds) across all hits; if it expires after at least
        one hit, returns the partial rows collected so far instead of discarding them.
        Always clears the temporary breakpoint afterwards.
        """
        predicate = until if until is not None else keep
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        rows: list[dict[str, str]] = []
        with self.temp_bp(loc, cond=cond) as bp:
            for _ in range(count):
                rem: float | None = None
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        if rows:
                            break
                        raise TimeoutError(
                            f"collect timed out after {timeout}s with 0 hits"
                        )
                try:
                    stop = self.cont(timeout=rem)
                except TimeoutError:
                    if rows:
                        break
                    raise
                if stop.exited or stop.bp_id != bp["id"]:
                    break
                row = {e: self.eval(e) for e in exprs}
                rows.append(row)
                if predicate and predicate(row):
                    break
        return rows

    def detach(self, kill: bool = False) -> None:
        """Halt if running, clear user breakpoints, and detach (default: keep target running)."""
        if self._cont_fut is not None and not self._cont_fut.done():
            with contextlib.suppress(Exception):
                self.halt()
        try:
            self.clear_all()
        except Exception:
            with contextlib.suppress(Exception):
                self._halt_raw()
                self.clear_all()
        try:
            self.rpc("Detach", {"Kill": kill})
        finally:
            self.close()
