#!/bin/bash -ex

command -v kind >/dev/null || { echo ">>> kind not found. Please install it."; exit 1; }
if [[ "$OSTYPE" == "darwin"* ]]; then
  command -v colima >/dev/null || { echo ">>> colima not found. Please install it."; exit 1; }
fi

if [[ "$OSTYPE" == "darwin"* ]]; then
  echo ">>> Ensuring colima is started..."
  colima start
fi

kind get clusters | grep -q contour && { echo ">>> Cluster 'contour' already exists."; exit 0; }

if [[ "$OSTYPE" == "darwin"* ]]; then
  echo ">>> Creating kind cluster 'contour'..."
  kind create cluster --name contour
else
  echo ">>> Creating kind cluster 'contour'..."
  cat <<'EOF' | kind create cluster --config - --name contour
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
fi

if kubectl -n projectcontour get deployment contour &>/dev/null; then
    echo ">>> Contour already installed."
    exit 0
fi

echo ">>> Installing Contour..."
kubectl apply -f https://projectcontour.io/quickstart/contour.yaml
echo ">>> Installing echoserver..."
cat <<'EOF' | kubectl apply -f -
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

echo ">>> Waiting for deployments to be ready..."
kubectl -n projectcontour wait --for=condition=available --timeout=300s deployment/contour
kubectl -n projectcontour wait --for=condition=ready --timeout=300s pod -l app=envoy
kubectl wait --for=condition=ready --timeout=300s pod -l app=echoserver
echo ">>> Cluster setup complete."
