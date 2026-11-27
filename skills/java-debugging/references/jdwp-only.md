# When JDWP is the only access

If the JDWP port is the only way into a JVM (no shell, no JMX), the debugger can still read JVM metrics and change log levels, because method calls can use the platform management API inside the target.
Each method call runs code in the target; use this for snapshots, not for monitoring.

JDWP access also exposes secrets: `h.cls("java.lang.System").getenv("DB_PASSWORD")`, `System.getProperty(...)` and the JVM arguments can all be read.

## Getting a thread for method calls

Method calls need a thread stopped by an event; a thread stopped with `j.vm.suspend()` or `t.suspend()` cannot run them.
On an idle JVM:

- Best: set a breakpoint on a timed wait that JDK threads call regularly, and wait without sending any request:
  ```python
  with j.events("java.lang.Object.wait(long)", timeout=60) as ev:
      h = ev.next()                # a background/timer thread, e.g. idle-timeout-task
      M = h.cls("java.lang.management.ManagementFactory")
      print(M.getMemoryMXBean().getHeapMemoryUsage().toString())
  ```
  Other candidates: `java.util.concurrent.locks.LockSupport.parkNanos(java.lang.Object,long)`, `java.lang.Thread.sleep(long)`.
- Or send a request that hits a breakpoint in the application.
- With side effects: `catch="java.lang.InterruptedException"`, then `j.thread("<idle pool thread>").interrupt()`. The interrupt can break application code, for example it closes interruptible NIO channels.

Heap queries (`j.instances`, `j.counts`, `j.retainers`) need no stopped thread.

## JVM metrics

In the table, `M` is `h.cls("java.lang.management.ManagementFactory")` (it loads the class if needed).

| Value | Expression |
|---|---|
| Heap use | `M.getMemoryMXBean().getHeapMemoryUsage().toString()` |
| Non-heap use (bytes) | `M.getMemoryMXBean().getNonHeapMemoryUsage().getUsed()` |
| Memory pools | `M.getMemoryPoolMXBeans().get(i).getName()`, `.getUsage().toString()` |
| GC count and time | `M.getGarbageCollectorMXBeans().get(i).getName()`, `.getCollectionCount()`, `.getCollectionTime()` |
| Threads, peak | `M.getThreadMXBean().getThreadCount()`, `.getPeakThreadCount()` |
| Deadlocks | `M.getThreadMXBean().findDeadlockedThreads()` (`None` if none) |
| CPU time and allocated bytes per thread | `M.getThreadMXBean().getThreadCpuTime(id)`, `.getThreadAllocatedBytes(id)`, `.getTotalThreadAllocatedBytes()` |
| Process CPU load, open file descriptors, load average | `M.getOperatingSystemMXBean().getProcessCpuLoad()`, `.getOpenFileDescriptorCount()`, `.getSystemLoadAverage()` |
| Uptime, JVM arguments | `M.getRuntimeMXBean().getUptime()`, `.getInputArguments().toString()` |
| Loaded classes, JIT time | `M.getClassLoadingMXBean().getLoadedClassCount()`, `M.getCompilationMXBean().getTotalCompilationTime()` |
| Direct and mapped buffers | `M.getPlatformMXBeans(h.cls("java.lang.Class").forName("java.lang.management.BufferPoolMXBean")).get(i).getName()`, `.getMemoryUsed()` |

- Methods of the `com.sun.management` extensions (allocated bytes, process CPU load, file descriptors) work without a cast, because the driver calls the method on the runtime class.
- Lists are `Obj`: read elements with `.get(i)`, or `.toArray()` for an `Arr` to iterate.
- GC and memory notifications cannot be received: they need a listener class.

## Changing log levels (java.util.logging)

Example: enable FINE and FINER logging for the JDK HTTP server without a restart:

```python
L, Level = h.cls("java.util.logging.Logger"), h.cls("java.util.logging.Level")
L.getLogger("com.sun.net.httpserver").setLevel(Level.ALL)
L.getLogger("").getHandlers()[0].setLevel(Level.ALL)
```

- The handler has its own level (INFO by default), so set both.
- `Logger.getLogger` holds loggers weakly (per its Javadoc): the level of a logger that no code holds can be lost after a GC.
- Other logging frameworks need their own API calls; find them with `j.classes("*Logger*")` and `[m.name() for m in j.vm.classesByName(name)[0].methods()]`.
- Set the levels back when done.
