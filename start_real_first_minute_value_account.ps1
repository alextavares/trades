$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env.real_first_minute_value"
$Python = "python"
$Script = Join-Path $Root "paper_polymarket_5m_live.py"
$Log = Join-Path $Root "real_first_minute_value_polymarket_5m_live.log"
$ErrLog = Join-Path $Root "real_first_minute_value_polymarket_5m_live.err.log"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Crie $EnvFile a partir de .env.real_first_minute_value.example antes de iniciar."
}

$Args = @(
    "-u", $Script,
    "--env-file", $EnvFile,
    "--real",
    "--i-understand-real-money",
    "--strategy", "first-minute-continuation",
    "--entry-offsets", "2",
    "--edge-min", "0.03",
    "--min-abs-z", "0.0",
    "--min-anchor-body-pct", "0.0003",
    "--min-contract-price", "0.45",
    "--max-contract-price", "0.70",
    "--poll-seconds", "5",
    "--stake", "10",
    "--real-signature-type", "3",
    "--max-real-trades", "1",
    "--max-open-positions", "1",
    "--max-real-loss-usdc", "10",
    "--trades-csv", "real_first_minute_value_polymarket_5m_trades.csv",
    "--real-shared-lock-file", "real_first_minute_value_lock.json",
    "--real-shared-lock-scope", "market"
)

Start-Process -FilePath $Python -ArgumentList $Args -WorkingDirectory $Root -RedirectStandardOutput $Log -RedirectStandardError $ErrLog -WindowStyle Hidden
