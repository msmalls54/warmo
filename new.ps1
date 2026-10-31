param(
    [switch]$Sim, [switch]$Real, [string]$XiaoUrl, [string]$MissionId,
    [switch]$TracksRaised, [switch]$FloorClear, [switch]$BenchPassed,
    [string]$SettingsFile
)
& (Join-Path $PSScriptRoot 'patrol.ps1') -Action new @PSBoundParameters
exit $LASTEXITCODE
