---
name: envoy-development
description: Use when needing a development environment to debug/troubleshoot, experiment or compile custom builds
---

# Envoy Development

## Lightweight Envoy Build

Build minimal Envoy (~25 extensions vs ~334 full) for fast compilation.
Covers HTTP/HTTPS reverse proxy with TLS, filesystem SDS, health checking, DNS, load balancing. HTTP/3 disabled.

### Setup

#### 1. Patch `bazel/toolchains.bzl` (re-apply after each git pull)

Change `_LLVM_VERSION_HERMETIC` from `"18.1.8"` to `"21.1.8"`.
This works on Ubuntu 24.04.4 LTS.

#### 2. `user.bazelrc`

```
build --config=clang
build --disk_cache=~/.cache/envoy-bazel
build --experimental_disk_cache_gc_max_size=20G
build --experimental_disk_cache_gc_max_age=14d
build --local_resources=cpu=HOST_CPUS*0.3
build --local_resources=memory=HOST_RAM*0.3
build --copt=-Wno-nullability-completeness
build --override_repository=envoy_build_config=%workspace%/envoy_lightweight_build_config
build --@envoy//bazel:http3=False
```

Resource limits at 0.3 keep the machine usable during builds.

#### 3. `envoy_lightweight_build_config/`

Directory in repo root. Overrides the `@envoy_build_config` Bazel repository to control which extensions are compiled.

Three files:

- `WORKSPACE` — empty (required by Bazel to recognize as repository)
- `BUILD` — empty (required by Bazel)
- `extensions_build_config.bzl` — content below:

```python
EXTENSIONS = {
    "envoy.access_loggers.file": "//source/extensions/access_loggers/file:config",
    "envoy.access_loggers.stdout": "//source/extensions/access_loggers/stream:config",
    "envoy.clusters.static": "//source/extensions/clusters/static:static_cluster_lib",
    "envoy.clusters.strict_dns": "//source/extensions/clusters/strict_dns:strict_dns_cluster_lib",
    "envoy.clusters.logical_dns": "//source/extensions/clusters/logical_dns:logical_dns_cluster_lib",
    "envoy.network.dns_resolver.cares": "//source/extensions/network/dns_resolver/cares:config",
    "envoy.config_subscription.filesystem": "//source/extensions/config_subscription/filesystem:filesystem_subscription_lib",
    "envoy.filters.http.router": "//source/extensions/filters/http/router:config",
    "envoy.filters.http.health_check": "//source/extensions/filters/http/health_check:config",
    "envoy.filters.http.buffer": "//source/extensions/filters/http/buffer:config",
    "envoy.filters.network.http_connection_manager": "//source/extensions/filters/network/http_connection_manager:config",
    "envoy.filters.network.tcp_proxy": "//source/extensions/filters/network/tcp_proxy:config",
    "envoy.filters.listener.original_dst": "//source/extensions/filters/listener/original_dst:config",
    "envoy.filters.listener.tls_inspector": "//source/extensions/filters/listener/tls_inspector:config",
    "envoy.filters.listener.http_inspector": "//source/extensions/filters/listener/http_inspector:config",
    "envoy.transport_sockets.raw_buffer": "//source/extensions/transport_sockets/raw_buffer:config",
    "envoy.transport_sockets.tls": "//source/extensions/transport_sockets/tls:config",
    "envoy.load_balancing_policies.round_robin": "//source/extensions/load_balancing_policies/round_robin:config",
    "envoy.load_balancing_policies.least_request": "//source/extensions/load_balancing_policies/least_request:config",
    "envoy.health_checkers.http": "//source/extensions/health_checkers/http:health_checker_lib",
    "envoy.health_checkers.tcp": "//source/extensions/health_checkers/tcp:health_checker_lib",
    "envoy.upstreams.http.http_protocol_options": "//source/extensions/upstreams/http/http:config",
    "envoy.upstreams.http.tcp": "//source/extensions/upstreams/tcp/generic:config",
    "envoy.upstreams.tcp.generic": "//source/extensions/upstreams/tcp/generic:config",
    "envoy.request_id.uuid": "//source/extensions/request_id/uuid:config",
    "envoy.retry_priorities.previous_priorities": "//source/extensions/retry/priority/previous_priorities:config",
    "envoy.retry_host_predicates.previous_hosts": "//source/extensions/retry/host/previous_hosts:config",
}

EXTENSION_CONFIG_VISIBILITY = ["//:extension_config", "//:contrib_library", "//:mobile_library"]
EXTENSION_PACKAGE_VISIBILITY = ["//:extension_library", "//:contrib_library", "//:mobile_library"]
CONTRIB_EXTENSION_PACKAGE_VISIBILITY = ["//:contrib_library"]
MOBILE_PACKAGE_VISIBILITY = ["//:mobile_library"]
LEGACY_ALWAYSLINK = 1
```

