# PowerShell script complet pentru rularea experimentului FIFO
# Face: build Docker, load in kind, deploy, run, copy results

param(
    [string]$Dataset = "dataset_fifo_1k.csv",
    [string]$SchedulerUrl = "http://localhost:8000",
    [switch]$SkipBuild = $false,
    [switch]$SkipDeploy = $false
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "FIFO Experiment - Full Automation" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Verifica ca kubectl functioneaza
Write-Host "Step 0: Checking Kubernetes cluster..." -ForegroundColor Green
try {
    $null = kubectl get nodes 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Cannot connect to Kubernetes cluster" -ForegroundColor Red
        Write-Host "Please ensure cluster is running (e.g., kind create cluster --name fifo-cluster)" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[OK] Cluster is accessible" -ForegroundColor Green
} catch {
    Write-Host "Error: kubectl not found or cluster not accessible" -ForegroundColor Red
    exit 1
}

# Verifica ConfigMap
Write-Host ""
Write-Host "Step 1: Checking ConfigMap..." -ForegroundColor Green
$configMap = kubectl get configmap sim-datasets-fifo 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ConfigMap not found. Creating..." -ForegroundColor Yellow
    if (-not (Test-Path $Dataset)) {
        Write-Host "Error: Dataset file '$Dataset' not found!" -ForegroundColor Red
        exit 1
    }
    kubectl create configmap sim-datasets-fifo --from-file=$Dataset --dry-run=client -o yaml | kubectl apply -f -
    Write-Host "[OK] ConfigMap created" -ForegroundColor Green
} else {
    Write-Host "[OK] ConfigMap exists" -ForegroundColor Green
}

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

# Ruleaza experimentul
Write-Host ""
Write-Host "Step 8: Running experiment..." -ForegroundColor Green
Write-Host "Dataset: $Dataset" -ForegroundColor Cyan
Write-Host "Scheduler: $SchedulerUrl" -ForegroundColor Cyan
Write-Host ""

$runOutput = python submit_runs.py --scheduler $SchedulerUrl --dataset $Dataset 2>&1
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

if (-not [string]::IsNullOrEmpty($runId)) {
    $jobsFile = "results_jobs_${runId}.csv"
    $runFile = "results_run_${runId}.csv"
} else {
    # Incearca sa gaseste ultimele fisiere
    Write-Host "Finding latest results in scheduler pod..." -ForegroundColor Yellow
    $resultsList = kubectl exec $schedulerPod -- ls /results 2>&1
    $jobsFile = ($resultsList | Select-String -Pattern "results_jobs_\w+\.csv" | Select-Object -Last 1).ToString().Trim()
    $runFile = ($resultsList | Select-String -Pattern "results_run_\w+\.csv" | Select-Object -Last 1).ToString().Trim()
}

if (-not [string]::IsNullOrEmpty($jobsFile) -and -not [string]::IsNullOrEmpty($runFile)) {
    kubectl cp "${schedulerPod}:/results/${jobsFile}" $jobsFile 2>&1 | Out-Null
    kubectl cp "${schedulerPod}:/results/${runFile}" $runFile 2>&1 | Out-Null
    
    if ((Test-Path $jobsFile) -and (Test-Path $runFile)) {
        Write-Host "[OK] Results copied:" -ForegroundColor Green
        Write-Host "  - $jobsFile" -ForegroundColor Cyan
        Write-Host "  - $runFile" -ForegroundColor Cyan
    } else {
        Write-Host "Warning: Some result files may not have been copied" -ForegroundColor Yellow
    }
} else {
    Write-Host "Warning: Could not determine result file names" -ForegroundColor Yellow
    Write-Host "You can manually copy with:" -ForegroundColor Yellow
    Write-Host "  kubectl cp ${schedulerPod}:/results/results_jobs_<run_id>.csv ." -ForegroundColor White
    Write-Host "  kubectl cp ${schedulerPod}:/results/results_run_<run_id>.csv ." -ForegroundColor White
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
