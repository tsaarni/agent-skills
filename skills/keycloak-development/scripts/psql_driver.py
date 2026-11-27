"""PostgreSQL from a pysess session through one long-running psql. Usage: see
keycloak-development references/database.md.

api() prints the API: signatures and docstring summaries; help(name) shows the details.

The driver starts the psql command you give it (local, kubectl exec -i, docker exec -i, ssh ...)
and writes SQL and psql meta-commands to its stdin, so session state (transactions, SET, temp
tables, \\c, psql variables) is kept between calls and nothing needs quoting for a remote shell.

Stdlib only. The module is loaded into the session's globals, so variables the caller defines
would shadow plain module imports: every import is aliased.

Checks (run after editing):
    uvx ruff format --line-length 100 psql_driver.py
    uvx ruff check --line-length 100 --select E,W,F,I,UP,B,SIM psql_driver.py
    uvx ty check psql_driver.py
"""

from __future__ import annotations

import datetime as _datetime
import decimal as _decimal
import inspect as _inspect
import json as _json
import os as _os
import re as _re
import secrets as _secrets
import shlex as _shlex
import subprocess as _subprocess
import threading as _threading
import time as _time
import weakref as _weakref
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_PREVIEW = 300
_MIN_PSQL = 130000  # \warn and \g (options) need psql 13
_SEVERITY = _re.compile(r"^(?:psql:[^:]*:\d+: )?(ERROR|FATAL|PANIC|WARNING|NOTICE|INFO|LOG|DEBUG)")
_CONTINUATION = _re.compile(r"^(?:\s|LINE \d+:|DETAIL:|HINT:|CONTEXT:|QUERY:|STATEMENT:|LOCATION:)")
_VAR_NAME = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONNECT = _re.compile(r"(?m)^\s*\\c(?:onnect)?\b")
_COMMENTS = _re.compile(r"--[^\n]*|/\*.*?\*/", _re.S)
# \pset options query() changes for \gdesc and restores; separators before their _zero flags.
_PSET_KEYS = (
    "format",
    "expanded",
    "fieldsep",
    "fieldsep_zero",
    "recordsep",
    "recordsep_zero",
    "footer",
    "tuples_only",
    "title",
    "null",
)


class SqlError(Exception):
    """Raised by query() and Output.expect(): the SQL or psql error message.

    Attributes: sqlstate (of the last statement, None if unknown), output (the Output).
    """

    def __init__(self, message: str, sqlstate: str | None, output: Output) -> None:
        super().__init__(message)
        self.sqlstate, self.output = sqlstate, output


class PsqlExited(RuntimeError):
    """Raised when the psql process has exited; Psql.restart() starts it again."""


def _messages(err: str) -> tuple[list[str], list[str]]:
    """Split psql's stderr into (errors, notices); each message keeps its LINE, DETAIL,
    HINT and STATEMENT lines."""
    msgs: list[tuple[str, list[str]]] = []
    for line in err.splitlines():
        m = _SEVERITY.match(line)
        if m:
            msgs.append((m.group(1), [line]))
        elif msgs and (
            _CONTINUATION.match(line)
            # later lines of a multi-line STATEMENT
            or any(x.startswith("STATEMENT:") for x in msgs[-1][1])
        ):
            msgs[-1][1].append(line)
        elif line.strip():
            msgs.append(("psql", [line]))  # psql's own errors: invalid command, \d not found
    errors = ["\n".join(lines) for sev, lines in msgs if sev in ("ERROR", "FATAL", "PANIC", "psql")]
    notices = [
        "\n".join(lines) for sev, lines in msgs if sev not in ("ERROR", "FATAL", "PANIC", "psql")
    ]
    return errors, notices


