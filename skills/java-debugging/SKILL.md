---
name: java-debugging
description: "Debug and profile a running JVM: breakpoints, state inspection, method calls, fault injection and heap references through JDI from Python; jcmd, jstat and JFR metrics, profiling and benchmarking."
---

# Java debugging and diagnostics

All tools here ship with the JDK. There are two distinct approaches:

- **Debugger (JDI over JDWP)**: Stops threads, inspects/changes state, steps, runs code, and queries the heap over TCP. Requires starting the JVM with `-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=localhost:5005` (`suspend=y` waits before `main()`; bind `localhost`, no auth), unless a dev runner enables it (e.g., `quarkus:dev` on port 5005).
- **Live observer (`jcmd`, `jstat`, `jfr` via Attach API)**: Observes without stopping threads (except full GC for heap histograms/dumps). Needs no JVM flags or restart; attaches by PID on the same host/container as the same user.

- **jstat** prints counters the JVM always keeps (heap areas, GC counts and times) at an interval. It shows that something is wrong, not which code causes it.
- **JFR** (Java Flight Recorder) records events inside the JVM to a `.jfr` file: CPU samples, allocations, lock waits, GC pauses, exceptions, I/O, with stack traces. Overhead is about 1% with `settings=default`, a few percent with `settings=profile`. `jfr view` turns the file into tables.

If only the JDWP port of a JVM is reachable (no shell, no JMX), read [references/jdwp-only.md](./references/jdwp-only.md): it shows how to read heap, GC, thread and CPU numbers and change log levels through the debugger.

Find the PID with `jcmd -l` (or `jps -l`).
In scripts, take care with `pgrep -f <name>`: it also matches the shell running the command, and two PIDs given to `jcmd` fail with `Unknown diagnostic command`.

---

# Debugger (JDI from Python)

[scripts/jdi_driver.py](./scripts/jdi_driver.py) uses JDI (`com.sun.jdi`), the JDK's debugger API, from Python through JPype, which runs a JVM inside the pysess process.

## Facts to know before you start

- The JDWP agent accepts one debugger at a time. A second attach fails with `java.net.ConnectException: Connection refused` (as does a port without an agent; check the JVM arguments with `jcmd <pid> VM.command_line`).
- Attach and detach do not restart the target. Detaching (`j.close()`, or the end of the pysess session) clears all breakpoints and resumes all threads.
- Method calls (`obj.toString()`, `map.get(k)`) run code in the target. Call only methods without side effects. Field reads, `repr()` of objects, `h.locals`, `o._fields()` run no code.
- Method calls need a thread stopped by an event (a breakpoint, step, exception or watch hit). They run in that thread only, so they deadlock if the method needs a lock that another suspended thread holds. Calls time out (default 10 s, `_timeout=` to change); resume the lock holder and the call finishes.
- Local variables need classes compiled with `-g` (Maven and Gradle do this by default). Otherwise `h.frame().args` gives the arguments by position.

## Start a session

Run the driver in a pysess session (see the [pysess skill](../pysess/SKILL.md)).
The first run installs JPype (pinned in `JPYPE_REQUIREMENT`) into the shared pysess venv `java-debugging`, which needs network access and a longer timeout.
JPype finds the JDK from `JAVA_HOME` or `java` on `PATH`; it needs a full JDK (module `jdk.jdi`).

```bash
pysess new -n jdi -t 120 <skill-dir>/scripts/jdi_driver.py
pysess exec jdi -t 30 <<'EOF'
import pysess, urllib.request, urllib.error
j = Jdi("localhost:5005")
def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:   # keep 4xx/5xx as a result, not an exception
        return e.code, e.read().decode()
EOF
```

A process can start only one JVM. After editing the driver, `j.close()` and load the file again (`pysess exec jdi <file>`); the JVM keeps running. `pysess cancel` interrupts waits and method calls; the call itself still finishes in the target.

## Events and hits

`j.events(...)` sets event sources, optionally starts `run` in the background, and returns an `Events`.
Each event arrives as a `Hit`: the thread stays suspended until the hit is resumed.
Moving to the next hit (`ev.next()`, or the next loop iteration) resumes the previous one; `h.hold()` keeps it suspended.
`ev.close()` (or the end of a `with` block) deletes the sources and resumes the current and queued hits, except held ones.

