#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

pkill -f "paper_mes_ema_scalp_live.py" || true
rm -f paper_mes_ema_scalp_remote.pid

stamp="$(date +%Y%m%d_%H%M%S)"
for log in paper_mes_ema_scalp_live.log paper_mes_ema_scalp_live.err.log; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_mes_ema_scalp_live.py \
  --data-source yahoo \
  --poll-seconds 60 \
  --state-file paper_mes_ema_scalp_state.json \
  --trades-csv paper_mes_ema_scalp_trades.csv \
  > paper_mes_ema_scalp_live.log \
  2> paper_mes_ema_scalp_live.err.log \
  < /dev/null &

echo $! > paper_mes_ema_scalp_remote.pid

sleep 2

echo "MES_PAPER_PID=$(cat paper_mes_ema_scalp_remote.pid)"
pgrep -af "[p]aper_mes_ema_scalp_live.py"
echo "---MES PAPER LOG---"
tail -n 20 paper_mes_ema_scalp_live.log || true
echo "---MES PAPER ERR---"
tail -n 20 paper_mes_ema_scalp_live.err.log || true
