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

start_bot \
  "paper_first_minute_down_00_11_live" \
  "paper_first_minute_down_00_11_trades.csv" \
  --strategy first-minute-continuation \
  --allowed-directions DOWN \
  --entry-offsets 2 \
  --min-anchor-body-pct 0.0003 \
  --anchor-volume-multiplier 0 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.06 \
  --stake 10 \
  --poll-seconds 5 \
  --exclude-entry-hours-brt 12,13,14,15,16,17,18,19,20,21,22,23

start_bot \
  "paper_first_minute_value_strong_hours_live" \
  "paper_first_minute_value_strong_hours_trades.csv" \
  --strategy first-minute-continuation \
  --entry-offsets 2 \
  --edge-min 0.03 \
  --min-abs-z 0.0 \
  --min-anchor-body-pct 0.0003 \
  --min-contract-price 0.45 \
  --max-contract-price 0.70 \
  --stake 10 \
  --poll-seconds 5 \
  --exclude-entry-hours-brt 0,1,6,9,10,11,14,15,16,17,18,19,23

start_bot \
  "paper_edge3_no10_11_polymarket_5m_live" \
  "paper_edge3_no10_11_polymarket_5m_trades.csv" \
  --strategy edge \
  --poll-seconds 2 \
  --entry-offsets 1 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --stake 5 \
  --exclude-entry-hours-brt 10,11

sleep 2

echo "---20 DAY CANDIDATE PAPER PIDS ON VPS---"
for name in \
  paper_first_minute_down_00_11_live \
  paper_first_minute_value_strong_hours_live \
  paper_edge3_no10_11_polymarket_5m_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---20 DAY CANDIDATE PAPER PROCESSES ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*(paper_first_minute_down_00_11_trades.csv|paper_first_minute_value_strong_hours_trades.csv|paper_edge3_no10_11_polymarket_5m_trades.csv)" || true

echo "---20 DAY CANDIDATE PAPER LOGS ON VPS---"
for name in \
  paper_first_minute_down_00_11_live \
  paper_first_minute_value_strong_hours_live \
  paper_edge3_no10_11_polymarket_5m_live; do
  echo "---${name}.log---"
  tail -n 10 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 10 "${name}.err.log" || true
done
