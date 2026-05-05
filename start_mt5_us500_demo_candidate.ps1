$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Python312\python.exe"
$script = Join-Path $root "mt5_us500_demo_bot.py"

$stateFile = Join-Path $root "mt5_us500_demo_candidate_state.json"
$tradesCsv = Join-Path $root "mt5_us500_demo_candidate_trades.csv"
$outLog = Join-Path $root "mt5_us500_demo_candidate_bot.out.log"
$errLog = Join-Path $root "mt5_us500_demo_candidate_bot.err.log"

$args = @(
    "-u",
    $script,
    "--execute",
    "--magic", "505018",
    "--comment", "codex-us500-demo-candidate",
    "--state-file", $stateFile,
    "--trades-csv", $tradesCsv,
    "--min-adx", "18",
    "--cooldown-bars", "5"
)

Start-Process `
    -FilePath $python `
    -ArgumentList $args `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

Write-Host "Started MT5 US500 candidate bot."
Write-Host "State: $stateFile"
Write-Host "Trades: $tradesCsv"
Write-Host "Out log: $outLog"
Write-Host "Err log: $errLog"
