# FIFO Task Scheduler

Implementare algoritm FIFO (First-In-First-Out) pentru schedularea task-urilor în sisteme distribuite.

## Structura Proiectului

```
fifo/
├── scheduler.py          # Master/Scheduler - distribuie task-uri FIFO
├── worker.py             # Workers/Slaves - execută task-uri
├── submit_runs.py        # Client pentru a porni și monitoriza run-uri
├── generate_dataset.py   # Script pentru generare CSV cu overlapping
├── Dockerfile            # Imagine Docker pentru scheduler și worker
├── requirements.txt      # Dependențe Python
├── scheduler.yaml        # Kubernetes Deployment pentru scheduler
└── worker.yaml           # Kubernetes Deployment pentru workers
```

## Componente Principale

### 1. Scheduler (`scheduler.py`)
- **Rol**: Master care distribuie task-uri folosind algoritm FIFO
- **Funcționalități**:
  - Gestionează coada FIFO de job-uri
  - Implementează arrival time progresiv (job-urile apar la `arrival_time_ms` din CSV)
  - Colectează metrici: waiting_time, execution_time, response_time
  - Salvează rezultate în CSV cu CPU/memory usage

### 2. Worker (`worker.py`)
- **Rol**: Worker care execută task-uri atribuite de scheduler
- **Funcționalități**:
  - Execuție non-preemptivă (FIFO: job-ul rulează complet până la finalizare)
  - Colectare metrici CPU/memory folosind `psutil`
  - Raportează finalizarea și metrici la scheduler

### 3. Generate Dataset (`generate_dataset.py`)
- **Rol**: Generează CSV cu job-uri care se suprapun (overlapping)
- **Parametri**:
  - `--num-jobs`: Număr de job-uri (ex: 1000, 10000)
  - `--arrival-interval-min/max`: Interval între arrivări (ms) - cât timp între job-uri
  - `--service-time-min/max`: Durată execuție per job (ms) - cât durează fiecare job
  - **Overlapping garantat**: `service_time > arrival_interval` înseamnă că job-urile se suprapun și se formează coadă

### 4. Submit Runs (`submit_runs.py`)
- **Rol**: Client care pornește un run și așteaptă finalizarea
- **Parametri**:
  - `--scheduler`: URL scheduler (ex: `http://localhost:8000`)
  - `--dataset`: Fișier CSV cu job-uri
  - `--speedup`: Multiplicator simulare (20000 = 20000x mai rapid)
  - `--min-slots`: Număr minim de core-uri necesare

## Quick Start (Deploy în Kubernetes)

**Cel mai rapid mod de a începe**:

```bash
# 1. Construiește imaginea Docker
cd fifo
docker build -t fifo-sim:latest .

# 2. Generează dataset (opțional - dacă nu ai deja)
python generate_dataset.py --output dataset_fifo_1k.csv --num-jobs 1000

# 3. Deploy în Kubernetes cu port-forward automat
chmod +x deploy_background.sh
./deploy_background.sh

# 4. În alt terminal, rulează experimentul
python submit_runs.py --scheduler http://localhost:8000 --dataset dataset_fifo_1k.csv

# 5. Când termini, oprește port-forward
./stop_port_forward.sh
```

Scheduler-ul va fi disponibil pe `http://localhost:8000` după deploy.

## Pași de Rulare (Detaliat)

### 1. Generare Dataset

Generează un CSV cu job-uri care se suprapun:

```bash
python fifo/generate_dataset.py \
  --output dataset_fifo_1k.csv \
  --num-jobs 1000 \
  --arrival-interval-min 10 \
  --arrival-interval-max 50 \
  --service-time-min 100 \
  --service-time-max 500
```

**Explicații parametri**:
- `--num-jobs 1000`: Generează 1000 job-uri
- `--arrival-interval-min 10`: Minim 10ms între arrivări
- `--arrival-interval-max 50`: Maxim 50ms între arrivări
- `--service-time-min 100`: Fiecare job durează minim 100ms
- `--service-time-max 500`: Fiecare job durează maxim 500ms

**De ce overlapping?**
- Dacă `service_time > arrival_interval`, job-urile se suprapun
- Ex: job vine la 0ms (100ms), următorul la 20ms (150ms) → al doilea așteaptă în coadă
- Asta simulează scenarii reale unde job-urile vin mai rapid decât pot fi procesate

### 2. Rulare Locală (fără Kubernetes)

#### A. Pornește Scheduler
```bash
cd fifo
python -m uvicorn scheduler:app --host 0.0.0.0 --port 8000
```