```python
# scripted, one pysess step
with j.events("com.example.OrderService.place", run=lambda: get("/orders/1")) as ev:
    for h in ev:
        print(h, h.stack(5, include="com.example"), h.locals)
print(ev.result())                      # return value of run()

# interactive, across pysess steps
ev = j.events("com.example.OrderService.place", run=lambda: get("/orders/1"))
h = ev.next()                           # step 2: h.locals, h.this, h.frame(1).locals ...
h2 = h.step()                           # step 3: next line
ev.close(); ev.result()
```

| `j.events(...)` argument | Meaning |
|---|---|
| `"C.m"`, `"C.m(int,java.lang.String)"`, `"C:line"` | Breakpoints. `C.m` covers all overloads declared in `C` (not inherited ones); nested classes are `Outer$Inner`. A line without code in `C` is looked up in loaded nested classes of `C`. |
| `catch="X"`, `caught=`, `uncaught=` | Stop where exception `X` (or a subclass) is thrown. |
| `watch="C.f"`, `access=True` | Stop when the field is written (also read with `access`). |
| `calls="com.example.*"` | Method entry and exit events for matching classes (`exit` hits carry the return value). Use with `thread=` and `suspend="none"`. |
| `suspend=` | `"thread"` (default: only the hitting thread stops), `"all"` (the VM stops), `"none"` (record only). |
| `thread=`, `instance=`, `nth=` | Report only events in this thread (name, `ThreadReference` or `Hit`), only where `this` is this object, or only the nth occurrence. |
| `run=`, `timeout=`, `count=` | Background action; seconds `next()` waits (iteration ends when it expires, or 1 s after `run` returned); maximum hits when iterating. |

Classes that are not loaded yet get their sources when they load, in every classloader (`ev.pending` lists them).
Two sources at the same location produce one hit.
Iterating ends with the hits resumed: inspect each hit inside the loop. `list(ev)` gives resumed hits, whose `location`, `thread_name`, `kind` and `detail` stay readable.

| `Hit` | Does |
|---|---|
| `h.kind`, `h.location`, `h.class_name`, `h.method`, `h.line`, `h.thread_name`, `h.detail` | `breakpoint`, `step`, `exception`, `watch`, `access`, `enter`, `exit`; `Class.method:line`; exception and catch site, `field: old -> new`, or return value. |
| `h.locals`, `h.this`, `h.frame(n)` | Locals and arguments of frame 0, `this`; frame `n` with `.locals`, `.this`, `.args`, `.location`, `.set(name, value)`. |
| `h.stack(depth, include="com.example")` | Frames as `Class.method:line`, optionally only classes with a prefix. |
| `h.exception`, `h.value` | Thrown exception; return value (`exit`) or new field value (`watch`). |
| `h.cls("C")`, `h.cls("C")(*args)`, `h.bind(obj)` | Static fields and methods (loads the class if needed), like JPype's `JClass`; calling it creates an object; make method calls on `obj` run in this thread. |
| `h.step("over"\|"into"\|"out")` | Resume for one line (into skips JDK classes) and return the step's hit. A step that does not complete within its timeout is cancelled. |
| `h.force_return(value)`, `h.throw("C", "msg")` | Fault injection, see below. |
| `h.hold()`, `h.resume()` | Keep suspended when the Events moves on or closes; resume now. |
| `h.thread`, `h.event` | Raw JDI `ThreadReference` and `Event`. |

| `Jdi` | Does |
|---|---|
| `j.threads()`, `j.thread(name)` | `{name: status}`; the `ThreadReference`. |
| `j.classes("*Cache*")`, `j.cls("C")` | Loaded class names; static fields (no thread needed). |
| `j.instances("C", max)`, `j.counts("C", ...)` | Live instances, live instance counts (all classloaders). No code runs; the target pauses while the heap is walked. |
| `j.referrers(obj)`, `j.retainers(obj)` | Objects that refer to `obj`; reference chains from a static field down to `obj`. |
| `j.redefine("C", "C.class")` | Hot swap: changed method bodies take effect at once; adding methods fails. |
| `j.resume()`, `j.close()` | Resume all threads; detach. |
| `j.vm`, `j.erm`, `j.wait(timeout)` | Raw `VirtualMachine` and `EventRequestManager`; hits of requests created with raw JDI. |
| `call_tree(ev.hits)` | Indented call tree per thread from `calls=` hits. |

