---
name: go-debugging
description: "Debug, profile, and benchmark Go programs with Delve, pprof, execution trace, and runtime diagnostics."
---

# Go debugging and diagnostics

Pick the lightest tool for the symptom. **Never run multiple profilers or tracers simultaneously**—they skew each other.

- **Data races**: `go test -race ./...` (or `go build -race` for short reproductions).
- **Hung process (zero setup)**: `kill -QUIT $PID` (dumps all goroutine stacks to stderr), or run with `GOTRACEBACK=all` / `GOTRACEBACK=crash`.
- **Live runtime counters**: `GODEBUG=gctrace=1,schedtrace=1000` (also `http2debug=1|2` for HTTP/2/gRPC, `netdns=go+2` for DNS).
- **CPU, heap, allocs, mutex, block, goroutines**: `pprof` (`/debug/pprof/` or `go test -*profile`).
- **Off-CPU, scheduler latency, GC pauses**: `runtime/trace` → `go tool trace -pprof=sync|net|syscall|sched`.
- **Interactive state inspection & watchpoints**: headless Delve (`dlv`) via [scripts/dlv_driver.py](./scripts/dlv_driver.py) in `pysess`.

For `dlv core`, Linux `rr` reverse debugging, `trace.FlightRecorder`, `testing/synctest`, or compiler escape/BCE/PGO flags, read [references/advanced.md](./references/advanced.md).

---

# Debugger (Delve)

## Pitfalls before you start

- Build unoptimized (`go build -gcflags="all=-N -l"`) so locals are not optimized out and functions are not inlined.
- Any breakpoint or watchpoint stops **all** goroutines (stop-the-world). Other requests wait until `cont()`.
- Breakpoint names must be alphanumeric only (`visitk5`, not `visit-k5` or `visit_k5`).
- `dlv attach $PID` + `detach(kill=False)` keeps the target running; `dlv exec` + `detach(kill=False)` may exit the launched child (especially on macOS/debugserver).
- Linux/container `attach` requires `SYS_PTRACE` (or `kernel.yama.ptrace_scope=0`).

## Start headless Delve

Always pass `--headless --api-version=2 --accept-multiclient` (required for `d.halt()`):

```bash
runagent run -n dlv-target -- dlv exec /tmp/app \
    --headless --listen=127.0.0.1:2345 --api-version=2 --accept-multiclient -- --flag=val

# Or attach to a running PID:
runagent run -n dlv-target -- dlv attach $PID \
    --headless --listen=127.0.0.1:2345 --api-version=2 --accept-multiclient
```

## Drive Delve from `pysess`

Load [scripts/dlv_driver.py](./scripts/dlv_driver.py) into a `pysess` session (see [pysess](../pysess/SKILL.md)):

```bash
pysess new -n dlv <skill-dir>/scripts/dlv_driver.py
pysess exec dlv -c 'd = Dlv("127.0.0.1:2345")'
```

| Call | Does |
|---|---|
| `d.bp(loc, cond=None, hit_cond=None, name=None)` | Set a breakpoint (`"main.visit"` or `"file.go:42"`). `cond` is a native Go boolean expression. |
| `d.tracepoint(loc, vars=(), cond=None, name=None)` | Non-stopping tracepoint; logs `vars` to `dlv` output (visible via `runagent`). |
| `d.watch(expr, wtype="write")` | Hardware watchpoint (`"read"`, `"write"`, `"rw"`). |
| `d.bps()` / `d.clear(id_or_name)` / `d.clear_all()` | List or remove breakpoints/watchpoints. |
| `d.cont(timeout=None)` / `d.halt()` | Continue (auto-halts on `timeout` or `pysess cancel`) / pause a running target. |
| `d.resume()` / `d.wait(timeout=None)` | Run in background across turns; `d.halt()` pauses and joins. |
| `d.next()`, `d.step()`, `d.stepout()` | Step over, into, out. |
| `d.where(stack=5, depth=None, source=1)` / `d.list(context=4)` | One-shot summary (goroutine, source snippet, args, locals, stack) / surrounding source lines. |
| `d.eval(expr, frame=None, goroutine=None, depth=None)` | Evaluate Go expression (`depth` bumps struct/pointer recursion). |
| `d.locals(...)` / `d.args(...)` | List locals or args (accept `frame=`, `goroutine=`, `depth=`). |
| `d.set(symbol, value, frame=None)` / `d.call(expr)` | Mutate a variable / call a function and return formatted result(s) (do not call blocking code). |
| `d.stack(depth=10, goroutine=None)` | Stack trace for current or specified goroutine. |
| `d.switch_frame(n)` / `d.up()` / `d.down()` | Navigate stack frames. |
| `d.goroutines(match=None, limit=50)` / `d.switch_goroutine(gid)` | List/filter goroutines (`limit=0` for all) and switch context. |
| `d.collect(loc, exprs, count=10, cond=None, until=None, timeout=None)` | Hit `loc` up to `count` times, returning `[{expr: val}, ...]`; stop early when `until(row)` is true. |
| `d.detach(kill=False)` | Clear breakpoints and detach. |

Set `pysess exec -t` above `d.cont(timeout=...)` so `cont`'s timeout fires before `pysess` detaches:

