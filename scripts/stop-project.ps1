Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$StateFile = Join-Path $RuntimeRoot "server-state.json"

if (-not (Test-Path -LiteralPath $StateFile)) {
    Write-Host "[INFO] No recorded dashboard service is running."
    exit 0
}

try {
    $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch {
    Write-Error "Runtime state file is unreadable. No process was stopped: $StateFile"
    exit 1
}

try {
    if (-not $state.projectRoot -or -not $state.pid -or -not $state.processStartTimeUtc) {
        throw "Required process identity fields are missing."
    }

    $expectedRoot = [System.IO.Path]::GetFullPath([string]$state.projectRoot).TrimEnd("\")
    $actualRoot = $ProjectRoot.TrimEnd("\")
    if (-not [string]::Equals($expectedRoot, $actualRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The recorded project path does not match this project."
    }

    $processId = [int]$state.pid
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $StateFile -Force
        Write-Host "[OK] Cleared stale runtime state. The recorded process was already stopped."
        exit 0
    }

    if ($state.processName -and $process.ProcessName -ne [string]$state.processName) {
        throw "PID $processId now belongs to a different process name."
    }

    $expectedStart = [DateTimeOffset]::Parse([string]$state.processStartTimeUtc).UtcDateTime
    $actualStart = $process.StartTime.ToUniversalTime()
    if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -ge 2) {
        throw "PID $processId has been reused by a different process."
    }
}
catch {
    Write-Error "Safety check failed; no process was stopped. $($_.Exception.Message)"
    exit 1
}

$taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
if (Test-Path -LiteralPath $taskkill) {
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $taskkill /PID $processId /T /F 2>$null | Out-Null
    }
    catch { }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
}
if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

$stopped = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        $stopped = $true
        break
    }
    Start-Sleep -Milliseconds 100
}

if (-not $stopped) {
    Write-Error "Process $processId did not stop. Runtime state was preserved for inspection."
    exit 1
}

Remove-Item -LiteralPath $StateFile -Force
Write-Host "[OK] Dashboard service stopped (PID $processId). Runtime logs were preserved."
