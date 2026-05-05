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
    --min-contract-price 0.45 \
    --max-contract-price 0.70 \
    --poll-seconds 5 \
    --trades-csv "$csv" \
    > "${name}.log" \
    2> "${name}.err.log" \
    < /dev/null &

  echo $! > "${name}.pid"
}

pkill -f "[p]aper_polymarket_5m_live.py.*paper_momentum_confirmed_value_trades.csv" || true
rm -f paper_momentum_confirmed_value_live.pid

start_bot \
  "paper_edge3_value_polymarket_5m_live" \
  "paper_edge3_value_polymarket_5m_trades.csv" \
  --strategy edge \
  --entry-offsets 1 \
  --edge-min 0.03 \
  --min-abs-z 0.8

start_bot \
  "paper_first_minute_value_live" \
  "paper_first_minute_value_trades.csv" \
  --strategy first-minute-continuation \
  --entry-offsets 2 \
  --edge-min 0.03 \
  --min-abs-z 0.0 \
  --min-anchor-body-pct 0.0003

start_bot \
  "paper_ema_1s_value_live" \
  "paper_ema_1s_value_trades.csv" \
  --strategy ema-1s-trend \
  --entry-offsets 1 \
  --edge-min 0.03 \
  --min-abs-z 0.0 \
  --ema-fast-seconds 60 \
  --ema-mid-seconds 300 \
  --ema-slow-seconds 600 \
  --ema-slope-lookback-seconds 15 \
  --min-ema-gap-usd 2.0 \
  --min-ema-slope-usd 0.2 \
  --max-price-ema-fast-distance-usd 80.0

sleep 2

echo "---VALUE PAPER PIDS---"
for name in \
  paper_edge3_value_polymarket_5m_live \
  paper_first_minute_value_live \
  paper_ema_1s_value_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---VALUE PAPER PROCESSES---"
pgrep -af "[p]aper_polymarket_5m_live.py.*paper_.*_value.*trades.csv"

echo "---VALUE PAPER LOGS---"
for name in \
  paper_edge3_value_polymarket_5m_live \
  paper_first_minute_value_live \
  paper_ema_1s_value_live; do
  echo "---${name}.log---"
  tail -n 8 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 8 "${name}.err.log" || true
done
