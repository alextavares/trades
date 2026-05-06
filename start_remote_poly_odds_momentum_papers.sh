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
  --strategy poly-odds-momentum
  --poll-seconds 2
  --entry-offsets 2
  --min-contract-price 0.55
  --max-contract-price 0.75
  --odds-momentum-min-move 0.08
  --odds-momentum-opposite-move 0.05
  --stake 5
)

start_bot \
  "paper_poly_odds_momentum_60s_live" \
  "paper_poly_odds_momentum_60s_trades.csv" \
  "${base_args[@]}" \
  --odds-momentum-observation-seconds 60

start_bot \
  "paper_poly_odds_momentum_90s_live" \
  "paper_poly_odds_momentum_90s_trades.csv" \
  "${base_args[@]}" \
  --odds-momentum-observation-seconds 90

sleep 2

echo "---POLY ODDS MOMENTUM PAPER PIDS ON VPS---"
for name in \
  paper_poly_odds_momentum_60s_live \
  paper_poly_odds_momentum_90s_live; do
  echo "${name}=$(cat "${name}.pid")"
done

echo "---POLY ODDS MOMENTUM PAPER PROCESSES ON VPS---"
pgrep -af "[p]aper_polymarket_5m_live.py.*paper_poly_odds_momentum_(60s|90s)_trades.csv" || true

echo "---POLY ODDS MOMENTUM PAPER LOGS ON VPS---"
for name in \
  paper_poly_odds_momentum_60s_live \
  paper_poly_odds_momentum_90s_live; do
  echo "---${name}.log---"
  tail -n 10 "${name}.log" || true
  echo "---${name}.err.log---"
  tail -n 10 "${name}.err.log" || true
done
