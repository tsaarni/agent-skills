---
name: contour-development
description: Contour+kind cluster management (create/delete/setup), run Contour locally against cluster Envoy, development environment setup, custom image builds, debugging, e2e testing
---

# Contour Development

## Scope
- **Target**: Contour repository (github.com/projectcontour/contour)
- **Supported Branches**: `main`, `release-X.Y` branches, and development branches
- **Platform**: Linux/macOS
- **Prerequisites**: Contour repository checked out locally, `kind`, `kubectl`, `go`, `make`, `http` (httpie), `docker` installed


## Global Preconditions (MUST validate before ANY task)

Execute these checks and STOP if any fail:

```bash
# 1. Verify current directory is Contour repo root
test -f Makefile && test -f go.mod && grep -q 'module github.com/projectcontour/contour' go.mod && echo "✓ In Contour repo root" || echo "✗ NOT in Contour root"
```

STOP if any check fails. Install missing tools or navigate to Contour repo root before proceeding.

## Critical Warnings

**NEVER:**
- Skip `make checkall` before committing changes
- Use `curl` for testing endpoints → ONLY use `http` (httpie CLI command)

---

## Task: VS Code Setup for Contour Development

**What this does**: Configures VS Code to support e2e test code navigation and Go completion.

### Step 1: Create VS Code Settings Configuration (REQUIRED)

MUST execute exactly:
```bash
mkdir -p .vscode
cat <<EOF > .vscode/settings.json
{
  "go.buildFlags": [
    "-tags=e2e"
  ]
}
EOF
```

Expected: `.vscode/settings.json` file created with e2e build tags.

### Step 2: Reload Go Language Server (REQUIRED)

MUST execute in VS Code:
```
Go: Install/Update Tools → gopls
```

OR run command in terminal:
```bash
go install github.com/golang/tools/gopls@latest
```

Expected: gopls reinstalled and reloaded.

### Step 3: Verify Setup (REQUIRED)

MUST open any e2e test file:
- Navigate to: `test/e2e/` folder
- Open any `test_*.go` file

Expected: No syntax errors in e2e test files, code navigation working (Ctrl+Click navigates to definitions).

---

## Task: Run Unit Tests and Linting

**What this does**: Execute local unit tests and code quality checks without requiring a Kind cluster.

**Precondition**: Contour repo root, no changes to CRD files since last `make generate`.

### Step 1: Execute All Quality Checks (REQUIRED)

MUST execute exactly:
```bash
make checkall
```

Expected:
- All unit tests pass ✓
- No linting errors ✓
- No generated code differences (if CRDs unchanged) ✓

IF tests fail: Review error output and fix issues before proceeding.
IF generated code differs: Execute `make generate` and commit changes.

STOP if `make checkall` does not exit with status 0.

### Step 2: Run Individual Checks (OPTIONAL)

For faster iteration, use individual commands:

```bash
make check      # Unit tests only
make lint       # Linting only
make generate   # Code generation (after modifying CRDs)
```

Expected: Each command completes successfully.

---

## Task: Setup Local Kind Cluster

**What this does**: Create and configure local Kubernetes cluster for Contour testing and development.

**Precondition**: Docker daemon running, 6+ GB free disk space.

### Step 1: Create Kind Cluster (NON-REVOCABLE)

MUST execute exactly:
```bash
scripts/create-cluster.sh
```

Expected:
- Kind cluster named 'contour' created
- Contour deployed in projectcontour namespace
- echoserver deployed in echoserver namespace
- Output ends with: "✓ Cluster ready"

IF script fails: Check error message (usually docker issues), review `kind get clusters` output.
STOP if cluster not created successfully.

### Step 2: Verify Cluster Health (REQUIRED)

MUST execute:
```bash
kubectl cluster-info
kubectl -n projectcontour get pods -o wide
kubectl -n echoserver get pods -o wide
```

Expected:
- All pods in 'Running' or 'Completed' state
- contour pod ready (1/1)
- envoy daemonset pods ready
- echoserver pod running

IF pods not ready: Wait 30 seconds, check logs with `kubectl -n projectcontour logs deployment/contour`.

---

## Task: Delete Kind Cluster

