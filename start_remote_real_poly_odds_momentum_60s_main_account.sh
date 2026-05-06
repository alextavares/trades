#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

name="real_poly_odds_momentum_60s_main_live"
csv="real_poly_odds_momentum_60s_trades.csv"
env_file=".env"

if [ ! -f "$env_file" ]; then
  echo "Env file not found: $env_file" >&2
  exit 1
fi

venv/bin/python - <<'PY'
import os
from decimal import Decimal

from dotenv import load_dotenv
from py_clob_client_v2 import ApiCreds, ClobClient
from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

load_dotenv(".env", override=True)
creds = ApiCreds(os.environ["API_KEY"], os.environ["API_SECRET"], os.environ["API_PASSPHRASE"])
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=os.environ["PK"],
    creds=creds,
    signature_type=1,
    funder=os.getenv("FUNDER"),
)
client.get_api_keys()
balance = client.get_balance_allowance(
    BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=1)
)
raw_balance = Decimal(str(balance.get("balance", "0")))
if raw_balance <= 0:
    raise SystemExit("CLOB preflight failed: usable collateral balance is zero for main env/funder")
print("CLOB preflight OK")
PY

pkill -f "paper_polymarket_5m_live.py.*${csv}" || true
rm -f "${name}.pid"

stamp="$(date +%Y%m%d_%H%M%S)"
for log in "${name}.log" "${name}.err.log"; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_polymarket_5m_live.py \
  --real \
  --i-understand-real-money \
  --strategy poly-odds-momentum \
  --poll-seconds 2 \
  --entry-offsets 2 \
  --min-contract-price 0.55 \
  --max-contract-price 0.75 \
  --odds-momentum-observation-seconds 60 \
  --odds-momentum-min-move 0.08 \
  --odds-momentum-opposite-move 0.05 \
  --real-order-type FAK \
  --real-price-slippage 0.01 \
  --real-signature-type 1 \
  --stake 4 \
  --max-real-trades 999999 \
  --max-open-positions 1 \
  --max-real-loss-usdc 16 \
  --real-shared-lock-file real_shared_position_lock.json \
  --real-shared-lock-scope market \
  --trades-csv "$csv" \
  > "${name}.log" \
  2> "${name}.err.log" \
  < /dev/null &

echo $! > "${name}.pid"

sleep 2

echo "---REAL POLY ODDS MOMENTUM 60S MAIN PID ON VPS---"
cat "${name}.pid"
echo "---REAL POLY ODDS MOMENTUM 60S MAIN PROCESS ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*${csv}" || true
echo "---REAL POLY ODDS MOMENTUM 60S MAIN LOG---"
tail -n 20 "${name}.log" || true
echo "---REAL POLY ODDS MOMENTUM 60S MAIN ERR---"
tail -n 20 "${name}.err.log" || true
