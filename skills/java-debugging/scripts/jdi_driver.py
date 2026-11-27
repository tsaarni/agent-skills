"""Debug a JVM over JDWP with JDI, called from Python through JPype. Usage: java-debugging SKILL.md.

JDI (com.sun.jdi, module jdk.jdi) is the JDK's debugger API. JPype runs a JVM inside the Python
process, so JDI objects are Python objects. Method calls in the target resume only the calling
thread.

Load it into a pysess session (the first run installs JPype into the shared pysess venv
"java-debugging", so give it a longer timeout):

    pysess new -n jdi -t 120 <skill-dir>/scripts/jdi_driver.py
    pysess exec jdi -c 'j = Jdi("localhost:5005")'

The raw JDI API stays available: j.vm (VirtualMachine), h.thread (ThreadReference),
h.event (Event), o._ref (ObjectReference), ev.requests (EventRequests).

Checks (run after editing; ty needs the venv with JPype, see `pysess venv ls`):
    uvx ruff format --line-length 100 jdi_driver.py
    uvx ruff check --line-length 100 --select E,W,F,I,UP,B,SIM jdi_driver.py
    uvx ty check --python <java-debugging venv> jdi_driver.py
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import fnmatch
import pathlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any, Literal, Self

JPYPE_REQUIREMENT = "JPype1==1.7.1"


def _load_jpype() -> Any:
    """Import JPype; inside pysess, install it into the shared venv 'java-debugging' if needed."""
    try:
        import pysess  # noqa: PLC0415  # ty: ignore[import-not-found]

        if pysess.venv.current() is None:
            pysess.venv.use("java-debugging")
    except ImportError:
        pysess = None
    try:
        import jpype  # noqa: PLC0415
    except ImportError:
        if pysess is None:
            raise
        pysess.venv.install(JPYPE_REQUIREMENT)
        import jpype  # noqa: PLC0415
    import jpype.imports  # noqa: F401, PLC0415  (enables `from com.sun.jdi import ...`)

    if not jpype.isJVMStarted():
        # interrupt=False: SIGINT (pysess cancel) raises KeyboardInterrupt in Python instead
        # of shutting down the JVM, which would end the session.
        jpype.startJVM("--enable-native-access=ALL-UNNAMED", interrupt=False)
    return jpype


jpype = _load_jpype()
# JPype's casts select an overload, as in JPype: obj.append(JInt(1)), Math.max(JLong(3), JLong(7)).
from jpype import (  # noqa: E402
    JBoolean,
    JByte,
    JChar,
    JDouble,
    JFloat,
    JInt,
    JLong,
    JShort,
    JString,
)

_CASTS = {JBoolean: "boolean", JByte: "byte", JChar: "char", JShort: "short", JInt: "int",
          JLong: "long", JFloat: "float", JDouble: "double"}


def _cast_type(value: Any) -> str | None:
    """Java primitive type of a JPype cast (JInt(1) -> 'int'), else None."""
    for cast, prim in _CASTS.items():
        if isinstance(value, cast):
            return prim
    return None

from com.sun.jdi import (  # noqa: E402  # ty: ignore[import-not-found]
    AbsentInformationException,
    ArrayReference,
    BooleanValue,
    Bootstrap,
    CharValue,
    ClassObjectReference,
    ClassType,
    DoubleValue,
    FloatValue,
    IncompatibleThreadStateException,
    InterfaceType,
    InvocationException,
    ObjectReference,
    PrimitiveValue,
    StringReference,
    ThreadReference,
    VMDisconnectedException,
    VoidValue,
)
from com.sun.jdi.event import (  # noqa: E402  # ty: ignore[import-not-found]
    AccessWatchpointEvent,
    BreakpointEvent,
    ClassPrepareEvent,
    ExceptionEvent,
    LocatableEvent,
    MethodEntryEvent,
    MethodExitEvent,
    ModificationWatchpointEvent,
    StepEvent,
    VMDeathEvent,
    VMDisconnectEvent,
)
from com.sun.jdi.request import EventRequest, StepRequest  # noqa: E402  # ty: ignore[import-not-found]
from java.util import ArrayList, HashMap  # noqa: E402  # ty: ignore[import-not-found]

Suspend = Literal["thread", "all", "none"]
_POLICY: dict[Suspend, int] = {
    "thread": EventRequest.SUSPEND_EVENT_THREAD,
    "all": EventRequest.SUSPEND_ALL,
    "none": EventRequest.SUSPEND_NONE,
}
_POLICY_NAME: dict[int, Suspend] = {v: k for k, v in _POLICY.items()}  # type: ignore[misc]
_OWNER_KEY = "jdi_driver.owner"
_PRIMITIVES = {"boolean", "byte", "char", "short", "int", "long", "float", "double"}
_BOXES = {
    "java.lang.Integer": "int",
    "java.lang.Long": "long",
    "java.lang.Short": "short",
    "java.lang.Byte": "byte",
    "java.lang.Character": "char",
    "java.lang.Boolean": "boolean",
    "java.lang.Float": "float",
    "java.lang.Double": "double",
}
_STEP_EXCLUDE = ("java.*", "javax.*", "jdk.*", "sun.*", "com.sun.*")
_THREAD_STATUS = {
    ThreadReference.THREAD_STATUS_UNKNOWN: "unknown",
    ThreadReference.THREAD_STATUS_ZOMBIE: "zombie",
    ThreadReference.THREAD_STATUS_RUNNING: "running",
    ThreadReference.THREAD_STATUS_SLEEPING: "sleeping",
    ThreadReference.THREAD_STATUS_MONITOR: "waiting for monitor",
    ThreadReference.THREAD_STATUS_WAIT: "waiting",
    ThreadReference.THREAD_STATUS_NOT_STARTED: "not started",
}


class JavaException(Exception):  # noqa: N818 - mirrors the Java name
    """A method called in the target threw. `.obj` is the exception object (Obj)."""

    def __init__(self, type_name: str, message: str | None, obj: Obj) -> None:
        super().__init__(f"{type_name}: {message}" if message is not None else type_name)
        self.type = type_name
        self.message = message
        self.obj = obj


def _bg(fn: Callable[[], Any]) -> concurrent.futures.Future[Any]:
    """Run fn() in a daemon thread and return a Future (like pysess.bg)."""
    fut: concurrent.futures.Future[Any] = concurrent.futures.Future()

    def work() -> None:
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn())
        except BaseException as e:  # noqa: BLE001 - kept in the Future
            fut.set_exception(e)

    threading.Thread(target=work, daemon=True).start()
    return fut


def _loc_str(loc: Any) -> str:
    line = int(loc.lineNumber())
    name = f"{loc.declaringType().name()}.{loc.method().name()}"
    return name if line < 0 else f"{name}:{line}"


def _supertypes(t: Any) -> set[str]:
    """Names of t, its superclasses and all its interfaces."""
    names = {str(t.name())}
    if isinstance(t, ClassType):
        names |= {str(i.name()) for i in t.allInterfaces()}
        s = t.superclass()
        while s is not None:
            names.add(str(s.name()))
            s = s.superclass()
    elif isinstance(t, InterfaceType):
        names |= {str(i.name()) for i in t.superinterfaces()}
        names.add("java.lang.Object")
    else:  # array type
        names |= {"java.lang.Object", "java.lang.Cloneable", "java.io.Serializable"}
    return names


class Jdi:
    """A JDI connection to one target JVM."""

    def __init__(self, address: str = "localhost:5005", timeout: float = 10.0) -> None:
        host, _, port = address.rpartition(":")
        connector = next(
            c
            for c in Bootstrap.virtualMachineManager().attachingConnectors()
            if c.name() == "com.sun.jdi.SocketAttach"
        )
        args = connector.defaultArguments()
        args.get("hostname").setValue(host or "localhost")
        args.get("port").setValue(port)
        args.get("timeout").setValue(str(int(timeout * 1000)))
        self.vm: Any = connector.attach(args)
        self.erm: Any = self.vm.eventRequestManager()
        self.connected = True
        self._lock = threading.Condition()
        self._owners: dict[int, Events] = {}
        self._next_owner = 1
        self._unowned: collections.deque[Hit] = collections.deque()
        self._stop = False
        self._dispatcher = threading.Thread(target=self._dispatch, daemon=True, name="jdi-events")
        self._dispatcher.start()

    def __repr__(self) -> str:
        state = "connected" if self.connected else "disconnected"
        return f"<Jdi {self.vm.name()} {self.vm.version()} {state}>"

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

    # --- events -----------------------------------------------------------------------

    def _register(self, ev: Events) -> int:
        with self._lock:
            oid = self._next_owner
            self._next_owner += 1
            self._owners[oid] = ev
            return oid

    def _owner_of(self, request: Any) -> Events | None:
        if request is None:
            return None
        oid = request.getProperty(_OWNER_KEY)
        if oid is None:
            return None
        with self._lock:
            return self._owners.get(int(oid))

    def _dispatch(self) -> None:
        q = self.vm.eventQueue()
        while not self._stop:
            try:
                es = q.remove(200)
            except VMDisconnectedException:
                break
            except Exception:  # noqa: BLE001 - keep the dispatcher alive
                continue
            if es is None:
                continue
            events = list(es)
            if any(isinstance(e, (VMDeathEvent, VMDisconnectEvent)) for e in events):
                break
            prepares = [e for e in events if isinstance(e, ClassPrepareEvent)]
            if prepares:
                for e in prepares:
                    owner = self._owner_of(e.request())
                    if owner is not None:
                        owner._class_prepared(e.referenceType())
                es.resume()
                continue
            located = [e for e in events if isinstance(e, LocatableEvent)]
            if not located:
                es.resume()
                continue
            owner = next(
                (o for e in located if (o := self._owner_of(e.request())) is not None), None
            )
            hit = Hit(self, es, located, owner)
            if owner is not None:
                owner._deliver(hit)
            else:
                with self._lock:
                    self._unowned.append(hit)
                    self._lock.notify_all()
        with self._lock:
            self.connected = False
            self._lock.notify_all()

    def events(
        self,
        *bps: str,
        catch: str | Sequence[str] = (),
        caught: bool = True,
        uncaught: bool = True,
        watch: str | Sequence[str] = (),
        access: bool = False,
        calls: str | Sequence[str] = (),
        run: Callable[[], Any] | None = None,
        suspend: Suspend = "thread",
        thread: str | Any | None = None,
        instance: Obj | None = None,
        nth: int | None = None,
        timeout: float = 10.0,
        count: int | None = None,
    ) -> Events:
        """Create event sources, start `run()` in the background, return an Events.

        bps: breakpoints `C.m` (all overloads), `C.m(int,java.lang.String)` or `C:line`.
        catch: exception classes (subclasses match too); `caught`/`uncaught` select which.
        watch: fields `C.f`; field writes, or reads too with `access=True`.
        calls: class patterns (`com.example.*`) for method entry and exit events.
        suspend: 'thread' (stop the hitting thread), 'all' (stop the VM), 'none' (log only).
        thread / instance / nth: report only events in this thread, only with `this` being
        this object, or only the nth occurrence.
        timeout: default seconds next() waits; count: maximum hits when iterating.
        Classes that are not loaded yet get their breakpoints when they load.
        """

        def as_list(v: str | Sequence[str]) -> list[str]:
            return [v] if isinstance(v, str) else list(v)

        return Events(
            self,
            bps=list(bps),
            catch=as_list(catch),
            caught=caught,
            uncaught=uncaught,
            watch=as_list(watch),
            access=access,
            calls=as_list(calls),
            run=run,
            suspend=suspend,
            thread=thread,
            instance=instance,
            nth=nth,
            timeout=timeout,
            count=count,
        )

    def wait(self, timeout: float = 10.0) -> Hit:
        """Next event of a request created with raw JDI (not through events())."""
        end = time.monotonic() + timeout
        with self._lock:
            while not self._unowned:
                rem = end - time.monotonic()
                if rem <= 0 or not self.connected:
                    raise TimeoutError(f"no event within {timeout}s")
                self._lock.wait(min(rem, 0.2))
            return self._unowned.popleft()

    # --- threads ----------------------------------------------------------------------

    def threads(self) -> dict[str, str]:
        """{name: status} for all threads, e.g. 'waiting', 'running (suspended)'."""
        res: dict[str, str] = {}
        for t in self.vm.allThreads():
            status = _THREAD_STATUS.get(int(t.status()), "unknown")
            if t.isAtBreakpoint():
                status += " (at breakpoint)"
            elif t.isSuspended():
                status += " (suspended)"
            res[str(t.name())] = status
        return res

    def thread(self, name: str) -> Any:
        """ThreadReference of the thread with this name."""
        found = [t for t in self.vm.allThreads() if str(t.name()) == name]
        if not found:
            raise KeyError(f"no thread {name!r}")
        return found[0]

    def resume(self) -> None:
        """Resume all threads (VM-wide; also undoes a VM-wide suspend)."""
        self.vm.resume()

    # --- classes, heap ----------------------------------------------------------------

    def classes(self, pattern: str = "*") -> list[str]:
        """Names of loaded classes matching a glob pattern, e.g. '*Cache*'."""
        return sorted({str(t.name()) for t in self.vm.allClasses()
                       if fnmatch.fnmatchcase(str(t.name()), pattern)})

    def _types(self, name: str) -> list[Any]:
        return list(self.vm.classesByName(name))

    def cls(self, name: str, hit: Hit | None = None) -> Cls:
        """Static fields (and, with `hit`, static methods) of a loaded class."""
        types = self._types(name)
        if not types:
            raise LookupError(f"class {name} is not loaded")
        if len(types) > 1:
            loaders = [str(t.classLoader()) for t in types]
            raise LookupError(f"{name} is loaded by {len(types)} classloaders: {loaders}; "
                              "use Cls(j, j.vm.classesByName(name)[i], hit)")
        return Cls(self, types[0], hit)

    def instances(self, name: str, max: int = 100) -> list[Obj]:  # noqa: A002
        """Live instances of a class (all classloaders); no code runs in the target."""
        out: list[Obj] = []
        for t in self._types(name):
            out += [_to_py(self, o, None) for o in t.instances(jpype.JLong(max))]
        return out[:max]

    def counts(self, *names: str) -> dict[str, int]:
        """Live instance counts per class name (all classloaders)."""
        res: dict[str, int] = {}
        for n in names:
            types = self._types(n)
            if not types:
                res[n] = 0
                continue
            lst = ArrayList()
            for t in types:
                lst.add(t)
            res[n] = sum(int(c) for c in self.vm.instanceCounts(lst))
        return res

    def referrers(self, obj: Obj, max: int = 20) -> list[Obj]:  # noqa: A002
        """Objects that hold a reference to `obj`."""
        return [_to_py(self, r, obj._hit) for r in obj._ref.referringObjects(jpype.JLong(max))]

    def retainers(self, obj: Obj, depth: int = 8, width: int = 5) -> list[str]:
        """Reference chains that keep `obj` alive, found by walking referrers up to `depth`.

        Each chain reads from the holder down to `obj`, e.g.
        'DebugTarget.retained (static) -> java.util.ArrayList.elementData -> [3] -> byte[]#12'.
        A chain ends at a class (static field) or at an object with no referrers within reach.
        """
        chains: list[str] = []
        start = obj._ref
        frontier: list[tuple[Any, list[str]]] = [(start, [_ref_label(start)])]
        seen = {int(start.uniqueID())}
        for _ in range(depth):
            nxt: list[tuple[Any, list[str]]] = []
            for ref, path in frontier:
                refs = list(ref.referringObjects(jpype.JLong(width)))
                if not refs:
                    chains.append(" -> ".join(reversed(path)))
                for r in refs:
                    rid = int(r.uniqueID())
                    if rid in seen:
                        continue
                    seen.add(rid)
                    link = _link(r, ref)
                    if link.endswith("(static)"):
                        chains.append(" -> ".join([link, *reversed(path)]))
                        continue
                    nxt.append((r, [*path, link]))
            frontier = nxt
            if not frontier:
                break
        chains += [" -> ".join(["...", *reversed(p)]) for _, p in frontier]
        return chains

    def redefine(self, name: str, class_file: str) -> None:
        """Hot swap: replace the bytecode of a loaded class (method bodies only)."""
        data = pathlib.Path(class_file).read_bytes()
        m = HashMap()
        for t in self._types(name):
            m.put(t, jpype.JArray(jpype.JByte)(data))
        if m.isEmpty():
            raise LookupError(f"class {name} is not loaded")
        self.vm.redefineClasses(m)

    # --- teardown ---------------------------------------------------------------------

    def close(self) -> None:
        """Close all Events, then detach: the target clears all requests and resumes."""
        with self._lock:
            owners = list(self._owners.values())
        for ev in owners:
            with contextlib.suppress(Exception):
                ev.close()
        self._stop = True
        if self.connected:
            with contextlib.suppress(Exception):
                self.vm.dispose()
        self._dispatcher.join(timeout=2)
        self.connected = False


def _ref_label(ref: Any) -> str:
    if isinstance(ref, ClassObjectReference):
        return f"class {ref.reflectedType().name()}"
    return f"{ref.referenceType().name()}#{ref.uniqueID()}"


def _link(holder: Any, target: Any) -> str:
    """How `holder` refers to `target`: a field name, an array index, or a static field."""
    tid = int(target.uniqueID())
    if isinstance(holder, ArrayReference):
        for i, v in enumerate(holder.getValues()):
            if isinstance(v, ObjectReference) and int(v.uniqueID()) == tid:
                return f"{holder.referenceType().name()}[{i}]"
        return f"{holder.referenceType().name()}[?]"
    if isinstance(holder, ClassObjectReference):
        t = holder.reflectedType()
        for f in t.allFields():
            if f.isStatic():
                v = t.getValue(f)
                if isinstance(v, ObjectReference) and int(v.uniqueID()) == tid:
                    return f"{t.name()}.{f.name()} (static)"
        return f"class {t.name()}"
    t = holder.referenceType()
    for f in t.allFields():
        if f.isStatic():
            continue
        v = holder.getValue(f)
        if isinstance(v, ObjectReference) and int(v.uniqueID()) == tid:
            return f"{t.name()}.{f.name()}"
    return _ref_label(holder)


class Hit:
    """One event (or several at the same place and time). Inspect it until it is resumed."""

    def __init__(self, j: Jdi, es: Any, events: list[Any], owner: Events | None) -> None:
        self._j = j
        self._es = es
        self.event: Any = events[0]
        self.events: list[Any] = events
        self.owner = owner
        self.policy: Suspend = _POLICY_NAME[int(es.suspendPolicy())]
        self.resumed = self.policy == "none"
        self.held = False
        self._pinned: list[Any] = []
        self._calls: list[concurrent.futures.Future[Any]] = []  # method calls in this thread
        e = self.event
        self.thread: Any = e.thread()
        self.thread_name = str(self.thread.name())
        self.location = _loc_str(e.location())
        self.class_name = str(e.location().declaringType().name())
        self.method = str(e.location().method().name())
        line = int(e.location().lineNumber())
        self.line: int | None = line if line >= 0 else None
        self.kind, self.detail = self._describe(e)

    def _describe(self, e: Any) -> tuple[str, str]:
        if isinstance(e, BreakpointEvent):
            return "breakpoint", ""
        if isinstance(e, StepEvent):
            return "step", ""
        if isinstance(e, ExceptionEvent):
            where = e.catchLocation()
            caught = f"caught at {_loc_str(where)}" if where is not None else "uncaught"
            return "exception", f"{e.exception().referenceType().name()} ({caught})"
        if isinstance(e, ModificationWatchpointEvent):
            old, new = self._py(e.valueCurrent()), self._py(e.valueToBe())
            field = f"{e.field().declaringType().name()}.{e.field().name()}"
            return "watch", f"{field}: {old!r} -> {new!r}"
        if isinstance(e, AccessWatchpointEvent):
            return "access", f"{e.field().declaringType().name()}.{e.field().name()}"
        if isinstance(e, MethodEntryEvent):
            return "enter", ""
        if isinstance(e, MethodExitEvent):
            return "exit", repr(self._py(e.returnValue()))
        return type(e).__name__, ""

    def __repr__(self) -> str:
        state = " held" if self.held else (" resumed" if self.resumed else "")
        detail = f" {self.detail}" if self.detail else ""
        return f"<Hit {self.kind} {self.location} [{self.thread_name}]{detail}{state}>"

    # --- values -----------------------------------------------------------------------

    def _check(self) -> None:
        if self.resumed:
            raise RuntimeError(f"{self!r}: the thread was resumed, its frames are gone")

    def _py(self, v: Any) -> Any:
        return _to_py(self._j, v, self)

    def frame(self, n: int = 0) -> Frame:
        """Stack frame `n` (0 = where the event happened)."""
        self._check()
        return Frame(self, n)

    @property
    def this(self) -> Obj | None:
        """`this` in frame 0 (None in static methods)."""
        return self.frame(0).this

    @property
    def locals(self) -> dict[str, Any]:
        """Arguments and local variables of frame 0 (needs classes compiled with -g)."""
        return self.frame(0).locals

    @property
    def exception(self) -> Obj | None:
        """The thrown exception for exception events."""
        if isinstance(self.event, ExceptionEvent):
            return Obj(self._j, self.event.exception(), self)
        return None

    @property
    def value(self) -> Any:
        """Return value (exit events) or the new field value (watch events)."""
        if isinstance(self.event, MethodExitEvent):
            return self._py(self.event.returnValue())
        if isinstance(self.event, ModificationWatchpointEvent):
            return self._py(self.event.valueToBe())
        return None

    def stack(self, depth: int = 10, include: str | Sequence[str] | None = None) -> list[str]:
        """Frames as `Class.method:line`, innermost first, optionally only classes starting
        with `include` (a prefix or several). `depth` counts the frames kept."""
        self._check()
        prefixes = (include,) if isinstance(include, str) else tuple(include or ())
        out: list[str] = []
        for f in self.thread.frames():
            loc = f.location()
            if prefixes and not str(loc.declaringType().name()).startswith(prefixes):
                continue
            out.append(_loc_str(loc))
            if len(out) >= depth:
                break
        return out

    def cls(self, name: str) -> Cls:
        """A class whose static methods are called in this hit's thread. A class that is not
        loaded yet is loaded with Class.forName(name, true, <system class loader>)."""
        try:
            return self._j.cls(name, hit=self)
        except LookupError:
            if self._j._types(name):
                raise
        loader = self._j.cls("java.lang.ClassLoader", hit=self).getSystemClassLoader()
        self._j.cls("java.lang.Class", hit=self).forName(name, True, loader)
        return self._j.cls(name, hit=self)

    def bind(self, obj: Obj) -> Obj:
        """The same object, with method calls running in this hit's thread."""
        return Obj(self._j, obj._ref, self)

    def new(self, cls_name: str, *args: Any) -> Obj:
        """Create an object in the target: calls a constructor in this hit's thread."""
        self._check()
        t = self.cls(cls_name)._type
        ctor, jargs = _select(self._j, t.methodsByName("<init>"), args, self, f"{cls_name}()")
        return self._py(self._invoke(lambda: t.newInstance(self.thread, ctor, jargs,
                                                           ObjectReference.INVOKE_SINGLE_THREADED)))

    def _invoke(self, call: Callable[[], Any], timeout: float = 10.0) -> Any:
        """Run a JDI invoke with a timeout; map exceptions; pin the returned object."""
        self._check()
        fut = _bg(call)
        self._calls = [f for f in self._calls if not f.done()] + [fut]
        end = time.monotonic() + timeout
        try:
            while True:  # short waits: SIGINT (pysess cancel) may arrive in a JVM thread
                try:
                    v = fut.result(timeout=min(0.2, max(0.0, end - time.monotonic())))
                    break
                except concurrent.futures.TimeoutError:
                    if time.monotonic() >= end:
                        raise
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"method call in {self.thread_name} still running after {timeout}s. It may "
                "wait for a lock held by another suspended thread: resume that thread."
            ) from None
        except InvocationException as e:
            exc = e.exception()
            obj = Obj(self._j, exc, self)
            msg = _string_field(exc, "detailMessage")
            raise JavaException(str(exc.referenceType().name()), msg, obj) from None
        except IncompatibleThreadStateException:
            raise RuntimeError(f"{self.thread_name} is not suspended by an event") from None
        if isinstance(v, ObjectReference):
            with contextlib.suppress(Exception):
                v.disableCollection()
                self._pinned.append(v)
        return v

    # --- control ----------------------------------------------------------------------

    def hold(self) -> Self:
        """Keep the thread suspended when the Events moves on or closes."""
        self.held = True
        return self

    def resume(self) -> None:
        """Resume what the event suspended (its thread, or all threads).

        If a method call in this thread is still running (timed out or cancelled), the resume
        happens when the call ends: the end of a call suspends the thread again."""
        if self.resumed:
            return
        self.resumed = True

        def do() -> None:
            for o in self._pinned:
                with contextlib.suppress(Exception):
                    o.enableCollection()
            self._pinned.clear()
            with contextlib.suppress(Exception):
                self._es.resume()

        running = [f for f in self._calls if not f.done()]
        if running:
            running[-1].add_done_callback(lambda _f: do())
        else:
            do()

    def force_return(self, value: Any = None) -> None:
        """Make the method of frame 0 return `value` when the thread resumes, without running
        the rest of it. At a method breakpoint the method body does not run at all."""
        self._check()
        method = self.thread.frame(0).location().method()
        rtype = str(method.returnTypeName())
        jv = self._j.vm.mirrorOfVoid() if rtype == "void" else _to_java(self._j, value, rtype, self)
        self.thread.forceEarlyReturn(jv)

    def throw(self, exc: Obj | str, message: str | None = None, timeout: float = 10.0) -> Hit:
        """Throw an exception in this thread: an Obj, or a class name to create, e.g.
        h.throw("java.io.IOException", "injected"). Resumes the thread and returns the Hit
        where the exception is thrown (the application's catch blocks have not run yet).

        Side effect: JDI delivers it as an asynchronous exception, and HotSpot interrupts the
        thread to deliver it (JavaThread::install_async_exception). Later code in the thread
        that checks the interrupt then fails too: in tests with the JDK HTTP server the
        socket closed on write and the response was lost, also after clearing the flag with
        Thread.interrupted(). Prefer force_return() where the application turns a return
        value into an error, or use this only where losing the thread's I/O is acceptable."""
        self._check()
        if isinstance(exc, str):
            exc = self.new(exc, message) if message is not None else self.new(exc)
        erm = self._j.erm
        req = erm.createExceptionRequest(exc._ref.referenceType(), True, True)
        req.addThreadFilter(self.thread)
        owner = self.owner if self.owner is not None else _StepOwner(self._j)
        req.setSuspendPolicy(_POLICY["thread"])
        req.putProperty(_OWNER_KEY, jpype.JInt(owner._oid))
        req.enable()
        target = exc._id
        try:
            self.thread.stop(exc._ref)
            self.held = False
            self.resume()
            hit = owner._wait_for(
                lambda h: h.event.request() == req and h.exception is not None
                and h.exception._id == target, timeout)
        finally:
            with contextlib.suppress(Exception):
                erm.deleteEventRequest(req)
            if isinstance(owner, _StepOwner):
                owner.close()
        return hit

    def step(self, kind: Literal["over", "into", "out"] = "over", timeout: float = 10.0) -> Hit:
        """Resume this thread for one source line (or out of the method); return the new Hit.

        Steps into JDK classes are skipped. If the step does not complete within `timeout`,
        it is cancelled (a pending step would fire later in a random request)."""
        self._check()
        depth = {"over": StepRequest.STEP_OVER, "into": StepRequest.STEP_INTO,
                 "out": StepRequest.STEP_OUT}[kind]
        erm = self._j.erm
        for r in list(erm.stepRequests()):
            if r.thread() == self.thread:
                erm.deleteEventRequest(r)
        req = erm.createStepRequest(self.thread, StepRequest.STEP_LINE, depth)
        for pattern in _STEP_EXCLUDE:
            req.addClassExclusionFilter(pattern)
        req.addCountFilter(1)
        req.setSuspendPolicy(_POLICY["thread"])
        owner = self.owner if self.owner is not None else _StepOwner(self._j)
        req.putProperty(_OWNER_KEY, jpype.JInt(owner._oid))
        req.enable()
        self.held = False
        self.resume()
        try:
            hit = owner._wait_for(lambda h: h.event.request() == req, timeout)
        finally:
            with contextlib.suppress(Exception):
                erm.deleteEventRequest(req)
            if isinstance(owner, _StepOwner):
                owner.close()
        return hit


