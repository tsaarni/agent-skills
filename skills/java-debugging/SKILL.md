---
name: java-debugging
description: "Debug and profile a running JVM: jdb breakpoints and state inspection, jcmd, jstat and JFR metrics, profiling and benchmarking."
---

# Java debugging and diagnostics

All tools here ship with the JDK and work on a JVM that is already running.

- **jdb** connects to the JDWP agent over a TCP port. The JVM must start with the agent (see [Debugger](#debugger-jdb)). jdb can stop threads, read and change state, and run code in the target. Use it to confirm which code path runs, which object a method gets, and what a field holds.
- **jcmd, jstat, jfr** use the local attach API by PID. They need no JVM options, but must run on the same host (or in the same container) as the target, as the same user. They only observe: they do not stop threads, except a full GC for heap histograms and dumps.
- **jstat** prints counters the JVM always keeps (heap areas, GC counts and times) at an interval. It shows that something is wrong, not which code causes it.
- **JFR** (Java Flight Recorder) records events inside the JVM to a `.jfr` file: CPU samples, allocations, lock waits, GC pauses, exceptions, I/O, with stack traces. Overhead is about 1% with `settings=default`, a few percent with `settings=profile`. `jfr view` turns the file into tables.

If only the JDWP port of a JVM is reachable (no shell, no JMX), read [references/jdwp-only.md](./references/jdwp-only.md): it shows how to read heap, GC, thread and CPU numbers and change log levels through jdb.

Find the PID with `jcmd -l` (or `jps -l`).
In scripts, take care with `pgrep -f <name>`: it also matches the shell running the command, and two PIDs given to `jcmd` fail with `Unknown diagnostic command`.

---

# Debugger (jdb)

The target JVM must run with the JDWP agent:

```
-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=localhost:5005
```

`suspend=y` makes the JVM wait for a debugger before `main()`.
Bind to `localhost`: JDWP has no authentication, and anyone who can connect can run code in the JVM.
Check that the port is open with `ss -ltnp | grep 5005`.

## Facts to know before you start

- The JDWP agent accepts one debugger at a time. A second attach fails with `java.net.ConnectException: Connection refused`.
- Attach and detach do not restart the target. Detaching (`exit`, or killing jdb) clears all breakpoints and resumes all threads.
- On attach, jdb sets `catch uncaught java.lang.Throwable`. An uncaught exception in any thread then stops the JVM. Run `ignore uncaught java.lang.Throwable` if you do not need it.
- `print` of a method call, and `print obj` (which calls `toString()`), runs code in the target. Call only methods without side effects.
- Do not print objects that can hold secrets (keys, passwords, tokens). Print `keySet()` or `size()` instead.
- Local variables need classes compiled with `-g` (Maven and Gradle do this by default). JDK classes show `No local variables`; read their fields with `dump this`.

## Drive jdb from a persistent pysess session

jdb is interactive and each bash call is a new process, so run jdb inside a pysess session (see the [pysess skill](../pysess/SKILL.md)).
Start the session with the helper [scripts/jdb_driver.py](./scripts/jdb_driver.py) loaded, then attach.
The helper waits for the jdb prompt after each command, so commands and output stay in sync.

```bash
pysess new -n jdb <skill-dir>/scripts/jdb_driver.py
pysess exec jdb -t 30 <<'EOF'
import pysess, urllib.request, urllib.error
j = Jdb("localhost:5005")
def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:   # keep 4xx/5xx as a result, not an exception
        return e.code, e.read().decode()
EOF
```

| Call | Does |
|---|---|
| `j.cmd("...")` | Send one jdb command, return its output. |
| `j.p("expr")` | `print expr`, return only the value. Resumes other threads after a method call (see pitfalls). |
| `j.wait_hit(timeout)` | Wait for the next breakpoint, watchpoint, exception or step event. Select its thread, store it in `j.thread`, return the event line. |
| `j.cont()` | Resume the thread of the last event. |
| `j.collect(exprs, count, keep)` | Logging or conditional breakpoint: at each event evaluate `exprs` and resume, until `keep(values)` is true. |
| `j.threads()` | `{name: (id, status)}`. |
| `j.select(name)` | Make a thread current. |
| `j.release_others()` | Resume every thread except the current one. |
| `j.field_ref(obj, "field")` | `instance of X(id=N)` for a field, from `dump`. |
| `j.where(depth)` | Top of the stack of the current thread. |
| `j.drain()` | Forget events printed so far. |
| `j.cleanup()` | Clear breakpoints, resume all threads, detach. |

A breakpoint blocks the request that hits it, and a pysess session runs one step at a time.
Send the request with `pysess.bg` (see the pysess skill), then call `wait_hit()`; read the response later with `.result(timeout)`.
Give the client a timeout longer than you will spend inspecting: if it times out, the server finishes the request after resume, but the response is lost.

```python
j.cmd("stop thread in com.example.OrderService.place")
order = pysess.bg(get, "/orders/1")
print(j.wait_hit())
print(j.where(4))
print(j.cmd("locals"))
j.cmd("clear com.example.OrderService.place")
j.cont()
print(order.result(timeout=10))
```

## Breakpoints and other events

| Command | Suspends | Notes |
|---|---|---|
| `stop thread in C.m` / `stop thread at C:line` | The hitting thread | Default choice. Other requests keep running. |
| `stop go in C.m` | Nothing | Prints `Breakpoint hit` and continues. Cost about 20 µs per hit. |
| `stop in C.m` | All threads | Other requests hang until resume. |
| `stop <thread_id> in C.m` | All threads | Fires only for that thread. |
| `catch caught\|uncaught\|all <Exception>` | All threads | Stops where the exception is thrown, with the throwing stack. |
| `watch [access\|all] C.field` | All threads | Stops when the field is assigned (`all`: also read). Prints old and new value. |
| `trace go methods` | Nothing | Prints every method entry and exit with return values. About 40 µs per event: a request with many small calls became 900 times slower in a test. Set `exclude java.*,javax.*,jdk.*,sun.*,com.sun.*` first. |

Notes (verified on JDK 25):

- jdb has no conditional breakpoints. Use `j.collect(...)` with a `keep` function.
- For overloaded methods, give the signature: `stop thread in C.m(java.lang.String,int)`.
- `clear` without arguments lists breakpoints. Watches are not listed; remove them with `unwatch`.
- `catch` on a class that is not loaded yet is deferred and set when the class loads.
- A watchpoint fires on assignments to the field only, not on changes inside the object it points to (for example `map.remove()`).
- Avoid: `watch go` (printed wrong values), `trace go methods <thread_id>` (printed nothing), `trace method exit` (reports a callee's exit), `monitor` (does not run for `stop thread` or `stop go` events; use `j.collect`).

## Thread selection and resume pitfalls

- `stop thread in` does not make the stopped thread current. Commands then fail with `No thread specified`. `j.wait_hit()` selects the thread from the event line.
- After the breakpoint is cleared, `threads` no longer shows that thread as `at breakpoint`. Remember the thread from the event (`j.thread`).
- `cont` answers `Nothing suspended` when only one thread is suspended and it is not current. `resume <thread_id>` always works; `j.cont()` does this.
- Any method call in an expression (also `print obj`) leaves all threads suspended afterwards, even at a `stop thread` breakpoint: the target stops serving. It is a VM-wide suspend, so threads that start later (for example a new pool worker for the next request) also start suspended. Resuming threads one by one does not undo it; `resume` without arguments does. `j.p()` does `suspend <current>` then `resume`, so only the current thread stays stopped. `locals`, `dump` and reads of primitive or String fields and variables do not need this.
- Method calls need a thread stopped by an event. After the `suspend` command, they fail with `Thread not suspended for method invoke`.
- A `step` or `next` whose thread does not run (for example a parked pool thread) stays pending. It fires later, when that thread handles a request, and suspends all threads. Only detaching removes it.

## Reading state

- Arguments and locals by name: `locals`, `print n`. At method entry the locals list is empty; stop at a later line or use `next`.
- Fields: `print this.field`, `print Class.staticField`, `dump obj`. Private fields and methods are accessible.
- Method calls with arguments, also static: `print this.map.get("k").getConfig().keySet()`, `print java.lang.System.identityHashCode(obj)`.
- Only loaded classes can be named. `print java.lang.Class.forName("java.lang.management.ManagementFactory")` loads a class so later expressions can use it.
- Stack: `where`, `where all`, `up n`, `down n`. Lambdas and generated classes show `(null)` as the line.
- Source: `list` shows `Source file not found` until you set the path: `use src/main/java:other/src/main/java`. `use` replaces the whole path.
- Discovery: `classes` (all loaded classes), `methods C`, `fields C`.

Expression language (verified on JDK 25):

| Works | Does not work |
|---|---|
| Arithmetic, string concatenation, `?:`, comparisons | `instanceof` (`operation not yet supported`) |
| Casts: `(java.lang.Object) x`, `(long) 3` | Class literals: `java.lang.String.class` (use `java.lang.Class.forName("...")`) |
| `new C(args)`, chained calls | Array creation: `new String[]{"x"}`, `new int[3]` |
| Array index and `.length`: `s.split(",")[1]` | Lambdas and method references |
| Assignment: `print C.field = 1` (also `set`) | Varargs calls with loose arguments: `String.format("%s", "a")`, `Path.of("/a", "b")` |
| Static calls with a fixed number of arguments: `java.util.List.of("a", "b")` | |

For varargs, pass an array that an existing method returns: `java.lang.String.format("%s-%s", "a,b".split(","))`.
Methods that need an `Object[]` of mixed types (for example `MBeanServer.invoke`) cannot be called.

Object identity: compare the JDI object id from `dump` or `locals`, for example `HashMap(id=1504)`.
The same id means the same object, and no code runs in the target to get it.
The default `toString()` (`Foo@7d9864bc`) also works if the class does not override it.
Do not use `hashCode()` of collections: it is computed from the content.

## Changing the running program

| Command | Effect |
|---|---|
| `set n = 5`, `set C.field = 1000` | Changes a local, argument or field. The program continues with the new value. |
| `step`, `next`, `step up`, `stepi` | Step into, over, out of the method, one bytecode. `step up` does not show the return value. |
| `pop` | Drops the current frame; the caller calls the method again, with its side effects. |
| `redefine C /path/C.class` | Hot swap: replaces the class bytecode. Changed method bodies take effect at once. Adding a method fails: `UnsupportedOperationException: add method not implemented`. |
| `interrupt <thread_id>` | Interrupts a thread; a waiting thread gets `InterruptedException`. |

## Locks and hangs

- `suspend` stops all threads; then `threads` shows `waiting in a monitor` for blocked threads.
- `threadlocks <thread_id>` shows owned monitors and the monitor a thread waits for.
- `lock <expr>` (needs a thread stopped by an event) shows the owner and the waiting threads of an object's monitor.
- `where all` dumps all stacks while suspended. Resume with `resume`.
- Without a debugger, `jcmd <pid> Thread.print` (or `jstack <pid>`) gives all stacks with lock info and deadlock detection, without stopping anything. For contention over time, use JFR `contention-by-site`.

## Workflow: confirm which object reaches which code path

1. Read the source and choose methods to stop in. Instance methods of the class that holds the state let you use `this.<field>`.
2. Bring the target into a known state (for example, invalidate a cache).
3. Set `stop thread in` at the first method, trigger the request with `pysess.bg`, call `wait_hit()`.
4. Record the object ids (`dump`, `locals`).
5. `clear` that breakpoint, set the next, `j.cont()`, `wait_hit()`, compare ids.
6. For a method that runs many times, use `j.collect(["this.id"], count=50, keep=lambda v: v[0] == '"x"')`.
7. When done, `j.cleanup()`. Check that no jdb is left: `pgrep -af 'jdb -attach'`.

## Workflow: force a race

Breakpoints that suspend only one thread let you choose the interleaving, instead of running many requests and hoping for a failure.

1. Set `stop thread in` at a method that runs while thread A holds the shared state (for example inside an iteration loop).
2. Trigger A with `pysess.bg`. Use `j.collect(...)` to let it run until it reaches the point you want, then `clear` the breakpoint. A stays suspended.
3. Run the conflicting request (thread B) in the foreground. It completes while A waits.
4. Set `catch caught <Exception>` for the failure you expect, then `j.cont()` to resume A.
5. `j.wait_hit()` and `j.where(5)` show the stack at the throw. All threads are suspended now.
6. `ignore caught <Exception>`, `j.cmd("resume")`, then read A's response with `a.result()`.

```python
j.cmd("stop thread in com.example.Registry.visit")
a = pysess.bg(get, "/iterate")
j.collect(["e.getKey()"], count=20, keep=lambda v: v[0] == '"k5"')
j.cmd("clear com.example.Registry.visit")
print(get("/remove?key=k8"))
j.cmd("catch caught java.util.ConcurrentModificationException")
j.cont()
print(j.wait_hit()); print(j.where(5))
j.cmd("ignore caught java.util.ConcurrentModificationException")
j.cmd("resume")
print(a.result(timeout=10))
```

## Recovery

- Target hangs after debugging: threads are still suspended. Attach and run `resume`, or kill jdb. Detaching resumes all threads.
- `pysess kill` stops the session; check with `pgrep -af 'jdb -attach'` that jdb is gone too.
- `Connection refused` on attach: another debugger is attached, or the JVM has no JDWP agent (check the JVM arguments with `jcmd <pid> VM.command_line`).

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
A heap dump contains all data in memory, including secrets; keep it local and delete it after use.

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
- `jfr help view` lists all views. Useful ones: `hot-methods`, `cpu-time-hot-methods`, `allocation-by-site`, `allocation-by-class`, `contention-by-site`, `gc`, `gc-pauses`, `exception-by-site`, `thread-cpu-load`, `cpu-load`, `latencies-by-type`, `socket-reads-by-host`.
- `jfr print --events <type> file.jfr` prints raw events with stack traces, for example `jdk.MethodTrace`.
- A recording includes the environment variables of the process (event `jdk.InitialEnvironmentVariable`), so it can contain secrets.

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
Without `memory-leaks=gc-roots` the view was empty in a test.
A simpler check: run `GC.class_histogram` before and after the load and compare the counts.

## Benchmarking

1. Detach jdb: breakpoints and traces make the measured code much slower. Warm up: run the load once for 10-30 s and discard the results. The JIT compiles hot code during the first seconds, so early numbers are slow and vary.
2. Record the baseline: start JFR, run the load for a fixed duration and concurrency, save the client numbers (requests per second, p50/p95/p99 latency, errors) and `jstat -gcutil` before and after.
3. Change one thing, repeat step 2 with the same load, compare.
4. Repeat each run at least 3 times. Report the spread, not only one number. Do not compare runs where the machine had other load.
5. Use JFR views to explain the difference: `hot-methods`, `allocation-by-site`, `gc`, `contention-by-site`.

Load generators (see the echoclient skill; `ab` also works):

```bash
go run github.com/tsaarni/echoclient/cmd/echoclient@latest get \
  -url "http://127.0.0.1:8080/api/items" -concurrency 4 -duration 30s
ab -n 1000 -c 4 "http://127.0.0.1:8080/api/items"
```

## Access from another host

jcmd, jstat and jfr need a shell on the host of the JVM.
If you can run commands there (for example over ssh, or by exec into a container), run `jcmd` there and copy JFR files back.
jdb works over any TCP path to the JDWP port, for example an ssh tunnel; see also [references/jdwp-only.md](./references/jdwp-only.md).

Without a shell on the host, use JMX. It gives heap, GC, thread and CPU metrics, runs jcmd commands (`DiagnosticCommand` MBean) and controls JFR recordings (`FlightRecorder` MBean). Clients: `jconsole`, or a small Java program.
Turn it on with JVM startup options (or in `JAVA_TOOL_OPTIONS`):

```
-Dcom.sun.management.jmxremote.port=9010 -Dcom.sun.management.jmxremote.rmi.port=9010
-Dcom.sun.management.jmxremote.host=127.0.0.1 -Djava.rmi.server.hostname=127.0.0.1
-Dcom.sun.management.jmxremote.authenticate=false -Dcom.sun.management.jmxremote.ssl=false
```

- Use the same value for both ports, so one tunnel or port forward is enough.
- Set `java.rmi.server.hostname` to the address the client connects to. Without it, the server tells the client to reconnect to the address of the host name (seen: `Connection refused to host: 127.0.1.1`).
- Without authentication and SSL, anyone who reaches the port controls the JVM. Bind to localhost and use a tunnel, or turn on authentication.
- With a shell on the host, `jcmd <pid> ManagementAgent.start jmxremote.port=...` starts the same agent at runtime, and `ManagementAgent.stop` stops it.
- Attaching by PID with the Attach API and `startLocalManagementAgent()` starts the local agent. It listens on all interfaces, cannot be stopped, and stays until the JVM exits.
