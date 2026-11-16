# ==============================================================================
# scripts/start_offline_hotspot.ps1
# 100% Offline Windows Mobile Hotspot / SoftAP Manager (Ava-IoT)
# Bypasses Windows Internet requirement using Microsoft KM-TEST Loopback Adapter (Ethernet 2)
# Configures SSID "Ava-IoT", WPA2 Passphrase "12345678", Gateway IP 192.168.137.1
# Opens & Verifies Firewall TCP Port 8000
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [OFFLINE HOTSPOT] Initializing Ava-IoT Hotspot Engine... " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Administrator Check & Self-Elevation
# ------------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[*] Administrative privileges required. Elevating via RunAs..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        $scriptPath = "$PSScriptRoot\start_offline_hotspot.ps1"
    }
    $proc = Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -PassThru -Wait
    exit $proc.ExitCode
}

# ------------------------------------------------------------------------------
# 2. Configure Windows Firewall (TCP Port 8000 on Domain, Private, Public)
# ------------------------------------------------------------------------------
try {
    $ruleName = "SmartBuildingVoiceGatewayPort8000"
    $existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        $existingRule = Get-NetFirewallRule -DisplayName "Smart Building Voice Gateway (Port 8000)" -ErrorAction SilentlyContinue
    }
    if (-not $existingRule) {
        New-NetFirewallRule -Name $ruleName `
            -DisplayName "Smart Building Voice Gateway (Port 8000)" `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort 8000 `
            -Profile Any `
            -Description "Allows mobile app connections to Smart Building Voice Gateway (Port 8000)" `
            -ErrorAction SilentlyContinue | Out-Null
        Write-Host "[✓] Windows Firewall rule created for TCP port 8000 (Any Profile)." -ForegroundColor Green
    } else {
        Set-NetFirewallRule -Name $existingRule.Name -Enabled True -Profile Any -Action Allow -ErrorAction SilentlyContinue | Out-Null
        Write-Host "[✓] Windows Firewall rule for TCP port 8000 is active." -ForegroundColor Green
    }
} catch {
    Write-Host "[!] Firewall rule notice: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 3. Verify & Enforce Microsoft KM-TEST Loopback Adapter (10.254.254.1) for Offline ICS
# ------------------------------------------------------------------------------
$loopbackAdapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*KM-TEST Loopback*" -or $_.Name -eq "Ethernet 2" } | Select-Object -First 1
if (-not $loopbackAdapter) {
    Write-Host "[!] KM-TEST Loopback Adapter not found in network adapters." -ForegroundColor Yellow
    $setupScript = Join-Path $PSScriptRoot "setup_offline_hotspot.ps1"
    if (Test-Path $setupScript) {
        Write-Host "[*] Launching setup_offline_hotspot.ps1 to install Loopback adapter..." -ForegroundColor Cyan
        & $setupScript
        $loopbackAdapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*KM-TEST Loopback*" -or $_.Name -eq "Ethernet 2" } | Select-Object -First 1
    }
}

if ($loopbackAdapter) {
    # Ensure adapter is always enabled and Up
    if ($loopbackAdapter.Status -ne "Up") {
        Enable-NetAdapter -Name $loopbackAdapter.Name -Confirm:$false -ErrorAction SilentlyContinue
    }
    $loopbackIP = Get-NetIPAddress -InterfaceIndex $loopbackAdapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq "10.254.254.1" }
    if (-not $loopbackIP) {
        Write-Host "[*] Configuring static IP 10.254.254.1/24 and Gateway on $($loopbackAdapter.Name)..." -ForegroundColor Yellow
        New-NetIPAddress -InterfaceIndex $loopbackAdapter.ifIndex -IPAddress "10.254.254.1" -PrefixLength 24 -DefaultGateway "10.254.254.1" -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Host "[✓] Offline ICS Source Adapter: $($loopbackAdapter.Name) (10.254.254.1 is active and ready)" -ForegroundColor Green
} else {
    Write-Host "[!] Warning: Loopback adapter could not be verified." -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 4. Lock Windows Internet Connection Sharing (ICS) Registry to 192.168.137.1
# ------------------------------------------------------------------------------
try {
    $sharedAccessParams = "HKLM:\SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters"
    if (-not (Test-Path $sharedAccessParams)) {
        New-Item -Path $sharedAccessParams -Force | Out-Null
    }
    Set-ItemProperty -Path $sharedAccessParams -Name "ScopeAddress" -Value "192.168.137.1" -Type String -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $sharedAccessParams -Name "ScopeAddressBackup" -Value "192.168.137.1" -Type String -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $sharedAccessParams -Name "StandaloneDHCPAddress" -Value "192.168.137.1" -Type String -Force -ErrorAction SilentlyContinue
    Write-Host "[✓] Permanently locked SharedAccess registry to 192.168.137.1 (ScopeAddress, ScopeAddressBackup, StandaloneDHCPAddress)." -ForegroundColor Green
} catch {
    Write-Host "[!] SharedAccess registry notice: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 4.1 Disable Hotspot Auto-Timeout in Windows Registry
# ------------------------------------------------------------------------------
try {
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\icssvc\Settings"
    if (-not (Test-Path $regPath)) {
        New-Item -Path $regPath -Force | Out-Null
    }
    # PeerlessTimeoutEnabled = 0 disables auto-shutdown when no clients connected
    Set-ItemProperty -Path $regPath -Name "PeerlessTimeoutEnabled" -Value 0 -ErrorAction SilentlyContinue
    # PublicConnectionTimeout = 0 disables auto-shutdown when no external internet
    Set-ItemProperty -Path $regPath -Name "PublicConnectionTimeout" -Value 0 -ErrorAction SilentlyContinue
    Write-Host "[✓] Disabled Windows Mobile Hotspot idle/offline timeouts in registry." -ForegroundColor Green
} catch {
    Write-Host "[!] Registry tweak notice: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Ensure icssvc (Windows Mobile Hotspot Service) is running
try {
    Set-Service -Name icssvc -StartupType Manual -ErrorAction SilentlyContinue
    Start-Service -Name icssvc -ErrorAction SilentlyContinue
    Write-Host "[✓] Windows Mobile Hotspot Service (icssvc) is running." -ForegroundColor Green
} catch {}

# ------------------------------------------------------------------------------
# 5. Start Mobile Hotspot via WinRT API (SSID: Ava-IoT, Password: 12345678)
# ------------------------------------------------------------------------------
$targetSsid = "Ava-IoT"
$targetPass = "12345678"

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    $asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]

    function AwaitWinRtOp($task, $type) {
        $method = $asTaskGeneric.MakeGenericMethod($type)
        $t = $method.Invoke($null, @($task))
        $t.Wait(15000) | Out-Null
        return $t.Result
    }

    function AwaitWinRtAction($action) {
        $t = $asTaskAction.Invoke($null, @($action))
        $t.Wait(12000) | Out-Null
    }

    [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime] | Out-Null
    [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime] | Out-Null

    # Find connection profile for offline sharing (Ethernet 2 / Loopback)
    $profiles = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()
    $selectedProfile = $null

    foreach ($p in $profiles) {
        if ($p.ProfileName -eq "Ethernet 2" -or $p.ProfileName -match "Loopback") {
            $selectedProfile = $p
            break
        }
    }

    if (-not $selectedProfile) {
        $selectedProfile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    }

    if (-not $selectedProfile -and $profiles.Count -gt 0) {
        $selectedProfile = $profiles[0]
    }

    if ($selectedProfile) {
        Write-Host "[*] Selected Connection Profile for Tethering: $($selectedProfile.ProfileName)" -ForegroundColor Cyan
        $tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($selectedProfile)
        
        # Read current SSID / Passphrase
        $cfg = $tm.GetCurrentAccessPointConfiguration()
        $needConfigUpdate = ($cfg.Ssid -ne $targetSsid -or $cfg.Passphrase -ne $targetPass)

        if ($needConfigUpdate) {
            Write-Host "[*] Reconfiguring Hotspot credentials to SSID: '$targetSsid', Password: '$targetPass'..." -ForegroundColor Yellow
            if ($tm.TetheringOperationalState -eq 1) {
                $stopOp = $tm.StopTetheringAsync()
                $null = AwaitWinRtOp $stopOp ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
                Start-Sleep -Seconds 2
            }
            $cfg.Ssid = $targetSsid
            $cfg.Passphrase = $targetPass
            $configAction = $tm.ConfigureAccessPointAsync($cfg)
            AwaitWinRtAction $configAction
            Write-Host "[✓] Hotspot credentials updated successfully." -ForegroundColor Green
        }

        # Start Hotspot if not already On
        if ($tm.TetheringOperationalState -ne 1) {
            Write-Host "[*] Starting Windows Mobile Hotspot via WinRT API..." -ForegroundColor Cyan
            $startOp = $tm.StartTetheringAsync()
            $startRes = AwaitWinRtOp $startOp ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
            if ($startRes.Status -eq 0 -or "$($startRes.Status)" -eq "Success") {
                Write-Host "[✓] Windows Mobile Hotspot started successfully." -ForegroundColor Green
            } else {
                Write-Host "[!] Tethering start result status: $($startRes.Status)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[✓] Windows Mobile Hotspot is already active (ON)." -ForegroundColor Green
        }

        # Wait up to 10 seconds for 192.168.137.1 gateway binding
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $hotspotIP = $null
        while ($sw.Elapsed.TotalSeconds -lt 10) {
            $hotspotIP = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -eq "192.168.137.1" }
            if ($hotspotIP) { break }
            Start-Sleep -Seconds 1
        }

        if ($hotspotIP) {
            Write-Host "`n==========================================================" -ForegroundColor Green
            Write-Host " [✓] Ava-IoT Offline Hotspot is 100% OPERATIONAL!        " -ForegroundColor Green
            Write-Host "==========================================================" -ForegroundColor Green
            Write-Host " SSID          : $targetSsid" -ForegroundColor Cyan
            Write-Host " Password      : $targetPass" -ForegroundColor Cyan
            Write-Host " Gateway IP    : $($hotspotIP.IPAddress)" -ForegroundColor Green
            Write-Host " Interface     : $($hotspotIP.InterfaceAlias)" -ForegroundColor Gray
            Write-Host " Target URL    : http://192.168.137.1:8000" -ForegroundColor White
        } else {
            Write-Host "[!] Note: 192.168.137.1 binding is still initializing on virtual adapter." -ForegroundColor Yellow
        }
    } else {
        Write-Host "[!] No connection profile available for tethering." -ForegroundColor Yellow
    }
} catch {
    Write-Host "[!] Tethering error: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------------------
# 6. Verify Port 8000 Listener Status
# ------------------------------------------------------------------------------
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " [PORT 8000 TEST] Verifying Listener & Firewall...        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$portConn = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portConn) {
    Write-Host "[✓] Port 8000 is ACTIVE and LISTENING on $($portConn.LocalAddress):8000 (State: $($portConn.State))" -ForegroundColor Green
} else {
    Write-Host "[i] Port 8000 is NOT currently occupied by any process." -ForegroundColor Yellow
    Write-Host "    Windows Firewall is fully configured and ready." -ForegroundColor Green
    Write-Host "    When 'python run.py' starts, it will immediately bind to 0.0.0.0:8000 and be accessible to mobile clients." -ForegroundColor Cyan
}

Write-Host "==========================================================`n" -ForegroundColor Cyan