class Frame:
    """A stack frame of a Hit. Values are read when accessed (frames change after calls)."""

    def __init__(self, hit: Hit, n: int) -> None:
        self._hit = hit
        self.n = n

    def _f(self) -> Any:
        self._hit._check()
        return self._hit.thread.frame(self.n)

    def __repr__(self) -> str:
        return f"<Frame {self.n} {self.location}>"

    @property
    def location(self) -> str:
        return _loc_str(self._f().location())

    @property
    def this(self) -> Obj | None:
        t = self._f().thisObject()
        return None if t is None else Obj(self._hit._j, t, self._hit)

    @property
    def locals(self) -> dict[str, Any]:
        f = self._f()
        try:
            variables = list(f.visibleVariables())
        except AbsentInformationException:
            raise RuntimeError(f"{self.location}: no local variable info (compile with -g); "
                               "arguments by position: h.frame().args") from None
        values = f.getValues(f.visibleVariables())
        return {str(v.name()): self._hit._py(values.get(v)) for v in variables}

    @property
    def args(self) -> list[Any]:
        """Argument values by position (also works without -g)."""
        return [self._hit._py(v) for v in self._f().getArgumentValues()]

    def set(self, name: str, value: Any) -> None:
        """Assign a local variable or argument."""
        f = self._f()
        var = f.visibleVariableByName(name)
        if var is None:
            raise KeyError(f"no local variable {name!r}")
        f.setValue(var, _to_java(self._hit._j, value, str(var.typeName()), self._hit))


