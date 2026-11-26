// Test target for the go-debugging skill: a small HTTP server where each endpoint
// has one known behaviour, so dlv, pprof, trace, FlightRecorder, and GODEBUG can be
// verified against known results before using them on a real application.
//
// Flags & arguments:
//   -flight                     enable runtime/trace.FlightRecorder for /flight (Go 1.25+;
//                               off by default so it does not conflict with /debug/pprof/trace)
//   [port]                      first positional argument, default 8081
//
// Endpoints:
//   /                           returns "ok"
//   /cpu?n=32                   CPU work, recursive fib(n)
//   /alloc?mb=5&retain=true     allocates 1 MiB slices; retain=true keeps them (leak)
//   /lock?ms=100                holds a shared sync.Mutex for ms (mutex/sync contention)
//   /leak-goroutine             spawns a goroutine blocked forever on a channel (goroutine leak)
//   /iterate                    iterates shared map, calls visit(k, v) per entry
//   /remove?key=k3              removes a key from shared map (unlocked: races with /iterate)
//   /reset                      refills shared map with k0..k9 and clears retained slices
//   /flight                     writes the trace.FlightRecorder ring buffer (requires -flight)
//   /debug/pprof/*              standard net/http/pprof endpoints
//
// Build unoptimized for Delve (-N -l preserves locals and prevents inlining):
//
//   go build -gcflags="all=-N -l" -o /tmp/debug-target <skill-dir>/scripts/debug_target.go
//
// Run directly or under headless Delve:
//
//   runagent run -n go-target -- /tmp/debug-target 8081
//   runagent run -n dlv-target -- dlv exec /tmp/debug-target \
//       --headless --listen=127.0.0.1:2345 --api-version=2 --accept-multiclient -- 8081
package main

import (
	"flag"
	"fmt"
	"net/http"
	_ "net/http/pprof"
	"os"
	"runtime"
	"runtime/trace"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

var (
	shared     = map[string]string{} // intentionally unlocked to demonstrate map races
	mu         sync.Mutex            // dedicated to /lock contention testing
	retainedMu sync.Mutex
	retained   [][]byte
	requests   atomic.Int64 // request counter (useful target for Delve eval/watch)
	fr         *trace.FlightRecorder
)

func main() {
	enableFlight := flag.Bool("flight", false, "enable runtime/trace.FlightRecorder for /flight")
	flag.Parse()

	port := "8081"
	if flag.NArg() > 0 {
		port = flag.Arg(0)
	}

	// Enable mutex and block profiling so /debug/pprof/mutex and /debug/pprof/block
	// have data out of the box (both are 0/disabled by default in Go).
	runtime.SetMutexProfileFraction(5)
	runtime.SetBlockProfileRate(1)

	if *enableFlight {
		fr = trace.NewFlightRecorder(trace.FlightRecorderConfig{
			MinAge:   5 * time.Second,
			MaxBytes: 10 << 20,
		})
		_ = fr.Start()
	}

	reset()

	http.HandleFunc("/", handleRoot)
	http.HandleFunc("/cpu", handleCPU)
	http.HandleFunc("/alloc", handleAlloc)
	http.HandleFunc("/lock", handleLock)
	http.HandleFunc("/leak-goroutine", handleLeakGoroutine)
	http.HandleFunc("/iterate", handleIterate)
	http.HandleFunc("/remove", handleRemove)
	http.HandleFunc("/reset", handleReset)
	http.HandleFunc("/flight", handleFlight)

	addr := "127.0.0.1:" + port
	fmt.Println("listening on http://" + addr)
	if err := http.ListenAndServe(addr, nil); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func handleRoot(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	fmt.Fprintln(w, "ok")
}

func handleCPU(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	n := queryInt(r, "n", 30)
	fmt.Fprintf(w, "fib(%d) = %d\n", n, fib(n))
}

func fib(n int) int64 {
	if n < 2 {
		return int64(n)
	}
	return fib(n-1) + fib(n-2)
}

func handleAlloc(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	mb := queryInt(r, "mb", 5)
	retain := r.URL.Query().Get("retain") == "true"
	total := alloc(mb, retain)
	fmt.Fprintf(w, "allocated %d MiB, retained %d MiB\n", mb, total)
}

func alloc(mb int, retain bool) int {
	for i := 0; i < mb; i++ {
		b := make([]byte, 1<<20)
		b[0] = 1
		if retain {
			retainedMu.Lock()
			retained = append(retained, b)
			retainedMu.Unlock()
		}
	}
	retainedMu.Lock()
	defer retainedMu.Unlock()
	return len(retained)
}

func handleLock(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	ms := queryInt(r, "ms", 100)
	locked(time.Duration(ms) * time.Millisecond)
	fmt.Fprintf(w, "held lock %d ms\n", ms)
}

func locked(d time.Duration) {
	mu.Lock()
	defer mu.Unlock()
	time.Sleep(d)
}

func handleLeakGoroutine(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	ch := make(chan struct{})
	go leakedWorker(ch)
	fmt.Fprintf(w, "leaked goroutine (total=%d)\n", runtime.NumGoroutine())
}

func leakedWorker(ch <-chan struct{}) {
	<-ch
}

func handleIterate(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	visited := 0
	for k, v := range shared {
		visited += visit(k, v)
	}
	fmt.Fprintf(w, "visited %d\n", visited)
}

func visit(k, v string) int {
	if v == "" {
		return 0
	}
	return 1
}

func handleRemove(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	key := r.URL.Query().Get("key")
	if key == "" {
		key = "k0"
	}
	delete(shared, key)
	fmt.Fprintf(w, "removed %s, size %d\n", key, len(shared))
}

func handleReset(w http.ResponseWriter, r *http.Request) {
	requests.Add(1)
	reset()
	fmt.Fprintf(w, "size %d\n", len(shared))
}

func reset() {
	clear(shared)
	for i := 0; i < 10; i++ {
		shared[fmt.Sprintf("k%d", i)] = fmt.Sprintf("v%d", i)
	}
	retainedMu.Lock()
	retained = nil
	retainedMu.Unlock()
}

func handleFlight(w http.ResponseWriter, r *http.Request) {
	if fr == nil {
		http.Error(w, "flight recorder not enabled (start with -flight)", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	if _, err := fr.WriteTo(w); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func queryInt(r *http.Request, name string, def int) int {
	if s := r.URL.Query().Get(name); s != "" {
		if v, err := strconv.Atoi(s); err == nil {
			return v
		}
	}
	return def
}