#### B. Pornește Worker (în alt terminal)
```bash
cd fifo
export SCHEDULER_URL=http://localhost:8000
export WORKER_ID=worker-1
export CORES=2
python -m uvicorn worker:app --host 0.0.0.0 --port 8001
```

#### C. Pornește Run
```bash
python fifo/submit_runs.py \
  --scheduler http://localhost:8000 \
  --dataset dataset_fifo_1k.csv \
  --speedup 20000 \
  --min-slots 2
```

### 3. Rulare în Kubernetes

#### A. Construiește Imaginea Docker

```bash
cd fifo
docker build -t fifo-sim:latest .
```

#### B. Configurează Numărul de Replici și Core-uri

Modifică `worker.yaml` pentru a schimba:
- **Numărul de replici** (workers): `replicas: 2`
- **Numărul de core-uri per worker**: `CORES: "2"`

**Exemplu - 5 workers cu 4 core-uri fiecare**:
```yaml
# worker.yaml
spec:
  replicas: 5  # ← 5 workers
  ...
  env:
  - name: CORES
    value: "4"  # ← 4 core-uri per worker
```

**Total core-uri**: 5 workers × 4 core-uri = **20 core-uri totale**

#### C. Deploy în Kubernetes (Opțiune 1: Script Automat)

**Recomandat**: Folosește scriptul `deploy_background.sh` pentru deploy automat și port-forward în background:

```bash
cd fifo
chmod +x deploy_background.sh
./deploy_background.sh
```

Acest script:
- Deploy scheduler și workers în Kubernetes
- Așteaptă ca pods să fie ready
- Face port-forward pe `localhost:8000` în background
- Scheduler-ul este disponibil pe `http://localhost:8000`

**Pentru oprire**:
```bash
./stop_port_forward.sh
```

**Opțiune 2: Deploy Manual**

```bash
# Deploy scheduler
kubectl apply -f fifo/scheduler.yaml

# Deploy workers
kubectl apply -f fifo/worker.yaml

# Verifică status
kubectl get pods
kubectl get svc

# Port-forward manual (într-un terminal separat)
kubectl port-forward svc/scheduler-svc-fifo 8000:8000
```

**Opțiunea 3: Deploy cu Port-Forward în Foreground**

Dacă vrei să vezi logurile port-forward-ului:

```bash
cd fifo
chmod +x deploy.sh
./deploy.sh
```

Acest script face deploy și rămâne activ cu port-forward în foreground (blochează terminalul până la Ctrl+C).

#### D. Pornește Run

**Dacă ai folosit `deploy_background.sh` sau ai făcut port-forward manual**, scheduler-ul este disponibil pe `http://localhost:8000`:

**Opțiunea 1: Port-forward**
```bash
kubectl port-forward svc/scheduler-svc-fifo 8000:8000
python fifo/submit_runs.py --scheduler http://localhost:8000 --dataset dataset_fifo_1k.csv
```

**Opțiunea 2: Execuție în pod**
```bash
kubectl exec -it <scheduler-pod> -- python submit_runs.py \
  --scheduler http://localhost:8000 \
  --dataset /data/dataset_fifo_1k.csv
```

#### E. Colectează Rezultatele

Rezultatele sunt salvate în `/results` în scheduler pod:
```bash
# Listă fișiere rezultate
kubectl exec <scheduler-pod> -- ls /results

# Copiază rezultatele local
kubectl cp <scheduler-pod>:/results/results_jobs_<run_id>.csv ./results_jobs.csv
kubectl cp <scheduler-pod>:/results/results_run_<run_id>.csv ./results_run.csv
```

## Experimente cu Diferite Configurații

### Produs Cartezian: Replici × Core-uri

Pentru analiză comparativă, poți rula experimente cu toate combinațiile:

| Replici Workers | Core-uri per Worker | Total Core-uri |
|----------------|-------------------|---------------|
| 2 | 2 | 4 |
| 2 | 4 | 8 |
| 2 | 8 | 16 |
| 2 | 16 | 32 |
| 3 | 2 | 6 |
| 3 | 4 | 12 |
| 3 | 8 | 24 |
| 3 | 16 | 48 |
| 5 | 2 | 10 |
| 5 | 4 | 20 |
| 5 | 8 | 40 |
| 5 | 16 | 80 |
| 10 | 2 | 20 |
| 10 | 4 | 40 |
| 10 | 8 | 80 |
| 10 | 16 | 160 |
| 20 | 2 | 40 |
| 20 | 4 | 80 |
| 20 | 8 | 160 |
| 20 | 16 | 320 |

