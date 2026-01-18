#!/bin/bash
# Script pentru deploy în Kubernetes și port-forward în background

set -e  # Exit on error

SCHEDULER_NAME="scheduler-svc-fifo"
LOCAL_PORT=8000
REMOTE_PORT=8000
NAMESPACE="${NAMESPACE:-default}"
PORT_FORWARD_PID_FILE="/tmp/fifo-port-forward.pid"

echo "=========================================="
echo "FIFO Scheduler - Kubernetes Deploy (Background)"
echo "=========================================="
echo ""

# Funcție pentru cleanup la exit
cleanup() {
    echo ""
    echo "Cleaning up..."
    if [ -f "$PORT_FORWARD_PID_FILE" ]; then
        PORT_FORWARD_PID=$(cat "$PORT_FORWARD_PID_FILE")
        if kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
            echo "Stopping port-forward (PID: $PORT_FORWARD_PID)..."
            kill "$PORT_FORWARD_PID" 2>/dev/null || true
        fi
        rm -f "$PORT_FORWARD_PID_FILE"
    fi
}

trap cleanup EXIT INT TERM

# Verifică că kubectl este disponibil
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found. Please install kubectl."
    exit 1
fi

# Verifică dacă port-forward este deja activ
if [ -f "$PORT_FORWARD_PID_FILE" ]; then
    OLD_PID=$(cat "$PORT_FORWARD_PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Port-forward already running (PID: $OLD_PID)"
        echo "Stopping old port-forward..."
        kill "$OLD_PID" 2>/dev/null || true
        rm -f "$PORT_FORWARD_PID_FILE"
    fi
fi

echo ""
echo "Step 0: Checking if ConfigMap exists..."
if ! kubectl get configmap sim-datasets-fifo &>/dev/null; then
    echo "Warning: ConfigMap 'sim-datasets-fifo' not found!"
    echo "Please create it first with:"
    echo "  ./setup_configmap.sh dataset_fifo_1k.csv"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "ConfigMap 'sim-datasets-fifo' found."
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
echo "Scheduler:"
kubectl get pods -l app=scheduler-fifo
echo ""
echo "Workers:"
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
echo "Step 7: Starting port-forward in background..."
kubectl port-forward pod/${SCHEDULER_POD} ${LOCAL_PORT}:${REMOTE_PORT} > /tmp/fifo-port-forward.log 2>&1 &
PORT_FORWARD_PID=$!
echo $PORT_FORWARD_PID > "$PORT_FORWARD_PID_FILE"

# Așteaptă puțin pentru port-forward să se stabilească
sleep 2

# Verifică dacă port-forward-ul încă rulează
if ! kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
    echo "Error: Port-forward failed to start"
    cat /tmp/fifo-port-forward.log
    exit 1
fi

echo ""
echo "=========================================="
echo "Deploy complete!"
echo "=========================================="
echo "Scheduler available at: http://localhost:${LOCAL_PORT}"
echo "Port-forward PID: $PORT_FORWARD_PID"
echo "Port-forward log: /tmp/fifo-port-forward.log"
echo ""
echo "To stop port-forward, run:"
echo "  ./stop_port_forward.sh"
echo "  OR"
echo "  kill $(cat $PORT_FORWARD_PID_FILE)"
echo ""
echo "To check status:"
echo "  kubectl get pods -l app=scheduler-fifo"
echo "  kubectl get pods -l app=worker-fifo"
echo ""
echo "To view logs:"
echo "  kubectl logs -f <scheduler-pod>"
echo "  kubectl logs -f <worker-pod>"
echo ""
echo "To test, run:"
echo "  python submit_runs.py --scheduler http://localhost:${LOCAL_PORT} --dataset <dataset>.csv"
echo "=========================================="
echo ""

# Script-ul iese, dar port-forward-ul continuă în background
exit 0
