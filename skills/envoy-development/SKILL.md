---
name: envoy-development
description: Use when needing a development environment to debug/troubleshoot, experiment or compile custom builds
---

# Envoy Development

## Lightweight Envoy Build

Build minimal Envoy (~25 extensions vs ~334 full) for fast compilation.
Covers HTTP/HTTPS reverse proxy with TLS, filesystem SDS, health checking, DNS, load balancing. HTTP/3 disabled.

### Setup

Verified against Envoy `main` (1.40.0-dev, bzlmod, Bazel 8.8.0, hermetic LLVM 22.1.8) on
Ubuntu 24.04. See "Pre-bzlmod branches" below for release branches such as v1.38.x.

#### 1. No toolchain patch needed

Do **not** patch `bazel/toolchains.bzl`. On main that file no longer defines a version —
it re-exports from `@llvm_toolchain_llvm//:llvm.bzl`. The hermetic LLVM version lives in
`bazel/extensions.bzl` (`_LLVM_VERSION`) and `MODULE.bazel`, and is used by default.

#### 2. `user.bazelrc`

```
build --disk_cache=~/.cache/envoy-bazel
build --experimental_disk_cache_gc_max_size=20G
build --experimental_disk_cache_gc_max_age=14d
build --local_resources=cpu=HOST_CPUS*0.3
build --local_resources=memory=HOST_RAM*0.3
build --copt=-Wno-nullability-completeness

# Must be `common`, not `build`: otherwise query/cquery ignore the override and
# silently analyse the FULL extension set.
common --override_repository=+envoy_build_config_ext+envoy_build_config=%workspace%/envoy_lightweight_build_config

build --//bazel:http3=False
```

Three things that are easy to get wrong:

- **No `--config=clang`.** That config no longer exists in `.bazelrc` on main; it fails with
  `Config value 'clang' is not defined in any .rc file`. Check with
  `bazel canonicalize-flags --config=clang -- --compilation_mode=fastbuild`.
- **The override repo name must be canonical.** `envoy_build_config` is created by the
  `envoy_build_config_ext` module extension, so plain
  `--override_repository=envoy_build_config=...` is *silently ignored* and you get a full
  ~334-extension build. The canonical name is `+envoy_build_config_ext+envoy_build_config`.
  This is documented in `bazel/README.md` → "Customize extension build config".
- **Keep resource limits at 0.3.** Envoy compiles are memory-hungry; raising these pushes the
  machine into swap. 0.3 of 20 CPUs ≈ 6 concurrent actions, visible as
  `(N actions, 6 running)` in the build output.

Verify the override actually took effect before starting a long build:

```bash
OB=$(bazel info output_base)
ls -l "$OB/external/+envoy_build_config_ext+envoy_build_config/extensions_build_config.bzl"
```

A **symlink into `source/extensions/`** means the override was ignored (full build).
A **regular file** matching your lightweight copy means it worked.

#### 3. `envoy_lightweight_build_config/`

Directory in repo root. Overrides the `@envoy_build_config` Bazel repository to control which extensions are compiled.

Files:

- `MODULE.bazel` — empty (required under bzlmod for `--override_repository` to accept the dir)
- `BUILD` — empty (required by Bazel)
- `WORKSPACE` — empty (harmless; only needed for pre-bzlmod branches)
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

Expect roughly **7.7k actions** for the lightweight set (a full build is 30k+). From a warm
disk cache that is ~15 min at `cpu=HOST_CPUS*0.3`; from cold, closer to an hour.

**Requires ~20 GB free disk.** Bazel reports `No space left on device` from inside a sandbox
action, which looks like a compile error but is not — check `df -h /` first. See Maintenance
for what is safe to reclaim.

`-c fastbuild` is unstripped, so crashes produce **fully symbolized backtraces** with Envoy's
signal handler. This is the main reason to build locally when debugging a crash: the released
`envoyproxy/envoy` images are stripped (`nm` reports "no symbols"), so their backtraces are
bare addresses and `tools/stack_decode.py` has nothing to work with. A local fastbuild also
enables `ASSERT()`, which is compiled out of release builds — so a release-build SIGSEGV often
surfaces locally as a clean assert naming the exact failing invariant.

### Adding extensions

If runtime error "No registered factory for X" — find the extension in `source/extensions/extensions_build_config.bzl` and add to your override.

Common additions: `envoy.config_subscription.grpc`, `envoy.clusters.eds`, `envoy.filters.http.fault`, `envoy.compression.gzip.compressor`/`.decompressor`.

