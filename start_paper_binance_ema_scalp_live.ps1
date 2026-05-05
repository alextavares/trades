$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Python312\python.exe"
$script = Join-Path $root "paper_binance_ema_scalp_live.py"
$stdout = Join-Path $root "paper_binance_ema_scalp_live.log"
$stderr = Join-Path $root "paper_binance_ema_scalp_live.err.log"

$args = @(
    "-u"
    $script
    "--trades-csv", "paper_binance_ema_scalp_live_trades.csv"
    "--state-json", "paper_binance_ema_scalp_state.json"
)

Start-Process -FilePath $python `
    -ArgumentList $args `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden
