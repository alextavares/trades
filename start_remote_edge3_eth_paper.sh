#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

name="paper_edge3_eth_polymarket_5m_live"
csv="paper_edge3_eth_polymarket_5m_trades.csv"

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
  --symbol ETHUSDT \
  --event-slug-asset eth \
  --strategy edge \
  --poll-seconds 2 \
  --entry-offsets 1 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --stake 5 \
  --trades-csv "$csv" \
  > "${name}.log" \
  2> "${name}.err.log" \
  < /dev/null &

echo $! > "${name}.pid"

sleep 2
echo "---ETH EDGE3 PAPER PID ON VPS---"
cat "${name}.pid"
echo "---ETH EDGE3 PAPER PROCESS ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*${csv}" || true
echo "---ETH EDGE3 PAPER LOG---"
tail -n 12 "${name}.log" || true
echo "---ETH EDGE3 PAPER ERR---"
tail -n 12 "${name}.err.log" || true
