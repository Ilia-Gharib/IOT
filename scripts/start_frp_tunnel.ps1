# ==============================================================================
# scripts/start_frp_tunnel.ps1
# Start FRP Client for Low-Latency Remote Access over Iran National Network (اینترنت ملی)
# Maps local port 8000 to Iran VPS (ArvanCloud / ParsPack) port 8000
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = "$PWD\scripts"
}

$frpcExe = Join-Path $scriptDir "frpc.exe"
$configFile = Join-Path $scriptDir "frpc.toml"
$logFile = Join-Path $scriptDir "frpc.log"
$pidFile = Join-Path $scriptDir "frpc.pid"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [FRP] Checking Fast Reverse Proxy (FRP) Client...         " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (-not (Test-Path $frpcExe)) {
    Write-Error "frpc.exe not found in $scriptDir. Please verify installation."
    exit 1
}

if (-not (Test-Path $configFile)) {
    Write-Error "frpc.toml not found in $scriptDir."
    exit 1
}

$version = & $frpcExe -v
Write-Host "[✓] FRP Client version: $version" -ForegroundColor Green

# Validate configuration syntax
$verify = & $frpcExe verify -c $configFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "FRP configuration validation failed: $verify"
    exit 1
}
Write-Host "[✓] FRP configuration syntax is valid." -ForegroundColor Green

# Check server address
$configContent = Get-Content -Path $configFile -Raw
if ($configContent -match 'serverAddr\s*=\s*"([^"]+)"') {
    $currentServer = $matches[1]
    if ($currentServer -eq "127.0.0.1" -or $currentServer -eq "YOUR_IRAN_VPS_IP") {
        Write-Host "[!] Note: serverAddr is currently '$currentServer'." -ForegroundColor Yellow
        Write-Host "    To route traffic via your Iran VPS (Arvan / ParsPack), update 'serverAddr' in scripts/frpc.toml." -ForegroundColor Yellow
    } else {
        Write-Host "[✓] Target Iran VPS: $currentServer" -ForegroundColor Cyan
    }
}

# Stop old running instance if registered
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($oldPid) {
        $existingProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($existingProc -and $existingProc.ProcessName -like "*frpc*") {
            Write-Host "[*] Stopping previous frpc process (PID: $oldPid)..." -ForegroundColor Yellow
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        }
    }
}

# Start detached process
$commandLine = "`"$frpcExe`" -c `"$configFile`""
$procResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $commandLine}

if ($procResult.ReturnValue -ne 0 -or -not $procResult.ProcessId) {
    Write-Error "Failed to start frpc process. ReturnCode: $($procResult.ReturnValue)"
    exit 1
}

$newPid = $procResult.ProcessId
$newPid | Out-File -FilePath $pidFile -Encoding ascii
Write-Host "[✓] FRP client running in background with PID: $newPid" -ForegroundColor Green
Write-Host "    Configuration : $configFile" -ForegroundColor Gray
Write-Host "    Local Gateway : 127.0.0.1:8000" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan
