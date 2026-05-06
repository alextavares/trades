#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

name="real_poly_odds_momentum_60s_live"
csv="real_poly_odds_momentum_60s_trades.csv"
env_file=".env.real_poly_odds_momentum_60s"

if [ ! -f "$env_file" ]; then
  echo "Env file not found: $env_file" >&2
  exit 1
fi

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
  --env-file "$env_file" \
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
  --real-signature-type 3 \
  --stake 5 \
  --max-real-trades 999999 \
  --max-open-positions 1 \
  --max-real-loss-usdc 20 \
  --real-shared-lock-file real_poly_odds_momentum_60s_lock.json \
  --real-shared-lock-scope market \
  --trades-csv "$csv" \
  > "${name}.log" \
  2> "${name}.err.log" \
  < /dev/null &

echo $! > "${name}.pid"

sleep 2

echo "---REAL POLY ODDS MOMENTUM 60S PID ON VPS---"
cat "${name}.pid"
echo "---REAL POLY ODDS MOMENTUM 60S PROCESS ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*${csv}" || true
echo "---REAL POLY ODDS MOMENTUM 60S LOG---"
tail -n 20 "${name}.log" || true
echo "---REAL POLY ODDS MOMENTUM 60S ERR---"
tail -n 20 "${name}.err.log" || true