**What this does**: Remove Kind cluster and free resources when development is complete.

**Precondition**: Kind cluster exists.

### Step 1: Delete Cluster (NON-REVOCABLE)

MUST execute exactly:
```bash
scripts/delete-cluster.sh
```

Expected:
- Kind cluster deleted
- Docker containers and networks cleaned up
- Output: "✓ Cluster deleted"

### Step 2: Verify Deletion (REQUIRED)

MUST execute:
```bash
kind get clusters | grep -q contour && echo "✗ Cluster still exists" || echo "✓ Cluster deleted"
```

Expected: "✓ Cluster deleted"

---

## Task: Configure Cluster for Local Contour Development

**What this does**: Patch cluster networking so Envoy (in cluster) can connect to Contour running on host machine.

**Precondition**: Kind cluster running (from "Setup Local Kind Cluster" task), Contour NOT yet running on host.

### Step 1: Apply Cluster Configuration (REQUIRED)

MUST execute exactly:
```bash
scripts/prepare-for-contour-on-host.sh
```

Expected:
- Service and EndpointSlice modified
- Envoy DaemonSet redeployed
- Output confirms configuration applied

IF script fails: Review error output, do not proceed to Step 2.
STOP if configuration not applied.

### Step 2: Verify Configuration (REQUIRED)

MUST execute:
```bash
kubectl -n projectcontour get service contour -o wide
kubectl -n projectcontour get endpointslice -l app=contour
```

Expected:
- Service endpoint points to host machine IP
- EndpointSlice exists with correct addresses

### Step 3: Monitor Envoy Readiness (SEQUENTIAL)

MUST wait for Envoy to reconnect:
```bash
kubectl -n projectcontour logs -f daemonset/envoy -c envoy --tail=20 | grep -i "rds|cds"
```

Expected: After 10-20 seconds, log output shows Envoy connecting and receiving configuration.
Press Ctrl+C to stop log following.

---

## Task: Run Contour from Source (Host) with Debugger

**What this does**: Execute Contour on host machine with VS Code debugger attached for real-time debugging.

**Precondition**:
- Kind cluster prepared with `prepare-for-contour-on-host.sh`
- VS Code open with Contour repository
- No Contour instance already running on port 8001

### Step 1: Configure Launch Configuration (REQUIRED - ONE TIME)

MUST create/update `.vscode/launch.json`:

```bash
mkdir -p .vscode
cat <<EOF > .vscode/launch.json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run contour",
      "type": "go",
      "request": "launch",
      "mode": "auto",
      "cwd": "\${workspaceRoot}",
      "program": "cmd/contour",
      "args": [
        "serve",
        "--xds-address=0.0.0.0",
        "--xds-port=8001",
        "--envoy-service-http-port=8080",
        "--envoy-service-https-port=8443",
        "--contour-cafile=ca.crt",
        "--contour-cert-file=tls.crt",
        "--contour-key-file=tls.key",
        "--debug"
      ]
    }
  ]
}
EOF
```

Expected: `.vscode/launch.json` created in .vscode directory.

### Step 2: Generate TLS Certificates (REQUIRED - ONE TIME)

IF certificates already exist in repo root:
  - Verify: `ls -la ca.crt tls.crt tls.key` shows all three files
  - PROCEED to Step 3
ELSE:
  - Refer to certyaml skill to generate test certificates
  - Place in repository root

### Step 3: Start Debugger (SEQUENTIAL)

MUST execute in VS Code:
```
Run → Start Debugging (or press F5)
OR
Run → Run Without Debugging (Ctrl+F5)
```

Expected:
- Debug console appears
- Output shows: "Contour is starting up"
- Debugger terminal active

IF debugger fails to start: Check VS Code output panel for build errors.
IF build fails: Run `make check` to diagnose issues.

### Step 4: Verify Contour Connected (SEQUENTIAL)

In separate terminal, MUST execute:
```bash
kubectl -n projectcontour logs -f daemonset/envoy -c envoy | grep -i "config_hash_mismatch" | head -5
```

Expected: Envoy logs show connection established (may see config_hash messages).
IF connection fails: Check firewall, verify cluster configuration from previous task.