### Build

```bash
bazel build -c fastbuild //source/exe:envoy-static
```

Binary at `bazel-bin/source/exe/envoy-static`.

### Adding extensions

If runtime error "No registered factory for X" — find the extension in `source/extensions/extensions_build_config.bzl` and add to your override.

Common additions: `envoy.config_subscription.grpc`, `envoy.clusters.eds`, `envoy.filters.http.fault`, `envoy.compression.gzip.compressor`/`.decompressor`.

### Maintenance

- Disk cache GC is automatic via `--experimental_disk_cache_gc_max_size=20G` and `--experimental_disk_cache_gc_max_age=14d` in `user.bazelrc`. Bazel prunes the cache during builds. Available since Bazel 7.4.
- Check cache size: `du -sh ~/.cache/envoy-bazel`
- Manual trim: `find ~/.cache/envoy-bazel -atime +14 -delete`
- `user.bazelrc` and `envoy_lightweight_build_config/` are untracked — survive git pull.
- `bazel/toolchains.bzl` patch is tracked — re-apply after pull.

### Format Source Code

Quick format and spelling check before committing:

```bash
tools/local_fix_format.sh          # uncommitted changes (default)
tools/local_fix_format.sh -main    # all changes since main
```

For individual files:

```bash
bazel run //tools/code_format:check_format -- fix <directrory>
```

Or if you just want clang-format on those specific files without the full checker:

```bash
bazel run @llvm_toolchain_llvm//:bin/clang-format -- -i <file1> <file2> ...
```

### Generate `compile_commands.json` for vscode

```bash
./tools/gen_compilation_database.py --vscode --exclude_contrib
```

## Testing Envoy Locally

### Tools

Use following tools

- **runagent** — `go run github.com/tsaarni/runagent/cmd/runagent@latest` — background process manager.
- **echoserver** — `go run github.com/tsaarni/echoserver@latest` — HTTP backend echoing request details as JSON.
- **echoclient** — `go get github.com/tsaarni/echoclient` — Go load testing library + CLI.
- **httpie** — `http` — manual HTTP requests.

### Starting Services

```bash
runagent run -n echoserver -- go run github.com/tsaarni/echoserver@latest
runagent run -n envoy -- bazel-bin/source/exe/envoy-static -c test-config.yaml --log-level warn
runagent status echoserver
runagent logs envoy --last 10
```

### Envoy Config Template

Proxies to echoserver on localhost:8080. Insert filter under test before the router.

```yaml
static_resources:
  listeners:
  - name: listener_0
    address:
      socket_address: { address: 127.0.0.1, port_value: 10000 }
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_http
          codec_type: AUTO
          route_config:
            name: local_route
            virtual_hosts:
            - name: local_service
              domains: ["*"]
              routes:
              - match: { prefix: "/" }
                route: { cluster: backend }
          http_filters:
          # Insert filter under test here
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
  clusters:
  - name: backend
    connect_timeout: 5s
    type: STATIC
    load_assignment:
      cluster_name: backend
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address: { address: 127.0.0.1, port_value: 8080 }
admin:
  address:
    socket_address: { address: 127.0.0.1, port_value: 9901 }
```

### Load Testing with echoclient (Go API)

Use echoclient API to build full traffic use cases.

```go
import (
    "github.com/tsaarni/echoclient/client"
    "github.com/tsaarni/echoclient/generator"
    "github.com/tsaarni/echoclient/metrics"
    "github.com/tsaarni/echoclient/worker"
)

httpClient := client.NewMeasuringHTTPClient()

// GET load test
pool := worker.NewWorkerPool(
    func(ctx context.Context, wp *worker.WorkerPool) error {
        resp, err := httpClient.Get("http://localhost:10000/test")
        if err == nil { resp.Body.Close() }
        return err
    },
    worker.WithConcurrency(10),
    worker.WithDuration(10*time.Second),
    worker.WithRateLimit(100, 100),
)
pool.Launch()
pool.Wait()
metrics.DumpMetricsJSON(os.Stdout)

// Upload with generated payload
body := generator.NewReader(generator.WithRandom(), generator.WithTotalSize(1024))
req, _ := http.NewRequestWithContext(ctx, "POST", "http://localhost:10000/upload", body)
resp, err := httpClient.Do(req)
```

See source code for more details:

- Local copy `~/work/echoclient/`
- GitHub https://github.com/tsaarni/echoclient

### Echoserver Endpoints

- `/{path}` — echoes request as JSON (headers, body, TLS, method, etc.)
- `/upload` — accepts large bodies, returns `{"bytes_uploaded": N}`
- `/download?bytes=N` — generates N bytes response
- `/status/{code}` — responds with given HTTP status
- `/status?set=503` — persists status for subsequent `/status` calls

See source code for more details

- Local copy `~/work/echoserver/`
- GitHub https://github.com/tsaarni/echoserver