class Output:
    """What psql printed for run(): never raised on SQL errors, like psql itself.

    Attributes: script, out (stdout), err (stderr), errors and notices (messages from stderr,
    each with its LINE/DETAIL/HINT/STATEMENT lines), sqlstate (of the last statement),
    timed_out. repr: one line; print(o): stdout, then stderr.
    """

    def __init__(
        self, script: str, out: str, err: str, sqlstate: str | None, timed_out: bool = False
    ) -> None:
        self.script, self.out, self.err, self.sqlstate = script, out, err, sqlstate
        self.timed_out = timed_out
        self.errors, self.notices = _messages(err)

    @property
    def ok(self) -> bool:
        """No errors in stderr."""
        return not self.errors

    def expect(self) -> Output:
        """Return self if ok, else raise SqlError with the first error."""
        if not self.ok:
            raise SqlError(self.errors[0], self.sqlstate, self)
        return self

    def __repr__(self) -> str:
        first = " ".join(self.errors[0].split()) if self.errors else " ".join(self.out.split())
        more = f" (+{len(self.errors) - 1} errors)" if len(self.errors) > 1 else ""
        timed_out = " TIMED OUT" if self.timed_out else ""
        first = first if len(first) <= _PREVIEW else first[:_PREVIEW] + "..."
        state = "ERROR" if self.errors else "ok"
        return f"<Output {state}{timed_out}{more} {len(self.out.splitlines())} lines: {first}>"

    def __str__(self) -> str:
        return self.out + self.err


class Rows(list):
    """Rows of query(): a list of dicts, column name -> Python value, or None for NULL.

    Values are converted by column type: integer types and oid -> int, numeric -> Decimal,
    real/double -> float, boolean -> bool, json/jsonb -> parsed, timestamp -> datetime,
    date -> date, bytea -> bytes. Others (text, uuid, arrays, intervals ...), and values that
    do not parse (e.g. 'infinity'), stay the text psql prints. Duplicate column names keep the
    last value: alias them. Attributes: columns, types (PostgreSQL type names), status
    (command tag such as "UPDATE 3", "" for SELECT), rowcount, tuples.
    repr: one line; print(rows): a table.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        columns: list[str],
        types: list[str],
        status: str,
        rowcount: int,
    ) -> None:
        super().__init__(rows)
        self.columns, self.types, self.status, self.rowcount = columns, types, status, rowcount

    @property
    def tuples(self) -> list[tuple[Any, ...]]:
        """Rows as tuples in column order."""
        return [tuple(r.values()) for r in self]

    def __repr__(self) -> str:
        if not self and self.status:
            return f"<Rows {self.status}>"
        head = f"<Rows {len(self)} {self.columns}" + (f" {self.status}" if self.status else "")
        if not self:
            return head + ">"
        body = ", ".join(repr(r) for r in self[:3])
        body = body if len(body) <= _PREVIEW else body[:_PREVIEW] + "..."
        return f"{head}: {body}{', ...' if len(self) > 3 else ''}>"

    def __str__(self) -> str:
        if not self.columns:
            return self.status or "(0 rows)"

        def cell(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, dict | list):
                v = _json.dumps(v)
            return str(v).replace("\n", "\\n")

        cells = [[cell(v) for v in r.values()] for r in self]
        columns = list(self[0]) if self else self.columns
        widths = [max([len(c), *(len(r[i]) for r in cells)]) for i, c in enumerate(columns)]
        lines = [" | ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))]
        lines.append("-+-".join("-" * w for w in widths))
        lines += [" | ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in cells]
        lines.append(f"({len(self)} row{'' if len(self) == 1 else 's'})")
        return "\n".join(lines)


def _timestamp(text: str) -> _datetime.datetime:
    # psql prints "+00" or "+05:30" offsets (DateStyle ISO); fromisoformat needs "+HH:MM".
    return _datetime.datetime.fromisoformat(_re.sub(r"([+-]\d\d)$", r"\1:00", text))


def _bytea(text: str) -> bytes:
    if not text.startswith("\\x"):
        raise ValueError("bytea_output is not hex")
    return bytes.fromhex(text[2:])


_CONVERT: dict[str, Callable[[str], Any]] = {
    "smallint": int,
    "integer": int,
    "bigint": int,
    "oid": int,
    "numeric": _decimal.Decimal,
    "real": float,
    "double precision": float,
    "boolean": {"t": True, "f": False}.__getitem__,
    "json": _json.loads,
    "jsonb": _json.loads,
    "timestamp with time zone": _timestamp,
    "timestamp without time zone": _timestamp,
    "date": _datetime.date.fromisoformat,
    "bytea": _bytea,
}


def _converter(type_name: str) -> Callable[[str], Any] | None:
    """Converter for a format_type() name; precision is ignored: numeric(10,2), timestamp(3)."""
    return _CONVERT.get(_re.sub(r"\(\d+(?:,\d+)?\)", "", type_name))


def _convert(value: str, conv: Callable[[str], Any] | None) -> Any:
    if conv is None:
        return value
    try:
        return conv(value)
    except (ValueError, KeyError, ArithmeticError):
        return value  # e.g. 'infinity', 'NaN' dates, a non-ISO DateStyle


def _terminated(script: str) -> str:
    """script, plus ";" if it ends with a statement without one (psql runs that at the end of a
    file; here the end marker would follow it into the query buffer). Not added otherwise: an
    empty query resets :SQLSTATE and :ERROR."""
    last = _COMMENTS.sub("", script).rstrip()
    last_line = last.rsplit("\n", 1)[-1].lstrip()
    done = not last or last.endswith(";") or last_line.startswith("\\")
    return script + ("\n" if done else "\n;\n")


def _quote_value(value: Any) -> str:
    """A psql meta-command argument that \\set stores as exactly this text."""
    if value is None:
        raise ValueError("psql variables cannot be NULL: write NULL in the SQL instead")
    text = str(value).lower() if isinstance(value, bool) else str(value)
    escaped = text.replace("\\", "\\\\").replace("'", "''")
    return "'" + escaped.replace("\n", "\\n").replace("\r", "\\r") + "'"


def _end_input(stdin: Any) -> None:
    try:
        stdin.write(b"\\q\n")
        stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


class _Reader:
    """Collects a pipe's bytes in a daemon thread."""

    def __init__(self, pipe: Any, cond: _threading.Condition) -> None:
        self.buf = bytearray()
        self.eof = False
        self._pipe, self._cond = pipe, cond
        _threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        fd = self._pipe.fileno()
        while True:
            try:
                chunk = _os.read(fd, 65536)
            except OSError:
                chunk = b""
            with self._cond:
                if chunk:
                    self.buf += chunk
                else:
                    self.eof = True
                self._cond.notify_all()
            if not chunk:
                return


