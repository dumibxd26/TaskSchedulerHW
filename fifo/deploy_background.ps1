# PowerShell script pentru deploy în Kubernetes și port-forward în background

$SchedulerName = "scheduler-svc-fifo"
$LocalPort = 8000
$RemotePort = 8000
$PortForwardPidFile = "$env:TEMP\fifo-port-forward.pid"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "FIFO Scheduler - Kubernetes Deploy (Background)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Verifică că kubectl este disponibil
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    Write-Host "Error: kubectl not found. Please install kubectl." -ForegroundColor Red
    exit 1
}

# Verifică dacă port-forward este deja activ
if (Test-Path $PortForwardPidFile) {
    $OldPid = Get-Content $PortForwardPidFile
    $Process = Get-Process -Id $OldPid -ErrorAction SilentlyContinue
    if ($Process) {
        Write-Host "Port-forward already running (PID: $OldPid)" -ForegroundColor Yellow
        Write-Host "Stopping old port-forward..." -ForegroundColor Yellow
        Stop-Process -Id $OldPid -Force -ErrorAction SilentlyContinue
        Remove-Item $PortForwardPidFile -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Step 0: Checking if ConfigMap exists..." -ForegroundColor Green
$ConfigMap = kubectl get configmap sim-datasets-fifo 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: ConfigMap 'sim-datasets-fifo' not found!" -ForegroundColor Yellow
    Write-Host "Please create it first with:" -ForegroundColor Yellow
    Write-Host "  kubectl create configmap sim-datasets-fifo --from-file=dataset_fifo_burst_1k.csv" -ForegroundColor Yellow
    Write-Host ""
    $response = Read-Host "Continue anyway? (y/n)"
    if ($response -ne "y" -and $response -ne "Y") {
        exit 1
    }
} else {
    Write-Host "ConfigMap 'sim-datasets-fifo' found." -ForegroundColor Green
}

Write-Host ""
Write-Host "Step 1: Deploying scheduler..." -ForegroundColor Green
kubectl apply -f scheduler.yaml

Write-Host ""
Write-Host "Step 2: Deploying workers..." -ForegroundColor Green
kubectl apply -f worker.yaml

Write-Host ""
Write-Host "Step 3: Waiting for scheduler to be ready..." -ForegroundColor Green
kubectl wait --for=condition=ready pod -l app=scheduler-fifo --timeout=60s
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: Scheduler pod may not be ready yet" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 4: Waiting for workers to be ready..." -ForegroundColor Green
kubectl wait --for=condition=ready pod -l app=worker-fifo --timeout=120s
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: Some worker pods may not be ready yet" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 5: Checking pod status..." -ForegroundColor Green
Write-Host "Scheduler:" -ForegroundColor Cyan
kubectl get pods -l app=scheduler-fifo
Write-Host ""
Write-Host "Workers:" -ForegroundColor Cyan
kubectl get pods -l app=worker-fifo

Write-Host ""
Write-Host "Step 6: Getting scheduler pod name..." -ForegroundColor Green
$SchedulerPod = kubectl get pods -l app=scheduler-fifo -o jsonpath='{.items[0].metadata.name}'
if ([string]::IsNullOrEmpty($SchedulerPod)) {
    Write-Host "Error: Could not find scheduler pod" -ForegroundColor Red
    exit 1
}
Write-Host "Scheduler pod: $SchedulerPod" -ForegroundColor Cyan

Write-Host ""
Write-Host "Step 7: Starting port-forward in background..." -ForegroundColor Green
$Job = Start-Job -ScriptBlock {
    param($Pod, $LocalPort, $RemotePort)
    kubectl port-forward pod/$Pod ${LocalPort}:${RemotePort}
} -ArgumentList $SchedulerPod, $LocalPort, $RemotePort

# Salvează PID-ul job-ului
$Job.Id | Out-File $PortForwardPidFile

# Așteaptă puțin pentru port-forward să se stabilească
Start-Sleep -Seconds 2

# Verifică dacă job-ul încă rulează
if ($Job.State -eq "Running") {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host "Deploy complete!" -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host "Scheduler available at: http://localhost:${LocalPort}" -ForegroundColor Cyan
    Write-Host "Port-forward Job ID: $($Job.Id)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To stop port-forward, run:" -ForegroundColor Yellow
    Write-Host "  .\stop_port_forward.ps1" -ForegroundColor Yellow
    Write-Host "  OR" -ForegroundColor Yellow
    Write-Host "  Stop-Job -Id $($Job.Id); Remove-Job -Id $($Job.Id)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To check status:" -ForegroundColor Yellow
    Write-Host "  kubectl get pods -l app=scheduler-fifo" -ForegroundColor Yellow
    Write-Host "  kubectl get pods -l app=worker-fifo" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To test, run:" -ForegroundColor Yellow
    Write-Host "  python submit_runs.py --scheduler http://localhost:${LocalPort} --dataset dataset_fifo_burst_1k.csv" -ForegroundColor Yellow
    Write-Host "==========================================" -ForegroundColor Green
} else {
    Write-Host "Error: Port-forward failed to start" -ForegroundColor Red
    Receive-Job $Job
    Remove-Job $Job
    exit 1
}