## Values and method calls

- Values convert to Python: primitives, `str` for strings, boxed numbers unboxed, `None` for null. Other objects are `Obj`, arrays `Arr` (`len`, index, slices, iteration).
- `o.name` reads a field; `o.name(args)` calls a method in the thread of the hit the object came from. When a field and a method have the same name (`HashMap.size`), the method wins: read the field with `o._field("size")`.
- `o._fields()` reads all fields, `o._set(name, value)` (or `o.name = value`) assigns, `o._type` is the class name, `o._id` the object id. Equal ids mean the same object (`o1 == o2`); no code runs to get them.
- `repr(o)` shows `<type#id>` (enums add the constant name) and runs no code; call `o.toString()` for the Java string.
- The object model follows JPype, so JPype habits apply: `o.method(args)`, `o.field`, arrays with `len`/index/slices, `h.cls("java.util.HashMap")()` like `JClass("java.util.HashMap")()`, and JPype's casts (`JInt`, `JLong`, `JShort`, `JByte`, `JChar`, `JFloat`, `JDouble`, `JBoolean`, `JString`, loaded by the driver) to select an overload: `sb.append(JInt(1))`.
- Arguments convert from Python: `str`, `int` (prefers `int`, then `long`), `float`, `bool`, `None`, `Obj`; numbers are boxed for `Object` parameters (a cast boxes as its type: `map.put("k", JLong(5))`). Ambiguous overloads raise `TypeError` listing the JNI signatures: cast an argument, or pass `_sig="(I)Ljava/lang/String;"`.
- Unlike JPype, strings come back as `str` and boxed numbers as `int`/`float`/`bool`. The box type is lost: pass `JLong(v)` to give a `Long` back to Java.
- `from java.lang import System` and `jpype.JClass(...)` are JPype's own JVM inside the debugger, not the target: `System.getProperty("user.dir")` returns the debugger's value. Reach the target only through `j` and hits (`h.cls("java.lang.System")`).
- An exception thrown by the called method raises `JavaException` (`.type`, `.message`, `.obj`).
- Objects returned by calls are kept from garbage collection until the hit resumes. Objects from `j.instances()` have no thread: `h.bind(o)` before calling methods.
- Static calls: `h.cls("java.lang.System").identityHashCode(o)`. `h.cls` loads a class that is not loaded yet with `Class.forName(name, true, <system class loader>)`; classes of other classloaders must already be loaded.

## Workflow: which code path does a request take

```python
with j.events("com.example.Cache.get", "com.example.Db.load", run=lambda: get("/x")) as ev:
    path = [(h.location, h.stack(4, include="com.example")) for h in ev]

with j.events(calls="com.example.*", suspend="none", run=lambda: get("/x")) as ev:
    hits = list(ev)                     # iterate: hits that were not taken are dropped on close
print(call_tree(hits))                  # every call of com.example classes, with return values
```

Each method event crosses JDWP (about 0.5 ms per event with `suspend="none"`): narrow the class pattern, and filter by `thread=` when other requests run.

## Workflow: conditional breakpoint, object identity

```python
ev = j.events("com.example.Registry.visit", run=lambda: get("/iterate"))
h = next(h for h in ev if h.locals["e"].getKey() == "k5")      # stops at the matching hit
cache = h.this.cache                                             # Obj; compare with == later
ev.close()
with j.events("com.example.Cache.put", instance=cache, run=lambda: get("/y")) as ev2:
    print([h.locals for h in ev2])      # only puts into that cache object
```

## Workflow: force a race

Hold one thread at a chosen point, run the conflicting request, then let the first thread continue.

```python
ev = j.events("com.example.Registry.visit", run=lambda: get("/iterate"))
a = next(h for h in ev if h.locals["e"].getKey() == "k5").hold()
ev.close()                              # breakpoint gone, thread A stays at the hit
print(get("/remove?key=k8"))            # thread B changes the map while A waits
with j.events(catch="java.util.ConcurrentModificationException") as cme:
    a.resume()
    x = cme.next(); print(x, x.stack(5))
print(ev.result())
```

## Workflow: fault injection

