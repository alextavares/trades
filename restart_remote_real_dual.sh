#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

pkill -f "paper_polymarket_5m_live.py.*--real" || true
sleep 1

rm -f real_edge3_bot.pid real_ema_1s_trend_bot.pid real_first_minute_bot.pid real_bot.pid
rm -f real_shared_position_lock.json.lock

for log in \
  real_ema_1s_trend_polymarket_5m_live.log \
  real_ema_1s_trend_polymarket_5m_live.err.log
do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$(date +%Y%m%d_%H%M%S)"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_polymarket_5m_live.py \
  --strategy ema-1s-trend \
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
  --trades-csv real_ema_1s_trend_polymarket_5m_trades.csv \
  > real_ema_1s_trend_polymarket_5m_live.log \
  2> real_ema_1s_trend_polymarket_5m_live.err.log \
  < /dev/null &

echo $! > real_ema_1s_trend_bot.pid

sleep 2

echo "EMA_1S_TREND_PID=$(cat real_ema_1s_trend_bot.pid)"
pgrep -af "[p]aper_polymarket_5m_live.py.*--real"
echo "---EMA 1S TREND LOG---"
tail -n 20 real_ema_1s_trend_polymarket_5m_live.log || true
