---
name: contour-development
description: Use when needing development environment with Kind to do development, debug/troubleshoot, and creating custom container images.
---

# Contour Development

Verify working directory before any task:

```bash
test -f go.mod && grep -q 'module github.com/projectcontour/contour' go.mod && echo "OK" || echo "NOT in Contour root"
```

Use `http` (httpie) instead of `curl` for testing endpoints.

---

## Create Kind Cluster

```bash
kind get clusters | grep -q contour && { echo "Cluster 'contour' already exists."; exit 0; }

cat <<EOF | kind create cluster --config - --name contour
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    listenAddress: "127.0.0.101"
  - containerPort: 443
    hostPort: 443
    listenAddress: "127.0.0.101"
EOF
```

Install Contour:

```bash
kubectl apply -f https://projectcontour.io/quickstart/contour.yaml
kubectl -n projectcontour wait --for=condition=available --timeout=300s deployment/contour
kubectl -n projectcontour wait --for=condition=ready --timeout=300s pod -l app=envoy
```


To use the latest unreleased development build from `main` branch use

```bash
kubectl apply -f https://raw.githubusercontent.com/projectcontour/contour/main/examples/render/contour.yaml
```

---

## Deploy Echoserver

```bash
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: echoserver
spec:
  selector:
    matchLabels:
      app: echoserver
  template:
    metadata:
      labels:
        app: echoserver
    spec:
      containers:
      - name: echoserver
        image: ghcr.io/tsaarni/echoserver:latest
        env:
        - name: ENV_POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: ENV_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: ENV_POD_IP
          valueFrom:
            fieldRef:
              fieldPath: status.podIP
        - name: ENV_NODE_NAME
          valueFrom:
            fieldRef:
              fieldPath: spec.nodeName
        - name: ENV_POD_UID
          valueFrom:
            fieldRef:
              fieldPath: metadata.uid
        ports:
        - name: http-api
          containerPort: 8080
        - name: https-api
          containerPort: 8443
---
apiVersion: v1
kind: Service
metadata:
  name: echoserver
spec:
  ports:
  - name: http
    port: 80
    targetPort: http-api
  - name: http2
    port: 443
    targetPort: https-api
  selector:
    app: echoserver
---
apiVersion: projectcontour.io/v1
kind: HTTPProxy
metadata:
  name: echoserver
spec:
  virtualhost:
    fqdn: echoserver.127-0-0-101.nip.io
  routes:
    - services:
        - name: echoserver
          port: 80
EOF

kubectl wait --for=condition=ready --timeout=300s pod -l app=echoserver
```

---

## Delete Kind Cluster

```bash
kind delete cluster --name contour
```

---

## Run Contour from Source on Host Against Kind Cluster

Patch cluster networking so Envoy connects to Contour running on the host. Run after creating the cluster and before starting Contour on host.

Get the host gateway address:

```bash
HOST_IP=$(docker network inspect kind | jq -r '.[0].IPAM.Config[0].Gateway')
```

Apply service and endpoint pointing to host:

```bash
cat <<EOF | kubectl apply -f -
kind: Service
apiVersion: v1
metadata:
  name: contour
  namespace: projectcontour
spec:
  type: ClusterIP
  ports:
  - port: 8001
    targetPort: 8001
---
kind: EndpointSlice
apiVersion: discovery.k8s.io/v1
metadata:
  name: contour-1
  namespace: projectcontour
  labels:
    kubernetes.io/service-name: contour
addressType: IPv4
endpoints:
- addresses:
  - ${HOST_IP}
ports:
- port: 8001
EOF
```

Scale down in-cluster Contour and restart Envoy:

```bash
kubectl -n projectcontour scale deployment contour --replicas=0
kubectl -n projectcontour delete daemonset -l app=envoy --force
```

Extract TLS certificates for local Contour:

```bash
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.ca\.crt}' | base64 -d > ca.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.crt}' | base64 -d > tls.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.key}' | base64 -d > tls.key
```

Run Contour from source on host:

```bash
cd ~/work/contour && runagent run -n contour -- go run ./cmd/contour serve \
    --xds-address=0.0.0.0 \
    --xds-port=8001 \
    --envoy-service-http-port=8080 \
    --envoy-service-https-port=8443 \
    --contour-cafile=ca.crt \
    --contour-cert-file=tls.crt \
    --contour-key-file=tls.key \
    --debug
```

To test config file add `--config-path=/tmp/myconfig.yaml` to the command above.

To test `ContourConfiguration` add `--contour-config-name=contour` to the command above and create resource in cluster:

```yaml
kind: ContourConfiguration
metadata:
  name: contour
  namespace: projectcontour
spec:
  xdsServer:
    address: 0.0.0.0
    port: 8001
    tls:
      caFile: ca.crt
      certFile: tls.crt
      keyFile: tls.key
  envoy:
    http:
      port: 8080
    https:
      port: 8443
```

### VS Code Debugger

If user asks to set up VS code debugging for Contour, create `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run contour",
      "type": "go",
      "request": "launch",
      "mode": "auto",
      "cwd": "${workspaceRoot}",
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
```

---

## Build and Deploy Custom Contour Image

Build from source and deploy into the Kind cluster for in-cluster testing.

```bash
make container VERSION=latest
kind load docker-image ghcr.io/projectcontour/contour:latest --name contour
```

Patch deployments to use local image (first time only):

