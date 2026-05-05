#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

start_bot() {
  local name="$1"
  local csv="$2"
  shift 2

  pkill -f "paper_polymarket_5m_live.py.*${csv}" || true
  rm -f "${name}.pid"

  local stamp
  stamp="$(date +%Y%m%d_%H%M%S)"
  for log in "${name}.log" "${name}.err.log"; do
    if [ -f "$log" ]; then
      mv "$log" "$log.bak.$stamp"
    fi
    : > "$log"
  done

  nohup venv/bin/python -u paper_polymarket_5m_live.py "$@" \
    --min-contract-price 0.40 \
    --max-contract-price 0.65 \
    --poll-seconds 5 \
    --trades-csv "$csv" \
    > "${name}.log" \
    2> "${name}.err.log" \
    < /dev/null &

  echo $! > "${name}.pid"
}

start_bot \
  "paper_edge3_40_65_polymarket_5m_live" \
  "paper_edge3_40_65_polymarket_5m_trades.csv" \
  --strategy edge \
  --entry-offsets 1 \
  --edge-min 0.03 \
  --min-abs-z 0.8

start_bot \
  "paper_polymarket_15m_edge_40_65_live" \
  "paper_polymarket_15m_edge_40_65_trades.csv" \
  --event-duration-minutes 15 \
  --event-slug-duration 15m \
  --strategy edge \
  --entry-offsets 6,7,8,9,10 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --lookback-minutes 60 \
  --momentum-minutes 5

sleep 2

echo "---40_65 PAPER PIDS---"
for name in \
  paper_edge3_40_65_polymarket_5m_live \
  paper_polymarket_15m_edge_40_65_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---40_65 PAPER PROCESSES---"
pgrep -af "[p]aper_polymarket_5m_live.py.*40_65.*trades.csv"

echo "---40_65 PAPER LOGS---"
for name in \
  paper_edge3_40_65_polymarket_5m_live \
  paper_polymarket_15m_edge_40_65_live; do
  echo "---${name}.log---"
  tail -n 8 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 8 "${name}.err.log" || true
done
