#!/bin/bash -ex

echo ">>> Deleting kind cluster 'contour'..."

if ! kind get clusters | grep -q "^contour$"; then
    echo ">>> Cluster 'contour' not found. Already deleted."
    exit 0
fi

kind delete cluster --name contour

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo ">>> Stopping colima..."
    colima stop || true
fi

echo ">>> Cleanup complete."
