$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env.real_ema_1s_trend"
$Python = "python"
$Script = Join-Path $Root "paper_polymarket_5m_live.py"
$Log = Join-Path $Root "real_ema_1s_trend_polymarket_5m_live.log"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Crie $EnvFile a partir de .env.real_ema_1s_trend.example antes de iniciar."
}

$Args = @(
    "-u", $Script,
    "--env-file", $EnvFile,
    "--real",
    "--i-understand-real-money",
    "--strategy", "ema-1s-trend",
    "--poll-seconds", "2",
    "--entry-offsets", "1",
    "--min-contract-price", "0.50",
    "--max-contract-price", "0.85",
    "--edge-min", "0.03",
    "--min-abs-z", "0.8",
    "--stake", "5",
    "--max-real-trades", "1",
    "--max-open-positions", "1",
    "--max-real-loss-usdc", "10",
    "--trades-csv", "real_ema_1s_trend_polymarket_5m_trades.csv",
    "--real-shared-lock-file", "real_ema_1s_trend_lock.json",
    "--real-shared-lock-scope", "market"
)

Start-Process -FilePath $Python -ArgumentList $Args -WorkingDirectory $Root -RedirectStandardOutput $Log -RedirectStandardError $Log -WindowStyle Hidden