class Psql:
    """A psql process kept open: run() takes anything psql accepts, query() returns rows.

    cmd: the command that starts psql, as you would type it (list, or a string split like a
    shell does): ["psql", "-h", "localhost", "-U", "app"], ["kubectl", "exec", "-i",
    "deploy/postgres", "--", "psql", "-U", "app"], ["docker", "exec", "-i", "db", "psql", ...],
    ["ssh", "host", "psql", ...]. stdin must reach psql (-i for kubectl/docker exec), and psql
    cannot prompt for a password: use env (PGPASSWORD), ~/.pgpass, or the server's local trust.
    env: added to this process's environment for cmd. timeout: default seconds per call; on
    timeout the statement is canceled (pg_cancel_backend from a second psql started with cmd).

    Attributes: cmd, env, timeout, pid (server backend pid), client_version, server_version.
    """

    def __init__(
        self,
        cmd: str | Sequence[str],
        env: Mapping[str, str] | None = None,
        timeout: float = 30,
    ) -> None:
        self.cmd = _shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        self.env, self.timeout = dict(env or {}), timeout
        self.pid: int | None = None
        self.client_version = self.server_version = 0
        self._lock = _threading.RLock()
        self._marker = "PSQLDRV" + _secrets.token_hex(6)
        self._null = "NULL" + _secrets.token_hex(6)
        self._calls = 0
        self._pset: dict[str, str] | None = None
        self._start()

    def __repr__(self) -> str:
        cmd = " ".join(_shlex.quote(a) for a in self.cmd)
        alive = self._proc.poll() is None
        state = f"pid={self.pid}, server={self.server_version}" if alive else "exited"
        return f"Psql({cmd!r}, {state})"

    def _start(self) -> None:
        self._proc = _subprocess.Popen(
            self.cmd,
            stdin=_subprocess.PIPE,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            env={**_os.environ, **self.env} if self.env else None,
            bufsize=0,
        )
        self._cond = _threading.Condition()
        self._out = _Reader(self._proc.stdout, self._cond)
        self._err = _Reader(self._proc.stderr, self._cond)
        # A dropped Psql ends psql: subprocess keeps a running child's Popen (and its stdin)
        # alive, so close stdin explicitly; psql exits at end of input.
        _weakref.finalize(self, _end_input, self._proc.stdin)
        try:
            self._setup()
        except BaseException:
            self.close()  # a failed start must not leave psql (or kubectl exec) running
            raise

    def _setup(self) -> None:
        # Settings the driver relies on (psqlrc may change them), without psql's confirmations.
        setup = (
            "\\set QUIET on\n\\pset pager off\n\\encoding UTF8\n"
            "\\set ON_ERROR_STOP off\n\\set ECHO errors\n\\set QUIET off\n"
            f"\\echo {self._marker}-VERSION :VERSION_NUM :SERVER_VERSION_NUM\n"
        )
        out = self._call(setup, self.timeout)
        m = _re.search(rf"{self._marker}-VERSION (\d+) (\d+)", out.out)
        if not m:
            raise PsqlExited(f"unexpected psql startup output: {out.out!r} {out.err!r}")
        self.client_version, self.server_version = int(m.group(1)), int(m.group(2))
        if self.client_version < _MIN_PSQL:
            raise RuntimeError(f"psql {self.client_version} is too old, needs >= {_MIN_PSQL}")
        self._refresh_pid()

    def _refresh_pid(self) -> None:
        rows = self.query("select pg_backend_pid() as pid")
        self.pid = int(rows[0]["pid"] or 0)

    def _exited(self, out: str, err: str) -> PsqlExited:
        code = self._proc.wait()
        hint = ""
        if not out and not err and self._calls <= 1:
            hint = " (no output: is stdin connected? kubectl/docker exec need -i)"
        elif code == 3:
            hint = " (an error with ON_ERROR_STOP on: keep it off in this driver)"
        again = "; restart() starts it again" if self.pid else ""
        tail = "\n".join(err.splitlines()[-10:])
        return PsqlExited(f"psql exited with code {code}{hint}{again}. stderr:\n{tail}")

    def _call(self, script: str, timeout: float) -> Output:
        """Write script and an end marker; return what psql printed until the marker."""
        with self._lock:
            self._calls += 1
            end = f"{self._marker}-END-{self._calls}"
            tail = f"\\echo {end} :ERROR :SQLSTATE\n\\warn {end}\n"
            out_start, err_start = len(self._out.buf), len(self._err.buf)
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write((script + tail).encode())
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # reported below once the readers see EOF
            done = self._wait(end, out_start, err_start, timeout)
            timed_out = False
            if not done and self._proc.poll() is None:
                timed_out = True
                self.cancel()
                done = self._wait(end, out_start, err_start, 5)
            out = self._out.buf[out_start:].decode("utf-8", "replace")
            err = self._err.buf[err_start:].decode("utf-8", "replace")
            if not done:
                if self._proc.poll() is not None:
                    raise self._exited(out, err)
                self.restart()
                raise TimeoutError(
                    f"psql did not answer within {timeout}s, not even after cancel (unterminated "
                    "quote or COPY FROM STDIN without \\.?). Restarted psql: session state is "
                    f"lost. Output so far:\n{out[-2000:]}{err[-2000:]}"
                )
            out, _, status = out.partition(end)
            err = err.partition(end)[0]
            parts = status.split()
            sqlstate = parts[1] if len(parts) > 1 and not parts[1].startswith(":") else None
            if timed_out:
                err += f"psql_driver: timed out after {timeout}s, canceled the statement\n"
            return Output(script, out, err, sqlstate, timed_out)

    def _wait(self, end: str, out_start: int, err_start: int, timeout: float) -> bool:
        marker = end.encode()
        deadline = _time.monotonic() + timeout
        with self._cond:
            while True:
                if marker in self._out.buf[out_start:] and marker in self._err.buf[err_start:]:
                    return True
                if self._out.eof and self._err.eof:
                    return False
                left = deadline - _time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(min(left, 0.5))

    def _with_vars(self, script: str, variables: dict[str, Any]) -> str:
        for name in variables:
            if not _VAR_NAME.match(name):
                raise ValueError(f"not a psql variable name: {name!r}")
        sets = "".join(f"\\set {k} {_quote_value(v)}\n" for k, v in variables.items())
        unsets = "".join(f"\\unset {k}\n" for k in variables)
        return f"{sets}{_terminated(script)}{unsets}"

    def run(self, script: str, timeout: float | None = None, **variables: Any) -> Output:
        """Run anything psql accepts (SQL, several statements, \\d, \\x, COPY ...); return Output.

        variables: psql variables for this call, used in the script as :'name' (SQL literal),
        :"name" (identifier) or :name (as is), so values need no quoting.
        """
        out = self._call(self._with_vars(script, variables), timeout or self.timeout)
        self._pset = None  # the script may have changed \pset options
        if _CONNECT.search(script) and self._proc.poll() is None:
            self._refresh_pid()
        return out

    def _psets(self) -> dict[str, str]:
        """Current values of _PSET_KEYS, as \\pset prints them (reusable as its arguments)."""
        if self._pset is None:
            out = self._call("\\pset\n", self.timeout)
            found = dict(_re.findall(r"(?m)^(\w+) +(.*)$", out.out))
            self._pset = {k: found.get(k, "") for k in _PSET_KEYS}
        return self._pset

    def _table(self, text: str) -> list[list[tuple[str, str | None]]] | None:
        """The result table printed with the driver's format (expanded, NUL separated, titled):
        rows of (column, value) pairs; None if text has no table."""
        title = f"{self._marker}\0"
        at = text.rfind(title)
        if at < 0 or (at > 0 and text[at - 1] != "\n"):
            return None
        parts = text[at + len(title) :].split("\0")
        parts.pop()  # after the last record: the command tag or nothing
        rows: list[list[tuple[str, str | None]]] = []
        i = 0
        while i < len(parts):
            if parts[i] == "":  # expanded mode: an empty record before each row
                rows.append([])
                i += 1
                continue
            value = parts[i + 1] if i + 1 < len(parts) else ""
            rows[-1].append((parts[i], None if value == self._null else value))
            i += 2
        return rows

    def query(self, sql: str, timeout: float | None = None, **variables: Any) -> Rows:
        """Run one SQL statement and return its rows as Python values; raise SqlError on errors.

        variables: as in run(), e.g. query("select * from t where name = :'n'", n="x").
        Several statements all run; the rows are those of the last one. Cast to ::text in SQL
        to get a column as psql prints it.
        """
        body = sql.rstrip().rstrip(";").rstrip()
        if not _COMMENTS.sub("", body).strip():
            # \g with an empty buffer would run the previous statement again.
            raise ValueError("query() needs an SQL statement")
        m = self._marker
        fmt = {
            "format": "unaligned",
            "expanded": "on",
            "fieldsep_zero": "on",
            "recordsep_zero": "on",
            "footer": "off",
            "tuples_only": "off",
            "title": m,
            "null": self._null,
        }
        g = " ".join(f"{k}={v}" for k, v in fmt.items())
        # \gdesc describes the statement just run (without running it again) and prints with
        # the session's \pset options: set the driver's format, then restore the caller's.
        psets = self._psets()
        use = "".join(f"\\pset {k} {v}\n" for k, v in fmt.items())
        restore = "".join(f"\\pset {k} {psets[k]}\n" for k in _PSET_KEYS)
        script = (
            f"{body}\n\\g ({g})\n\\echo {m}-COUNT :ROW_COUNT\n"
            f"\\set {m}_quiet :QUIET\n\\set QUIET on\n\\if :ERROR\n\\else\n"
            f"{use}\\echo {m}-DESC\n\\warn {m}-DESC\n\\gdesc\n{restore}\\endif\n"
            f"\\set QUIET :{m}_quiet\n\\unset {m}_quiet\n"
        )
        out = self._call(self._with_vars(script, variables), timeout or self.timeout)
        # stderr after the DESC marker is from \gdesc, which cannot describe some statements.
        out = Output(out.script, out.out, out.err.partition(f"{m}-DESC")[0], out.sqlstate)
        if not out.ok:
            raise SqlError(out.errors[0], out.sqlstate, out)
        main, _, rest = out.out.rpartition(f"{m}-COUNT ")
        count, _, desc_text = rest.partition(f"{m}-DESC")
        rowcount = int(count.split()[0]) if count.split() and count.split()[0].isdigit() else 0
        desc = self._table(desc_text) or []
        types = [dict(d).get("Type") or "" for d in desc]
        table = self._table(main)
        if table is None:
            # No result table: a command without rows. Its tag is the last line.
            status = (main.strip().splitlines() or [""])[-1]
            return Rows([], [], [], status, rowcount)
        status = main[main.rfind("\0") + 1 :].strip()
        columns = [dict(d).get("Column") or "" for d in desc]
        if not columns and table:
            columns = [c for c, _ in table[0]]
        convs = [_converter(t) for t in types]
        rows = []
        for r in table:
            if len(convs) == len(r):
                pairs = zip(r, convs, strict=True)
                rows.append({c: None if v is None else _convert(v, f) for (c, v), f in pairs})
            else:
                rows.append(dict(r))
        return Rows(rows, columns, types, status, rowcount)

    def cancel(self) -> bool:
        """Cancel the running statement (pg_cancel_backend from a second psql); True if sent."""
        if not self.pid:
            return False
        try:
            r = _subprocess.run(
                self.cmd,
                input=f"select pg_cancel_backend({self.pid});\n",
                capture_output=True,
                text=True,
                timeout=15,
                env={**_os.environ, **self.env} if self.env else None,
            )
        except (OSError, _subprocess.TimeoutExpired):
            return False
        return bool(_re.search(r"^\s*t\s*$", r.stdout, _re.M))

    def close(self) -> None:
        """Quit psql (\\q, then end of input); kill it if it does not exit within 5 s."""
        if self._proc.poll() is None:
            _end_input(self._proc.stdin)
            try:
                self._proc.wait(5)
            except _subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def restart(self) -> None:
        """Close psql and start cmd again: a new connection without the old session state."""
        self.close()
        self._calls = 0
        self._pset = None
        self._start()