```bash
kubectl -n projectcontour patch deployment contour --patch '
spec:
  template:
    spec:
      containers:
      - name: contour
        image: localhost/contour:latest
        imagePullPolicy: Never'

kubectl -n projectcontour patch daemonset envoy --patch '
spec:
  template:
    spec:
      containers:
      - name: shutdown-manager
        image: localhost/contour:latest
        imagePullPolicy: Never
      initContainers:
      - name: envoy-initconfig
        image: localhost/contour:latest
        imagePullPolicy: Never'
```

Wait for rollout:

```bash
kubectl -n projectcontour rollout status deployment/contour --timeout=2m
kubectl -n projectcontour rollout status daemonset/envoy --timeout=2m
```

For subsequent code changes, rebuild and restart:

```bash
make container VERSION=latest
kind load docker-image ghcr.io/projectcontour/contour:latest --name contour
kubectl -n projectcontour rollout restart deployment/contour
kubectl -n projectcontour rollout status deployment/contour --timeout=2m
```

---

## Run Unit Tests and Linting

```bash
make checkall    # All checks (unit tests + lint + generated code diff)
make check       # Unit tests only
make lint        # Lint only
make generate    # Regenerate code after CRD changes
```

---

## Run End-to-End Tests (Makefile Workflow)

Use the Makefile targets that manage a separate `contour-e2e` Kind cluster.

```bash
make setup-kind-cluster load-contour-image-kind   # Create cluster and build/load image
make run-e2e                                       # Run all e2e tests
```

Run focused tests:

```bash
make run-e2e CONTOUR_E2E_TEST_FOCUS="httpproxy-jwt"
make run-e2e CONTOUR_E2E_PACKAGE_FOCUS=./test/e2e/httpproxy CONTOUR_E2E_TEST_FOCUS="httpproxy-jwt"
```

For verbose output, run ginkgo directly:

```bash
CONTOUR_E2E_LOCAL_HOST=$(make -s print-local-ip) \
CONTOUR_E2E_IMAGE=ghcr.io/projectcontour/contour:main \
go run github.com/onsi/ginkgo/v2/ginkgo -tags=e2e -vv -poll-progress-after=120s \
  --focus "httpproxy-jwt" ./test/e2e/httpproxy/
```

After code changes, rebuild image then re-run tests:

```bash
make load-contour-image-kind
make run-e2e
```

Clean up: `make cleanup-kind`

---

## Run End-to-End Tests (Development Cluster)

Run e2e tests against the scripts-based development cluster.

```bash
CONTOUR_E2E_LOCAL_HOST=127.0.0.101 make run-e2e
CONTOUR_E2E_TEST_FOCUS="some test name" CONTOUR_E2E_LOCAL_HOST=127.0.0.101 make run-e2e
```

If tests fail mid-run and leave namespaces behind:

```bash
kubectl delete ns <test-namespace> --ignore-not-found
```

---

## Test Traffic

```bash
http http://echoserver.127-0-0-101.nip.io/host
```

Load test: see `echoclient` skill for more info.

---

## Inspect Contour Debug Endpoints

If Contour runs in-cluster, port-forward first:

```bash
runagent run -n contour-debug-port-forward -- kubectl -n projectcontour port-forward deployment/contour 8000:8000
```

Then use the debug API:

```bash
http localhost:8000/metrics | head -50
http localhost:8000/debug/dag | jq .
http localhost:8000/debug/pprof/
```

---

## Inspect Envoy Admin API

```bash
runagent run -n envoy-admin-port-forward -- kubectl -n projectcontour port-forward daemonset/envoy 9001:9001
```

Then use the admin API

```bash
http http://localhost:9001/config_dump?include_eds | jq '.configs[].dynamic_active_clusters'
http http://localhost:9001/config_dump | jq '.configs[].dynamic_route_configs'
http http://localhost:9001/clusters
http http://localhost:9001/listeners
```

---

## View Logs

```bash
kubectl -n projectcontour logs -f deployment/contour --tail=50
kubectl -n projectcontour logs -f daemonset/envoy -c envoy --tail=50
kubectl -n projectcontour logs deployment/contour --previous --tail=100  # After crash
```

---

## VS Code Setup for E2E Tests

Create `.vscode/settings.json` to enable Go language server support for e2e test files:

```json
{
  "go.buildFlags": ["-tags=e2e"]
}
```

---

## Website Documentation

Run the doc site locally 

```bash
runagent run -n hugo-site --cwd /home/tsaarni/work/contour/site -- hugo server --disableFastRender 
```

View at http://localhost:1313. Edit markdown in `site/content/`.

---

## Development Rules

- Run `make generate` after modifying CRDs.
- Add e2e tests in `test/e2e/` for new features.
- Write unit tests in corresponding `*_test.go` files.

---

## Resources

- Contour docs: https://projectcontour.io/docs/
- Contour repo: https://github.com/projectcontour/contour
- Contour CRDs: https://projectcontour.io/docs/main/config/api/
- Contour config: https://projectcontour.io/docs/main/configuration/
- Envoy docs: https://www.envoyproxy.io/docs/envoy/latest/
- Envoy XDS API: https://www.envoyproxy.io/docs/envoy/latest/api-v3/api
- Envoy admin: https://www.envoyproxy.io/docs/envoy/latest/operations/admin.html
- Kind docs: https://kind.sigs.k8s.io/
- Echoserver: https://github.com/tsaarni/echoserver
- Echoclient: https://github.com/tsaarni/echoclient
- HTTPie: https://httpie.io/docs
