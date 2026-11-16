[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function AwaitOp($task, $type) {
        $m = $asTaskGeneric.MakeGenericMethod($type)
        $t = $m.Invoke($null, @($task))
        $t.Wait(10000) | Out-Null
        return $t.Result
    }

    [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime] | Out-Null
    [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime] | Out-Null

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if (-not $profile) {
        $profiles = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()
        foreach ($p in $profiles) {
            if ($p.ProfileName -match "Loopback|Ethernet|Wi-Fi") {
                $profile = $p
                break
            }
        }
        if (-not $profile -and $profiles.Count -gt 0) {
            $profile = $profiles[0]
        }
    }

    Write-Host "Selected Profile: $($profile.ProfileName)"
    if ($profile) {
        $tm = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
        Write-Host "TetheringCapability: $($tm.TetheringCapability)"
        Write-Host "CurrentState: $($tm.TetheringOperationalState)"
        if ($tm.TetheringOperationalState -ne 1) { # 1 is On
            Write-Host "Turning ON Tethering..."
            $op = $tm.StartTetheringAsync()
            $res = AwaitOp $op ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
            Write-Host "Result: $($res.Status)"
        } else {
            Write-Host "Already ON"
        }
    }
} catch {
    Write-Host "Error: $_"
}
