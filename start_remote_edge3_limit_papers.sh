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
    --trades-csv "$csv" \
    > "${name}.log" \
    2> "${name}.err.log" \
    < /dev/null &

  echo $! > "${name}.pid"
}

base_args=(
  --strategy edge
  --poll-seconds 2
  --entry-offsets 1
  --min-contract-price 0.50
  --max-contract-price 0.85
  --edge-min 0.03
  --min-abs-z 0.8
  --stake 5
)

start_bot \
  "paper_edge3_limit55_polymarket_5m_live" \
  "paper_edge3_limit55_polymarket_5m_trades.csv" \
  "${base_args[@]}" \
  --paper-limit-entry-price 0.55

start_bot \
  "paper_edge3_limit60_polymarket_5m_live" \
  "paper_edge3_limit60_polymarket_5m_trades.csv" \
  "${base_args[@]}" \
  --paper-limit-entry-price 0.60

sleep 2

echo "---EDGE3 LIMIT PAPER PIDS ON VPS---"
for name in \
  paper_edge3_limit55_polymarket_5m_live \
  paper_edge3_limit60_polymarket_5m_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---EDGE3 LIMIT PAPER PROCESSES ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*paper_edge3_limit(55|60)_polymarket_5m_trades.csv" || true

echo "---EDGE3 LIMIT PAPER LOGS ON VPS---"
for name in \
  paper_edge3_limit55_polymarket_5m_live \
  paper_edge3_limit60_polymarket_5m_live; do
  echo "---${name}.log---"
  tail -n 10 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 10 "${name}.err.log" || true
done
