param(
    [Parameter(Mandatory=$true)][ValidateSet('new','go')][string]$Action,
    [switch]$Sim, [switch]$Real, [string]$XiaoUrl, [string]$MissionId,
    [switch]$TracksRaised, [switch]$FloorClear, [switch]$BenchPassed,
    [string]$SettingsFile
)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $env:USERPROFILE 'hw-test\warmo-terse-runtime\.venv\Scripts\python.exe'
$taskLauncher = Join-Path $env:USERPROFILE 'hw-test\terse-vm\patrol_launcher.py'
$taskArguments = @($taskLauncher, $Action)
if ($Sim) { $taskArguments += '--sim' }
if ($Real) { $taskArguments += '--real' }
if ($XiaoUrl) { $taskArguments += @('--xiao-url', $XiaoUrl) }
if ($MissionId) { $taskArguments += @('--mission-id', $MissionId) }
if ($TracksRaised) { $taskArguments += '--tracks-raised' }
if ($FloorClear) { $taskArguments += '--floor-clear' }
if ($BenchPassed) { $taskArguments += '--bench-passed' }
if ($SettingsFile) { $taskArguments += @('--settings-file', $SettingsFile) }
$env:PYTHONDONTWRITEBYTECODE = '1'
& $taskPython @taskArguments
exit $LASTEXITCODE