_API = (Psql, Output, Rows, SqlError, PsqlExited)


def _summary(obj: Any) -> str:
    return ((_inspect.getdoc(obj) or "").splitlines() or [""])[0]


def _signature(obj: Any) -> str:
    try:
        sig = _inspect.signature(obj)
    except (TypeError, ValueError):
        return "(...)"
    params = [
        p.replace(annotation=_inspect.Parameter.empty)
        for p in sig.parameters.values()
        if p.name not in ("self", "cls")
    ]
    ret = sig.return_annotation
    text = str(sig.replace(parameters=params, return_annotation=_inspect.Signature.empty))
    return text + (f" -> {ret}" if ret is not _inspect.Signature.empty and ret != "None" else "")


def api(name: Any = None) -> None:
    """Print the driver API: signatures and one-line summaries.

    api(Psql) prints one class; help(Psql.run) shows the details.
    """
    for obj in [name] if name is not None else _API:
        print(f"{obj.__name__}{_signature(obj)}  # {_summary(obj)}")
        if not isinstance(obj, type):
            continue
        seen: set[str] = set()
        for cls in obj.__mro__:
            if cls in (object, list, Exception, BaseException, RuntimeError):
                break
            for attr, member in vars(cls).items():
                if attr.startswith("_") or attr in seen:
                    continue
                seen.add(attr)
                if isinstance(member, property):
                    print(f"  .{attr}  # {_summary(member)}")
                elif callable(member):
                    print(f"  .{attr}{_signature(member)}  # {_summary(member)}")
