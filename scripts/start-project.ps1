param(
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Managed terminals can expose both Path and PATH. Normalize the inherited
# process environment so Start-Process does not see duplicate dictionary keys.
$inheritedPath = $env:Path
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $null, "Process")
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $inheritedPath, "Process")

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$PackageJson = Join-Path $FrontendRoot "package.json"
$ViteScript = Join-Path $FrontendRoot "node_modules\vite\bin\vite.js"
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$DemoGenerator = Join-Path $ProjectRoot "scripts\generate_demo_output.py"
$DemoOutput = Join-Path $FrontendRoot "public\demo-output.json"
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$StateFile = Join-Path $RuntimeRoot "server-state.json"
$AIConfigFile = Join-Path $RuntimeRoot "ai-config.json"
$BackendStdoutLog = Join-Path $RuntimeRoot "backend.stdout.log"
$BackendStderrLog = Join-Path $RuntimeRoot "backend.stderr.log"
$FrontendStdoutLog = Join-Path $RuntimeRoot "frontend.stdout.log"
$FrontendStderrLog = Join-Path $RuntimeRoot "frontend.stderr.log"

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

function Get-TextSha256 {
    param([string]$Text)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha256.ComputeHash($bytes)
        return (-join ($hash | ForEach-Object { $_.ToString("x2") }))
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-EffectiveAIConfiguration {
    if (Test-Path -LiteralPath $AIConfigFile) {
        try {
            $rawConfig = Get-Content -LiteralPath $AIConfigFile -Raw -Encoding UTF8
            $config = $rawConfig | ConvertFrom-Json
            $schemaVersion = [int](Get-PropertyValue -InputObject $config -Name "schema_version")
            $provider = [string](Get-PropertyValue -InputObject $config -Name "provider")
            $baseUrl = [string](Get-PropertyValue -InputObject $config -Name "base_url")
            $model = [string](Get-PropertyValue -InputObject $config -Name "model")
            $protectedApiKey = [string](Get-PropertyValue -InputObject $config -Name "api_key_protected")
            if ($schemaVersion -ne 1 -or $provider -ne "openai_compatible" -or
                [string]::IsNullOrWhiteSpace($baseUrl) -or [string]::IsNullOrWhiteSpace($model) -or
                [string]::IsNullOrWhiteSpace($protectedApiKey)) {
                throw "The local AI configuration is incomplete."
            }

            $parsedUrl = $null
            if (-not [System.Uri]::TryCreate($baseUrl, [System.UriKind]::Absolute, [ref]$parsedUrl) -or
                $parsedUrl.Scheme -notin @("http", "https") -or [string]::IsNullOrWhiteSpace($parsedUrl.Host) -or
                $parsedUrl.UserInfo -or $parsedUrl.Query -or $parsedUrl.Fragment) {
                throw "The local AI base URL is invalid."
            }
            if ($model.Length -gt 80 -or $model -notmatch '^[A-Za-z0-9._:/-]+$') {
                throw "The local AI model label is invalid."
            }

            $secureApiKey = ConvertTo-SecureString -String $protectedApiKey
            return [pscustomobject]@{
                configured = $true
                source = "encrypted_file"
                baseUrl = $baseUrl.TrimEnd("/")
                model = $model
                secureApiKey = $secureApiKey
                fingerprint = (Get-FileHash -LiteralPath $AIConfigFile -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        catch {
            throw "Local AI configuration could not be loaded safely. Re-run the Configure AI entry. $($_.Exception.Message)"
        }
    }

    $environmentApiKey = [string]$env:AI_API_KEY
    $environmentBaseUrl = [string]$env:AI_BASE_URL
    $environmentModel = [string]$env:AI_MODEL
    if (-not [string]::IsNullOrWhiteSpace($environmentApiKey) -and
        -not [string]::IsNullOrWhiteSpace($environmentBaseUrl) -and
        -not [string]::IsNullOrWhiteSpace($environmentModel)) {
        return [pscustomobject]@{
            configured = $true
            source = "environment"
            baseUrl = $environmentBaseUrl.TrimEnd("/")
            model = $environmentModel
            secureApiKey = $null
            fingerprint = (Get-TextSha256 -Text ("environment|" + $environmentBaseUrl.TrimEnd("/") + "|" + $environmentModel))
        }
    }

    return [pscustomobject]@{
        configured = $false
        source = "none"
        baseUrl = $null
        model = $null
        secureApiKey = $null
        fingerprint = $null
    }
}

function Set-ProcessEnvironmentValue {
    param(
        [string]$Name,
        [AllowNull()][string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
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

function Find-AvailablePort {
    param(
        [int]$StartPort,
        [int]$EndPort,
        [string]$ServiceName
    )

    foreach ($candidatePort in $StartPort..$EndPort) {
        if (Test-PortAvailable -Port $candidatePort) {
            return $candidatePort
        }
    }
    throw "No available $ServiceName port was found in the $StartPort-$EndPort range."
}

function Test-HttpReady {
    param(
        [int]$ProcessId,
        [string]$Url,
        [int]$Attempts = 80
    )

    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return $false
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $true
            }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    return $false
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

function New-ServiceRecord {
    param(
        [string]$Name,
        [System.Diagnostics.Process]$Process,
        [string]$ExecutablePath,
        [int]$Port,
        [string]$Url,
        [string]$HealthUrl,
        [string]$WorkingDirectory,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    $Process.Refresh()
    $normalizedExecutablePath = if ($ExecutablePath) {
        [System.IO.Path]::GetFullPath($ExecutablePath)
    } else {
        $null
    }

    return [pscustomobject][ordered]@{
        name = $Name
        pid = $Process.Id
        port = $Port
        url = $Url
        healthUrl = $HealthUrl
        processName = $Process.ProcessName
        processStartTimeUtc = $Process.StartTime.ToUniversalTime().ToString("o")
        executablePath = $normalizedExecutablePath
        workingDirectory = $WorkingDirectory
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
    }
}

function Write-RuntimeState {
    param(
        [object[]]$Services,
        [string]$Status,
        [string]$BackendUrl,
        [string]$FrontendUrl,
        [string]$LaunchedAtUtc,
        [AllowNull()][string]$AiConfigFingerprint
    )

    $state = [ordered]@{
        schema_version = 2
        projectRoot = $ProjectRoot
        status = $Status
        backendUrl = $BackendUrl
        frontendUrl = $FrontendUrl
        launchedAtUtc = $LaunchedAtUtc
        aiConfigFingerprint = $AiConfigFingerprint
        services = @($Services)
    }
    $temporaryStateFile = "$StateFile.tmp"
    try {
        $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryStateFile -Encoding UTF8
        Move-Item -LiteralPath $temporaryStateFile -Destination $StateFile -Force
    }
    finally {
        Remove-Item -LiteralPath $temporaryStateFile -Force -ErrorAction SilentlyContinue
    }
}

function Get-ServiceIdentityState {
    param([object]$Service)

    $serviceName = [string](Get-PropertyValue -InputObject $Service -Name "name")
    $processIdValue = Get-PropertyValue -InputObject $Service -Name "pid"
    $processName = [string](Get-PropertyValue -InputObject $Service -Name "processName")
    $startTimeValue = [string](Get-PropertyValue -InputObject $Service -Name "processStartTimeUtc")
    if (-not $serviceName -or -not $processIdValue -or -not $processName -or -not $startTimeValue) {
        return [pscustomobject]@{ status = "invalid"; name = $serviceName; process = $null }
    }

    $process = Get-Process -Id ([int]$processIdValue) -ErrorAction SilentlyContinue
    if (-not $process) {
        return [pscustomobject]@{ status = "missing"; name = $serviceName; process = $null }
    }

    try {
        if (-not [string]::Equals($process.ProcessName, $processName, [System.StringComparison]::OrdinalIgnoreCase)) {
            return [pscustomobject]@{ status = "reused"; name = $serviceName; process = $process }
        }
        $expectedStart = [DateTimeOffset]::Parse($startTimeValue).UtcDateTime
        if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expectedStart).TotalSeconds) -ge 2) {
            return [pscustomobject]@{ status = "reused"; name = $serviceName; process = $process }
        }

        $expectedPath = Get-ExpectedExecutablePath -Service $Service
        if ($expectedPath) {
            $actualPath = $null
            try { $actualPath = $process.Path } catch { }
            if ($actualPath) {
                $normalizedExpected = [System.IO.Path]::GetFullPath($expectedPath)
                $normalizedActual = [System.IO.Path]::GetFullPath($actualPath)
                if (-not [string]::Equals($normalizedExpected, $normalizedActual, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return [pscustomobject]@{ status = "reused"; name = $serviceName; process = $process }
                }
            }
        }
        return [pscustomobject]@{ status = "valid"; name = $serviceName; process = $process }
    }
    catch {
        return [pscustomobject]@{ status = "invalid"; name = $serviceName; process = $process }
    }
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

if (-not (Test-Path -LiteralPath $PackageJson)) {
    throw "Frontend package file not found: $PackageJson"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project Python environment was not found: $PythonPath"
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$aiConfiguration = Get-EffectiveAIConfiguration
if ($aiConfiguration.configured) {
    Write-Host "[OK] AI provider configured: $($aiConfiguration.model)"
}
else {
    Write-Host "[INFO] AI provider is not configured; deterministic assistance remains available."
}

if (Test-Path -LiteralPath $StateFile) {
    $existingState = $null
    try {
        $existingState = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "Runtime state is unreadable. No process was stopped or replaced: $StateFile"
    }

    $recordedRoot = [string](Get-PropertyValue -InputObject $existingState -Name "projectRoot")
    if (-not $recordedRoot) {
        throw "Runtime state has no project path. No process was stopped or replaced: $StateFile"
    }
    $expectedRoot = [System.IO.Path]::GetFullPath($recordedRoot).TrimEnd("\")
    $actualRoot = $ProjectRoot.TrimEnd("\")
    if (-not [string]::Equals($expectedRoot, $actualRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime state belongs to a different project. No process was stopped or replaced."
    }

    $recordedServices = @(Get-RecordedServices -State $existingState)
    if ($recordedServices.Count -eq 0) {
        throw "Runtime state contains no usable service records. No process was stopped or replaced."
    }
    $recordedServiceNames = @($recordedServices | ForEach-Object { [string](Get-PropertyValue -InputObject $_ -Name "name") })
    if (@($recordedServiceNames | Select-Object -Unique).Count -ne $recordedServiceNames.Count -or
        @($recordedServiceNames | Where-Object { $_ -notin @("backend", "frontend") }).Count -gt 0) {
        throw "Runtime state contains duplicate or unknown service records. No process was stopped or replaced."
    }

    $identityStates = @($recordedServices | ForEach-Object { Get-ServiceIdentityState -Service $_ })
    if (@($identityStates | Where-Object { $_.status -eq "invalid" }).Count -gt 0) {
        throw "Runtime state contains an invalid service identity. Inspect $StateFile; no process was stopped or replaced."
    }

    $validServices = @($identityStates | Where-Object { $_.status -eq "valid" })
    if ($validServices.Count -eq 0) {
        Write-Warning "Removing stale runtime state; recorded processes are stopped or their PIDs have been safely reused."
        Remove-Item -LiteralPath $StateFile -Force
    }
    else {
        $schemaVersion = Get-PropertyValue -InputObject $existingState -Name "schema_version"
        $recordedAiFingerprint = [string](Get-PropertyValue -InputObject $existingState -Name "aiConfigFingerprint")
        $currentAiFingerprint = [string]$aiConfiguration.fingerprint
        $canReuse = [int]$schemaVersion -eq 2 -and $validServices.Count -eq $recordedServices.Count -and
            [string]::Equals($recordedAiFingerprint, $currentAiFingerprint, [System.StringComparison]::OrdinalIgnoreCase)
        $backendRecord = $null
        $frontendRecord = $null
        if ($canReuse) {
            $backendRecord = $recordedServices | Where-Object { (Get-PropertyValue -InputObject $_ -Name "name") -eq "backend" } | Select-Object -First 1
            $frontendRecord = $recordedServices | Where-Object { (Get-PropertyValue -InputObject $_ -Name "name") -eq "frontend" } | Select-Object -First 1
            $canReuse = $null -ne $backendRecord -and $null -ne $frontendRecord
        }

        if ($canReuse) {
            $backendHealthUrl = [string](Get-PropertyValue -InputObject $backendRecord -Name "healthUrl")
            $frontendUrl = [string](Get-PropertyValue -InputObject $frontendRecord -Name "url")
            $backendPid = [int](Get-PropertyValue -InputObject $backendRecord -Name "pid")
            $frontendPid = [int](Get-PropertyValue -InputObject $frontendRecord -Name "pid")
            $canReuse = (Test-HttpReady -ProcessId $backendPid -Url $backendHealthUrl -Attempts 4) -and
                (Test-HttpReady -ProcessId $frontendPid -Url $frontendUrl -Attempts 4)
        }

        if ($canReuse) {
            Write-Host "[OK] Backend and frontend are already running: $frontendUrl"
            if (-not $NoBrowser) {
                try { Open-ProjectBrowser -Url $frontendUrl }
                catch { Write-Warning "Services are running, but the browser could not be opened. Visit $frontendUrl manually." }
            }
            exit 0
        }

        Write-Warning "Cleaning up a partial or unhealthy recorded service group before restart. Reused PIDs will not be stopped."
        $validServicesForStop = @(
            $validServices | Sort-Object @{ Expression = {
                if ($_.name -eq "frontend") { 0 }
                elseif ($_.name -eq "backend") { 1 }
                else { 2 }
            } }
        )
        foreach ($validService in $validServicesForStop) {
            Stop-NewServerProcess -ProcessId ([int]$validService.process.Id)
        }
        Remove-Item -LiteralPath $StateFile -Force
    }
}

Write-Host "[INFO] Refreshing simulation dashboard data..."
& $PythonPath $DemoGenerator
if ($LASTEXITCODE -ne 0) {
    if (Test-Path -LiteralPath $DemoOutput) {
        Write-Warning "Data refresh failed. The existing demo-output.json will be used."
    }
    else {
        throw "Data refresh failed and no existing demo-output.json is available."
    }
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

$backendPort = Find-AvailablePort -StartPort 8000 -EndPort 8020 -ServiceName "backend"
$frontendPort = Find-AvailablePort -StartPort 4173 -EndPort 4199 -ServiceName "frontend"
$backendUrl = "http://127.0.0.1:$backendPort"
$backendHealthUrl = "$backendUrl/api/v1/health"
$frontendUrl = "http://127.0.0.1:$frontendPort"
$launchedAtUtc = [DateTime]::UtcNow.ToString("o")
$startedServices = New-Object System.Collections.Generic.List[object]

try {
    Write-Host "[INFO] Starting backend service at $backendUrl ..."
    $aiEnvironmentNames = @("AI_API_KEY", "AI_BASE_URL", "AI_MODEL")
    $previousAiEnvironment = @{}
    foreach ($name in $aiEnvironmentNames) {
        $previousAiEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    $apiKeyPointer = [IntPtr]::Zero
    try {
        if ($aiConfiguration.source -eq "encrypted_file") {
            $apiKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($aiConfiguration.secureApiKey)
            $plainApiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($apiKeyPointer)
            if ([string]::IsNullOrWhiteSpace($plainApiKey)) {
                throw "The encrypted AI API key is empty. Re-run the Configure AI entry."
            }
            Set-ProcessEnvironmentValue -Name "AI_API_KEY" -Value $plainApiKey
            Set-ProcessEnvironmentValue -Name "AI_BASE_URL" -Value ([string]$aiConfiguration.baseUrl)
            Set-ProcessEnvironmentValue -Name "AI_MODEL" -Value ([string]$aiConfiguration.model)
        }
        $backendProcess = Start-Process `
            -FilePath $PythonPath `
            -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", [string]$backendPort) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $BackendStdoutLog `
            -RedirectStandardError $BackendStderrLog `
            -PassThru
    }
    finally {
        if ($apiKeyPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($apiKeyPointer)
        }
        foreach ($name in $aiEnvironmentNames) {
            Set-ProcessEnvironmentValue -Name $name -Value $previousAiEnvironment[$name]
        }
    }
    $backendRecord = New-ServiceRecord `
        -Name "backend" `
        -Process $backendProcess `
        -ExecutablePath $PythonPath `
        -Port $backendPort `
        -Url $backendUrl `
        -HealthUrl $backendHealthUrl `
        -WorkingDirectory $ProjectRoot `
        -StdoutLog $BackendStdoutLog `
        -StderrLog $BackendStderrLog
    $startedServices.Add($backendRecord)
    Write-RuntimeState -Services $startedServices.ToArray() -Status "starting" -BackendUrl $backendUrl -FrontendUrl $null -LaunchedAtUtc $launchedAtUtc -AiConfigFingerprint $aiConfiguration.fingerprint

    if (-not (Test-HttpReady -ProcessId $backendProcess.Id -Url $backendHealthUrl)) {
        $recentBackendError = if (Test-Path -LiteralPath $BackendStderrLog) {
            (Get-Content -LiteralPath $BackendStderrLog -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        } else { "" }
        throw "Backend did not become healthy. See $BackendStderrLog.`n$recentBackendError"
    }
    Write-Host "[OK] Backend is healthy (PID $($backendProcess.Id)): $backendHealthUrl"

    Write-Host "[INFO] Starting frontend service at $frontendUrl ..."
    $quotedViteScript = '"' + $ViteScript.Replace('"', '\"') + '"'
    $viteArguments = @($quotedViteScript, "--host", "127.0.0.1", "--port", [string]$frontendPort, "--strictPort")
    $previousApiTarget = $env:VITE_API_TARGET
    try {
        $env:VITE_API_TARGET = $backendUrl
        $frontendProcess = Start-Process `
            -FilePath $nodeCommand.Source `
            -ArgumentList $viteArguments `
            -WorkingDirectory $FrontendRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $FrontendStdoutLog `
            -RedirectStandardError $FrontendStderrLog `
            -PassThru
    }
    finally {
        $env:VITE_API_TARGET = $previousApiTarget
    }

    $frontendRecord = New-ServiceRecord `
        -Name "frontend" `
        -Process $frontendProcess `
        -ExecutablePath $nodeCommand.Source `
        -Port $frontendPort `
        -Url $frontendUrl `
        -HealthUrl $frontendUrl `
        -WorkingDirectory $FrontendRoot `
        -StdoutLog $FrontendStdoutLog `
        -StderrLog $FrontendStderrLog
    $startedServices.Add($frontendRecord)
    Write-RuntimeState -Services $startedServices.ToArray() -Status "starting" -BackendUrl $backendUrl -FrontendUrl $frontendUrl -LaunchedAtUtc $launchedAtUtc -AiConfigFingerprint $aiConfiguration.fingerprint

    if (-not (Test-HttpReady -ProcessId $frontendProcess.Id -Url $frontendUrl)) {
        $recentFrontendError = if (Test-Path -LiteralPath $FrontendStderrLog) {
            (Get-Content -LiteralPath $FrontendStderrLog -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        } else { "" }
        throw "Frontend did not become ready. See $FrontendStderrLog.`n$recentFrontendError"
    }

    Write-RuntimeState -Services $startedServices.ToArray() -Status "ready" -BackendUrl $backendUrl -FrontendUrl $frontendUrl -LaunchedAtUtc $launchedAtUtc -AiConfigFingerprint $aiConfiguration.fingerprint
}
catch {
    for ($index = $startedServices.Count - 1; $index -ge 0; $index--) {
        Stop-NewServerProcess -ProcessId ([int]$startedServices[$index].pid)
    }
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "[OK] Joint Assurance service group is ready: $frontendUrl"
Write-Host "[INFO] Backend: $backendUrl"
Write-Host "[INFO] Double-click Stop Joint Assurance Dashboard to stop both services."
if (-not $NoBrowser) {
    try { Open-ProjectBrowser -Url $frontendUrl }
    catch { Write-Warning "Services are running, but the browser could not be opened. Visit $frontendUrl manually." }
}
