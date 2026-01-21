# PowerShell script complet pentru rularea experimentului FIFO
# Face: build Docker, load in kind, deploy, run, copy results

param(
    [string]$Dataset = "dataset_fifo_burst_1k.csv",
    [string]$SchedulerUrl = "http://localhost:8000",
    [switch]$SkipBuild = $false,
    [switch]$SkipDeploy = $false
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "FIFO Experiment - Full Automation" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Verifica ca Docker ruleaza
Write-Host "Step 0: Checking Docker..." -ForegroundColor Green
try {
    $null = docker ps 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Docker is not running or not accessible" -ForegroundColor Red
        Write-Host "Please start Docker Desktop and wait for it to be ready" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[OK] Docker is running" -ForegroundColor Green
} catch {
    Write-Host "Error: Docker not found or not accessible" -ForegroundColor Red
    Write-Host "Please install and start Docker Desktop" -ForegroundColor Yellow
    exit 1
}

# Verifica si creeaza clusterul kind daca nu exista
Write-Host ""
Write-Host "Step 0.5: Checking kind cluster 'fifo-cluster'..." -ForegroundColor Green
$oldErrorAction = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$clustersOutput = kind get clusters 2>&1
$ErrorActionPreference = $oldErrorAction

if ($LASTEXITCODE -eq 0 -and $clustersOutput) {
    $clusterExists = ($clustersOutput -match "fifo-cluster")
} else {
    $clusterExists = $false
}

if (-not $clusterExists) {
    Write-Host "Cluster 'fifo-cluster' does not exist. Creating it..." -ForegroundColor Yellow
    kind create cluster --name fifo-cluster
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Failed to create kind cluster" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Cluster 'fifo-cluster' created" -ForegroundColor Green
    # Asteapta putin ca clusterul sa fie gata
    Start-Sleep -Seconds 5
} else {
    Write-Host "[OK] Cluster 'fifo-cluster' already exists" -ForegroundColor Green
}

# Verifica ca kubectl poate accesa clusterul
Write-Host ""
Write-Host "Step 0.6: Verifying cluster accessibility..." -ForegroundColor Green
try {
    $null = kubectl get nodes 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: Cannot connect to cluster. Trying to set context..." -ForegroundColor Yellow
        kubectl cluster-info --context kind-fifo-cluster 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Error: Cannot connect to Kubernetes cluster" -ForegroundColor Red
            Write-Host "Try: kind delete cluster --name fifo-cluster, then run this script again" -ForegroundColor Yellow
            exit 1
        }
    }
    Write-Host "[OK] Cluster is accessible" -ForegroundColor Green
} catch {
    Write-Host "Error: kubectl not found or cluster not accessible" -ForegroundColor Red
    exit 1
}

# Verifica si actualizeaza ConfigMap cu noul dataset
Write-Host ""
Write-Host "Step 1: Updating ConfigMap with dataset..." -ForegroundColor Green
if (-not (Test-Path $Dataset)) {
    Write-Host "Error: Dataset file '$Dataset' not found!" -ForegroundColor Red
    exit 1
}
Write-Host "Creating/updating ConfigMap with dataset: $Dataset" -ForegroundColor Yellow
# ConfigMap-ul trebuie să aibă numele din scheduler.yaml (sim-datasets-fifo)
# și fișierul trebuie să aibă numele corect pentru ca scheduler-ul să-l găsească
kubectl create configmap sim-datasets-fifo --from-file=$Dataset --dry-run=client -o yaml | kubectl apply -f -
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to create/update ConfigMap" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] ConfigMap updated with dataset" -ForegroundColor Green

# Build Docker image
if (-not $SkipBuild) {
    Write-Host ""
    Write-Host "Step 2: Building Docker image..." -ForegroundColor Green
    docker build -t fifo-sim:latest .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Docker build failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Docker image built" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Step 3: Loading image into kind cluster..." -ForegroundColor Green
    kind load docker-image fifo-sim:latest --name fifo-cluster
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Failed to load image into kind" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Image loaded into cluster" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Step 3.5: Restarting pods to use new image..." -ForegroundColor Green
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $schedulerPods = kubectl get pods -l app=scheduler-fifo --no-headers 2>&1
    $workerPods = kubectl get pods -l app=worker-fifo --no-headers 2>&1
    $ErrorActionPreference = $oldErrorAction
    
    if ($schedulerPods -or $workerPods) {
        Write-Host "Deleting old pods..." -ForegroundColor Yellow
        kubectl delete pods -l app=scheduler-fifo 2>&1 | Out-Null
        kubectl delete pods -l app=worker-fifo 2>&1 | Out-Null
        Write-Host "Waiting for new pods to be ready..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
        
        $ErrorActionPreference = "SilentlyContinue"
        kubectl wait --for=condition=ready pod -l app=scheduler-fifo --timeout=60s 2>&1 | Out-Null
        kubectl wait --for=condition=ready pod -l app=worker-fifo --timeout=120s 2>&1 | Out-Null
        $ErrorActionPreference = $oldErrorAction
        
        Write-Host "[OK] Pods restarted with new image" -ForegroundColor Green
    } else {
        Write-Host "No existing pods found. Pods will be created during deployment." -ForegroundColor Yellow
    }
} else {
    Write-Host ""
    Write-Host "Step 2-3: Skipping build (using existing image)" -ForegroundColor Yellow
}

