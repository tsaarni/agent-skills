#!/bin/bash -ex

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS (colima/lima host IP: 192.168.5.2)
    REPLACE_ADDRESS="192.168.5.2"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    REPLACE_ADDRESS=$(docker network inspect kind | jq -r '.[0].IPAM.Config[0].Gateway')
fi

echo ">>> Configuring network connectivity for $OSTYPE (address: $REPLACE_ADDRESS)"
cat <<'EOF' | sed "s|REPLACE_ADDRESS_HERE|$REPLACE_ADDRESS|" | kubectl apply -f -
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
  - REPLACE_ADDRESS_HERE
ports:
- port: 8001
EOF

echo ">>> Scaling down in-cluster Contour and restarting Envoy..."
kubectl -n projectcontour scale deployment contour --replicas=0
kubectl -n projectcontour delete daemonset -l app=envoy --force

echo ">>> Extracting Contour's TLS certificates for local testing..."

kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.ca\.crt}' | base64 -d > ca.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.crt}' | base64 -d > tls.crt
kubectl -n projectcontour get secret contourcert -o jsonpath='{.data.tls\.key}' | base64 -d > tls.key
