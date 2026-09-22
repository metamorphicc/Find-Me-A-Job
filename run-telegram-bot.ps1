$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ProjectRoot ".venv\Scripts\job-search.exe"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config.toml"
$ExampleConfig = Join-Path $ProjectRoot "config.example.toml"
$Profile = Join-Path $ProjectRoot "profile.json"
$ExampleProfile = Join-Path $ProjectRoot "profile.example.json"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Virtual environment is missing. Follow the setup steps in README.md."
}

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath $ExampleConfig -Destination $Config
    Write-Host "Created config.toml. Set telegram.bot_token and search.queries there."
}

if (-not (Test-Path -LiteralPath $Profile)) {
    Copy-Item -LiteralPath $ExampleProfile -Destination $Profile
    Write-Host "Created profile.json. Fill name, about, and contact before searching."
}

& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Chromium installation failed. Check the internet connection and try again."
}

& $Runner --config $Config bot
exit $LASTEXITCODE
