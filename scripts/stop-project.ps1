Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$StateFile = Join-Path $RuntimeRoot "server-state.json"
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Get-PropertyValue {
    param(
        [object]$InputObject,
        [string]$Name
    )

    if ($null -eq $InputObject) {
        return $null
    }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($property) {
        return $property.Value
    }
    return $null
}

function Get-ServiceExecutablePath {
    param([string]$ServiceName)

    if ($ServiceName -eq "backend" -and (Test-Path -LiteralPath $PythonPath)) {
        return [System.IO.Path]::GetFullPath($PythonPath)
    }
    if ($ServiceName -eq "frontend") {
        $nodeExecutable = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($nodeExecutable -and $nodeExecutable.Source) {
            return [System.IO.Path]::GetFullPath($nodeExecutable.Source)
        }
    }
    return $null
}

function Get-ExpectedExecutablePath {
    param([object]$Service)

    $recordedPath = [string](Get-PropertyValue -InputObject $Service -Name "executablePath")
    if ($recordedPath -and [System.IO.Path]::GetExtension($recordedPath) -ieq ".exe") {
        return [System.IO.Path]::GetFullPath($recordedPath)
    }

    $serviceName = [string](Get-PropertyValue -InputObject $Service -Name "name")
    return Get-ServiceExecutablePath -ServiceName $serviceName
}

function Get-RecordedServices {
    param([object]$State)

    $servicesValue = Get-PropertyValue -InputObject $State -Name "services"
    if ($null -ne $servicesValue) {
        return @($servicesValue)
    }

    $legacyPid = Get-PropertyValue -InputObject $State -Name "pid"
    if ($legacyPid) {
        return @([pscustomobject]@{
            name = "frontend"
            pid = $legacyPid
            port = Get-PropertyValue -InputObject $State -Name "port"
            url = Get-PropertyValue -InputObject $State -Name "url"
            processName = Get-PropertyValue -InputObject $State -Name "processName"
            processStartTimeUtc = Get-PropertyValue -InputObject $State -Name "processStartTimeUtc"
            executablePath = Get-PropertyValue -InputObject $State -Name "nodePath"
        })
    }
    return @()
}

function Get-ServiceIdentityState {
    param([object]$Service)

    $serviceName = [string](Get-PropertyValue -InputObject $Service -Name "name")
    $processIdValue = Get-PropertyValue -InputObject $Service -Name "pid"
    $processName = [string](Get-PropertyValue -InputObject $Service -Name "processName")
    $startTimeValue = [string](Get-PropertyValue -InputObject $Service -Name "processStartTimeUtc")
    if (-not $serviceName -or -not $processIdValue -or -not $processName -or -not $startTimeValue) {
        return [pscustomobject]@{ status = "invalid"; name = $serviceName; process = $null; service = $Service }
    }
    if ($serviceName -notin @("frontend", "backend")) {
        return [pscustomobject]@{ status = "invalid"; name = $serviceName; process = $null; service = $Service }
    }

    $process = Get-Process -Id ([int]$processIdValue) -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{ status = "missing"; name = $serviceName; process = $null; service = $Service }
    }

    try {
        if (-not [string]::Equals($process.ProcessName, $processName, [System.StringComparison]::OrdinalIgnoreCase)) {
            return [pscustomobject]@{ status = "mismatch"; name = $serviceName; process = $process; service = $Service }
        }
        $expectedStart = [DateTimeOffset]::Parse($startTimeValue).UtcDateTime
        if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedStart).TotalSeconds) -ge 2) {
            return [pscustomobject]@{ status = "mismatch"; name = $serviceName; process = $process; service = $Service }
        }

        $expectedPath = Get-ExpectedExecutablePath -Service $Service
        if ($expectedPath) {
            $actualPath = $null
            try { $actualPath = $process.Path } catch { }
            if ($actualPath) {
                $normalizedExpected = [System.IO.Path]::GetFullPath($expectedPath)
                $normalizedActual = [System.IO.Path]::GetFullPath($actualPath)
                if (-not [string]::Equals($normalizedExpected, $normalizedActual, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return [pscustomobject]@{ status = "mismatch"; name = $serviceName; process = $process; service = $Service }
                }
            }
        }
        return [pscustomobject]@{ status = "valid"; name = $serviceName; process = $process; service = $Service }
    }
    catch {
        return [pscustomobject]@{ status = "mismatch"; name = $serviceName; process = $process; service = $Service }
    }
}

function Stop-RecordedProcess {
    param(
        [int]$ProcessId,
        [string]$ServiceName
    )

    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    if (Test-Path -LiteralPath $taskkill) {
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & $taskkill /PID $ProcessId /T /F 2>$null | Out-Null
        }
        catch { }
        finally {
            $ErrorActionPreference = $previousErrorPreference
        }
    }
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            Write-Host "[OK] Stopped $ServiceName service (PID $ProcessId)."
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "$ServiceName process $ProcessId did not stop."
}

if (-not (Test-Path -LiteralPath $StateFile)) {
    Write-Host "[INFO] No recorded Joint Assurance service group is running."
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
    $recordedRoot = [string](Get-PropertyValue -InputObject $state -Name "projectRoot")
    if (-not $recordedRoot) {
        throw "The recorded project path is missing."
    }
    $expectedRoot = [System.IO.Path]::GetFullPath($recordedRoot).TrimEnd("\")
    $actualRoot = $ProjectRoot.TrimEnd("\")
    if (-not [string]::Equals($expectedRoot, $actualRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The recorded project path does not match this project."
    }

    $services = @(Get-RecordedServices -State $state)
    if ($services.Count -eq 0) {
        throw "No usable service records were found."
    }
    $serviceNames = @($services | ForEach-Object { [string](Get-PropertyValue -InputObject $_ -Name "name") })
    if (@($serviceNames | Select-Object -Unique).Count -ne $serviceNames.Count) {
        throw "Duplicate service records were found."
    }

    $identityStates = @($services | ForEach-Object { Get-ServiceIdentityState -Service $_ })
    $unsafeStates = @($identityStates | Where-Object { $_.status -in @("invalid", "mismatch") })
    if ($unsafeStates.Count -gt 0) {
        $unsafeNames = ($unsafeStates | ForEach-Object { if ($_.name) { $_.name } else { "unknown" } }) -join ", "
        throw "Recorded process identity failed validation for: $unsafeNames."
    }
}
catch {
    Write-Error "Safety check failed; no process was stopped. $($_.Exception.Message)"
    exit 1
}

$orderedStates = @(
    $identityStates | Sort-Object @{ Expression = {
        if ($_.name -eq "frontend") { 0 }
        elseif ($_.name -eq "backend") { 1 }
        else { 2 }
    } }
)

try {
    foreach ($identityState in $orderedStates) {
        if ($identityState.status -eq "missing") {
            Write-Host "[INFO] Recorded $($identityState.name) service was already stopped."
            continue
        }
        Stop-RecordedProcess -ProcessId ([int]$identityState.process.Id) -ServiceName $identityState.name
    }
}
catch {
    Write-Error "$($_.Exception.Message) Runtime state was preserved for inspection."
    exit 1
}

Remove-Item -LiteralPath $StateFile -Force
Write-Host "[OK] Joint Assurance service group stopped. Runtime logs were preserved."
