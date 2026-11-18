<#
FinanceOps demo launcher (Windows PowerShell). Starts the API (which serves the
owner console at /app/) and the worker loop together; Ctrl+C stops both.

  .\start.ps1                 # free: scripted agents, template customers, no API key
  .\start.ps1 -Live           # real Claude agents + Haiku customers (COSTS MONEY, capped)
  .\start.ps1 -Fresh          # start from an empty demo database

Live mode needs ANTHROPIC_API_KEY in YOUR terminal first (never paste it in chat):
  $env:ANTHROPIC_API_KEY = "sk-ant-..."
Hard spend caps (US cents, real clock) default to 450/day and 2500 total; override with
$env:FINANCEOPS_DAILY_CAP_CENTS / $env:FINANCEOPS_TOTAL_CAP_CENTS before starting.
#>
param(
    [switch]$Live,
    [switch]$Fresh,
    [string]$Token = "demo-owner",
    [int]$Port = 8000,
    [string]$Db = "demo.sqlite3",
    [double]$Interval = 2
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No virtual environment yet. Run:  py -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if ($Live -and -not $env:ANTHROPIC_API_KEY) {
    Write-Host "Live mode needs ANTHROPIC_API_KEY set in this terminal:  `$env:ANTHROPIC_API_KEY = 'sk-ant-...'"
    exit 1
}
if ($Fresh) {
    foreach ($f in @($Db, "$Db-wal", "$Db-shm")) { if (Test-Path $f) { Remove-Item $f } }
}
if (-not (Test-Path "frontend\dist\index.html")) {
    Write-Host "Building the owner console (first run)..."
    Push-Location frontend
    if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund }
    npm run build
    Pop-Location
}

$env:FINANCEOPS_DB = $Db
$env:FINANCEOPS_ENABLE_DEMO = "1"
$env:FINANCEOPS_OWNER_TOKEN = $Token
$env:FINANCEOPS_AGENT_AUTOSEND = "1"   # agent-written messages may go out under the policy rules
if (-not $env:FINANCEOPS_DAILY_CAP_CENTS) { $env:FINANCEOPS_DAILY_CAP_CENTS = "450" }
if (-not $env:FINANCEOPS_TOTAL_CAP_CENTS) { $env:FINANCEOPS_TOTAL_CAP_CENTS = "2500" }

$agents = if ($Live) { "--agents" } else { "--scripted-agents" }
$api = Start-Process -FilePath $python -PassThru -NoNewWindow -ArgumentList @(
    "-m", "uvicorn", "api:create_app", "--factory", "--host", "127.0.0.1", "--port", "$Port")
Start-Sleep -Seconds 2
$worker = Start-Process -FilePath $python -PassThru -NoNewWindow -ArgumentList @(
    "worker.py", "--loop", "--interval", "$Interval", $agents)

Write-Host ""
Write-Host "FinanceOps is running ($(if ($Live) {'LIVE Claude agents, capped at $' + ([int]$env:FINANCEOPS_DAILY_CAP_CENTS / 100) + '/day and $' + ([int]$env:FINANCEOPS_TOTAL_CAP_CENTS / 100) + ' total'} else {'scripted agents, no cost'}))."
Write-Host "  Console:  http://127.0.0.1:$Port/app/   (owner token: $Token)"
Write-Host "  API docs: http://127.0.0.1:$Port/docs"
Write-Host "  Press 'Run the month' in the console. Ctrl+C here stops everything."
try {
    Wait-Process -Id $api.Id, $worker.Id
} finally {
    foreach ($p in @($worker, $api)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
}
