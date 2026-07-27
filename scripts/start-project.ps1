param(
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Some managed terminals inject both Path and PATH. Windows PowerShell's
# Start-Process treats them as duplicate dictionary keys, so normalize the
# inherited process environment without changing user or machine settings.
$inheritedPath = $env:Path
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $null, "Process")
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $inheritedPath, "Process")

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$PackageJson = Join-Path $FrontendRoot "package.json"
$ViteScript = Join-Path $FrontendRoot "node_modules\vite\bin\vite.js"
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$StateFile = Join-Path $RuntimeRoot "server-state.json"
$StdoutLog = Join-Path $RuntimeRoot "frontend.stdout.log"
$StderrLog = Join-Path $RuntimeRoot "frontend.stderr.log"
$DemoOutput = Join-Path $FrontendRoot "public\demo-output.json"

function Test-StateProcess {
    param([object]$State)

    try {
        if (-not $State.pid -or -not $State.processStartTimeUtc -or -not $State.projectRoot) {
            return $false
        }

        $expectedRoot = [System.IO.Path]::GetFullPath([string]$State.projectRoot).TrimEnd("\")
        $actualRoot = $ProjectRoot.TrimEnd("\")
        if (-not [string]::Equals($expectedRoot, $actualRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }

        $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        if ($State.processName -and $process.ProcessName -ne [string]$State.processName) {
            return $false
        }

        $expectedStart = [DateTimeOffset]::Parse([string]$State.processStartTimeUtc).UtcDateTime
        $actualStart = $process.StartTime.ToUniversalTime()
        return [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -lt 2
    }
    catch {
        return $false
    }
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        try { $listener.Stop() } catch { }
    }
}

function Open-ProjectBrowser {
    param([string]$Url)

    $edgeCandidates = @()
    if (${env:ProgramFiles(x86)}) {
        $edgeCandidates += Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"
    }
    if ($env:ProgramFiles) {
        $edgeCandidates += Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"
    }

    foreach ($edgePath in ($edgeCandidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $edgePath) {
            Start-Process -FilePath $edgePath -ArgumentList @("--new-window", $Url) | Out-Null
            Write-Host "[OK] Opened in Microsoft Edge: $Url"
            return
        }
    }

    $firefoxCandidates = @()
    if ($env:ProgramFiles) {
        $firefoxCandidates += Join-Path $env:ProgramFiles "Mozilla Firefox\firefox.exe"
    }
    if (${env:ProgramFiles(x86)}) {
        $firefoxCandidates += Join-Path ${env:ProgramFiles(x86)} "Mozilla Firefox\firefox.exe"
    }

    foreach ($firefoxPath in ($firefoxCandidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $firefoxPath) {
            Start-Process -FilePath $firefoxPath -ArgumentList @("-new-window", $Url) | Out-Null
            Write-Host "[OK] Opened in Firefox: $Url"
            return
        }
    }

    Write-Warning "Edge and Firefox were not found. Opening the system default browser."
    Start-Process $Url | Out-Null
}

function Stop-NewServerProcess {
    param([int]$ProcessId)

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
}

if (-not (Test-Path -LiteralPath $PackageJson)) {
    throw "Frontend package file not found: $PackageJson"
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

if (Test-Path -LiteralPath $StateFile) {
    try {
        $existingState = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if (Test-StateProcess -State $existingState) {
            $existingUrl = if ($existingState.url) { [string]$existingState.url } else { "http://127.0.0.1:$($existingState.port)" }
            Write-Host "[OK] Service is already running (PID $($existingState.pid)): $existingUrl"
            if (-not $NoBrowser) {
                try {
                    Open-ProjectBrowser -Url $existingUrl
                }
                catch {
                    Write-Warning "The service is running, but the browser could not be opened. Visit $existingUrl manually."
                }
            }
            exit 0
        }
        Write-Warning "Removing a stale runtime state file. No process will be stopped."
        Remove-Item -LiteralPath $StateFile -Force
    }
    catch {
        Write-Warning "Removing an unreadable runtime state file. No process will be stopped."
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    }
}

$pythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$demoGenerator = Join-Path $ProjectRoot "scripts\generate_demo_output.py"
if (Test-Path -LiteralPath $pythonPath) {
    Write-Host "[INFO] Refreshing simulation dashboard data..."
    & $pythonPath $demoGenerator
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath $DemoOutput) {
            Write-Warning "Data refresh failed. The existing demo-output.json will be used."
        }
        else {
            throw "Data refresh failed and no existing demo-output.json is available."
        }
    }
}
elseif (-not (Test-Path -LiteralPath $DemoOutput)) {
    throw "Project Python environment and demo-output.json are both missing."
}
else {
    Write-Warning "Project .venv was not found. The existing demo-output.json will be used."
}

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeCommand) {
    throw "Node.js was not found. Install Node.js and retry."
}

if (-not (Test-Path -LiteralPath $ViteScript)) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $npmCommand) {
        throw "npm was not found, so frontend dependencies cannot be installed."
    }

    Write-Host "[INFO] Installing frontend dependencies..."
    Push-Location $FrontendRoot
    try {
        & $npmCommand.Source install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $ViteScript)) {
    throw "Vite entry file was not found after dependency installation: $ViteScript"
}

$port = $null
foreach ($candidatePort in 4173..4199) {
    if (Test-PortAvailable -Port $candidatePort) {
        $port = $candidatePort
        break
    }
}
if (-not $port) {
    throw "No available frontend port was found in the 4173-4199 range."
}

$url = "http://127.0.0.1:$port"
$quotedViteScript = '"' + $ViteScript.Replace('"', '\"') + '"'
$viteArguments = @($quotedViteScript, "--host", "127.0.0.1", "--port", [string]$port, "--strictPort")

Write-Host "[INFO] Starting dashboard at $url ..."
$serverProcess = Start-Process `
    -FilePath $nodeCommand.Source `
    -ArgumentList $viteArguments `
    -WorkingDirectory $FrontendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru
$serverProcess.Refresh()

$state = [ordered]@{
    projectRoot = $ProjectRoot
    pid = $serverProcess.Id
    port = $port
    url = $url
    processName = $serverProcess.ProcessName
    processStartTimeUtc = $serverProcess.StartTime.ToUniversalTime().ToString("o")
    launchedAtUtc = [DateTime]::UtcNow.ToString("o")
    nodePath = $nodeCommand.Source
}
try {
    $state | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
}
catch {
    Stop-NewServerProcess -ProcessId $serverProcess.Id
    throw "The service started, but its runtime state could not be recorded. The new process was stopped. $($_.Exception.Message)"
}

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (-not (Get-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue)) {
        break
    }
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            $ready = $true
            break
        }
    }
    catch { }
    Start-Sleep -Milliseconds 250
}

if (-not $ready) {
    Stop-NewServerProcess -ProcessId $serverProcess.Id
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    $recentError = ""
    if (Test-Path -LiteralPath $StderrLog) {
        $recentError = (Get-Content -LiteralPath $StderrLog -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    }
    throw "Dashboard did not become ready. See $StderrLog.`n$recentError"
}

Write-Host "[OK] Dashboard is running (PID $($serverProcess.Id)): $url"
Write-Host "[INFO] Double-click Stop Joint Assurance Dashboard to stop it."
if (-not $NoBrowser) {
    try {
        Open-ProjectBrowser -Url $url
    }
    catch {
        Write-Warning "The service is running, but the browser could not be opened. Visit $url manually."
    }
}
