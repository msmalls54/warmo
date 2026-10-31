param(
    [ValidateSet('Status','Software','Network','SimDemo','CheckTank','Flash','RealDemo','DesktopReal','DesktopSim')]
    [string]$Action = 'Status',
    [string]$Port,
    [string]$XiaoUrl,
    [string]$MissionId,
    [switch]$BatteryDisconnected,
    [switch]$TracksRaised,
    [switch]$FloorClear,
    [switch]$BenchPassed,
    [switch]$Resume
)
$ErrorActionPreference = 'Stop'
$taskProject = $PSScriptRoot
$taskRoot = Join-Path $env:USERPROFILE 'hw-test'
$taskRuntime = Join-Path $taskRoot 'warmo-desktop-runtime'
$taskPython = Join-Path $taskRuntime '.venv\Scripts\python.exe'
$taskTersePython = Join-Path $taskRoot 'warmo-terse-runtime\.venv\Scripts\python.exe'
$taskVmStarter = Join-Path $taskRoot 'terse-vm\start_software.py'
$taskWindowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskNode = (Get-Command node.exe -ErrorAction Stop).Source
$env:PYTHONDONTWRITEBYTECODE = '1'

function Invoke-WarmoCommand([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "WARMO action stopped (exit $LASTEXITCODE). No later steps were started." }
}

function Start-WarmoSoftware {
    Invoke-WarmoCommand $taskTersePython @($taskVmStarter)
}

function Resolve-WarmoAddress {
    if ($XiaoUrl) { return $XiaoUrl }
    $latest = Get-ChildItem -LiteralPath $taskRuntime -Filter 'connection-*.json' -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { throw 'Supply -XiaoUrl from the replacement board serial output. No confirmed connection record exists yet.' }
    $record = Get-Content -LiteralPath $latest.FullName -Raw | ConvertFrom-Json
    if (-not $record.xiao_url) { throw 'The saved board connection has no IP address.' }
    return $record.xiao_url
}

switch ($Action) {
    'Status' {
        Invoke-WarmoCommand $taskTersePython @($taskVmStarter, '--status')
        Invoke-WarmoCommand $taskPython @((Join-Path $taskProject 'flash_warmo.py'))
        Invoke-WarmoCommand $taskPython @('-m', 'serial.tools.list_ports')
        Write-Output 'Status only. No serial port was opened and no movement or flash command was sent.'
    }
    'Software' { Start-WarmoSoftware }
    'Network' {
        Invoke-WarmoCommand $taskWindowsPowerShell @('-NoProfile', '-File', (Join-Path $taskProject 'hotspot.ps1'), '-Action', 'Start')
    }
    'SimDemo' {
        Start-WarmoSoftware
        if ($Resume -and -not $MissionId) { throw 'Supply the saved -MissionId when resuming.' }
        if (-not $MissionId) { $MissionId = 'warmo-sim-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
        $taskArguments = @((Join-Path $taskProject 'demo.mjs'), '--mission-id', $MissionId)
        if ($Resume) { $taskArguments += '--resume' }
        Invoke-WarmoCommand $taskNode $taskArguments
    }
    'Flash' {
        if (-not $BatteryDisconnected -or -not $Port) {
            throw 'Owner present: disconnect the motor battery, identify the replacement USB port, then supply -BatteryDisconnected -Port COMn.'
        }
        Invoke-WarmoCommand $taskWindowsPowerShell @('-NoProfile', '-File', (Join-Path $taskProject 'hotspot.ps1'), '-Action', 'Start')
        Invoke-WarmoCommand $taskPython @((Join-Path $taskProject 'flash_warmo.py'), '--flash', '--port', $Port, '--battery-disconnected')
    }
    'CheckTank' {
        $address = Resolve-WarmoAddress
        Invoke-WarmoCommand $taskPython @((Join-Path $taskProject 'set_tank.py'), '--real', '--xiao-url', $address)
        Start-WarmoSoftware
        Invoke-WarmoCommand $taskNode @((Join-Path $taskProject 'demo.mjs'), '--real', '--xiao-url', $address, '--check')
    }
    'RealDemo' {
        if (($TracksRaised -and $FloorClear) -or (-not $TracksRaised -and -not ($FloorClear -and $BenchPassed))) {
            throw 'First use -TracksRaised with the owner ready; a later floor demo requires -FloorClear -BenchPassed instead.'
        }
        if ($Resume -and -not $MissionId) { throw 'Supply the saved -MissionId when resuming; first check the physical tank position.' }
        $address = Resolve-WarmoAddress
        Start-WarmoSoftware
        if (-not $MissionId) { $MissionId = 'warmo-real-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
        $taskArguments = @((Join-Path $taskProject 'demo.mjs'), '--real', '--xiao-url', $address, '--mission-id', $MissionId)
        if ($TracksRaised) { $taskArguments += '--tracks-raised' }
        if ($FloorClear) { $taskArguments += @('--floor-clear', '--bench-passed') }
        if ($Resume) { $taskArguments += '--resume' }
        Invoke-WarmoCommand $taskNode $taskArguments
    }
    'DesktopReal' {
        $address = Resolve-WarmoAddress
        Start-WarmoSoftware
        Invoke-WarmoCommand $taskPython @((Join-Path $taskProject 'set_tank.py'), '--real', '--xiao-url', $address, '--apply')
    }
    'DesktopSim' {
        Invoke-WarmoCommand $taskPython @((Join-Path $taskProject 'set_tank.py'), '--sim', '--apply')
    }
}
