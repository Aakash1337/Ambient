$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath ".venv")) {
    Write-Host "Creating Python 3.11 virtual environment..."
    py -3.11 -m venv .venv
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

# The Microsoft Store build of Python runs inside an app container whose
# microphone capability is separate from the normal Windows privacy settings and
# frequently reads "Deny". Under it, EVERY microphone fails to open with
# -9996/-9999 across MME, DirectSound and WASAPI while loopback capture keeps
# working -- so the app starts, shows "mic:off", and silently never hears you.
# Refuse to build on it rather than shipping that trap.
$BasePrefix = & $VenvPython -c "import sys; print(sys.base_prefix)"
if ($BasePrefix -match "WindowsApps") {
    Write-Error @"
This .venv is built on the Microsoft Store Python:
    $BasePrefix
Its app container blocks microphone access, so Ambient would run
system-audio-only and never hear you speak.

Install a normal Python 3.11 and rebuild:
    winget install --id Python.Python.3.11 --scope user
    Remove-Item -Recurse -Force .venv
    .\setup.ps1
"@
    exit 1
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

$GateSettings = & $VenvPython -c "from ambientqa.config import load_config; c=load_config().gate; print(c.model); print(c.ollama_url)"
$GateModel = $GateSettings[0]
$GateUrl = $GateSettings[1]
Write-Host "Warming Ollama model $GateModel (the first load can take about 67 seconds)..."
$WarmupBody = @{
    model = $GateModel
    messages = @(@{ role = "user"; content = "Reply with JSON: {`"q`":false,`"query`":`"`"}" })
    think = $false
    stream = $false
    keep_alive = "30m"
    options = @{ temperature = 0; num_predict = 64 }
    format = @{
        type = "object"
        properties = @{
            q = @{ type = "boolean" }
            query = @{ type = "string" }
        }
        required = @("q", "query")
        additionalProperties = $false
    }
} | ConvertTo-Json -Depth 8

try {
    Invoke-RestMethod -Uri $GateUrl `
        -Method Post -ContentType "application/json" -Body $WarmupBody | Out-Null
    Write-Host "Ollama model is warm."
}
catch {
    Write-Warning "Ollama warmup failed. Start Ollama and run setup.ps1 again. $($_.Exception.Message)"
}

Write-Host "Available capture devices:"
& $VenvPython scripts/list_devices.py
