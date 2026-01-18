#!/bin/bash
# Script pentru crearea ConfigMap-ului cu dataset-ul

DATASET_FILE="${1:-dataset_fifo_1k.csv}"
CONFIGMAP_NAME="sim-datasets-fifo"

if [ ! -f "$DATASET_FILE" ]; then
    echo "Error: Dataset file '$DATASET_FILE' not found!"
    echo "Usage: ./setup_configmap.sh [dataset_file.csv]"
    exit 1
fi

echo "Creating ConfigMap '$CONFIGMAP_NAME' from '$DATASET_FILE'..."

# Verifică că kubectl este disponibil
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl not found. Please install kubectl or use PowerShell version: setup_configmap.ps1"
    exit 1
fi

# Creează ConfigMap din fișier
kubectl create configmap "$CONFIGMAP_NAME" \
    --from-file="$DATASET_FILE" \
    --dry-run=client -o yaml | kubectl apply -f -

if [ $? -eq 0 ]; then
    echo ""
    echo "ConfigMap created/updated successfully!"
    echo "To verify: kubectl get configmap $CONFIGMAP_NAME"
    echo "To view: kubectl describe configmap $CONFIGMAP_NAME"
else
    echo ""
    echo "Error: Failed to create ConfigMap"
    exit 1
fi
