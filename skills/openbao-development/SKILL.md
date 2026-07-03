---
name: openbao-development
description: Use when working on OpenBao testing, troubleshooting, learning runtime behavior, raft storage.
---

# OpenBao Development

## Tools

- **runagent** — `go run github.com/tsaarni/runagent/cmd/runagent@latest` — background process manager
- **raft-inspector** — `go run github.com/tsaarni/raft-inspector@latest` — raft storage inspector
- **echoclient** — `github.com/tsaarni/echoclient` — Go SDK for load testing
- **httpie** — `http` — manual HTTP requests, http://httpie.io/

## Build and Run

Quick setup without compiling: dev server with fixed root token:

```bash
go run . server -dev -dev-root-token-id=root  # fixed root token
```

Compile and run with integrated storage (Raft):

```bash
make   # binary in bin/bao
```

Create configuration file and data directories:

```bash
mkdir -p ${TMP_DATA}/node1/data
cat > ${TMP_DATA}/node1/config.hcl <<EOF
storage "raft" { path = "${TMP_DATA}/node1/data" }
listener "tcp" { address = "127.0.0.1:8200"; tls_disable = true }
telemetry { prometheus_retention_time = "24h"; disable_hostname = true }
api_addr     = "http://127.0.0.1:8200"
cluster_addr = "https://127.0.0.1:8201"
EOF
```

Run the server on background and initialize/unseal it (store unseal keys and root token in `init.json` for later use):

```bash
runagent run -n bao -- bin/bao server -config=${TMP_DATA}/node1/config.hcl

export BAO_ADDR=http://127.0.0.1:8200
bin/bao operator init -key-shares=1 -key-threshold=1 -format=json > ${TMP_DATA}/init.json
bin/bao operator unseal $(jq -r '.unseal_keys_b64[0]' ${TMP_DATA}/init.json)

# Use with bao CLI
export BAO_TOKEN=$(jq -r '.root_token' ${TMP_DATA}/init.json)
bin/bao secrets enable -path=secret kv

# Use with httpie
http http://127.0.0.1:8200/v1/sys/mounts X-Vault-Token:$(jq -r '.root_token' ${TMP_DATA}/init.json)
```


## Run Tests

```bash
make test TEST=./path/to/pkg TESTARGS="-v"    # run specific tests
```

## Configure VSCode Debug

VSCode debug: `program: "${workspaceFolder}"`, args: `["server", "-dev", "-dev-root-token-id=root", "-log-level=debug"]`.

### Integrated Storage (Raft) Internals

Following files are created in `${TMP_DATA}/node1/data` when using Raft storage:
* `vault.db` — FSM state (secrets, config, leases)
* `raft/raft.db` — Raft log

Use `raft-inspector` to inspect both files:

```bash
go run github.com/tsaarni/raft-inspector@latest <subcommand> --data-dir ${TMP_DATA}/node1/data
```

Subcommands:
- `status` — health overview: term, log index range, applied index, bbolt stats, free pages.
- `log [range]` — list/inspect log entries. Range: `5`, `100..110`, `~10`, `2024-06-01..`.
- `log --stats` — operation distribution and hot keys.
- `fsm` — inspect vault.db data bucket. Use `--prefix` to filter keys.
- `snapshot <file>` — inspect external snapshot archive.

Key flags:
- `--data-dir PATH` — data directory (expects `raft/raft.db` and `vault.db`)
- `--unseal-key-file ${TMP_DATA}/init.json` — decrypt values
- `--prefix PREFIX` — filter keys (fsm/snapshot)
- `--max-value-length N` — truncate decrypted values (default 256, 0=unlimited)

See source code for more details

- Local copy `~/work/raft-inspector/`
- GitHub https://github.com/tsaarni/raft-inspector


## Load Testing with Echoclient Go SDK

Example: write KV secrets at 200 rps with 10 workers:

```go
import (
    "github.com/tsaarni/echoclient/client"
    "github.com/tsaarni/echoclient/metrics"
    "github.com/tsaarni/echoclient/worker"
)

httpClient := client.NewMeasuringHTTPClient()

var counter atomic.Int64
pool := worker.NewWorkerPool(
    func(ctx context.Context, _ *worker.WorkerPool) error {
        i := counter.Add(1)
        body := bytes.NewReader([]byte(`{"data":{"value":"test"}}`))
        req, _ := http.NewRequestWithContext(ctx, "POST", fmt.Sprintf("http://127.0.0.1:8200/v1/secret/data/key-%d", i), body)
        req.Header.Set("X-Vault-Token", token) // root_token from init.json
        resp, err := httpClient.Do(req)
        if err == nil { io.Copy(io.Discard, resp.Body); resp.Body.Close() }
        return err
    },
    worker.WithConcurrency(10),
    worker.WithRateLimit(200, 200),
    worker.WithRepetitions(1000),
)
pool.Launch()
pool.Wait()
metrics.DumpMetricsJSON(os.Stdout)
```

See source code for more details

- Local copy `~/work/echoclient/`
- GitHub https://github.com/tsaarni/echoclient


## OpenBao REST API Reference

- `website/content/api-docs/` — full API reference (`.mdx` files with method, path, parameters, examples)
  - `secret/kv/` — KV v1 and v2
  - `secret/transit.mdx` — Transit encrypt/decrypt/sign
  - `secret/pki.mdx` — PKI certificates
  - `auth/` — auth methods (cert, ldap, jwt, approle, userpass, etc.)
  - `system/` — sys endpoints (init, unseal, mounts, health, leases, storage/raft)
