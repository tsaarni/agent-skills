# When JDWP is the only access

If the JDWP port is the only way into a JVM (no shell, no JMX), jdb can still read JVM metrics and change log levels, because expressions can call the platform management API inside the target.
Each method call pauses the JVM briefly, so use this for snapshots, not for monitoring.

JDWP access also exposes secrets: `print java.lang.System.getenv("DB_PASSWORD")`, `System.getProperty(...)` and the JVM arguments can all be read.

## Getting a thread for method calls

Method calls need a thread stopped by an event; after `suspend` they fail with `Thread not suspended for method invoke`.
On an idle JVM:

- Best: set a breakpoint on a timed wait that JDK threads call regularly, and wait without sending any request:
  ```python
  j.cmd("stop thread in java.lang.Object.wait(long)")
  print(j.wait_hit(60))            # waits for a background/timer thread to hit wait()
  j.cmd("clear java.lang.Object.wait(long)")
  # ... j.p(...) calls ...
  j.cont()
  ```
  Other candidates: `java.util.concurrent.locks.LockSupport.parkNanos(java.lang.Object,long)`, `java.lang.Thread.sleep(long)`. Overloaded JDK methods need the signature.
- Or send a request that hits a breakpoint in the application.
- With side effects: `catch caught java.lang.InterruptedException`, then `interrupt <thread_id>` of an idle pool thread. The interrupt can break application code, for example it closes interruptible NIO channels.
- Does not work: `suspend`, `thread <id>`, `step`. A step on a thread that does not run stays pending (see the pitfalls in [SKILL.md](../SKILL.md)).

## JVM metrics

Load the management classes once with `print java.lang.Class.forName("java.lang.management.ManagementFactory")`.
In the table, `M` stands for `java.lang.management.ManagementFactory`; write the full name in expressions.

| Value | Expression |
|---|---|
| Heap use | `M.getMemoryMXBean().getHeapMemoryUsage().toString()` |
| Non-heap use (bytes) | `M.getMemoryMXBean().getNonHeapMemoryUsage().getUsed()` |
| Memory pools | `M.getMemoryPoolMXBeans().get(i).getName()`, `.getUsage().toString()` |
| GC count and time | `M.getGarbageCollectorMXBeans().get(i).getName()`, `.getCollectionCount()`, `.getCollectionTime()` |
| Threads, peak | `M.getThreadMXBean().getThreadCount()`, `.getPeakThreadCount()` |
| Deadlocks | `M.getThreadMXBean().findDeadlockedThreads()` (`null` if none) |
| CPU time and allocated bytes per thread | `M.getThreadMXBean().getThreadCpuTime(id)`, `.getThreadAllocatedBytes(id)`, `.getTotalThreadAllocatedBytes()` |
| Process CPU load, open file descriptors, load average | `M.getOperatingSystemMXBean().getProcessCpuLoad()`, `.getOpenFileDescriptorCount()`, `.getSystemLoadAverage()` |
| Uptime, JVM arguments | `M.getRuntimeMXBean().getUptime()`, `.getInputArguments().toString()` |
| Loaded classes, JIT time | `M.getClassLoadingMXBean().getLoadedClassCount()`, `M.getCompilationMXBean().getTotalCompilationTime()` |
| Direct and mapped buffers | `M.getPlatformMXBeans(java.lang.Class.forName("java.lang.management.BufferPoolMXBean")).get(i).getName()`, `.getMemoryUsed()` |

- Methods of the `com.sun.management` extensions (allocated bytes, process CPU load, file descriptors) work without a cast, because jdb calls the method on the runtime class.
- Lists print as `[Impl@1234, ...]`; read the elements with `.get(i)`.
- GC and memory notifications cannot be received: they need a listener class.

## Changing log levels (java.util.logging)

Example: enable FINE and FINER logging for the JDK HTTP server without a restart:

```
print java.util.logging.Logger.getLogger("com.sun.net.httpserver").setLevel(java.util.logging.Level.ALL)
print java.util.logging.Logger.getLogger("").getHandlers()[0].setLevel(java.util.logging.Level.ALL)
```

- The handler has its own level (INFO by default), so set both.
- `Logger.getLogger` holds loggers weakly (per its Javadoc): the level of a logger that no code holds can be lost after a GC.
- Other logging frameworks need their own API calls; find them with `classes` and `methods`.
- Set the levels back when done.
