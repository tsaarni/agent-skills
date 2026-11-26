# Advanced Go Debugging & Analysis

## Post-mortem core dumps (`dlv core`)

Run with `GOTRACEBACK=crash` so panics or `SIGABRT` write a core dump, then inspect offline:

```bash
ulimit -c unlimited
GOTRACEBACK=crash ./app
dlv core ./app /path/to/core --headless --listen=127.0.0.1:2345 --api-version=2
```

Connect with `dlv_driver.py` for read-only inspection (`d.goroutines()`, `d.stack()`, `d.where()`, `d.locals()`, `d.eval()`; `cont`/`halt` do not apply to core dumps).

## Reverse / time-travel debugging with `rr` (Linux)

Record a rare race or corruption once, then step backwards from the failure:

```bash
rr record ./app --flags          # writes trace under ~/.local/share/rr/
dlv replay                       # opens latest trace (or pass trace dir)
# Inside dlv:
#   watch <expr> ; continue      # stop at corruption
#   rewind (rw)                  # run backwards to previous breakpoint/watchpoint
#   rev / reverse-next           # step backwards
```

## `runtime/trace.FlightRecorder` (Go 1.25+)

Continuously records execution events into a bounded in-memory ring buffer so a service can dump the last few seconds only when an anomaly occurs:

```go
fr := trace.NewFlightRecorder(trace.FlightRecorderConfig{
    MinAge:   5 * time.Second,
    MaxBytes: 10 << 20,
})
_ = fr.Start()
// On slow request, error spike, or debug HTTP handler:
_, _ = fr.WriteTo(w)
```

Analyze the dumped `.trace` headless via `go tool trace -pprof=sync|net|syscall|sched flight.trace` → `go tool pprof -top -cum`.

## Deterministic concurrency tests (`testing/synctest`, Go 1.25+)

Replace flaky `time.Sleep` tests with a virtual-clock bubble. Time advances instantly once all goroutines in the bubble are durably blocked; leaked goroutines fail the test:

```go
synctest.Test(t, func(t *testing.T) {
    // start goroutines using time.After / time.Sleep / channels
    synctest.Wait() // blocks until all bubble goroutines settle
})
```

Avoid external I/O or goroutines created outside the bubble.

## Compiler introspection & PGO

```bash
go build -gcflags="-m" ./...                       # escape analysis & inlining (-m -m for full reasons)
go build -gcflags="-d=ssa/check_bce/debug=1" ./... # remaining slice bounds checks
go tool objdump -s "main\.fib" /tmp/app            # generated assembly
```

**Profile-Guided Optimization (PGO)**: save a representative production CPU profile as `default.pgo` in the `main` package directory; `go build` applies it automatically (typically 2–14% speedup).

## Compiler SSA HTML viewer (`ssa.html`)

When inspecting why a function wasn't optimized, generate `ssa.html` and offer to serve it in the background via `runagent` (`http://127.0.0.1:8088/ssa.html`):

```bash
GOSSAFUNC=fib go build ./...
mkdir -p /tmp/ssa-ui && mv ssa.html /tmp/ssa-ui/
runagent run -n ssa-ui -- python3 -m http.server 8088 --bind 127.0.0.1 --directory /tmp/ssa-ui
```
