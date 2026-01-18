#!/bin/bash
# Script pentru oprirea port-forward-ului

PORT_FORWARD_PID_FILE="/tmp/fifo-port-forward.pid"

if [ -f "$PORT_FORWARD_PID_FILE" ]; then
    PORT_FORWARD_PID=$(cat "$PORT_FORWARD_PID_FILE")
    if kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
        echo "Stopping port-forward (PID: $PORT_FORWARD_PID)..."
        kill "$PORT_FORWARD_PID"
        rm -f "$PORT_FORWARD_PID_FILE"
        echo "Port-forward stopped."
    else
        echo "Port-forward process not running."
        rm -f "$PORT_FORWARD_PID_FILE"
    fi
else
    echo "No port-forward PID file found. Port-forward may not be running."
    
    # Caută procese kubectl port-forward
    PIDS=$(pgrep -f "kubectl port-forward.*scheduler.*8000" || true)
    if [ -n "$PIDS" ]; then
        echo "Found kubectl port-forward processes: $PIDS"
        read -p "Kill these processes? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "$PIDS" | xargs kill
            echo "Processes killed."
        fi
    fi
fi
