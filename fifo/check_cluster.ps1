# PowerShell script pentru verificarea cluster-ului Kubernetes

Write-Host "Checking Kubernetes cluster status..." -ForegroundColor Cyan
Write-Host ""

# Verifică contextul curent
$Context = kubectl config current-context
Write-Host "Current context: $Context" -ForegroundColor Yellow

# Verifică dacă se poate conecta la cluster
Write-Host ""
Write-Host "Testing cluster connection..." -ForegroundColor Cyan
$Nodes = kubectl get nodes 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Cluster is accessible!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Nodes:" -ForegroundColor Cyan
    kubectl get nodes
} else {
    Write-Host "✗ Cannot connect to cluster" -ForegroundColor Red
    Write-Host ""
    Write-Host "Error details:" -ForegroundColor Yellow
    Write-Host $Nodes
    
    Write-Host ""
    Write-Host "Possible solutions:" -ForegroundColor Yellow
    Write-Host "1. Start Docker Desktop (if using kind/minikube)" -ForegroundColor White
    Write-Host "2. Start minikube: minikube start" -ForegroundColor White
    Write-Host "3. Check if kind cluster is running: kind get clusters" -ForegroundColor White
    Write-Host "4. Start kind cluster: kind create cluster --name restaurant-cluster" -ForegroundColor White
}
