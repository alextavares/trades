#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

pkill -f "paper_mes_orb_live.py" || true
rm -f paper_mes_orb_30m_remote.pid

stamp="$(date +%Y%m%d_%H%M%S)"
for log in paper_mes_orb_30m_live.log paper_mes_orb_30m_live.err.log; do
  if [ -f "$log" ]; then
    mv "$log" "$log.bak.$stamp"
  fi
  : > "$log"
done

nohup venv/bin/python -u paper_mes_orb_live.py \
  --data-source yahoo \
  --poll-seconds 60 \
  --max-stale-minutes 30 \
  --window-minutes 30 \
  --stop-points 8 \
  --take-profit-points 8 \
  --max-hold-bars 24 \
  --state-file paper_mes_orb_30m_state.json \
  --trades-csv paper_mes_orb_30m_trades.csv \
  > paper_mes_orb_30m_live.log \
  2> paper_mes_orb_30m_live.err.log \
  < /dev/null &

echo $! > paper_mes_orb_30m_remote.pid

sleep 2

echo "MES_ORB_PAPER_PID=$(cat paper_mes_orb_30m_remote.pid)"
pgrep -af "[p]aper_mes_orb_live.py"
echo "---MES ORB PAPER LOG---"
tail -n 20 paper_mes_orb_30m_live.log || true
echo "---MES ORB PAPER ERR---"
tail -n 20 paper_mes_orb_30m_live.err.log || true
