#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

name="paper_poly_75_breakout_live"
csv="paper_poly_75_breakout_trades.csv"

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
  --strategy poly-75-breakout \
  --poll-seconds 2 \
  --min-contract-price 0.75 \
  --max-contract-price 0.99 \
  --poly-breakout-trigger-price 0.75 \
  --poly-breakout-min-seconds-remaining 1 \
  --stake 5 \
  --trades-csv "$csv" \
  > "${name}.log" \
  2> "${name}.err.log" \
  < /dev/null &

echo $! > "${name}.pid"

sleep 2

echo "---POLY 75 BREAKOUT PAPER PID ON VPS---"
cat "${name}.pid"
echo "---POLY 75 BREAKOUT PAPER PROCESS ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*${csv}" || true
echo "---POLY 75 BREAKOUT PAPER LOG---"
tail -n 15 "${name}.log" || true
echo "---POLY 75 BREAKOUT PAPER ERR---"
tail -n 15 "${name}.err.log" || true