### Script pentru Experimente Multiple

Creează un script `run_experiments.sh`:

```bash
#!/bin/bash

# Configurații de testat
REPLICAS=(2 3 5 10 20)
CORES=(2 4 8 16)
DATASET="dataset_fifo_1k.csv"

for replicas in "${REPLICAS[@]}"; do
  for cores in "${CORES[@]}"; do
    echo "Running: $replicas replicas × $cores cores = $((replicas * cores)) total cores"
    
    # Modifică worker.yaml
    sed -i "s/replicas: [0-9]*/replicas: $replicas/" worker.yaml
    sed -i 's/value: "[0-9]*"/value: "'$cores'"/' worker.yaml
    
    # Deploy
    kubectl apply -f worker.yaml
    sleep 10  # Așteaptă ca workers să fie ready
    
    # Rulează
    python submit_runs.py --scheduler http://localhost:8000 --dataset $DATASET
    
    # Salvează rezultatele cu nume descriptiv
    kubectl cp <scheduler-pod>:/results ./results_${replicas}replicas_${cores}cores
    
    echo "Done: $replicas × $cores"
  done
done
```

## Configurație Inițială (Default)

**Recomandat pentru început** (ca Round-Robin):
- **1 Scheduler** (singur pod)
- **2 Workers** (replicas: 2)
- **2 Core-uri per Worker** (CORES: 2)
- **Total: 4 core-uri** (2 × 2)

Această configurație este setată în `worker.yaml`:
```yaml
spec:
  replicas: 2  # 2 workers
env:
- name: CORES
  value: "2"   # 2 core-uri per worker
```

## Metrici Colectate

Rezultatele sunt salvate în CSV cu următoarele coloane:

### `results_jobs_<run_id>.csv` (per job)
- `job_id`: ID job
- `arrival_time_ms`: Când job-ul intră în sistem
- `service_time_ms`: Durată estimată (din CSV)
- `start_time_ms`: Când job-ul începe să ruleze
- `finish_time_ms`: Când job-ul se termină
- `waiting_time_ms`: `start_time - arrival_time` (timp în coadă)
- `execution_time_ms`: `finish_time - start_time` (timp efectiv execuție)
- `response_time_ms`: `finish_time - arrival_time` (timp total în sistem)
- `cpu_usage_percent`: CPU usage mediu
- `memory_usage_mb`: Memory usage (MB)
- `slowdown`: `response_time / service_time`

### `results_run_<run_id>.csv` (sumar)
- `run_id`: ID run
- `dataset_file`: Fișier CSV folosit
- `jobs`: Număr total job-uri
- `mean_response_ms`: Răspuns mediu
- `p50/p95/p99_response_ms`: Percentile răspuns
- `mean_wait_ms`: Timp mediu de așteptare
- `mean_execution_ms`: Timp mediu execuție
- `total_slots_at_end`: Total core-uri disponibile

## Troubleshooting

### Workers nu se conectează
- Verifică `SCHEDULER_URL` în `worker.yaml` să fie corect: `http://scheduler-svc-fifo:8000`
- Verifică că service-ul `scheduler-svc-fifo` există: `kubectl get svc`

### Nu există suficiente slot-uri
- Verifică că workers sunt ready: `kubectl get pods`
- Verifică heartbeat-urile: logs worker (`kubectl logs <worker-pod>`)

### Rezultate lipsă
- Rezultatele sunt în `/results` în scheduler pod
- Verifică: `kubectl exec <scheduler-pod> -- ls /results`

### CPU/Memory = 0
- Verifică că `psutil` este instalat: `pip install psutil`
- Verifică `requirements.txt` conține `psutil==6.1.1`

## Exemple de Rulări

### 1. Test Rapid (100 job-uri, 2 workers, 2 cores)
```bash
# Generează dataset mic
python fifo/generate_dataset.py --output test_100.csv --num-jobs 100

# Rulează
python fifo/submit_runs.py --scheduler http://localhost:8000 --dataset test_100.csv
```

### 2. Experiment Scăzut (1000 job-uri, 5 workers, 4 cores)
```bash
# Modifică worker.yaml: replicas: 5, CORES: "4"
kubectl apply -f fifo/worker.yaml

# Generează dataset
python fifo/generate_dataset.py --output exp_1k.csv --num-jobs 1000

# Rulează
python fifo/submit_runs.py --scheduler http://localhost:8000 --dataset exp_1k.csv
```