- `h.force_return(value)` makes the method of frame 0 return `value` when the thread resumes. At a method breakpoint the body does not run: `force_return(None)` for a lookup that finds nothing, `force_return(False)` for a failed check, any value of the return type.
- `h.throw("java.io.IOException", "injected")` throws in the thread and returns the hit at the throw site. JVM side effect: delivering the exception also interrupts the thread, and code that later checks the interrupt fails too. Interruptible I/O fails, for example NIO channels close and an HTTP response is lost. Prefer `force_return` of a value that the application turns into an error.

## Workflow: memory leak

```python
j.counts("byte[]", "com.example.Session")     # repeat before and after the load
big = [b for b in j.instances("byte[]", max=20000) if len(b) == 1 << 20]
j.retainers(big[0])
# ['DebugTarget.retained (static) -> java.util.ArrayList.elementData -> java.lang.Object[][1] -> byte[]#3374']
```

`j.retainers` walks referrers up to `depth` levels and `width` referrers per object. Chains that do not reach a static field within the depth end with `...`. Heap queries need no stopped thread; they pause the target while they walk the heap (`instances("byte[]", max=20000)` takes about 1 s).

## Locks and hangs

- Without a debugger, `jcmd <pid> Thread.print` (or `jstack <pid>`) gives all stacks with lock info and deadlock detection, without stopping anything. For contention over time, use JFR `contention-by-site`.
- With JDI, for a suspended thread (a hit, or after `j.vm.suspend()`): `t.ownedMonitors()`, `t.currentContendedMonitor()`, `t.frames()`, `m.owningThread()`, `m.waitingThreads()` on the raw `ThreadReference`/`ObjectReference`. `j.vm.resume()` afterwards.

## Pitfalls

- Frames change after every method call and become invalid when the thread resumes: the driver reads them on access; do not keep raw `StackFrame` objects.
- `watch` with `suspend="none"`: the old value is read after the thread moved on and can show the new value.
- `h.locals` at the first line of a method shows only the arguments; locals appear after they are assigned (`h.step()`).
- A raw step request (not `h.step`) on a thread that does not run stays pending and fires later in another request.
- Setting a local that the current expression already loaded (`visited += visit(e)` while in `visit`) has no effect on that expression.

## Recovery

- Target hangs: threads are still suspended (held hits, or a `suspend="all"` hit). `j.resume()`, or `j.close()`; ending the pysess session also detaches.
- `pysess kill jdi` ends the session and its JVM; the target clears all requests and resumes.

---

# Metrics and profiling (jcmd, jstat, JFR)

`jcmd <pid> help` lists every command for that JVM; `jcmd <pid> help <command>` shows its options.
Other useful commands: `VM.flags`, `VM.version`, `VM.uptime`, `VM.system_properties`, `VM.command_line`.
Native (off-heap) memory: `jcmd <pid> VM.native_memory summary`; needs `-XX:NativeMemoryTracking=summary` at JVM start.

## Snapshots with jcmd and jstat

```bash
jcmd $PID GC.heap_info                    # heap regions, committed and used
jcmd $PID GC.class_histogram | head -15   # objects per class (forces a full GC)
jcmd $PID Thread.print                    # all stacks, locks, deadlock detection
jstat -gcutil $PID 1000 10                # GC use in percent, every 1 s, 10 times
```

`jstat -gcutil` columns: `E` eden (new objects), `O` old generation (objects that survived several GCs), `M` metaspace (class metadata), all in percent used; `YGC`/`FGC` young and full GC counts; `GCT` total GC seconds.
Rising `O` after each young GC, and rising `FGC`, point to a leak or a too small heap.

`GC.class_histogram` and `GC.heap_dump` run a full GC and pause the JVM.

## Profiling with JFR

```bash
jcmd $PID JFR.start name=r settings=profile filename=/tmp/r.jfr
# ... run the load ...
jcmd $PID JFR.dump name=r        # writes the file, recording continues
jcmd $PID JFR.stop name=r
jfr view hot-methods /tmp/r.jfr
```

- `jcmd $PID JFR.view hot-methods` shows a view of the running recording without writing a file.
- `jfr summary file.jfr` lists event counts. `jfr view --width 160 <view> file.jfr` gives wider columns.
- `jfr help view` lists all views. Useful ones: `hot-methods`, `cpu-time-hot-methods` (JDK 25+), `allocation-by-site`, `allocation-by-class`, `contention-by-site`, `gc`, `gc-pauses`, `exception-by-site`, `thread-cpu-load`, `cpu-load`, `latencies-by-type`, `socket-reads-by-host`.
- For `exception-by-site` on JDK 21 (where `settings=profile` only records `Error` throws by default), pass `exceptions=all` to `JFR.start`.
- `jfr print --events <type> file.jfr` prints raw events with stack traces, for example `jdk.MethodTrace`.

