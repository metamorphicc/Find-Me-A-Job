$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ProjectRoot ".venv\Scripts\job-search.exe"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config.toml"
$ExampleConfig = Join-Path $ProjectRoot "config.example.toml"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Virtual environment is missing. Follow the setup steps in README.md."
}

if (-not (Test-Path -LiteralPath $Config)) {
    Copy-Item -LiteralPath $ExampleConfig -Destination $Config
    Write-Host "Created config.toml. Edit search.queries before the next run."
}

Write-Host "Checking the local Chromium installation..."
& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Chromium installation failed. Check the internet connection and try again."
}

& $Runner --config $Config scan --open
exit $LASTEXITCODE