For gRPC xDS (`api_config_source` or `ads`) you need all of these, not just the subscription:

```python
"envoy.config_subscription.grpc": "//source/extensions/config_subscription/grpc:grpc_subscription_lib",
"envoy.config_subscription.delta_grpc": "//source/extensions/config_subscription/grpc:grpc_subscription_lib",
"envoy.config_subscription.ads": "//source/extensions/config_subscription/grpc:grpc_subscription_lib",
"envoy.config_mux.delta_grpc_mux_factory": "//source/extensions/config_subscription/grpc/xds_mux:grpc_mux_lib",
"envoy.config_mux.sotw_grpc_mux_factory": "//source/extensions/config_subscription/grpc/xds_mux:grpc_mux_lib",
```

Bootstrap also needs a `node` with `id` and `cluster` set, or Envoy exits with
`node 'id' and 'cluster' are required`.

Verify a target exists on your branch before adding it:

```bash
grep -n '"envoy\.your\.extension"' source/extensions/extensions_build_config.bzl
```

### Maintenance

- Disk cache GC is automatic via `--experimental_disk_cache_gc_max_size=20G` and `--experimental_disk_cache_gc_max_age=14d` in `user.bazelrc`. Bazel prunes the cache during builds. Available since Bazel 7.4.
- Check cache size: `du -sh ~/.cache/envoy-bazel`
- `user.bazelrc` and `envoy_lightweight_build_config/` are untracked — survive git pull.
- No tracked files need patching after a pull (the old `bazel/toolchains.bzl` patch is obsolete).

#### Reclaiming disk space

Bazel's GC only manages the `ac/` and `cas/` subdirs of the disk cache. Everything else
accumulates forever. Checked with `du -sh`, these are safe to delete:

```bash
cd ~/work/envoy && bazel shutdown
OB=$(bazel info output_base)          # e.g. ~/.cache/bazel/_bazel_$USER/<hash>

# Stale WORKSPACE-era execroot. bzlmod builds use execroot/_main, so any
# execroot/<workspace-name> left from a pre-bzlmod branch is dead weight (10G+).
rm -rf "$OB/execroot/envoy"

# Bazel sandbox leftovers.
rm -rf "$OB/sandbox/_moved_trash_dir" "$OB/sandbox/sandbox_stash"

# Stray dirs that are NOT part of a --disk_cache (which only uses ac/ and cas/).
rm -rf ~/.cache/envoy-bazel/bazel_root ~/.cache/envoy-bazel/repository_cache
```

Confirm which execroot is live before deleting: `ls -l bazel-bin` points at the one in use.

Do **not** delete `$OB/external` (the extracted external repos, 20G+) or `$OB/execroot/_main`
while you intend to keep incremental state.

The disk cache itself (`~/.cache/envoy-bazel/{ac,cas}`) is keyed to the toolchain. After an
LLVM bump it is nearly useless — check the build summary: a line like
`7084 action cache hit, 11 disk cache hit` means the 15G of `cas/` is buying you almost
nothing and can be dropped if you need the space.

### Pre-bzlmod branches (v1.38.x and older)

Release branches still use WORKSPACE, and the setup above does not apply:

- `bazel/toolchains.bzl` defines `_LLVM_VERSION_HERMETIC`; set it to `"21.1.8"` on
  Ubuntu 24.04. This file is tracked, so re-apply after each checkout/pull.
- Use `build --config=clang` (it exists there).
- Override with the plain apparent name:
  `build --override_repository=envoy_build_config=%workspace%/envoy_lightweight_build_config`
- The override dir needs `WORKSPACE` rather than `MODULE.bazel`.
- Use `--@envoy//bazel:http3=False`.

Switching branches between these two worlds invalidates the whole disk cache (different LLVM)
and leaves a stale `execroot/envoy` behind. Prefer debugging on `main` — a fix has to land
there anyway — and only build a release branch when you specifically need to confirm
release-branch behaviour.

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
runagent ps echoserver
runagent logs envoy --last 10
```

`runagent ps <name>` — there is no `runagent status`. For long jobs such as a build, use
`runagent wait <name>` to block until exit instead of polling with `sleep`.

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


### How to create reproduction scripts

See following examples and the scripts included on how to create good standalone bug reproduction scripts when user wants to show the bug to others

- https://github.com/envoyproxy/envoy/issues/46774
- https://gist.github.com/tsaarni/442dc84dc4824f0f27828f8ac8b931e3
