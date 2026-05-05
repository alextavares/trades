$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Python312\python.exe"
$script = Join-Path $root "paper_binance_spot_momentum_scanner.py"
$stdout = Join-Path $root "paper_binance_spot_momentum_scanner_live.log"
$stderr = Join-Path $root "paper_binance_spot_momentum_scanner_live.err.log"

$args = @(
    "-u"
    $script
    "--trades-csv", "paper_binance_spot_momentum_scanner_trades.csv"
    "--cooldowns-json", "paper_binance_spot_momentum_scanner_cooldowns.json"
    "--state-json", "paper_binance_spot_momentum_scanner_state.json"
)

Start-Process -FilePath $python `
    -ArgumentList $args `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden
