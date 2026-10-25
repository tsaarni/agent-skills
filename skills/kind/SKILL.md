---
name: kind
description: Use when creating or accessing a local Kind Kubernetes cluster.
---

# Kind Clusters

Each cluster publishes its ports on its own address: a loopback address on Linux, a VM address on macOS (see "macOS (colima)"). Examples use cluster `mycluster` at `127.0.0.101`, with hostnames `*.127-0-0-101.nip.io`.

---

## Create Cluster

List the addresses of existing clusters. Reuse the cluster's address if it already exists, otherwise pick an address not in the list: any `127.x.x.x` on Linux, any `192.168.64.2`-`192.168.64.254` on macOS:

```bash
docker ps --filter label=io.x-k8s.kind.cluster --format '{{.Label "io.x-k8s.kind.cluster"}} {{.Ports}}'
```

```bash
kind get clusters | grep -qx mycluster || kind create cluster --name mycluster --config - <<EOF
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

Options:

- NodePort services on low ports such as 80:
  ```yaml
  kubeadmConfigPatches:
  - |
    kind: ClusterConfiguration
    apiServer:
      extraArgs:
        service-node-port-range: "80-32767"
  ```
- Host directories:
  ```yaml
    extraMounts:
    - hostPath: /absolute/path/on/host
      containerPath: /path/in/node
      readOnly: true
  ```

---

## Reach Host Processes from Pods

Point a selector-less Service at the host's address as seen from pods, the `kind` network gateway:

```bash
docker network inspect kind | jq -r '.[0].IPAM.Config[] | select(.Subnet | contains(":") | not) | .Gateway'
```

The host process must listen on `0.0.0.0`. With gateway `172.18.0.1`:

```bash
cat <<EOF | kubectl apply -f -
kind: Service
apiVersion: v1
metadata:
  name: <service>
  namespace: <namespace>
spec:
  type: ClusterIP
  ports:
  - port: <port>
    targetPort: <port>
---
kind: EndpointSlice
apiVersion: discovery.k8s.io/v1
metadata:
  name: <service>-1
  namespace: <namespace>
  labels:
    kubernetes.io/service-name: <service>
addressType: IPv4
endpoints:
- addresses:
  - 172.18.0.1
ports:
- port: <port>
EOF
```

---

## Load Local Images

```bash
kind load docker-image <image>:<tag> --name mycluster
```

---

## macOS (colima)

Docker runs inside the colima VM, so published ports land in the VM. macOS has only `127.0.0.1` on `lo0` (more needs root), so loopback addresses like `127.0.0.101` are unreachable from the Mac.

Instead, the VM gets an address on a `vzNAT` network that the Mac reaches directly (no root, needs `vmType: vz`), and claims all of `192.168.64.0/24` as local except the Mac's `192.168.64.1`. Any of these can be a cluster's `listenAddress` and is reachable at the same address from the Mac, on any port and for any protocol.

**On macOS, replace `127.0.0.X` with `192.168.64.X`** wherever instructions use it (also in hostnames): `127.0.0.101` becomes `192.168.64.101`, `*.127-0-0-101.nip.io` becomes `*.192-168-64-101.nip.io`. X can be 2 to 254.

Check the setup:

```bash
colima ssh -- ip route show table local | grep -q '^local 192.168.64.0/24 ' && echo OK
```

If not OK, set up once and restart colima (restarts all Kind clusters):

1. `~/.colima/_lima/_config/override.yaml` (lima override, merged into colima's VM config):
   ```yaml
   networks:
   - vzNAT: true
   ```
2. `provision` in `~/.colima/default/colima.yaml`, runs at every VM start before Docker:
   ```yaml
   provision:
     - mode: after-boot
       script: |
         sudo ip route replace local 192.168.64.0/24 dev lo
         sudo ip route replace 192.168.64.1 dev lima1 table local
   ```
3. `colima stop && colima start`

The vzNAT subnet is chosen by macOS and is normally `192.168.64.0/24`. Check with `colima ssh -- ip -4 -br addr show lima1`; if it differs, use that subnet instead. Other VMs on vzNAT would conflict with the claimed subnet.

Differences from the sections above:

- The host as seen from pods is `192.168.5.2`, not the `kind` network gateway shown in "Reach Host Processes from Pods".
- `extraMounts` `hostPath` must be under `$HOME`. colima shares only `$HOME` with the VM where the nodes run, so paths like `/tmp` mount an empty directory.