## Method timing and tracing (JDK 25+)

JFR can time and count calls to chosen methods, without code changes or a debugger:

```bash
jcmd $PID JFR.start name=mt method-timing='com.example.Foo::bar;com.example.Baz' \
     method-trace='com.example.Foo::slow' filename=/tmp/mt.jfr
jcmd $PID JFR.dump name=mt && jcmd $PID JFR.stop name=mt
jfr view method-timing /tmp/mt.jfr   # invocations, min, average, max time per method
jfr view method-calls /tmp/mt.jfr    # callers of traced methods
jfr print --events jdk.MethodTrace /tmp/mt.jfr   # each call with duration and stack
```

A filter is `Class::method`, a class, or `@Annotation`.
The JVM instruments the bytecode of the named methods, so keep the filter narrow.

## Memory leaks

```bash
jcmd $PID JFR.start name=leak settings=profile memory-leaks=gc-roots
# ... run the load that leaks ...
jcmd $PID JFR.dump name=leak filename=/tmp/leak.jfr && jcmd $PID JFR.stop name=leak
jfr view memory-leaks-by-site /tmp/leak.jfr
```

The view lists sampled objects that are still alive, with the allocating method.
`memory-leaks=gc-roots` is required; without it, the view is empty.
A simpler check: run `GC.class_histogram` before and after the load and compare the counts.

## Benchmarking

1. Detach the debugger: breakpoints and traces make the measured code much slower. Warm up: run the load once for 10-30 s and discard the results. The JIT compiles hot code during the first seconds, so early numbers are slow and vary.
2. Record the baseline: start JFR, run the load for a fixed duration and concurrency, save the client numbers (requests per second, p50/p95/p99 latency, errors) and `jstat -gcutil` before and after.
3. Change one thing, repeat step 2 with the same load, compare.
4. Repeat each run at least 3 times. Report the spread, not only one number. Do not compare runs where the machine had other load.
5. Use JFR views to explain the difference: `hot-methods`, `allocation-by-site`, `gc`, `contention-by-site`.

Load generators (see the [echoclient skill](../echoclient/SKILL.md); `ab` also works):

```bash
go run github.com/tsaarni/echoclient/cmd/echoclient@latest get \
  -url "http://127.0.0.1:8080/api/items" -concurrency 4 -duration 30s
ab -n 1000 -c 4 "http://127.0.0.1:8080/api/items"
```

## Access from another host

jcmd, jstat and jfr need a shell on the host of the JVM.
If you can run commands there (for example over ssh, or by exec into a container), run `jcmd` there and copy JFR files back.
The debugger works over any TCP path to the JDWP port, for example an ssh tunnel or `kubectl port-forward`; see also [references/jdwp-only.md](./references/jdwp-only.md).

Without a shell on the host, use JMX. It gives heap, GC, thread and CPU metrics, runs jcmd commands (`DiagnosticCommand` MBean) and controls JFR recordings (`FlightRecorder` MBean). Clients: `jconsole`, or a small Java program.
Turn it on with JVM startup options (or in `JAVA_TOOL_OPTIONS`):

```
-Dcom.sun.management.jmxremote.port=9010 -Dcom.sun.management.jmxremote.rmi.port=9010
-Dcom.sun.management.jmxremote.host=127.0.0.1 -Djava.rmi.server.hostname=127.0.0.1
-Dcom.sun.management.jmxremote.authenticate=false -Dcom.sun.management.jmxremote.ssl=false
```

- Use the same value for both ports, so one tunnel or port forward is enough.
- Set `java.rmi.server.hostname` to the address the client connects to. Without it, the server tells the client to reconnect to the address of the host name (for example, `Connection refused to host: 127.0.1.1`).
- Without authentication and SSL, anyone who reaches the port controls the JVM. Bind to localhost and use a tunnel, or turn on authentication.
- With a shell on the host, `jcmd <pid> ManagementAgent.start jmxremote.port=...` starts the same agent at runtime, and `ManagementAgent.stop` stops it.
