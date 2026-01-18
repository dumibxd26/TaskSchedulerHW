# PowerShell script pentru crearea ConfigMap-ului cu dataset-ul

param(
    [string]$DatasetFile = "dataset_fifo_1k.csv"
)

$ConfigMapName = "sim-datasets-fifo"

if (-not (Test-Path $DatasetFile)) {
    Write-Host "Error: Dataset file '$DatasetFile' not found!" -ForegroundColor Red
    Write-Host "Usage: .\setup_configmap.ps1 [-DatasetFile dataset_file.csv]"
    exit 1
}

Write-Host "Creating ConfigMap '$ConfigMapName' from '$DatasetFile'..." -ForegroundColor Green

# Creează ConfigMap din fișier
kubectl create configmap $ConfigMapName `
    --from-file=$DatasetFile `
    --dry-run=client -o yaml | kubectl apply -f -

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "ConfigMap created/updated successfully!" -ForegroundColor Green
    Write-Host "To verify: kubectl get configmap $ConfigMapName"
    Write-Host "To view: kubectl describe configmap $ConfigMapName"
} else {
    Write-Host "Error: Failed to create ConfigMap" -ForegroundColor Red
    exit 1
}
