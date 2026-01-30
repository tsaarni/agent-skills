---
name: contour
description: Contour ingress controller and Envoy proxy development environment setup, testing, and troubleshooting
---

# Contour Development

## Testing (No Cluster Required)

```shell
make check      # Unit tests only
make lint       # Linting
make generate   # Code generation (run after modifying CRDs)
make checkall   # All of the above (run before commits)
```

## Testing (Requires Kind Cluster)

```shell
make run-e2e    # End-to-end tests
```

## Kind Cluster Setup

**Purpose**: Create local Kubernetes cluster for Contour testing and development.

**macOS prerequisite**: `colima start`

```shell
kind create cluster --config assets/kind-cluster-config-linux.yaml --name contour # Linux
# kind create cluster --name contour # macOS
kubectl cluster-info --context kind-contour
kubectl apply -f https://projectcontour.io/quickstart/contour.yaml
kubectl -n projectcontour wait --for=condition=available --timeout=300s deployment/contour
kubectl -n projectcontour wait --for=condition=ready --timeout=300s pod -l app=envoy
```

**Cleanup**:
```shell
kind delete cluster --name contour
colima stop  # macOS only
```

## Run Contour from Source (Host) + Envoy (Cluster)

**Purpose**: Local Contour development with rapid iteration and debugging.

Steps 1-3 are one-time setup. Step 4 is repeated for each code change.

### 1. Configure Network Connectivity

**Linux**:
```shell
sed "s/REPLACE_ADDRESS_HERE/$(docker network inspect kind | jq -r '.[0].IPAM.Config[0].Gateway')/" assets/contour-endpoints-dev.yaml | kubectl apply -f -
```

**macOS** (colima/lima host IP: `192.168.5.2`):
```shell
sed "s/REPLACE_ADDRESS_HERE/192.168.5.2/" assets/contour-endpoints-dev.yaml | kubectl apply -f -
```

### 2. Scale Down In-Cluster Contour

```shell
kubectl -n projectcontour scale deployment contour --replicas=0
kubectl -n projectcontour rollout restart daemonset envoy
kubectl -n projectcontour rollout status daemonset envoy
```

### 3. Extract TLS Certificates

**macOS**: Use `base64 -D` (not `-d`).

```shell
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.ca\.crt}' | base64 -d > ca.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.crt}' | base64 -d > tls.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.key}' | base64 -d > tls.key
```

### 4. Run Contour

**CLI**:
```shell
go run github.com/projectcontour/contour/cmd/contour serve \
  --xds-address=0.0.0.0 \
  --xds-port=8001 \
  --envoy-service-http-port=8080 \
  --envoy-service-https-port=8443 \
  --contour-cafile=ca.crt \
  --contour-cert-file=tls.crt \
  --contour-key-file=tls.key
```

**VSCode Debugger**:
```shell
mkdir -p .vscode
cp assets/contour-vscode-launch.json .vscode/launch.json
cp assets/vscode-settings.json .vscode/settings.json
```
Start debug session using `workbench.action.debug.start` command.

## Build and Deploy Custom Contour Image to Cluster

**Purpose**: Test Contour changes running fully in-cluster.

Step 1 is repeated for each code change. Steps 2 and 3 are one-time setup.

### 1. Build and Load Image

```shell
make container VERSION=latest
kind load docker-image ghcr.io/projectcontour/contour:latest --name contour
```

### 2. Patch Deployments

```shell
kubectl -n projectcontour patch deployment contour --patch-file=assets/contour-deployment-patch.yaml
kubectl -n projectcontour patch daemonset envoy --patch-file=assets/envoy-daemonset-patch.yaml
```

### 3. Verify

```shell
kubectl -n projectcontour rollout status deployment/contour
kubectl -n projectcontour rollout status daemonset/envoy
kubectl -n projectcontour get pods -o wide
kubectl -n projectcontour describe pod -l app=contour | grep Image:
```

## Troubleshooting

**Purpose**: Diagnose Contour and Envoy runtime issues, inspect configuration, analyze metrics.

### Logs

```shell
kubectl -n projectcontour logs -f deployment/contour                    # Contour logs (stream)
kubectl -n projectcontour logs -f daemonset/envoy -c envoy              # Envoy logs (stream)
kubectl -n projectcontour logs -f daemonset/envoy -c shutdown-manager   # Shutdown manager (stream)
kubectl -n projectcontour logs -l app=contour --tail=100                # All Contour pods
kubectl -n projectcontour logs deployment/contour --previous            # Previous container (if crashed)
```

### Contour Metrics and Debug

```shell
kubectl -n projectcontour port-forward deployment/contour 8000:8000
http localhost:8000/metrics       # Prometheus metrics
http localhost:8000/debug/pprof/  # Debug info
http localhost:8000/debug/dag     # Internal DAG state
```

### Envoy Admin API

**Purpose**: Inspect Envoy runtime configuration, clusters, routes, and statistics.

```shell
kubectl -n projectcontour port-forward daemonset/envoy 9001:9001
http http://localhost:9001/config_dump?include_eds | jq -C . | less  # Full config dump with EDS
http http://localhost:9001/config_dump | jq '.configs[].dynamic_active_clusters'  # Active clusters
http http://localhost:9001/config_dump | jq '.configs[].dynamic_route_configs'    # Route configs
http http://localhost:9001/clusters      # Cluster statistics
http http://localhost:9001/listeners     # Listener statistics
http http://localhost:9001/server_info   # Server info
http http://localhost:9001/stats         # All stats
http http://localhost:9001/help          # Available endpoints
```

## Deploy Specific Contour Version

**Purpose**: Test compatibility, reproduce bugs, or regression testing against specific releases.

```shell
kubectl apply -f https://projectcontour.io/quickstart/v1.28.0/contour.yaml
# Available versions: https://raw.githubusercontent.com/projectcontour/contour/refs/heads/main/versions.yaml
```

## Custom Contour Configuration

**Purpose**: Modify global Contour behavior (timeouts, logging, etc).

Three methods: ConfigMap, ContourConfiguration CRD, or command-line flags.

### Configuration File (ConfigMap)

```shell
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: contour
  namespace: projectcontour
data:
  contour.yaml: |
    # Consult Contour configuration docs for available fields: https://projectcontour.io/docs/main/configuration/
    <configure-as-needed>
EOF

kubectl -n projectcontour delete pod -l app=contour  # Restart Contour to pick up changes
```

### ContourConfiguration CRD

```shell
cat <<EOF | kubectl apply -f -
apiVersion: projectcontour.io/v1alpha1
kind: ContourConfiguration
metadata:
  name: contour
  namespace: projectcontour
spec:
  # Consult ContourConfiguration API for available fields: https://projectcontour.io/docs/main/config/api/
  <configure-as-needed>
EOF

kubectl -n projectcontour delete pod -l app=contour  # Restart Contour to pick up changes
```

### Command-Line Flags

```shell
kubectl -n projectcontour patch deployment contour --type=json -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/containers/0/args/-",
    "value": "--debug"
  }
]'
```

When running from source, add flags to `go run` command or launch configuration.

## Update Website Documentation

```shell
cd site && hugo server --disableFastRender  # Starts at http://localhost:1313
```

## Development Guidelines

**New configuration options** - implement in both methods:
- ConfigMap: [pkg/config/parameters.go](pkg/config/parameters.go) + document in [site/content/docs/main/configuration.md](site/content/docs/main/configuration.md)
- CRD: [apis/projectcontour/v1alpha1/contourconfig.go](apis/projectcontour/v1alpha1/contourconfig.go)

**After dependency updates** (client-go, controller-runtime): Run `make generate` to update CRDs.

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
