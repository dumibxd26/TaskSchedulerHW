# PowerShell script pentru oprirea port-forward-ului

$PortForwardPidFile = "$env:TEMP\fifo-port-forward.pid"

if (Test-Path $PortForwardPidFile) {
    $JobId = Get-Content $PortForwardPidFile
    $Job = Get-Job -Id $JobId -ErrorAction SilentlyContinue
    
    if ($Job) {
        Write-Host "Stopping port-forward (Job ID: $JobId)..." -ForegroundColor Yellow
        Stop-Job -Id $JobId
        Remove-Job -Id $JobId
        Remove-Item $PortForwardPidFile
        Write-Host "Port-forward stopped." -ForegroundColor Green
    } else {
        Write-Host "Port-forward job not running." -ForegroundColor Yellow
        Remove-Item $PortForwardPidFile -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "No port-forward PID file found. Port-forward may not be running." -ForegroundColor Yellow
    
    # Caută job-uri kubectl port-forward
    $Jobs = Get-Job | Where-Object { $_.Command -like "*kubectl port-forward*" }
    if ($Jobs) {
        Write-Host "Found kubectl port-forward jobs:" -ForegroundColor Yellow
        $Jobs | ForEach-Object { Write-Host "  Job ID: $($_.Id)" }
        $response = Read-Host "Stop these jobs? (y/n)"
        if ($response -eq "y" -or $response -eq "Y") {
            $Jobs | ForEach-Object {
                Stop-Job -Id $_.Id
                Remove-Job -Id $_.Id
            }
            Write-Host "Jobs stopped." -ForegroundColor Green
        }
    }
}