```bash
pysess exec dlv -t 15 <<'EOF'
import pysess, urllib.request
d.bp("main.visit", cond='k == "k5"', name="visitk5")
req = pysess.bg(lambda: urllib.request.urlopen("http://127.0.0.1:8081/iterate", timeout=120).read().decode())
d.cont(timeout=10)
EOF

pysess exec dlv -c 'd.where(stack=4)'

pysess exec dlv -t 15 <<'EOF'
d.set("v", '""')
d.clear("visitk5")
d.resume()
req.result(timeout=10)
EOF

pysess exec dlv -c 'd.halt()'
```

---

# Profiling (`pprof`) & Tracing (`trace`)

## Critical `pprof` gotchas

- **Heap `inuse_space`**: always pass `?gc=1` (`/debug/pprof/heap?gc=1`), otherwise `inuse_space` only updates at GC boundaries and misses recent allocations.
- **Mutex & block profiles** are disabled (`0`) by default. Enable in the target with `runtime.SetMutexProfileFraction(5)` (`1` for short local tests) and `runtime.SetBlockProfileRate(1)`.
- **On-CPU vs off-CPU**: CPU `pprof` ignores I/O, lock, and channel waits. If latency is high but CPU `pprof` is empty, use `trace -pprof=sync|net|syscall`, `block`/`mutex` profiles, or `fgprof`.
- **Goroutine leaks**: fetch `/debug/pprof/goroutineleak` (built in on Go 1.27+), or diff `/debug/pprof/goroutine?debug=1` over time.

## CLI-first capture & analysis

```bash
# CPU
curl -s "http://127.0.0.1:8081/debug/pprof/profile?seconds=10" -o /tmp/cpu.pb.gz
go tool pprof -top -cum /tmp/cpu.pb.gz | head -20
go tool pprof -list main.fib /tmp/cpu.pb.gz

# Heap leak / regression diff (before -> load -> after)
curl -s "http://127.0.0.1:8081/debug/pprof/heap?gc=1" -o /tmp/heap_before.pb.gz
# ... run load ...
curl -s "http://127.0.0.1:8081/debug/pprof/heap?gc=1" -o /tmp/heap_after.pb.gz
go tool pprof -top -inuse_space -diff_base=/tmp/heap_before.pb.gz /tmp/heap_after.pb.gz | head -15
go tool pprof -top -alloc_space /tmp/heap_after.pb.gz | head -15

# Mutex & goroutines
curl -s "http://127.0.0.1:8081/debug/pprof/mutex" -o /tmp/mutex.pb.gz
go tool pprof -top -cum /tmp/mutex.pb.gz | head -15
curl -s "http://127.0.0.1:8081/debug/pprof/goroutine?debug=2" | head -60

# Execution trace -> headless pprof profiles (no browser needed)
curl -s "http://127.0.0.1:8081/debug/pprof/trace?seconds=5" -o /tmp/trace.out
go tool trace -pprof=sync /tmp/trace.out > /tmp/sync.pb.gz   # also: net, syscall, sched
go tool pprof -top -cum /tmp/sync.pb.gz | head -15
```

Slice profiles by endpoint/tenant with `pprof.Do(ctx, pprof.Labels("route", path), ...)` and filter with `go tool pprof -tagfocus="route=/api"`.

## Web visualizations (for the user)

Always analyze results via CLI first (`pprof -top/-list`, `trace -pprof`, `cover -func`), then offer the interactive web UI to the user, start it in the background with `runagent`, and share the URL:

```bash
# pprof UI (flame graph at /ui/flamegraph; also supports -diff_base=before.pb.gz)
runagent run -n pprof-ui -- go tool pprof -http=127.0.0.1:8085 -no_browser /tmp/cpu.pb.gz

# Execution trace / FlightRecorder UI (timeline, goroutine analysis, MMU, tasks)
runagent run -n trace-ui -- go tool trace -http=127.0.0.1:8086 /tmp/trace.out

# Test coverage: CLI summary first, then HTML heatmap (http://127.0.0.1:8087/coverage.html)
mkdir -p /tmp/cover-ui
go test -covermode=count -coverprofile=/tmp/cover-ui/cover.out ./...
go tool cover -func=/tmp/cover-ui/cover.out
go tool cover -html=/tmp/cover-ui/cover.out -o /tmp/cover-ui/coverage.html
runagent run -n cover-ui -- python3 -m http.server 8087 --bind 127.0.0.1 --directory /tmp/cover-ui
```

---

# Benchmarking

- **Microbenchmarks**: run `-count=10` with `-benchmem` and compare via `benchstat` (require `p < 0.05`):
  ```bash
  go test -run=^$ -bench=BenchmarkTarget -benchmem -count=10 ./... | tee /tmp/old.txt
  # ... change one thing, write /tmp/new.txt ...
  go run golang.org/x/perf/cmd/benchstat@latest /tmp/old.txt /tmp/new.txt
  ```
- **Macrobenchmarks**: detach `dlv`, build optimized (no `-N -l` or `-race`), warm up, drive load with [echoclient](../echoclient/SKILL.md), compare one profile at a time with `pprof -diff_base` plus `runagent ps` stats, and repeat ≥3 times.
