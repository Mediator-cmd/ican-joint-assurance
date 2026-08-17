Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackageRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "."))
$StateFile = Join-Path $PackageRoot ".runtime\offline-state.json"
$ExpectedExecutable = [System.IO.Path]::GetFullPath((Join-Path $PackageRoot "app\joint_assurance_offline\joint_assurance_offline.exe"))

if (-not (Test-Path -LiteralPath $StateFile)) {
    Write-Host "[INFO] No recorded offline demonstration is running."
    exit 0
}

try {
    $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.schema_version -ne 1 -or $state.app_id -ne "ican-joint-assurance-offline") {
        throw "The state file does not belong to this offline package."
    }
    $processId = [int]$state.pid
    $expectedStart = [DateTimeOffset]::Parse([string]$state.started_at_utc).UtcDateTime
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $StateFile -Force
        Write-Host "[INFO] The recorded offline demonstration was already stopped."
        exit 0
    }
    $actualExecutable = [System.IO.Path]::GetFullPath([string]$process.Path)
    if (-not [string]::Equals($actualExecutable, $ExpectedExecutable, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The recorded PID no longer belongs to this offline package. No process was stopped."
    }
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedStart).TotalSeconds) -ge 2) {
        throw "The recorded PID start time no longer matches. No process was stopped."
    }
}
catch {
    Write-Error "Safety check failed. $($_.Exception.Message)"
    exit 1
}

Stop-Process -Id $process.Id -Force
Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
Write-Host "[OK] Offline demonstration stopped."
