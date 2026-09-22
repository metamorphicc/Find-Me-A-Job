$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ProjectRoot ".venv\Scripts\job-search.exe"
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

& $Runner --config $Config scan --open
exit $LASTEXITCODE