def _string_field(obj: Any, name: str) -> str | None:
    f = obj.referenceType().fieldByName(name)
    if f is None:
        return None
    v = obj.getValue(f)
    return str(v.value()) if isinstance(v, StringReference) else None


def _to_py(j: Jdi, v: Any, hit: Hit | None) -> Any:
    """JDI Value -> Python: primitives, str, boxed numbers unboxed, other objects as Obj."""
    if v is None or isinstance(v, VoidValue):
        return None
    if isinstance(v, StringReference):
        return str(v.value())
    if isinstance(v, BooleanValue):
        return bool(v.value())
    if isinstance(v, CharValue):
        return str(v.value())
    if isinstance(v, (FloatValue, DoubleValue)):
        return float(v.value())
    if isinstance(v, PrimitiveValue):
        return int(v.longValue())
    if isinstance(v, ObjectReference):
        tname = str(v.referenceType().name())
        if tname in _BOXES:
            return _to_py(j, v.getValue(v.referenceType().fieldByName("value")), hit)
        if isinstance(v, ArrayReference):
            return Arr(j, v, hit)
        return Obj(j, v, hit)
    return v


def _to_java(j: Jdi, value: Any, type_name: str, hit: Hit | None) -> Any:
    """Python value -> JDI Value for a parameter or variable of `type_name`."""
    vm = j.vm
    if isinstance(value, Obj):
        return value._ref
    if isinstance(value, JString):
        value = str(value)
    if value is None:
        if type_name in _PRIMITIVES:
            raise TypeError(f"None for primitive {type_name}")
        return None
    prim = _BOXES.get(type_name, type_name)
    if prim in _PRIMITIVES:
        mirror = _mirror(vm, value, prim)
        if type_name in _BOXES:  # box: call Integer.valueOf(int) etc.
            if hit is None:
                raise TypeError(f"boxing to {type_name} needs a hit (a suspended thread)")
            return _box(j, mirror, type_name, prim, hit)
        return mirror
    if isinstance(value, str):
        s = vm.mirrorOf(jpype.JString(value))
        with contextlib.suppress(Exception):
            s.disableCollection()
            if hit is not None:
                hit._pinned.append(s)
        return s
    cast = _cast_type(value)
    if cast is not None and hit is not None:  # e.g. Object parameter: box as the cast says
        box = {v: k for k, v in _BOXES.items()}[cast]
        return _box(j, _mirror(vm, value, cast), box, cast, hit)
    if isinstance(value, bool | int | float) and hit is not None:  # e.g. Object parameter
        if isinstance(value, bool):
            box = "java.lang.Boolean"
        elif isinstance(value, float):
            box = "java.lang.Double"
        else:
            box = "java.lang.Integer" if -(2**31) <= value < 2**31 else "java.lang.Long"
        prim = _BOXES[box]
        return _box(j, _mirror(vm, value, prim), box, prim, hit)
    raise TypeError(f"cannot pass {value!r} as {type_name}")


