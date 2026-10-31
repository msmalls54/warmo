param([ValidateSet('Status','Start','Stop')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime] | Out-Null
[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime] | Out-Null
[Windows.Networking.NetworkOperators.NetworkOperatorTetheringSessionAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime] | Out-Null

function Wait-WarmoOperation($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(30000)) { throw 'Hotspot operation has not completed after 30 seconds; inspect status before retrying.' }
    return $task.Result
}

function Get-WarmoConfigurationFingerprint($Configuration) {
    $value = @($Configuration.Ssid, $Configuration.Passphrase, $Configuration.Band, $Configuration.AuthenticationKind) -join "`n"
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($value)))).Replace('-', '') }
    finally { $hash.Dispose() }
}

function Assert-WarmoOwnedSession($Owned, $Configuration) {
    if ($Owned.active -ne $true) { throw 'No active hotspot session is owned by this helper; refusing to stop it.' }
    if (-not $Owned.session_configuration_fingerprint -or
        (Get-WarmoConfigurationFingerprint $Configuration) -ne $Owned.session_configuration_fingerprint) {
        throw 'The current hotspot does not match the session started by WARMO; refusing to stop it.'
    }
}

function Wait-WarmoHotspotState($Manager, [string]$Expected) {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ($Manager.TetheringOperationalState.ToString() -ne $Expected -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if ($Manager.TetheringOperationalState.ToString() -ne $Expected) { throw "Hotspot has not reached $Expected; inspect status before retrying." }
}

$taskRoot = Join-Path $env:USERPROFILE 'hw-test'
$sessionPath = Join-Path $taskRoot 'hotspot-session.json'
$markerPath = Join-Path $taskRoot 'warmo-desktop-runtime\hotspot-started-by-warmo.json'
$profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if (-not $profile) { throw 'No internet connection profile is available. Connect the laptop to Wi-Fi first.' }
$managerType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]
$capability = $managerType::GetTetheringCapabilityFromConnectionProfile($profile)
if ($capability.ToString() -ne 'Enabled') { throw "Mobile hotspot is unavailable: $capability" }
$manager = $managerType::CreateFromConnectionProfile($profile)
$persistentBefore = $manager.GetCurrentAccessPointConfiguration()
$persistentFingerprint = Get-WarmoConfigurationFingerprint $persistentBefore
$before = $manager.TetheringOperationalState.ToString()
$outcome = 'ReadOnly'
$persistentChanged = $null
$configurationVerification = 'Read-only status; active-session configuration is not the persistent baseline'
if ($Action -eq 'Start') {
    if ($before -eq 'On') {
        $outcome = 'Existing hotspot left running; no settings were changed'
    } elseif ($before -eq 'Off') {
        $saved = Get-Content -LiteralPath $sessionPath -Raw | ConvertFrom-Json
        if (-not $saved.ssid -or $saved.passphrase.Length -lt 8) { throw 'Saved test hotspot credentials are missing or invalid.' }
        $configuration = New-Object Windows.Networking.NetworkOperators.NetworkOperatorTetheringSessionAccessPointConfiguration
        $configuration.Ssid = $saved.ssid
        $configuration.Passphrase = $saved.passphrase
        $configuration.Band = [Windows.Networking.NetworkOperators.TetheringWiFiBand]::TwoPointFourGigahertz
        $configuration.AuthenticationKind = [Windows.Networking.NetworkOperators.TetheringWiFiAuthenticationKind]::Wpa2
        $resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]
        $result = Wait-WarmoOperation ($manager.StartTetheringAsync($configuration)) $resultType
        if ($result.Status.ToString() -ne 'Success') { throw "Hotspot start failed: $($result.Status)" }
        $ownership = @{ started_utc = [DateTime]::UtcNow.ToString('o'); active = $true; temporary = $true; band = '2.4 GHz'; authentication = 'WPA2'; persistent_fingerprint_before = $persistentFingerprint; session_configuration_fingerprint = $null }
        $ownership | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        Wait-WarmoHotspotState $manager 'On'
        $ownership.session_configuration_fingerprint = Get-WarmoConfigurationFingerprint ($manager.GetCurrentAccessPointConfiguration())
        $ownership | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        $configurationVerification = 'Temporary session active; persistent baseline will be compared after Stop'
        $outcome = 'Started temporary WARMO test hotspot; persistent configuration unchanged'
    } else { throw "Hotspot is currently $before; inspect again before starting." }
} elseif ($Action -eq 'Stop') {
    if ($before -eq 'Off') { $outcome = 'Already off' }
    elseif (-not (Test-Path -LiteralPath $markerPath)) { throw 'This helper did not start the current hotspot; refusing to stop it.' }
    else {
        $owned = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        Assert-WarmoOwnedSession $owned ($manager.GetCurrentAccessPointConfiguration())
        $resultType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]
        $result = Wait-WarmoOperation ($manager.StopTetheringAsync()) $resultType
        if ($result.Status.ToString() -ne 'Success') { throw "Hotspot stop failed: $($result.Status)" }
        Wait-WarmoHotspotState $manager 'Off'
        $restored = Get-WarmoConfigurationFingerprint ($manager.GetCurrentAccessPointConfiguration())
        if (-not $owned.persistent_fingerprint_before) { throw 'No saved off-state baseline is available for verification.' }
        $persistentChanged = $restored -ne $owned.persistent_fingerprint_before
        if ($persistentChanged) { throw 'Off-state hotspot configuration differs from its saved baseline; inspect before continuing.' }
        $configurationVerification = 'Off-state persistent configuration matches the baseline saved before Start'
        $owned.active = $false
        $owned | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        $outcome = 'Stopped WARMO test hotspot'
    }
}
[PSCustomObject]@{
    action = $Action; before = $before; state = $manager.TetheringOperationalState.ToString()
    client_count = $manager.ClientCount; outcome = $outcome
    credentials_printed = $false; persistent_configuration_changed = $persistentChanged
    configuration_verification = $configurationVerification
} | ConvertTo-Json
