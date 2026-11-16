# ==============================================================================
# scripts/setup_remote_access.ps1
# Multi-Tier Remote & Local Access Manager:
# 1. Tailscale P2P Direct Mesh (Ultra-stable, No Censorship/VPN required)
# 2. FRP Reverse Proxy for Iran National Network (اینترنت ملی)
# 3. Cloudflare Tunnel (HTTP/2 Backup Link)
# 4. Local Offline Hotspot (192.168.137.1:8000)
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    $scriptDir = "$PWD\scripts"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       REMOTE ACCESS & CONNECTIVITY STATUS AUDIT          " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Tailscale Direct P2P Status
# ------------------------------------------------------------------------------
Write-Host "`n[1/4] Checking Tailscale Mesh Network..." -ForegroundColor Yellow

$tailscalePaths = @(
    (Get-Command tailscale.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "C:\Program Files\Tailscale\tailscale.exe",
    "C:\Program Files (x86)\Tailscale\tailscale.exe"
)

$tsExe = $null
foreach ($path in $tailscalePaths) {
    if ($path -and (Test-Path $path)) {
        $tsExe = $path
        break
    }
}

if (-not $tsExe) {
    Write-Host "[!] Tailscale is not installed. Installing via winget..." -ForegroundColor Yellow
    winget install --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
    $tsExe = "C:\Program Files\Tailscale\tailscale.exe"
}

if ($tsExe -and (Test-Path $tsExe)) {
    try {
        $tsJsonRaw = & $tsExe status --json 2>$null | Out-String
        $tsStatus = $tsJsonRaw | ConvertFrom-Json
        $backendState = $tsStatus.BackendState
        
        Write-Host "      Tailscale Client : $($tsStatus.Version)" -ForegroundColor White
        Write-Host "      Backend State    : $backendState" -ForegroundColor $(if ($backendState -eq "Running") { "Green" } else { "Yellow" })

        if ($backendState -eq "Running") {
            $tsIp = (& $tsExe ip -4 2>$null).Trim()
            $dnsName = $tsStatus.Self.DNSName
            Write-Host "      Tailscale IP (4) : $tsIp" -ForegroundColor Green
            Write-Host "      MagicDNS FQDN    : $dnsName" -ForegroundColor Cyan
            Write-Host "      Access URL       : http://$tsIp`:8000" -ForegroundColor Green
        } else {
            Write-Host "      [!] Tailscale is not currently logged in / running." -ForegroundColor Yellow
            Write-Host "      To connect your account, run in terminal: & `"$tsExe`" up" -ForegroundColor Cyan
            
            # Check if any 100.x.x.x IP address is already bound to network adapters
            $existingTsIP = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -like "100.*" } | Select-Object -First 1
            if ($existingTsIP) {
                Write-Host "      Detected Tailscale IP on adapter: $($existingTsIP.IPAddress)" -ForegroundColor Green
            }
        }
    } catch {
        Write-Host "      Tailscale query note: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "      [x] Tailscale binary not found." -ForegroundColor Red
}

# ------------------------------------------------------------------------------
# 2. FRP Client (Iran National Network / اینترنت ملی)
# ------------------------------------------------------------------------------
Write-Host "`n[2/4] Checking Fast Reverse Proxy (FRP) Client..." -ForegroundColor Yellow
$frpcExe = Join-Path $scriptDir "frpc.exe"
$frpcToml = Join-Path $scriptDir "frpc.toml"

if (Test-Path $frpcExe) {
    $frpVer = (& $frpcExe -v 2>$null).Trim()
    Write-Host "      FRP Client (frpc) : Ready (Version $frpVer)" -ForegroundColor Green
    if (Test-Path $frpcToml) {
        $tomlContent = Get-Content -Path $frpcToml -Raw
        if ($tomlContent -match 'serverAddr\s*=\s*"([^"]+)"') {
            $vps = $matches[1]
            Write-Host "      Configured VPS    : $vps" -ForegroundColor $(if ($vps -ne "127.0.0.1" -and $vps -ne "YOUR_IRAN_VPS_IP") { "Green" } else { "Gray" })
        }
        Write-Host "      Config File       : $frpcToml" -ForegroundColor White
        Write-Host "      Runner Script     : $scriptDir\start_frp_tunnel.ps1" -ForegroundColor Cyan
    }
} else {
    Write-Host "      [!] FRP client binary not found in scripts directory." -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 3. Cloudflare Quick Tunnel (HTTP/2 Backup Link)
# ------------------------------------------------------------------------------
Write-Host "`n[3/4] Checking Cloudflare Tunnel Link..." -ForegroundColor Yellow
$cfUrlFile = Join-Path $scriptDir "tunnel_url.txt"
$cfPidFile = Join-Path $scriptDir "cloudflared.pid"

if (Test-Path $cfUrlFile) {
    $cfUrl = (Get-Content -Path $cfUrlFile -ErrorAction SilentlyContinue).Trim()
    $isAlive = $false
    if (Test-Path $cfPidFile) {
        $cfPid = Get-Content -Path $cfPidFile -ErrorAction SilentlyContinue
        if ($cfPid -and (Get-Process -Id $cfPid -ErrorAction SilentlyContinue)) {
            $isAlive = $true
        }
    }
    Write-Host "      Public Cloudflare : $cfUrl" -ForegroundColor Cyan
    Write-Host "      Process Status    : $(if ($isAlive) { 'Active (PID ' + $cfPid + ')' } else { 'Standby / Stopped' })" -ForegroundColor $(if ($isAlive) { "Green" } else { "Yellow" })
} else {
    Write-Host "      Cloudflare Tunnel URL not generated yet. Run scripts/start_cloudflare_tunnel.ps1 to activate." -ForegroundColor Gray
}

# ------------------------------------------------------------------------------
# 4. Local Offline Hotspot (Ava-IoT)
# ------------------------------------------------------------------------------
Write-Host "`n[4/4] Checking Offline Hotspot Status..." -ForegroundColor Yellow
$hotspotIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq "192.168.137.1" }
if ($hotspotIp) {
    Write-Host "      Hotspot Gateway   : 192.168.137.1 (Active on $($hotspotIp.InterfaceAlias))" -ForegroundColor Green
    Write-Host "      Local Endpoint    : http://192.168.137.1:8000" -ForegroundColor Green
} else {
    Write-Host "      Hotspot Gateway   : Offline (Run scripts/start_offline_hotspot.ps1 to start)" -ForegroundColor Yellow
}

# Check Loopback ICS source
$loopbackIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq "10.254.254.1" }
if ($loopbackIp) {
    Write-Host "      Loopback Adapter  : 10.254.254.1 (Ready on $($loopbackIp.InterfaceAlias))" -ForegroundColor Green
}

# Check Port 8000 listening status
$port8000 = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Host "`n[+] Port 8000 Listener Status: $(if ($port8000) { 'OPEN / LISTENING (' + $port8000.LocalAddress + ':8000)' } else { 'CLOSED / WAITING FOR RUN.PY' })" -ForegroundColor $(if ($port8000) { "Green" } else { "Yellow" })

Write-Host "==========================================================" -ForegroundColor Cyan
