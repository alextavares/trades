#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

stamp="$(date -u +%Y%m%d_%H%M%S)"
round_start_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
round_csv="real_edge3_polymarket_5m_trades.csv"

pkill -f "paper_polymarket_5m_live.py.*--real" || true
sleep 1

rm -f real_edge3_bot.pid real_ema_1s_trend_bot.pid real_first_minute_bot.pid real_bot.pid
rm -f real_shared_position_lock.json.lock
echo "{}" > real_shared_position_lock.json

if [ -f "$round_csv" ] && [ -s "$round_csv" ]; then
  mv "$round_csv" "real_edge3_polymarket_5m_trades.before_round_${stamp}.csv"
fi

if [ ! -f real_edge3_rounds.csv ]; then
  echo "round_start_utc,strategy,csv_name,notes" > real_edge3_rounds.csv
fi
echo "${round_start_utc},edge3,${round_csv},restart from EMA 1s trend after negative round" >> real_edge3_rounds.csv

for log in real_edge3_polymarket_5m_live.log real_edge3_polymarket_5m_live.err.log; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_polymarket_5m_live.py \
  --strategy edge \
  --poll-seconds 2 \
  --real \
  --i-understand-real-money \
  --real-order-type FAK \
  --real-price-slippage 0.01 \
  --stake 5 \
  --max-real-trades 999999 \
  --max-open-positions 1 \
  --max-real-loss-usdc 0 \
  --real-shared-lock-file real_shared_position_lock.json \
  --real-shared-lock-scope market \
  --entry-offsets 1 \
  --min-contract-price 0.50 \
  --max-contract-price 0.85 \
  --edge-min 0.03 \
  --min-abs-z 0.8 \
  --trades-csv "$round_csv" \
  > real_edge3_polymarket_5m_live.log \
  2> real_edge3_polymarket_5m_live.err.log \
  < /dev/null &

echo $! > real_edge3_bot.pid

sleep 2

echo "ROUND_START_UTC=${round_start_utc}"
echo "REAL_EDGE3_PID=$(cat real_edge3_bot.pid)"
pgrep -af "[p]aper_polymarket_5m_live.py.*--real"
echo "---ROUNDS---"
tail -n 5 real_edge3_rounds.csv
echo "---REAL EDGE3 LOG---"
tail -n 20 real_edge3_polymarket_5m_live.log || true
echo "---REAL EDGE3 ERR---"
tail -n 20 real_edge3_polymarket_5m_live.err.log || true
