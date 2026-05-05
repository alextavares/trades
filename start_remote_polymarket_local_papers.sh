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

pkill -f "[p]aper_polymarket_5m_live.py.*paper_bollinger_rsi_reversal_trades.csv" || true
rm -f paper_bollinger_rsi_reversal_live.pid

start_bot \
  "paper_edge3_polymarket_5m_live" \
  "paper_edge3_polymarket_5m_trades.csv" \
  --entry-offsets 1 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --stake 10

start_bot \
  "paper_first_minute_continuation_live" \
  "paper_first_minute_continuation_trades.csv" \
  --strategy first-minute-continuation \
  --entry-offsets 2 \
  --min-anchor-body-pct 0.0003 \
  --anchor-volume-multiplier 0 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.06 \
  --stake 10

start_bot \
  "paper_first_minute_continuation_down_live" \
  "paper_first_minute_continuation_down_trades.csv" \
  --strategy first-minute-continuation \
  --allowed-directions DOWN \
  --entry-offsets 2 \
  --min-anchor-body-pct 0.0003 \
  --anchor-volume-multiplier 0 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.06 \
  --stake 10

start_bot \
  "paper_ema_1s_trend_live" \
  "paper_ema_1s_trend_trades.csv" \
  --strategy ema-1s-trend \
  --poll-seconds 2 \
  --entry-offsets 1 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --stake 10

sleep 2

echo "---LOCAL PAPER PIDS ON VPS---"
for name in \
  paper_edge3_polymarket_5m_live \
  paper_first_minute_continuation_live \
  paper_first_minute_continuation_down_live \
  paper_ema_1s_trend_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---LOCAL PAPER PROCESSES ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*(paper_edge3_polymarket_5m_trades.csv|paper_first_minute_continuation_trades.csv|paper_first_minute_continuation_down_trades.csv|paper_ema_1s_trend_trades.csv)" || true

echo "---LOCAL PAPER LOGS ON VPS---"
for name in \
  paper_edge3_polymarket_5m_live \
  paper_first_minute_continuation_live \
  paper_first_minute_continuation_down_live \
  paper_ema_1s_trend_live; do
  echo "---${name}.log---"
  tail -n 8 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 8 "${name}.err.log" || true
done