### Step 5: Test with Breakpoints (OPTIONAL)

To debug specific code:

1. Set breakpoint in code (line number)
2. Trigger request (use echoserver endpoint from "Test Traffic" task)
3. Debugger pauses at breakpoint
4. Inspect variables in VS Code Debug panel

### Step 6: Stop Debugger (REQUIRED)

MUST press: Ctrl+C in debug terminal
OR click Stop button in VS Code

Expected: Contour process terminates, debug session closes.

---

## Task: Run Contour from Source (CLI)

**What this does**: Execute Contour on host machine from command line (non-interactive).

**Precondition**:
- Kind cluster prepared with `prepare-for-contour-on-host.sh`
- TLS certificates available (ca.crt, tls.crt, tls.key)
- No other Contour instance running on port 8001

### Step 1: Start Contour (SEQUENTIAL)

MUST execute exactly:
```bash
go run github.com/projectcontour/contour/cmd/contour serve \
  --xds-address=0.0.0.0 \
  --xds-port=8001 \
  --envoy-service-http-port=8080 \
  --envoy-service-https-port=8443 \
  --contour-cafile=ca.crt \
  --contour-cert-file=tls.crt \
  --contour-key-file=tls.key \
  --debug
```

Expected:
- No build errors
- Output: "Contour is starting up"
- Process continues running, ready for requests

IF fails to start: Check TLS certificate files exist.
IF port already in use: Kill existing Contour process first.

### Step 2: Verify Connection (SEQUENTIAL - NEW TERMINAL)

In separate terminal, MUST execute:
```bash
http http://localhost:8000/debug/dag | head -20
```

Expected: JSON output showing Contour's internal DAG (Directed Acyclic Graph).
IF connection refused: Wait 5 seconds, Contour may still be initializing.

### Step 3: Keep Running (SEQUENTIAL)

Contour CLI process must continue running. To stop:
- Press Ctrl+C in the terminal
- Expected: Clean shutdown, no errors

---

## Task: Build and Deploy Custom Contour Image to Cluster

**What this does**: Build Contour container image from source code and deploy to Kind cluster for fully in-cluster testing.

**Precondition**:
- Kind cluster running
- Docker daemon running
- Sufficient disk space for image build (~500MB)

### Step 1: Build CONTOUR Container Image (SEQUENTIAL)

MUST execute exactly:
```bash
make container VERSION=latest
```

Expected:
- Docker image built successfully
- Output ends with: "Successfully built" or "Successfully tagged"
- Image named: `ghcr.io/projectcontour/contour:latest`

VERIFY:
```bash
docker images | grep projectcontour/contour
```

Expected: Image with tag 'latest' appears in list.

### Step 2: Load Image into Kind Cluster (SEQUENTIAL)

MUST execute exactly:
```bash
kind load docker-image ghcr.io/projectcontour/contour:latest --name contour
```

Expected:
- No errors
- Image available in cluster

STOP if this step fails; cluster may not exist.

### Step 3: Patch Deployments to Use Custom Image (SEQUENTIAL - ONE TIME)

MUST execute:
```bash
kubectl -n projectcontour patch deployment contour --patch-file=assets/contour-deployment-patch.yaml
kubectl -n projectcontour patch daemonset envoy --patch-file=assets/envoy-daemonset-patch.yaml
```

Expected:
- Deployments patched (output shows patch applied)
- Pods will begin redeploying

IF patch fails: Verify patch files exist in `assets/` directory.

### Step 4: Verify Rollout (SEQUENTIAL)

MUST execute:
```bash
kubectl -n projectcontour rollout status deployment/contour --timeout=2m
kubectl -n projectcontour rollout status daemonset/envoy --timeout=2m
```

Expected:
- "rollout successfully completed" message

IF rollout times out: Review deployment events and logs:
```bash
kubectl -n projectcontour describe deployment contour
kubectl -n projectcontour logs deployment/contour --tail=20
```

### Step 5: Verify Custom Image Running (REQUIRED)

MUST execute:
```bash
kubectl -n projectcontour describe pod -l app=contour | grep Image: | head -1
```