# Deploy
if (-not $SkipDeploy) {
    Write-Host ""
    Write-Host "Step 4: Deploying scheduler and workers..." -ForegroundColor Green
    kubectl apply -f scheduler.yaml
    kubectl apply -f worker.yaml
    
    Write-Host "Waiting for pods to be ready..." -ForegroundColor Yellow
    kubectl wait --for=condition=ready pod -l app=scheduler-fifo --timeout=60s
    kubectl wait --for=condition=ready pod -l app=worker-fifo --timeout=120s
    
    Write-Host "[OK] Deployment complete" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Step 4: Skipping deploy (using existing deployment)" -ForegroundColor Yellow
}

# Verifica pods
Write-Host ""
Write-Host "Step 5: Checking pod status..." -ForegroundColor Green
kubectl get pods -l app=scheduler-fifo
kubectl get pods -l app=worker-fifo

# Port-forward in background
Write-Host ""
Write-Host "Step 6: Setting up port-forward..." -ForegroundColor Green
$schedulerPod = kubectl get pods -l app=scheduler-fifo -o jsonpath='{.items[0].metadata.name}'
if ([string]::IsNullOrEmpty($schedulerPod)) {
    Write-Host "Error: Scheduler pod not found" -ForegroundColor Red
    exit 1
}

# Opreste port-forward vechi daca exista
$portForwardPidFile = "$env:TEMP\fifo-port-forward.pid"
if (Test-Path $portForwardPidFile) {
    $oldJobId = Get-Content $portForwardPidFile
    $oldJob = Get-Job -Id $oldJobId -ErrorAction SilentlyContinue
    if ($oldJob) {
        Write-Host "Stopping old port-forward job..." -ForegroundColor Yellow
        Stop-Job -Id $oldJobId -ErrorAction SilentlyContinue
        Remove-Job -Id $oldJobId -ErrorAction SilentlyContinue
    }
    Remove-Item $portForwardPidFile -ErrorAction SilentlyContinue
}

# Verifica si opreste orice proces care foloseste portul 8000
Write-Host "Checking for processes using port 8000..." -ForegroundColor Yellow
$connections = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        try {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -like "*kubectl*") {
                Write-Host "Stopping kubectl process on port 8000 (PID: $($conn.OwningProcess))..." -ForegroundColor Yellow
                Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        } catch {
            # Ignore errors
        }
    }
    Start-Sleep -Seconds 2
}

# Start port-forward in background
$job = Start-Job -ScriptBlock {
    param($Pod, $Port)
    kubectl port-forward pod/$Pod ${Port}:8000
} -ArgumentList $schedulerPod, 8000

$job.Id | Out-File $portForwardPidFile
Start-Sleep -Seconds 3

if ($job.State -ne "Running") {
    Write-Host "Error: Port-forward failed to start" -ForegroundColor Red
    Receive-Job $job
    Remove-Job $job
    exit 1
}

Write-Host "[OK] Port-forward active (Job ID: $($job.Id))" -ForegroundColor Green

# Test conexiune
Write-Host ""
Write-Host "Step 7: Testing scheduler connection..." -ForegroundColor Green
try {
    $response = Invoke-WebRequest -Uri "$SchedulerUrl/workers" -UseBasicParsing -TimeoutSec 5
    $workers = $response.Content | ConvertFrom-Json
    Write-Host "[OK] Scheduler is accessible" -ForegroundColor Green
    Write-Host "  Workers: $($workers.worker_count), Total slots: $($workers.total_slots)" -ForegroundColor Cyan
} catch {
    Write-Host "Warning: Cannot connect to scheduler. Waiting 5 more seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    try {
        $response = Invoke-WebRequest -Uri "$SchedulerUrl/workers" -UseBasicParsing -TimeoutSec 5
        Write-Host "[OK] Scheduler is now accessible" -ForegroundColor Green
    } catch {
        Write-Host "Error: Still cannot connect to scheduler" -ForegroundColor Red
        Write-Host "Port-forward log:" -ForegroundColor Yellow
        Receive-Job $job
        exit 1
    }
}

