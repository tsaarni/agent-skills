---
name: echoclient
description: Use this for HTTP load tests or custom traffic generation, also for non-HTTP load generation.
---

## CLI

Only for HTTP 

```
go run github.com/tsaarni/echoclient/cmd/echoclient@latest get -url http://localhost:8080 -concurrency 10 -duration 30s -rps 100 -ramp-up-period 5s
go run github.com/tsaarni/echoclient/cmd/echoclient@latest upload -url http://localhost:8080/upload -concurrency 5 -size 100MiB -chunk 64KiB -repetitions 10
```

Use `--help` on the subcommands for more options.

## Go API

Refer to source for details, you can find it here `go list -m -f '{{.Dir}}' github.com/tsaarni/echoclient@latest`

```go
pool := worker.NewWorkerPool(loadFunc,
    worker.WithConcurrency(10),
    worker.WithDuration(10*time.Second),
    worker.WithRateLimit(100, 100),
)
pool.Launch()
pool.Wait()
```

loadFunc is: `func(ctx context.Context, wp *worker.WorkerPool) error` — not limited to HTTP.

Features:
- Multi-step traffic profiles with `NewMultiStepWorkerPool` and easing functions (`EasingLinear`, `EasingIn`, `EasingOut`, `EasingInOut`)
- Worker composition with weighted mixing (`worker.Mix`)
- Streaming data generator (`generator.NewReader`)
- Instrumented HTTP client (`client.NewMeasuringHTTPClient`)
- Metrics: tabular dump (`metrics.DumpMetrics`) and Prometheus endpoint (`metrics.StartPrometheusServer`)
- Runtime control: `SetRateLimit`, `SetConcurrency`, `SetWorker`, `Stop`
