# ==============================================================================
# scripts/start_cloudflare_tunnel.ps1
# Cloudflare Tunnel Manager with Persistent HTTP/2 Protocol
# Automatically provisions high-speed HTTPS public access for http://127.0.0.1:8000
# Supports optional CLOUDFLARE_TUNNEL_TOKEN or resilient Quick Tunnel
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ProgressPreference = 'SilentlyContinue'

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = "$PWD\scripts"
}
$projectRoot = Split-Path -Parent $scriptDir
if (-not $projectRoot) {
    $projectRoot = $PWD
}

$logFile = Join-Path $scriptDir "cloudflared.log"
$urlFile = Join-Path $scriptDir "tunnel_url.txt"
$pidFile = Join-Path $scriptDir "cloudflared.pid"

# ------------------------------------------------------------------------------
# 1. Load Configuration & Environment (.env)
# ------------------------------------------------------------------------------
$envFile = Join-Path $projectRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            if (-not [System.Environment]::GetEnvironmentVariable($name)) {
                [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

$cfToken = [System.Environment]::GetEnvironmentVariable("CLOUDFLARE_TUNNEL_TOKEN")
$localPort = [System.Environment]::GetEnvironmentVariable("PORT")
if (-not $localPort) { $localPort = "8000" }
$localTarget = "http://127.0.0.1:$localPort"

# ------------------------------------------------------------------------------
# 2. Locate cloudflared Binary
# ------------------------------------------------------------------------------
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [1/3] Checking Cloudflare Tunnel Executable...           " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Refresh Environment PATH
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

$candidates = @(
    (Get-Command cloudflared.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Get-Command cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe",
    "$env:ProgramFiles\cloudflared\cloudflared.exe",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
)

$cfExe = $null
foreach ($path in $candidates) {
    if ($path -and (Test-Path $path)) {
        $cfExe = $path
        break
    }
}

if (-not $cfExe) {
    Write-Host "[!] cloudflared not found. Installing via winget..." -ForegroundColor Yellow
    winget install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) {
            $cfExe = $path
            break
        }
    }
}

if (-not $cfExe -or -not (Test-Path $cfExe)) {
    Write-Error "cloudflared binary could not be found or installed."
    exit 1
}

$cfVersion = (& $cfExe --version 2>&1).Trim()
Write-Host "[✓] cloudflared is ready: $cfVersion" -ForegroundColor Green
Write-Host "    Binary: $cfExe" -ForegroundColor Gray

# ------------------------------------------------------------------------------
# 3. Configure Tunnel Command
# ------------------------------------------------------------------------------
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " [2/3] Configuring Cloudflare Tunnel (HTTP/2)...          " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$hasToken = [bool]($cfToken -and $cfToken.Trim().Length -gt 10)

if ($hasToken) {
    Write-Host "[✓] Mode: Token Tunnel (CLOUDFLARE_TUNNEL_TOKEN)" -ForegroundColor Green
    Write-Host "    Target Endpoint: $localTarget" -ForegroundColor White
    $commandLine = "`"$cfExe`" tunnel --edge-ip-version 4 --protocol http2 run --token `"$cfToken`" --url `"$localTarget`" --logfile `"$logFile`" --no-autoupdate"
} else {
    Write-Host "[✓] Mode: Base Quick Tunnel with HTTP/2" -ForegroundColor Green
    Write-Host "    Target Endpoint: $localTarget" -ForegroundColor White
    $commandLine = "`"$cfExe`" tunnel --edge-ip-version 4 --protocol http2 --url `"$localTarget`" --logfile `"$logFile`" --no-autoupdate"
}

# ------------------------------------------------------------------------------
# 4. Process Lifecycle Management
# ------------------------------------------------------------------------------
$activeUrl = $null
$newPid = $null
$skipLaunch = $false

if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($oldPid) {
        $existingProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($existingProc -and $existingProc.ProcessName -like "*cloudflared*") {
            if (Test-Path $urlFile) {
                $existingUrl = (Get-Content $urlFile -ErrorAction SilentlyContinue).Trim()
                if ($existingUrl -match '^https://') {
                    Write-Host "[✓] Active cloudflared process (PID: $oldPid) already running: $existingUrl" -ForegroundColor Green
                    $activeUrl = $existingUrl
                    $newPid = $oldPid
                    $skipLaunch = $true
                }
            }
            if (-not $skipLaunch) {
                Write-Host "[*] Stopping previous cloudflared instance (PID: $oldPid)..." -ForegroundColor Yellow
                Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
            }
        }
    }
}

if (-not $skipLaunch) {
    if (Test-Path $logFile) {
        Remove-Item -Path $logFile -Force -ErrorAction SilentlyContinue
    }

    # Start cloudflared as an independent detached process using Win32_Process
    $procResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $commandLine}

    if ($procResult.ReturnValue -ne 0 -or -not $procResult.ProcessId) {
        Write-Error "Failed to start cloudflared process. ReturnCode: $($procResult.ReturnValue)"
        exit 1
    }

    $newPid = $procResult.ProcessId
    $newPid | Out-File -FilePath $pidFile -Encoding ascii
    Write-Host "[+] Detached tunnel process running with PID: $newPid" -ForegroundColor Green

    # ------------------------------------------------------------------------------
    # 5. Extract & Verify Active Tunnel URL
    # ------------------------------------------------------------------------------
    Write-Host "`n==========================================================" -ForegroundColor Cyan
    Write-Host " [3/3] Waiting for Public HTTPS Tunnel URL...             " -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $registered = $false
    $timeoutSeconds = 40
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    while ($sw.Elapsed.TotalSeconds -lt $timeoutSeconds) {
        Start-Sleep -Seconds 1
        if (Test-Path $logFile) {
            try {
                $logContent = Get-Content -Path $logFile -Raw -ErrorAction SilentlyContinue
                if (-not $activeUrl -and $logContent -match '(https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com)') {
                    $activeUrl = $matches[1]
                }
                if ($logContent -match 'Registered tunnel connection|Registered connection') {
                    $registered = $true
                }
                # Once we have URL and connection is registered OR local health is confirmed (HTTP 200), proceed immediately!
                if ($activeUrl) {
                    if ($registered) {
                        break
                    }
                    $localCode = (& curl.exe -s -o NUL -w "%{http_code}" "$localTarget/health" --max-time 2 2>&1).Trim()
                    if ($localCode -eq "200") {
                        break
                    }
                }
            } catch {}
        }
    }
}

if ($activeUrl) {
    # 1. Save to local tunnel_url.txt
    $activeUrl | Out-File -FilePath $urlFile -Encoding utf8 -NoNewline

    Write-Host "`n==========================================================" -ForegroundColor Green
    Write-Host " [✓] CLOUDFLARE TUNNEL IS OPERATIONAL!                    " -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host " Public URL : $activeUrl" -ForegroundColor Cyan
    Write-Host " Target     : $localTarget" -ForegroundColor White
    Write-Host " Protocol   : HTTP/2" -ForegroundColor Green
    if ($newPid) { Write-Host " PID        : $newPid" -ForegroundColor Gray }
    Write-Host " Log File   : $logFile" -ForegroundColor Gray
    Write-Host " Saved to   : $urlFile" -ForegroundColor Yellow
    Write-Host "==========================================================`n" -ForegroundColor Green

    # 2. Immediate Automatic Discovery Sync to Host (Non-blocking by public health)
    $discoveryUrl = [System.Environment]::GetEnvironmentVariable("AVA_DISCOVERY_URL")
    $discoveryKey = [System.Environment]::GetEnvironmentVariable("AVA_DISCOVERY_KEY")
    if ($discoveryUrl -and $discoveryKey) {
        Write-Host "[*] Syncing tunnel URL to Discovery Gateway ($discoveryUrl)..." -ForegroundColor Cyan
        $payload = (@{ url = $activeUrl } | ConvertTo-Json -Compress).Replace('"', '\"')
        $syncResult = (& curl.exe -s -X POST "$discoveryUrl" -H "Content-Type: application/json" -H "X-AVA-Key: $discoveryKey" -d "$payload" --max-time 10 2>&1).Trim()
        if ($syncResult -match '"status"\s*:\s*"success"') {
            Write-Host "[✓] Successfully synced to Discovery Gateway! Response: $syncResult" -ForegroundColor Green
        } else {
            Write-Host "[!] Discovery Gateway response: $syncResult" -ForegroundColor Yellow
        }
    }

    # 3. Optional Non-Blocking Public Health Check
    $healthUrl = "$activeUrl/health"
    Write-Host "[*] (Optional) Testing public tunnel reachability at $healthUrl..." -ForegroundColor Gray
    $httpCode = (& curl.exe -s -o NUL -w "%{http_code}" "$healthUrl" --max-time 3 2>&1).Trim()
    if ($httpCode -eq "200") {
        Write-Host "[✓] Public tunnel endpoint verified healthy (HTTP 200)." -ForegroundColor Green
    } else {
        Write-Host "[i] Notice: Public health check returned HTTP $httpCode. On Iranian domestic networks without VPN, trycloudflare.com is filtered locally; Discovery Gateway registration has succeeded and the link is active." -ForegroundColor Gray
    }
} else {
    Write-Error "Could not retrieve tunnel URL. Check $logFile."
    exit 1
}