def _mirror(vm: Any, value: Any, prim: str) -> Any:
    conv = {
        "boolean": jpype.JBoolean, "byte": jpype.JByte, "char": jpype.JChar,
        "short": jpype.JShort, "int": jpype.JInt, "long": jpype.JLong,
        "float": jpype.JFloat, "double": jpype.JDouble,
    }[prim]
    return vm.mirrorOf(conv(value))


def _box(j: Jdi, mirror: Any, box: str, prim: str, hit: Hit) -> Any:
    t = j.cls(box)._type
    m = next(m for m in t.methodsByName("valueOf") if list(m.argumentTypeNames()) == [prim])
    lst = ArrayList()
    lst.add(mirror)
    return hit._invoke(lambda: t.invokeMethod(hit.thread, m, lst,
                                              ObjectReference.INVOKE_SINGLE_THREADED))


def _arg_score(j: Jdi, value: Any, ptype: str) -> int | None:
    """How well `value` fits parameter type `ptype`: higher is better, None if it does not."""
    if isinstance(value, Obj):
        rt = value._ref.referenceType()
        if ptype == str(rt.name()):
            return 6
        if ptype == "java.lang.Object":
            return 2
        return 4 if ptype in _supertypes(rt) else None
    if value is None:
        return None if ptype in _PRIMITIVES else 1
    cast = _cast_type(value)
    if cast is not None:
        box = {v: k for k, v in _BOXES.items()}[cast]
        return {cast: 8, box: 7, "java.lang.Object": 2}.get(ptype)
    if isinstance(value, JString):
        value = str(value)
    if isinstance(value, bool):
        return {"boolean": 6, "java.lang.Boolean": 4, "java.lang.Object": 2}.get(ptype)
    if isinstance(value, int):  # like Java: int before long, then boxing, then Object
        return {"int": 6, "long": 5, "java.lang.Integer": 4, "java.lang.Long": 3,
                "short": 2, "byte": 2, "double": 1, "float": 1, "java.lang.Number": 1,
                "java.lang.Object": 1}.get(ptype)
    if isinstance(value, float):
        return {"double": 6, "float": 5, "java.lang.Double": 4, "java.lang.Number": 1,
                "java.lang.Object": 1}.get(ptype)
    if isinstance(value, str):
        if ptype == "char" and len(value) == 1:
            return 1
        return {"java.lang.String": 5, "java.lang.CharSequence": 4, "java.lang.Comparable": 3,
                "java.io.Serializable": 3, "java.lang.Object": 2}.get(ptype)
    return None


