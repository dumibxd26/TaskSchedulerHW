#!/bin/bash
# Script pentru rularea experimentelor cu diferite configurații (replici × core-uri)

# Configurații de testat
REPLICAS=(2 3 5 10 20)
CORES=(2 4 8 16)
DATASET="dataset_fifo_burst_1k.csv"
SCHEDULER_URL="http://localhost:8000"

# Backup original worker.yaml
cp worker.yaml worker.yaml.backup

echo "Starting experiments with all combinations..."
echo "Replicas: ${REPLICAS[@]}"
echo "Cores: ${CORES[@]}"
echo "Dataset: $DATASET"
echo ""

RESULTS_DIR="experiment_results_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULTS_DIR"

# Pentru fiecare combinație
for replicas in "${REPLICAS[@]}"; do
  for cores in "${CORES[@]}"; do
    total_cores=$((replicas * cores))
    config_name="${replicas}replicas_${cores}cores"
    
    echo "=========================================="
    echo "Running: $replicas replicas × $cores cores = $total_cores total cores"
    echo "Config: $config_name"
    echo "=========================================="
    
    # Modifică worker.yaml
    sed -i.bak "s/replicas: [0-9]*/replicas: $replicas/" worker.yaml
    sed -i.bak "s/- name: CORES$/{N;s/- name: CORES\n          value: \"[0-9]*\"/- name: CORES\n          value: \"$cores\"/}" worker.yaml || \
    sed -i.bak "/- name: CORES$/,/value:/ s/value: \"[0-9]*\"/value: \"$cores\"/" worker.yaml
    
    # Deploy workers
    echo "Deploying workers..."
    kubectl apply -f worker.yaml
    
    # Așteaptă ca workers să fie ready
    echo "Waiting for workers to be ready..."
    kubectl wait --for=condition=ready pod -l app=worker-fifo --timeout=60s || echo "Warning: Some pods may not be ready"
    
    sleep 5  # Așteaptă suplimentar pentru conectare
    
    # Rulează experimentul
    echo "Starting run..."
    python submit_runs.py --scheduler "$SCHEDULER_URL" --dataset "$DATASET" --replicas "$replicas" --cores "$cores" > "$RESULTS_DIR/${config_name}.log" 2>&1
    
    # Extrage run_id din log (pentru logging, dar nu mai folosim pentru numele fișierelor)
    run_id=$(grep "Started run_id=" "$RESULTS_DIR/${config_name}.log" | sed 's/.*run_id=\([a-f0-9]*\).*/\1/' | head -1)
    
    if [ ! -z "$run_id" ]; then
      echo "Run ID: $run_id"
    fi
    
    # Găsește scheduler pod
    scheduler_pod=$(kubectl get pods -l app=scheduler-fifo -o jsonpath='{.items[0].metadata.name}')
    
    if [ ! -z "$scheduler_pod" ]; then
      # Construiește numele fișierelor bazat pe dataset (fără extensie)
      dataset_name=$(basename "$DATASET" .csv)
      results_subdir="replicas_${replicas}_cores_${cores}"
      jobs_file="results_jobs_${dataset_name}.csv"
      run_file="results_run_${dataset_name}.csv"
      
      # Copiază rezultatele din folderul structurat
      echo "Copying results..."
      mkdir -p "$RESULTS_DIR/$config_name"
      kubectl cp "$scheduler_pod:/results/${results_subdir}/${jobs_file}" "$RESULTS_DIR/$config_name/results_jobs.csv" 2>/dev/null || echo "Warning: Could not copy jobs CSV"
      kubectl cp "$scheduler_pod:/results/${results_subdir}/${run_file}" "$RESULTS_DIR/$config_name/results_run.csv" 2>/dev/null || echo "Warning: Could not copy run CSV"
      
      echo "Results saved to: $RESULTS_DIR/$config_name/"
    else
      echo "Warning: Could not find scheduler pod"
    fi
    
    echo "Done: $config_name"
    echo ""
    
    # Pauză între experimente
    sleep 2
  done
done

# Restaurează worker.yaml original
mv worker.yaml.backup worker.yaml

echo "=========================================="
echo "All experiments completed!"
echo "Results saved to: $RESULTS_DIR/"
echo "=========================================="