Expected: Image contains 'latest' tag or custom VERSION you used.

### Step 6: Repeated Testing (ONE PER CODE CHANGE)

For subsequent code changes, REPEAT only Steps 1-2, then:

```bash
# Force pod restart to use updated image
kubectl -n projectcontour rollout restart deployment/contour
kubectl -n projectcontour rollout status deployment/contour --timeout=2m
```

Expected: New pods running with updated code.

---

## Task: Run End-to-End Tests

**What this does**: Execute e2e test suite against running Kind cluster.

**Precondition**:
- Kind cluster running and healthy (from setup task)
- No local Contour running on port 8001 (use in-cluster Contour)
- VS Code setup completed (for code navigation)

### Step 1: Run All E2E Tests (SEQUENTIAL)

MUST execute exactly:
```bash
CONTOUR_E2E_LOCAL_HOST=127.0.0.101 make run-e2e
```

Note: On macOS, use `127.0.0.1` instead:
```bash
CONTOUR_E2E_LOCAL_HOST=127.0.0.1 make run-e2e
```

Expected:
- Tests execute and report results at end
- Output shows: "ok" or "failed" status

IF tests hang: Ctrl+C to abort, check cluster health with `kubectl get pods -A`.
IF tests fail: Review failure message, consult test logs.

### Step 2: Run Specific E2E Tests (OPTIONAL)

To run only tests matching a pattern, execute:
```bash
CONTOUR_E2E_TEST_FOCUS="external name services work over https" CONTOUR_E2E_LOCAL_HOST=127.0.0.101 make run-e2e
```

Expected: Only matching tests execute.

Decision:
- IF test passes: Feature works correctly
- IF test fails: Debug with logs from previous task

### Step 3: Review Test Output (REQUIRED)

Key output sections:
- Test names and results (PASS/FAIL)
- Summary statistics at end
- Timing information

IF interested in specific test details:
```bash
# View test source
find test/e2e -name "*.go" -type f -exec grep -l "external name services" {} \;
```

---

## Task: Test Traffic With Echoserver

**What this does**: Send HTTP requests to test Contour routing and Envoy configuration.

**Precondition**:
- Kind cluster running with echoserver deployed
- `httpie` installed locally
- Proper hostname resolution to 127.0.0.101 (or 127.0.0.1 on macOS)

### Step 1: Verify Echoserver Accessibility (REQUIRED)

MUST execute:
```bash
http http://echoserver.127-0-0-101.nip.io/host
```

Note: On macOS:
```bash
http http://echoserver.127-0-0-1.nip.io/host
```

Expected:
- HTTP 200 response
- JSON response with host information
- No connection errors

IF connection refused: Cluster not properly configured, check Envoy logs.

### Step 2: Run Load Test (OPTIONAL)

For basic load testing:
```bash
go run github.com/tsaarni/echoclient/cmd/echoclient@latest get \
  -url http://echoserver.127-0-0-101.nip.io \
  -concurrency 10 \
  -duration 10s
```

Expected:
- Requests complete successfully
- Output shows request statistics (latency, throughput)
- No errors reported

---

## Task: Inspect Contour Metrics and Debug Info

**What this does**: Access Contour debug endpoints for runtime inspection.

**Precondition**: Contour running (either on host or in cluster)

### Step 1: Forward Contour Debug Port (REQUIRED)

If Contour running on host:
  - Already accessible at `localhost:8000`
  - PROCEED to Step 2

If Contour running in cluster:
  - MUST execute:
  ```bash
  kubectl -n projectcontour port-forward deployment/contour 8000:8000
  ```
  - PROCEED to Step 2

### Step 2: Access Debug Endpoints

**Metrics**:
```bash
http localhost:8000/metrics | head -50
```

**Internal DAG State**:
```bash
http localhost:8000/debug/dag | jq . | less
```

**Profiling Info**:
```bash
http localhost:8000/debug/pprof/
```

Expected: JSON or text responses with debug information.

---

## Task: Inspect Envoy Configuration (Cluster Only)

**What this does**: Access Envoy admin API to inspect runtime configuration, routes, and clusters.

**Precondition**:
- Kind cluster running with Envoy DaemonSet
- kubectl available