### 3. Experiment Mediu (10k job-uri, 10 workers, 8 cores)
```bash
# Modifică worker.yaml: replicas: 10, CORES: "8"
kubectl apply -f fifo/worker.yaml

# Generează dataset
python fifo/generate_dataset.py --output exp_10k.csv --num-jobs 10000

# Rulează
python fifo/submit_runs.py --scheduler http://localhost:8000 --dataset exp_10k.csv
```

## Script pentru Experimente Automate

Poți folosi `run_experiments.sh` pentru a rula toate combinațiile automat:

```bash
chmod +x fifo/run_experiments.sh
cd fifo
./run_experiments.sh
```

Script-ul va:
1. Rula experimentele cu toate combinațiile (replici × core-uri)
2. Modifica `worker.yaml` automat pentru fiecare configurație
3. Deploy workers în Kubernetes
4. Rulează și colectează rezultatele
5. Salvează rezultatele în `experiment_results_<timestamp>/<config>/`

**Modifică în script**:
- `REPLICAS=(2 3 5 10 20)` - Număr de replici de testat
- `CORES=(2 4 8 16)` - Număr de core-uri per worker
- `DATASET="dataset_fifo_1k.csv"` - Fișier CSV de folosit
- `SCHEDULER_URL="http://localhost:8000"` - URL scheduler (sau `http://scheduler-svc-fifo:8000` pentru în cluster)

## Fișiere Importante

- **Dataset CSV**: Trebuie să conțină `job_id`, `service_time_ms`, `arrival_time_ms` (opțional, default 0)
- **Rezultate**: `results_jobs_*.csv` (detalii per job), `results_run_*.csv` (sumar)
- **Logs**: Scheduler și worker loghează în JSON format pentru Loki/Grafana

## Analiza Rezultatelor în Jupyter

După rularea experimentelor, poți analiza rezultatele în Jupyter:

### 1. Încarcă Rezultatele

```python
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Încarcă rezultatele pentru fiecare configurație
results_2x2 = pd.read_csv('experiment_results_xxx/2replicas_2cores/results_jobs.csv')
results_2x4 = pd.read_csv('experiment_results_xxx/2replicas_4cores/results_jobs.csv')
# ... etc pentru toate configurațiile
```

### 2. Compară Metrici între Configurații

```python
# Compară response time
configs = {
    '2×2': results_2x2,
    '2×4': results_2x4,
    '5×4': results_5x4,
    # ... etc
}

for name, df in configs.items():
    print(f"{name}: Mean response={df['response_time_ms'].mean():.2f}ms, "
          f"Mean wait={df['waiting_time_ms'].mean():.2f}ms")
```

### 3. Grafice de Comparație

```python
# Grafic: Mean response time vs configurație
response_times = [df['response_time_ms'].mean() for df in configs.values()]
config_names = list(configs.keys())

plt.figure(figsize=(12, 6))
plt.bar(config_names, response_times)
plt.xlabel('Configurație (replici × core-uri)')
plt.ylabel('Mean Response Time (ms)')
plt.title('Mean Response Time per Configurație')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()
```

### 4. Analiză CPU/Memory

```python
# CPU usage per configurație
for name, df in configs.items():
    avg_cpu = df['cpu_usage_percent'].mean()
    avg_memory = df['memory_usage_mb'].mean()
    print(f"{name}: Avg CPU={avg_cpu:.2f}%, Avg Memory={avg_memory:.2f}MB")
```

### 5. Analiză Waiting Time vs Throughput

```python
# Scatter plot: Waiting time vs Număr de core-uri
total_cores = [2*2, 2*4, 5*4, 10*4]  # Exemple
wait_times = [df['waiting_time_ms'].mean() for df in [results_2x2, results_2x4, results_5x4, results_10x4]]

plt.scatter(total_cores, wait_times)
plt.xlabel('Total Core-uri')
plt.ylabel('Mean Waiting Time (ms)')
plt.title('Waiting Time vs Total Core-uri')
plt.show()
```

## Note Importante

1. **Arrival Time Progresiv**: Job-urile apar la `arrival_time_ms` din CSV, nu toate la 0
2. **FIFO Non-Preemptive**: Fiecare job rulează complet până la finalizare (fără preemption)
3. **Speedup**: `speedup=20000` înseamnă că simularea rulează 20000× mai rapid decât real
4. **Overlapping**: Pentru a vedea efectul cozii, asigură-te că `service_time > arrival_interval`
5. **Configurație Inițială**: 2 workers × 2 core-uri = 4 core-uri totale (setată în `worker.yaml`)
