#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

pkill -f "paper_polymarket_5m_live.py.*paper_polymarket_15m_edge_trades.csv" || true
rm -f paper_polymarket_15m_edge.pid

stamp="$(date +%Y%m%d_%H%M%S)"
for log in paper_polymarket_15m_edge_live.log paper_polymarket_15m_edge_live.err.log; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_polymarket_5m_live.py \
  --event-duration-minutes 15 \
  --event-slug-duration 15m \
  --strategy edge \
  --entry-offsets 6,7,8,9,10 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --lookback-minutes 60 \
  --momentum-minutes 5 \
  --poll-seconds 10 \
  --trades-csv paper_polymarket_15m_edge_trades.csv \
  > paper_polymarket_15m_edge_live.log \
  2> paper_polymarket_15m_edge_live.err.log \
  < /dev/null &

echo $! > paper_polymarket_15m_edge.pid

sleep 2

echo "POLYMARKET_15M_EDGE_PID=$(cat paper_polymarket_15m_edge.pid)"
pgrep -af "[p]aper_polymarket_5m_live.py.*paper_polymarket_15m_edge_trades.csv"
echo "---15M PAPER LOG---"
tail -n 20 paper_polymarket_15m_edge_live.log || true
echo "---15M PAPER ERR---"
tail -n 20 paper_polymarket_15m_edge_live.err.log || true
