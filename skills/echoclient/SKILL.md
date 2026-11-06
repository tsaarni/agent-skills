---
name: echoclient
description: "Load testing and traffic generation with the echoclient CLI or Go library, for HTTP and non-HTTP targets."
---

## CLI (HTTP only)

```
go run github.com/tsaarni/echoclient/cmd/echoclient@latest get -url http://localhost:8080 -concurrency 10 -duration 30s -rps 100 -ramp-up-period 5s
go run github.com/tsaarni/echoclient/cmd/echoclient@latest upload -url http://localhost:8080/upload -concurrency 5 -size 100MiB -chunk 64KiB -repetitions 10
```

Prints a metrics table every 5s and at exit. Use `--help` on subcommands. For anything else, write a Go program.

## Go program

Setup: `go mod init loadtest && go get github.com/tsaarni/echoclient@latest`, write `main.go`, then `go mod tidy && go run .`

```go
package main

import (
    "context"; "io"; "log"; "net/http"; "os"; "time"
    "github.com/tsaarni/echoclient/client"
    "github.com/tsaarni/echoclient/metrics"
    "github.com/tsaarni/echoclient/worker"
)

func main() {
    c := client.NewMeasuringHTTPClient() // records http_client_* metrics; TLS verification disabled
    load := func(ctx context.Context, wp *worker.WorkerPool) error {
        req, err := http.NewRequestWithContext(ctx, "GET", "http://localhost:8080/", nil)
        if err != nil { return err }
        resp, err := c.Do(req)
        if err != nil { return err }
        io.Copy(io.Discard, resp.Body); resp.Body.Close() // reuses the connection and records the duration
        return nil
    }
    pool := worker.NewWorkerPool(load,
        worker.WithConcurrency(10),
        worker.WithRateLimit(100, 100), // rps (0 = unlimited), burst (burst=rps recommended, 1 = strict pacing)
        worker.WithDuration(30*time.Second),
    )
    if err := pool.Launch(); err != nil { log.Fatal(err) } // non-blocking
    go func() { for range time.Tick(5 * time.Second) { metrics.DumpMetrics(os.Stdout) } }()
    err := pool.Wait() // error passed to wp.Abort, or nil
    metrics.DumpMetrics(os.Stdout)
    if err != nil { log.Fatal(err) }
}
```

Custom TLS or transport: `http.Client{Transport: client.NewMeasuringRoundTripper(transport)}`.

End the run early from the load func, a hook, or a goroutine: `wp.Stop()` when done, `wp.Abort(err)` when continuing makes no sense. The first call decides what `Wait` returns:

```go
if resp.StatusCode == http.StatusUnauthorized { wp.Abort(errors.New("credentials rejected")) }
```

Non-HTTP load with custom metrics. Anything created with `promauto` shows up in all metric outputs automatically:

```go
// import "github.com/prometheus/client_golang/prometheus" and ".../prometheus/promauto"
var (
    opDur = promauto.NewHistogram(prometheus.HistogramOpts{Name: "op_duration_seconds", NativeHistogramBucketFactor: 1.05})
    opErr = promauto.NewCounterVec(prometheus.CounterOpts{Name: "op_errors_total"}, []string{"kind"})
)

load := func(ctx context.Context, wp *worker.WorkerPool) error {
    start := time.Now()
    if err := doOperation(ctx); err != nil { opErr.WithLabelValues("op").Inc(); return err }
    opDur.Observe(time.Since(start).Seconds())
    return nil
}
```

Traffic profile. Each eased value goes from the previous step's value to the target over the step duration:

```go
pool := worker.NewMultiStepWorkerPool(load, []*worker.Step{
    worker.NewStep(worker.WithDuration(30*time.Second), worker.WithConcurrency(50), worker.WithRateLimit(500, 500, worker.EasingLinear)),
    worker.NewStep(worker.WithDuration(2*time.Minute), worker.WithConcurrency(50), worker.WithRateLimit(500, 500)),
    worker.NewStep(worker.WithDuration(30*time.Second), worker.WithConcurrency(1, worker.EasingOut), worker.WithRateLimit(1, 1, worker.EasingOut)),
})
```

Easings: `EasingLinear`, `EasingIn`, `EasingOut`, `EasingInOut`. Other step options: `WithRepetitions(n)` (total calls shared by all workers), `WithPause(d)` (workers stay alive but idle), `WithWorkerFunc(f)`, `WithHooks(onStart, onEnd)`.

Weighted mix (80/20):

```go
var read, write worker.WorkerFunc = readFn, writeFn // must be typed for .Weighted
pool := worker.NewWorkerPool(worker.Mix(read.Weighted(80), write.Weighted(20)), worker.WithConcurrency(5))
```

Upload with generated body (one reader per request; without `ContentLength` the body is sent chunked):

```go
const n = 100 << 20
body := generator.NewReader(generator.WithTotalSize(n), generator.WithRandom()) // or WithASCII(), WithRandomSeed(s), WithChunkSize(c)
req, err := http.NewRequestWithContext(ctx, "POST", "http://localhost:8080/upload", body)
if err != nil { return err }
req.ContentLength = n
```

Per-worker state, e.g. one connection per worker. The start hook runs when a worker starts and its returned context is passed to every call by that worker. The exit hook runs when the worker exits (stop, scale-down, end):

```go
type connKey struct{}

pool.SetWorkerHooks( // call before Launch
    func(ctx context.Context, wp *worker.WorkerPool) (context.Context, error) {
        conn, err := net.Dial("tcp", addr)
        if err != nil { return ctx, err } // worker exits and is replaced within 100ms; call wp.Abort to give up
        return context.WithValue(ctx, connKey{}, conn), nil
    },
    func(ctx context.Context, wp *worker.WorkerPool) { ctx.Value(connKey{}).(net.Conn).Close() },
)
// In the load func: conn := ctx.Value(connKey{}).(net.Conn)
```

Runtime control from hooks or goroutines: `pool.SetRateLimit(rps, burst)`, `pool.SetConcurrency(n)`, `pool.SetWorker(f)`. Eased values are re-applied every 100ms, overriding `Set*` calls for that value.

## Metrics output

- `metrics.DumpMetrics(w)`: table. Histograms are shown as p50/p90/p95/p99, accurate to about 4% when `NativeHistogramBucketFactor: 1.05` is set (built-in histograms have it).
- `metrics.DumpMetricsJSON(w)`: one JSON line per call, for parsing.
- `srv, err := metrics.StartPrometheusServer("localhost:9090")`: serves `/metrics` in the background, no authentication. `srv.Close()` stops it.

Built-in metrics:
- `http_client_requests_total{method,code,host}`
- `http_client_request_duration_seconds{method,host}`: until the body is read or closed.
- `http_client_response_headers_seconds{method,host}`: until response headers.
- `http_client_errors_total{error,host}`: transport and body read errors.
- `scheduler_request_latency_seconds`: load-func latency including queueing delay. Only when rate limited.
- `scheduler_skipped_requests_total`: requests skipped because the target could not keep up.
- `worker_calls_total`, `worker_errors_total`: load-func calls and returned errors (no labels).
- `worker_pool_active_workers`

## Gotchas

- Rate limit counts load-func calls, not HTTP requests.
- Always pass `ctx` to requests so `Stop()` and `Abort()` interrupt in-flight work.

## Source

Local checkout: `~/work/echoclient` (examples in `examples/`). Released version: `go mod download -json github.com/tsaarni/echoclient@latest` (the `Dir` field; downloads if needed).
