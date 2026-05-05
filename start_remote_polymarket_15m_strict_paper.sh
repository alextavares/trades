#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

pkill -f "paper_polymarket_5m_live.py.*paper_polymarket_15m_strict_trades.csv" || true
rm -f paper_polymarket_15m_strict.pid

stamp="$(date +%Y%m%d_%H%M%S)"
for log in paper_polymarket_15m_strict_live.log paper_polymarket_15m_strict_live.err.log; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_polymarket_5m_live.py \
  --event-duration-minutes 15 \
  --event-slug-duration 15m \
  --strategy edge \
  --entry-offsets 4,5,6,7 \
  --edge-min 0.06 \
  --min-abs-z 0.5 \
  --min-contract-price 0.45 \
  --max-contract-price 0.70 \
  --lookback-minutes 60 \
  --momentum-minutes 5 \
  --recent-move-filter-seconds 60 \
  --max-recent-move-pct 0.003 \
  --poll-seconds 5 \
  --trades-csv paper_polymarket_15m_strict_trades.csv \
  > paper_polymarket_15m_strict_live.log \
  2> paper_polymarket_15m_strict_live.err.log \
  < /dev/null &

echo $! > paper_polymarket_15m_strict.pid

sleep 2

echo "POLYMARKET_15M_STRICT_PID=$(cat paper_polymarket_15m_strict.pid)"
pgrep -af "[p]aper_polymarket_5m_live.py.*paper_polymarket_15m_strict_trades.csv"
echo "---15M STRICT PAPER LOG---"
tail -n 20 paper_polymarket_15m_strict_live.log || true
echo "---15M STRICT PAPER ERR---"
tail -n 20 paper_polymarket_15m_strict_live.err.log || true