# Citeste configurația din worker.yaml
Write-Host ""
Write-Host "Step 7.5: Reading configuration from worker.yaml..." -ForegroundColor Green
$workerYaml = Get-Content "worker.yaml" -Raw
$replicas = ($workerYaml | Select-String -Pattern "replicas:\s*(\d+)" | ForEach-Object { $_.Matches[0].Groups[1].Value })
$cores = ($workerYaml | Select-String -Pattern 'value:\s*"(\d+)"' | Select-Object -First 1 | ForEach-Object { $_.Matches[0].Groups[1].Value })

if ([string]::IsNullOrEmpty($replicas)) {
    $replicas = "2"
    Write-Host "Warning: Could not parse replicas from worker.yaml, using default: 2" -ForegroundColor Yellow
}
if ([string]::IsNullOrEmpty($cores)) {
    $cores = "2"
    Write-Host "Warning: Could not parse cores from worker.yaml, using default: 2" -ForegroundColor Yellow
}

Write-Host "Configuration: replicas=$replicas, cores=$cores" -ForegroundColor Cyan

# Ruleaza experimentul
Write-Host ""
Write-Host "Step 8: Running experiment..." -ForegroundColor Green
Write-Host "Dataset: $Dataset" -ForegroundColor Cyan
Write-Host "Scheduler: $SchedulerUrl" -ForegroundColor Cyan
Write-Host "Replicas: $replicas, Cores: $cores" -ForegroundColor Cyan
Write-Host ""

$runOutput = python submit_runs.py --scheduler $SchedulerUrl --dataset $Dataset --replicas $replicas --cores $cores 2>&1
$runOutput

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Experiment failed" -ForegroundColor Red
    exit 1
}

# Extrage run_id
$runId = ($runOutput | Select-String -Pattern "Started run_id=(\w+)" | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ([string]::IsNullOrEmpty($runId)) {
    Write-Host "Warning: Could not extract run_id from output" -ForegroundColor Yellow
    Write-Host "Trying to get latest results from scheduler pod..." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "[OK] Experiment completed (run_id: $runId)" -ForegroundColor Green
}

# Copiaza rezultatele
Write-Host ""
Write-Host "Step 9: Copying results..." -ForegroundColor Green

# Construiește numele fișierelor bazat pe dataset (fără extensie)
$datasetName = [System.IO.Path]::GetFileNameWithoutExtension($Dataset)
$resultsSubdir = "replicas_${replicas}_cores_${cores}"
$jobsFile = "results_jobs_${datasetName}.csv"
$runFile = "results_run_${datasetName}.csv"

# Creează folderul local pentru rezultate
$localResultsDir = "results\$resultsSubdir"
New-Item -ItemType Directory -Force -Path $localResultsDir | Out-Null

# Copiază fișierele din folderul structurat din pod
$remoteJobsPath = "/results/${resultsSubdir}/${jobsFile}"
$remoteRunPath = "/results/${resultsSubdir}/${runFile}"
$localJobsPath = "$localResultsDir\$jobsFile"
$localRunPath = "$localResultsDir\$runFile"

Write-Host "Copying from pod: $remoteJobsPath -> $localJobsPath" -ForegroundColor Yellow
kubectl cp "${schedulerPod}:${remoteJobsPath}" $localJobsPath 2>&1 | Out-Null
kubectl cp "${schedulerPod}:${remoteRunPath}" $localRunPath 2>&1 | Out-Null

if ((Test-Path $localJobsPath) -and (Test-Path $localRunPath)) {
    Write-Host "[OK] Results copied to:" -ForegroundColor Green
    Write-Host "  - $localJobsPath" -ForegroundColor Cyan
    Write-Host "  - $localRunPath" -ForegroundColor Cyan
} else {
    Write-Host "Warning: Some result files may not have been copied" -ForegroundColor Yellow
    Write-Host "Trying alternative paths..." -ForegroundColor Yellow
    # Fallback: încearcă să găsească fișierele manual
    $resultsList = kubectl exec $schedulerPod -- find /results -name "*.csv" 2>&1
    Write-Host "Available files in pod:" -ForegroundColor Yellow
    Write-Host $resultsList -ForegroundColor White
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Experiment completed successfully!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "To stop port-forward:" -ForegroundColor Yellow
Write-Host "  .\stop_port_forward.ps1" -ForegroundColor White
Write-Host "  OR" -ForegroundColor Yellow
$stopCmd = "Stop-Job -Id $($job.Id); Remove-Job -Id $($job.Id)"
Write-Host "  $stopCmd" -ForegroundColor White
Write-Host ""