def _select(j: Jdi, methods: Any, args: Sequence[Any], hit: Hit | None, what: str,
            sig: str | None = None) -> tuple[Any, Any]:
    """Pick the overload for `args` (or by JNI signature `sig`); return (method, java args)."""
    cands = [m for m in methods if not m.isAbstract() and not m.isBridge()] or list(methods)
    if sig is not None:
        cands = [m for m in cands if str(m.signature()) == sig]
    best: list[tuple[int, Any]] = []
    for m in cands:
        ptypes = [str(t) for t in m.argumentTypeNames()]
        if len(ptypes) != len(args):
            continue
        scores = [_arg_score(j, a, p) for a, p in zip(args, ptypes, strict=True)]
        if any(s is None for s in scores):
            continue
        best.append((sum(s for s in scores if s is not None), m))
    if not best:
        sigs = [f"{m.name()}({', '.join(str(t) for t in m.argumentTypeNames())})" for m in cands]
        raise TypeError(f"no {what} overload accepts {args!r}; candidates: {sigs}")
    best.sort(key=lambda b: -b[0])
    if len(best) > 1 and best[0][0] == best[1][0]:
        sigs = [str(m.signature()) for s, m in best if s == best[0][0]]
        raise TypeError(f"{what}: ambiguous overloads {sigs}; cast an argument as in JPype "
                        "(JInt(5), JLong(5), JDouble(1), JChar('a')) or pass _sig=<signature>")
    m = best[0][1]
    lst = ArrayList()
    for a, p in zip(args, [str(t) for t in m.argumentTypeNames()], strict=True):
        lst.add(_to_java(j, a, p, hit))
    return m, lst


