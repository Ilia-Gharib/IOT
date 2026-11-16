# ==============================================================================
# scripts/setup_offline_hotspot.ps1
# Setup Microsoft KM-TEST Loopback Adapter for Offline Windows Mobile Hotspot
# & Configure Windows Firewall for TCP Port 8000
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. Check Administrator Privileges & Self-Elevate if needed
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[*] Administrative privileges required. Elevating via RunAs..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        $scriptPath = "$PSScriptRoot\setup_offline_hotspot.ps1"
    }
    $proc = Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -PassThru -Wait
    exit $proc.ExitCode
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " [1/3] Checking Microsoft KM-TEST Loopback Adapter...     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$adapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*KM-TEST Loopback*" } | Select-Object -First 1

if (-not $adapter) {
    Write-Host "[+] Loopback Adapter not found. Installing native driver (netloop.inf)..." -ForegroundColor Yellow

    $cSharpCode = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public class LoopbackInstaller {
    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid ClassGuid, IntPtr hwndParent);

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool SetupDiCreateDeviceInfoW(
        IntPtr DeviceInfoSet,
        string DeviceName,
        ref Guid ClassGuid,
        string DeviceDescription,
        IntPtr hwndParent,
        uint CreationFlags,
        ref SP_DEVINFO_DATA DeviceInfoData
    );

    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool SetupDiSetDeviceRegistryPropertyW(
        IntPtr DeviceInfoSet,
        ref SP_DEVINFO_DATA DeviceInfoData,
        uint Property,
        byte[] PropertyBuffer,
        uint PropertyBufferSize
    );

    [DllImport("setupapi.dll", SetLastError = true)]
    public static extern bool SetupDiCallClassInstaller(
        uint InstallFunction,
        IntPtr DeviceInfoSet,
        ref SP_DEVINFO_DATA DeviceInfoData
    );

    [DllImport("setupapi.dll", SetLastError = true)]
    public static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    [DllImport("newdev.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool UpdateDriverForPlugAndPlayDevicesW(
        IntPtr hwndParent,
        string HardwareId,
        string FullInfPath,
        uint InstallFlags,
        out bool RebootRequired
    );

    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA {
        public uint cbSize;
        public Guid ClassGuid;
        public uint DevInst;
        public IntPtr Reserved;
    }

    public const uint DIF_REGISTERDEVICE = 0x00000019;
    public const uint SPDRP_HARDWAREID = 0x00000001;
    public const uint DICD_GENERATE_ID = 0x00000001;
    public const uint INSTALLFLAG_FORCE = 0x00000001;

    public static int Install(string infPath) {
        Guid netClassGuid = new Guid("{4d36e972-e325-11ce-bfc1-08002be10318}");
        IntPtr hDevInfo = SetupDiCreateDeviceInfoList(ref netClassGuid, IntPtr.Zero);
        if (hDevInfo == (IntPtr)(-1) || hDevInfo == IntPtr.Zero) {
            return Marshal.GetLastWin32Error();
        }

        try {
            SP_DEVINFO_DATA devData = new SP_DEVINFO_DATA();
            devData.cbSize = (uint)Marshal.SizeOf(typeof(SP_DEVINFO_DATA));

            if (!SetupDiCreateDeviceInfoW(hDevInfo, "Net", ref netClassGuid, null, IntPtr.Zero, DICD_GENERATE_ID, ref devData)) {
                return Marshal.GetLastWin32Error();
            }

            byte[] hwIdBytes = Encoding.Unicode.GetBytes("*MSLOOP\0\0");
            if (!SetupDiSetDeviceRegistryPropertyW(hDevInfo, ref devData, SPDRP_HARDWAREID, hwIdBytes, (uint)hwIdBytes.Length)) {
                return Marshal.GetLastWin32Error();
            }

            if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, hDevInfo, ref devData)) {
                return Marshal.GetLastWin32Error();
            }

            bool reboot = false;
            if (!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero, "*MSLOOP", infPath, INSTALLFLAG_FORCE, out reboot)) {
                return Marshal.GetLastWin32Error();
            }

            return 0;
        }
        catch (Exception) {
            return -1;
        }
        finally {
            SetupDiDestroyDeviceInfoList(hDevInfo);
        }
    }
}
'@

    Add-Type -TypeDefinition $cSharpCode
    $infPath = "C:\Windows\INF\netloop.inf"
    $installCode = [LoopbackInstaller]::Install($infPath)

    if ($installCode -ne 0) {
        Write-Error "Failed to install Loopback Adapter. Error Code: $installCode"
        exit 1
    }

    # Wait for adapter to register in network stack
    Write-Host "[+] Waiting for adapter to initialize..." -ForegroundColor Gray
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 20) {
        Start-Sleep -Seconds 2
        $adapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*KM-TEST Loopback*" } | Select-Object -First 1
        if ($adapter) { break }
    }
}