### Step 1: Forward Envoy Admin Port (SEQUENTIAL)

MUST execute:
```bash
kubectl -n projectcontour port-forward daemonset/envoy 9001:9001
```

Expected: Port forward active, ready for queries.

### Step 2: Inspect Envoy State (NEW TERMINAL - SEQUENTIAL)

**Full Configuration with EDS**:
```bash
http http://localhost:9001/config_dump?include_eds | jq '.configs[].dynamic_active_clusters' | head -50
```

**Route Configuration**:
```bash
http http://localhost:9001/config_dump | jq '.configs[].dynamic_route_configs' | head -50
```

**Cluster Statistics**:
```bash
http http://localhost:9001/clusters
```

**Available Listeners**:
```bash
http http://localhost:9001/listeners
```

Expected: JSON output showing Envoy's current state.

### Step 3: Stop Port Forward (REQUIRED)

Press Ctrl+C in port-forward terminal.

Expected: Port forward terminates cleanly.

---

## Task: View Contour and Envoy Logs

**What this does**: Stream and inspect runtime logs for debugging.

**Precondition**: Kind cluster running, kubectl available

### Step 1: Stream Contour Logs (SEQUENTIAL)

MUST execute:
```bash
kubectl -n projectcontour logs -f deployment/contour --tail=50
```

Expected:
- Real-time log streaming
- Shows Contour startup and configuration updates

Press Ctrl+C to stop streaming.

### Step 2: Stream Envoy Logs (SEQUENTIAL)

MUST execute:
```bash
kubectl -n projectcontour logs -f daemonset/envoy -c envoy --tail=50
```

Expected:
- Real-time Envoy proxy logs
- Shows request processing and configuration updates

### Step 3: View Previous Container Logs (OPTIONAL)

IF pod crashed, view previous container logs:
```bash
kubectl -n projectcontour logs deployment/contour --previous --tail=100
```

Expected: Logs from crashed container instance.

---

## Task: Update Website Documentation

**What this does**: Preview and edit Contour website documentation locally.

**Precondition**: Hugo installed, site/ directory exists

### Step 1: Start Hugo Server (SEQUENTIAL)

MUST execute:
```bash
cd site && hugo server --disableFastRender
```

Expected:
- Hugo builds site
- Output: "Web Server is available at http://localhost:1313"

### Step 2: View Documentation (SEQUENTIAL - NEW BROWSER)

Navigate to: http://localhost:1313

Expected:
- Contour documentation website loads
- Live editing: changes to markdown files auto-reload

### Step 3: Make Edits (SEQUENTIAL)

Edit markdown files in `site/content/` directory.

Expected:
- Changes appear in browser after save (may need refresh)

### Step 4: Stop Server (REQUIRED)

Press Ctrl+C in Hugo terminal.

Expected: Server stops cleanly.

---

## Development Guidelines

**Before Committing**: MUST run `make checkall` successfully

**After Modifying CRDs**: MUST run `make generate` to regenerate code

**New Features**: Add e2e tests in `test/e2e/`

**Code Changes**: Write unit tests in corresponding `*_test.go` files

---

## Resources

- Contour docs: https://projectcontour.io/docs/
- Contour repo: https://github.com/projectcontour/contour
- Contour helm chart: https://github.com/projectcontour/helm-charts
- Contour CRDs: https://projectcontour.io/docs/main/config/api/
- Contour config file and command-line flags: https://projectcontour.io/docs/main/configuration/
- Envoy docs: https://www.envoyproxy.io/docs/envoy/latest/
- Envoy XDS API: https://www.envoyproxy.io/docs/envoy/latest/api-v3/api
- Envoy Admin REST API: https://www.envoyproxy.io/docs/envoy/latest/operations/admin.html
- Envoy statistics: https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_conn_man/stats and https://www.envoyproxy.io/docs/envoy/latest/configuration/upstream/cluster_manager/cluster_stats
- Kind docs: https://kind.sigs.k8s.io/
- Echoserver docs: https://github.com/tsaarni/echoserver
- HTTPie docs: https://httpie.io/docs
- Echoclient docs: https://github.com/tsaarni/echoclient