class Obj:
    """A Java object in the target. Attribute access reads a field; calling an attribute
    calls the method in the thread of the Hit the object came from. When a field and a
    method have the same name (e.g. HashMap.size), the method wins; read the field with
    o._field("size"). repr() shows type and object id; it runs no code in the target."""

    __slots__ = ("_hit", "_j", "_ref")

    def __init__(self, j: Jdi, ref: Any, hit: Hit | None) -> None:
        object.__setattr__(self, "_j", j)
        object.__setattr__(self, "_ref", ref)
        object.__setattr__(self, "_hit", hit)

    @property
    def _type(self) -> str:
        return str(self._ref.referenceType().name())

    @property
    def _id(self) -> int:
        return int(self._ref.uniqueID())

    def __repr__(self) -> str:
        extra = ""
        t = self._ref.referenceType()
        if isinstance(t, ClassType) and "java.lang.Enum" in _supertypes(t):
            extra = f" {_string_field(self._ref, 'name')}"
        return f"<{self._type}#{self._id}{extra}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Obj) and self._id == other._id

    def __hash__(self) -> int:
        return self._id

    def _field(self, name: str) -> Any:
        f = self._ref.referenceType().fieldByName(name)
        if f is None:
            raise AttributeError(f"{self._type} has no field {name!r}")
        return _to_py(self._j, self._ref.getValue(f), self._hit)

    def _fields(self) -> dict[str, Any]:
        """All instance fields (also inherited ones). Runs no code in the target."""
        t = self._ref.referenceType()
        fields = [f for f in t.allFields() if not f.isStatic()]
        lst = ArrayList()
        for f in fields:
            lst.add(f)
        values = self._ref.getValues(lst)
        return {str(f.name()): _to_py(self._j, values.get(f), self._hit) for f in fields}

    def _set(self, name: str, value: Any) -> None:
        f = self._ref.referenceType().fieldByName(name)
        if f is None:
            raise AttributeError(f"{self._type} has no field {name!r}")
        self._ref.setValue(f, _to_java(self._j, value, str(f.typeName()), self._hit))

    def _call(self, name: str, *args: Any, _sig: str | None = None,
              _timeout: float = 10.0) -> Any:
        hit = self._hit
        if hit is None:
            raise RuntimeError(f"{self!r} has no thread for method calls: use h.bind(obj)")
        t = self._ref.referenceType()
        m, jargs = _select(self._j, t.methodsByName(name), args, hit, f"{self._type}.{name}",
                           _sig)
        opts = ObjectReference.INVOKE_SINGLE_THREADED
        target = m.declaringType() if m.isStatic() else self._ref

        def call() -> Any:
            return target.invokeMethod(hit.thread, m, jargs, opts)

        return hit._py(hit._invoke(call, _timeout))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        t = self._ref.referenceType()
        if list(t.methodsByName(name)):
            return lambda *a, _sig=None, _timeout=10.0: self._call(name, *a, _sig=_sig,
                                                                     _timeout=_timeout)
        if t.fieldByName(name) is not None:
            return self._field(name)
        raise AttributeError(f"{self._type} has no field or method {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        self._set(name, value)


class Arr(Obj):
    """A Java array: len(a), a[i], a[i:j], iteration."""

    __slots__ = ()

    def __len__(self) -> int:
        return int(self._ref.length())

    def __getitem__(self, i: int | slice) -> Any:
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        if i < 0:
            i += len(self)
        return _to_py(self._j, self._ref.getValue(i), self._hit)

    def __iter__(self) -> Iterator[Any]:
        for v in self._ref.getValues():
            yield _to_py(self._j, v, self._hit)

    def __repr__(self) -> str:
        return f"<{self._type}#{self._id} len={len(self)}>"


class Cls:
    """Static members of a class: attribute = static field, call = static method."""

    def __init__(self, j: Jdi, t: Any, hit: Hit | None) -> None:
        self._j = j
        self._type = t
        self._hit = hit

    def __repr__(self) -> str:
        return f"<class {self._type.name()}>"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        t = self._type
        methods = [m for m in t.methodsByName(name) if m.isStatic()]
        if methods:
            def call(*args: Any, _sig: str | None = None, _timeout: float = 10.0) -> Any:
                hit = self._hit
                if hit is None:
                    raise RuntimeError("static method calls need a thread: use h.cls(name)")
                m, jargs = _select(self._j, methods, args, hit, f"{t.name()}.{name}", _sig)
                opts = ObjectReference.INVOKE_SINGLE_THREADED
                return hit._py(hit._invoke(
                    lambda: m.declaringType().invokeMethod(hit.thread, m, jargs, opts), _timeout))
            return call
        f = t.fieldByName(name)
        if f is not None and f.isStatic():
            return _to_py(self._j, t.getValue(f), self._hit)
        raise AttributeError(f"{t.name()} has no static field or method {name!r}")

    def _fields(self) -> dict[str, Any]:
        t = self._type
        return {str(f.name()): _to_py(self._j, t.getValue(f), self._hit)
                for f in t.allFields() if f.isStatic()}

    def __call__(self, *args: Any) -> Obj:
        """Create an instance, like JPype's JClass("C")(args): h.cls("java.util.HashMap")()."""
        if self._hit is None:
            raise RuntimeError("constructors need a thread: use h.cls(name)(args)")
        return self._hit.new(str(self._type.name()), *args)


class Events:
    """Event sources and their hits. Use across pysess steps (next(), close()), iterate it,
    or use it in `with`. Moving to the next hit resumes the previous one unless it is held."""

    def __init__(self, j: Jdi, *, bps: list[str], catch: list[str], caught: bool,
                 uncaught: bool, watch: list[str], access: bool, calls: list[str],
                 run: Callable[[], Any] | None, suspend: Suspend, thread: Any,
                 instance: Obj | None, nth: int | None, timeout: float,
                 count: int | None) -> None:
        self._j = j
        self.timeout = timeout
        self.count = count
        self.hits: list[Hit] = []
        self.current: Hit | None = None
        self.closed = False
        self.requests: list[Any] = []
        self._setters: dict[str, list[Callable[[Any], bool]]] = {}  # class -> source setters
        self._resolved: set[str] = set()
        self._queue: collections.deque[Hit] = collections.deque()
        self._suspend = _POLICY[suspend]
        self._thread = j.thread(thread) if isinstance(thread, str) else (
            thread.thread if isinstance(thread, Hit) else thread)
        self._instance = instance
        self._nth = nth
        self._oid = j._register(self)
        try:
            for spec in bps:
                self._add_bp(spec)
            for exc in catch:
                self._add_catch(exc, caught, uncaught)
            for spec in watch:
                self._add_watch(spec, access)
            for pattern in calls:
                for create in (j.erm.createMethodEntryRequest, j.erm.createMethodExitRequest):
                    r = create()
                    r.addClassFilter(pattern)
                    self._enable(r)
        except BaseException:
            self.close()
            raise
        self.future: concurrent.futures.Future[Any] | None = _bg(run) if run else None

    def __repr__(self) -> str:
        state = "closed" if self.closed else "open"
        pend = f", pending: {self.pending}" if self.pending else ""
        return f"<Events {state}, {len(self.hits)} hits, current={self.current!r}{pend}>"

    # --- sources ----------------------------------------------------------------------

    def _enable(self, r: Any, filters: bool = True) -> None:
        r.setSuspendPolicy(self._suspend)
        r.putProperty(_OWNER_KEY, jpype.JInt(self._oid))
        if filters:
            if self._thread is not None:
                r.addThreadFilter(self._thread)
            if self._instance is not None:
                r.addInstanceFilter(self._instance._ref)
            if self._nth is not None:
                r.addCountFilter(self._nth)
        r.enable()
        self.requests.append(r)

    def _defer(self, cls_name: str, setter: Callable[[Any], bool]) -> None:
        """Run setter(type) for every class `cls_name` loaded now and later."""
        for t in self._j._types(cls_name):
            if setter(t):
                self._resolved.add(cls_name)
        if cls_name not in self._setters:
            r = self._j.erm.createClassPrepareRequest()
            r.addClassFilter(cls_name)
            r.setSuspendPolicy(_POLICY["thread"])  # set breakpoints before the class runs
            r.putProperty(_OWNER_KEY, jpype.JInt(self._oid))
            r.enable()
            self.requests.append(r)
            self._setters[cls_name] = []
        self._setters[cls_name].append(setter)

    @property
    def pending(self) -> list[str]:
        """Classes not loaded yet: their sources are set when they load."""
        return sorted(set(self._setters) - self._resolved)

    def _class_prepared(self, t: Any) -> None:
        name = str(t.name())
        for setter in self._setters.get(name, []):
            with contextlib.suppress(Exception):
                if setter(t):
                    self._resolved.add(name)

    def _add_bp(self, spec: str) -> None:
        if ":" in spec:
            cls_name, line_s = spec.rsplit(":", 1)
            line = int(line_s)

            def setter(t: Any) -> bool:
                types = [t] + [n for n in self._j.vm.allClasses()
                               if str(n.name()).startswith(str(t.name()) + "$")]
                for tt in types:
                    try:
                        locs = list(tt.locationsOfLine(line))
                    except AbsentInformationException:
                        continue
                    if locs:
                        self._enable(self._j.erm.createBreakpointRequest(locs[0]))
                        return True
                raise ValueError(f"{spec}: no code at line {line}")
        else:
            base, _, sig = spec.partition("(")
            cls_name, _, mname = base.rpartition(".")
            want = [s.strip() for s in sig.rstrip(")").split(",") if s.strip()] if sig else None

            def setter(t: Any) -> bool:
                methods = [m for m in t.methodsByName(mname) if m.declaringType() == t]
                if want is not None:
                    methods = [m for m in methods if [str(a) for a in m.argumentTypeNames()]
                               == want]
                methods = [m for m in methods if m.location() is not None]
                if not methods:
                    raise ValueError(f"{spec}: no method with code in {t.name()}")
                for m in methods:
                    self._enable(self._j.erm.createBreakpointRequest(m.location()))
                return True
        if not cls_name:
            raise ValueError(f"breakpoint {spec!r}: expected C.m, C.m(args) or C:line")
        self._defer(cls_name, setter)

    def _add_catch(self, exc: str, caught: bool, uncaught: bool) -> None:
        def setter(t: Any) -> bool:
            self._enable(self._j.erm.createExceptionRequest(t, caught, uncaught))
            return True

        self._defer(exc, setter)

    def _add_watch(self, spec: str, access: bool) -> None:
        cls_name, _, fname = spec.rpartition(".")

        def setter(t: Any) -> bool:
            f = t.fieldByName(fname)
            if f is None:
                raise ValueError(f"{spec}: no field {fname} in {t.name()}")
            self._enable(self._j.erm.createModificationWatchpointRequest(f))
            if access:
                self._enable(self._j.erm.createAccessWatchpointRequest(f))
            return True

        self._defer(cls_name, setter)

    # --- hits -------------------------------------------------------------------------

    def _deliver(self, hit: Hit) -> None:
        with self._j._lock:
            if self.closed:
                hit.resume()
                return
            self._queue.append(hit)
            self._j._lock.notify_all()

    def _wait_for(self, pred: Callable[[Hit], bool] | None, timeout: float) -> Hit:
        end = time.monotonic() + timeout
        lock = self._j._lock
        with lock:
            while True:
                for h in list(self._queue):
                    if pred is None or pred(h):
                        self._queue.remove(h)
                        self.hits.append(h)
                        self.current = h
                        return h
                now = time.monotonic()
                if self.future is not None and self.future.done():
                    end = min(end, now + 1.0)  # events still on the way after run() ended
                if now >= end:
                    raise TimeoutError(f"no event within {timeout}s")
                if not self._j.connected:
                    raise RuntimeError("target disconnected")
                lock.wait(min(0.2, end - now))

    def next(self, timeout: float | None = None) -> Hit:
        """Resume the current hit (unless held) and wait for the next one."""
        if self.closed:
            raise RuntimeError("Events is closed")
        if self.current is not None and not self.current.held:
            self.current.resume()
        self.current = None
        return self._wait_for(None, self.timeout if timeout is None else timeout)

    def __iter__(self) -> Iterator[Hit]:
        """Hits until `count` hits, or no event within `timeout`."""
        while self.count is None or len(self.hits) < self.count:
            try:
                yield self.next()
            except TimeoutError:
                return

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
        """Delete the requests, resume the current hit and queued hits (not held ones)."""
        if self.closed:
            return
        j = self._j
        for r in self.requests:
            with contextlib.suppress(Exception):
                j.erm.deleteEventRequest(r)
        self.requests.clear()
        self._setters.clear()
        with j._lock:
            self.closed = True
            queued = list(self._queue)
            self._queue.clear()
        if self.current is not None and not self.current.held:
            self.current.resume()
        for h in queued:
            h.resume()
        with j._lock:
            j._owners.pop(self._oid, None)

    def result(self, timeout: float = 30.0) -> Any:
        """Return value of run() (re-raises its exception); None without run."""
        return None if self.future is None else self.future.result(timeout=timeout)


class _StepOwner(Events):
    """Receives the step event of a Hit that has no Events (from Jdi.wait())."""

    def __init__(self, j: Jdi) -> None:  # noqa: D107 - minimal Events without sources
        self._j = j
        self.timeout = 10.0
        self.count = None
        self.hits = []
        self.current = None
        self.closed = False
        self.requests = []
        self._setters = {}
        self._resolved = set()
        self._queue = collections.deque()
        self.future = None
        self._oid = j._register(self)


class _Text(str):
    """str whose repr() is the text itself, so multi-line results print cleanly in pysess."""

    def __repr__(self) -> str:
        return str(self)


def call_tree(hits: Sequence[Hit]) -> str:
    """Indented call tree per thread from 'enter'/'exit' hits (events(calls=...)).

        worker-3:
          DebugTarget.handle
            DebugTarget.remove -> 'removed k2, size 9\\n'
    """
    out: list[str] = []
    by_thread: dict[str, list[Hit]] = {}
    for h in hits:
        if h.kind in ("enter", "exit"):
            by_thread.setdefault(h.thread_name, []).append(h)
    for name, hs in by_thread.items():
        out.append(f"{name}:")
        lines: list[str] = []
        open_at: list[int] = []  # index in `lines` of each open call
        for h in hs:
            label = f"{h.class_name}.{h.method}"
            if h.kind == "enter":
                open_at.append(len(lines))
                lines.append("  " * len(open_at) + label)
            elif open_at:
                i = open_at.pop()
                if h.detail != "None":
                    lines[i] += f" -> {h.detail}"
            else:
                lines.append(f"  {label} (entered before tracing) -> {h.detail}")
        out += lines
    return _Text("\n".join(out))
