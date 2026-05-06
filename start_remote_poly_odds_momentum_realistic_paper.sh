#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

name="paper_poly_odds_momentum_60s_realistic_live"
csv="paper_poly_odds_momentum_60s_realistic_trades.csv"

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
  --strategy poly-odds-momentum \
  --poll-seconds 2 \
  --entry-offsets 2 \
  --min-contract-price 0.55 \
  --max-contract-price 0.75 \
  --odds-momentum-min-move 0.08 \
  --odds-momentum-opposite-move 0.05 \
  --stake 5 \
  --odds-momentum-observation-seconds 60 \
  --paper-realistic-entry \
  --paper-realistic-price-slippage 0.01 \
  --paper-realistic-max-entry-price 0.67 \
  --trades-csv "$csv" \
  > "${name}.log" \
  2> "${name}.err.log" \
  < /dev/null &

echo $! > "${name}.pid"

sleep 2

echo "---POLY ODDS MOMENTUM REALISTIC PAPER PID ON VPS---"
echo "${name}=$(cat "${name}.pid")"

echo "---POLY ODDS MOMENTUM REALISTIC PAPER PROCESS ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*${csv}" || true

echo "---${name}.log---"
tail -n 20 "${name}.log" || true

echo "---${name}.err.log---"
tail -n 20 "${name}.err.log" || true
