param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Joint Assurance - Secure AI Configuration"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$ConfigFile = Join-Path $RuntimeRoot "ai-config.json"
$DefaultBaseUrl = "https://api.deepseek.com"
$DefaultModel = "deepseek-v4-flash"

function Read-ValueWithDefault {
    param(
        [string]$Prompt,
        [string]$Default
    )

    $value = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Validate-BaseUrl {
    param([string]$Value)

    $parsed = $null
    if (-not [System.Uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$parsed) -or
        $parsed.Scheme -notin @("http", "https") -or
        [string]::IsNullOrWhiteSpace($parsed.Host) -or
        $parsed.UserInfo -or $parsed.Query -or $parsed.Fragment) {
        throw "Base URL must be an absolute HTTP(S) URL without credentials, query, or fragment."
    }
    return $Value.TrimEnd("/")
}

function Protect-ApiKey {
    param([Security.SecureString]$SecureApiKey)

    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureApiKey)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 8) {
            throw "The API key must contain at least 8 characters."
        }
        return ConvertFrom-SecureString -SecureString $SecureApiKey
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$Host.UI.WriteLine("Configure the real backend model. API key input is hidden and is not written to Git or logs.")
$Host.UI.WriteLine("Press Enter to accept the DeepSeek defaults shown in brackets.")
$baseUrl = Validate-BaseUrl (Read-ValueWithDefault -Prompt "OpenAI-compatible base URL" -Default $DefaultBaseUrl)
$model = Read-ValueWithDefault -Prompt "Model" -Default $DefaultModel
if ($model.Length -gt 80 -or $model -notmatch '^[A-Za-z0-9._:/-]+$') {
    throw "Model may contain only letters, numbers, dot, underscore, colon, slash, or hyphen."
}

$secureApiKey = Read-Host "API key (input is hidden)" -AsSecureString
$protectedApiKey = Protect-ApiKey -SecureApiKey $secureApiKey
$config = [ordered]@{
    schema_version = 1
    provider = "openai_compatible"
    base_url = $baseUrl
    model = $model
    api_key_protected = $protectedApiKey
    configured_at_utc = [DateTime]::UtcNow.ToString("o")
}

$temporaryConfigFile = "$ConfigFile.tmp"
try {
    $config | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryConfigFile -Encoding UTF8
    Move-Item -LiteralPath $temporaryConfigFile -Destination $ConfigFile -Force
}
finally {
    Remove-Item -LiteralPath $temporaryConfigFile -Force -ErrorAction SilentlyContinue
}

Write-Host "[OK] AI configuration saved with Windows-user encryption."
Write-Host "[INFO] The next project start will restart the backend and verify the configured model."