if (-not $adapter) {
    Write-Error "Loopback adapter was not detected in Get-NetAdapter."
    exit 1
}

Write-Host "[✓] Loopback Adapter found: $($adapter.Name) (Index: $($adapter.ifIndex))" -ForegroundColor Green

# 2. Configure Static IP, Subnet Mask & Default Gateway
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " [2/3] Configuring Static IP (10.254.254.1/24) & Gateway... " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Ensure adapter is enabled
Enable-NetAdapter -Name $adapter.Name -Confirm:$false -ErrorAction SilentlyContinue

$currentIP = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue

$needConfig = $true
if ($currentIP -and $currentIP.IPAddress -eq "10.254.254.1" -and $currentIP.PrefixLength -eq 24) {
    $currentRoute = Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "0.0.0.0/0" -NextHop "10.254.254.1" -ErrorAction SilentlyContinue
    if ($currentRoute) {
        $needConfig = $false
        Write-Host "[✓] Static IP 10.254.254.1 and Gateway 10.254.254.1 are already configured." -ForegroundColor Green
    }
}

if ($needConfig) {
    Write-Host "[+] Setting IP 10.254.254.1, Mask 255.255.255.0, Gateway 10.254.254.1..." -ForegroundColor Yellow
    # Remove existing IPv4 configs on this adapter
    Remove-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix "0.0.0.0/0" -Confirm:$false -ErrorAction SilentlyContinue

    New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress "10.254.254.1" -PrefixLength 24 -DefaultGateway "10.254.254.1" | Out-Null
    Write-Host "[✓] Static IP and Gateway applied successfully." -ForegroundColor Green
}

# Ensure Mobile Hotspot Service (icssvc) is ready
Set-Service -Name icssvc -StartupType Manual -ErrorAction SilentlyContinue
Start-Service -Name icssvc -ErrorAction SilentlyContinue

# 3. Configure Windows Firewall Rule for Port TCP 8000
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host " [3/3] Configuring Windows Firewall for Port TCP 8000...   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$firewallRuleName = "IOT_Server_Port_8000"
$existingRule = Get-NetFirewallRule -Name $firewallRuleName -ErrorAction SilentlyContinue

if ($existingRule) {
    Set-NetFirewallRule -Name $firewallRuleName -DisplayName "IOT Server (TCP 8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Domain,Private,Public -Enabled True
    Write-Host "[✓] Existing Firewall Rule '$firewallRuleName' updated for Domain, Private, and Public profiles." -ForegroundColor Green
} else {
    New-NetFirewallRule -Name $firewallRuleName -DisplayName "IOT Server (TCP 8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Domain,Private,Public -Enabled True | Out-Null
    Write-Host "[✓] Inbound Firewall Rule created: Port TCP 8000 allowed for Domain, Private, and Public profiles." -ForegroundColor Green
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host " [✓] Offline Hotspot Setup Completed Successfully!        " -ForegroundColor Green
Write-Host "     - Adapter: $($adapter.Name) ($($adapter.InterfaceDescription))" -ForegroundColor White
Write-Host "     - IP Address: 10.254.254.1" -ForegroundColor White
Write-Host "     - Subnet Mask: 255.255.255.0 (/24)" -ForegroundColor White
Write-Host "     - Default Gateway: 10.254.254.1" -ForegroundColor White
Write-Host "     - Firewall: TCP Port 8000 Allowed (Domain/Private/Public)" -ForegroundColor White
Write-Host "     - Windows Mobile Hotspot is now ready to toggle ON offline!" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Green
