#!/bin/bash
# Script pentru deploy în Kubernetes și port-forward pentru FIFO scheduler

set -e  # Exit on error

SCHEDULER_NAME="scheduler-svc-fifo"
LOCAL_PORT=8000
REMOTE_PORT=8000
NAMESPACE="${NAMESPACE:-default}"

echo "=========================================="
echo "FIFO Scheduler - Kubernetes Deploy"
echo "=========================================="
echo ""

# Verifică că kubectl este disponibil
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found. Please install kubectl."
    exit 1
fi

# Verifică că imagina Docker există sau este disponibilă
echo "Checking if Docker image 'fifo-sim:latest' exists locally..."
if ! docker images | grep -q "fifo-sim.*latest"; then
    echo "Warning: Docker image 'fifo-sim:latest' not found locally."
    echo "You may need to build it first with: docker build -t fifo-sim:latest ."
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "Step 1: Deploying scheduler..."
kubectl apply -f scheduler.yaml

echo ""
echo "Step 2: Deploying workers..."
kubectl apply -f worker.yaml

echo ""
echo "Step 3: Waiting for scheduler to be ready..."
kubectl wait --for=condition=ready pod -l app=scheduler-fifo --timeout=60s || {
    echo "Warning: Scheduler pod may not be ready yet"
}

echo ""
echo "Step 4: Waiting for workers to be ready..."
kubectl wait --for=condition=ready pod -l app=worker-fifo --timeout=120s || {
    echo "Warning: Some worker pods may not be ready yet"
}

echo ""
echo "Step 5: Checking pod status..."
kubectl get pods -l app=scheduler-fifo
kubectl get pods -l app=worker-fifo

echo ""
echo "Step 6: Getting scheduler pod name..."
SCHEDULER_POD=$(kubectl get pods -l app=scheduler-fifo -o jsonpath='{.items[0].metadata.name}')
if [ -z "$SCHEDULER_POD" ]; then
    echo "Error: Could not find scheduler pod"
    exit 1
fi
echo "Scheduler pod: $SCHEDULER_POD"

echo ""
echo "Step 7: Starting port-forward..."
echo "Forwarding: localhost:${LOCAL_PORT} -> ${SCHEDULER_POD}:${REMOTE_PORT}"
echo ""
echo "=========================================="
echo "Port-forward active!"
echo "Scheduler available at: http://localhost:${LOCAL_PORT}"
echo ""
echo "To test, run in another terminal:"
echo "  python submit_runs.py --scheduler http://localhost:${LOCAL_PORT} --dataset <dataset>.csv"
echo ""
echo "Press Ctrl+C to stop port-forward and exit"
echo "=========================================="
echo ""

# Port-forward în foreground (blochează până la Ctrl+C)
kubectl port-forward pod/${SCHEDULER_POD} ${LOCAL_PORT}:${REMOTE_PORT}
