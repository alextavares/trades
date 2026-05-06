#!/usr/bin/env bash
set -euo pipefail

cd /root/fintechtrading_real

csvs=(
  paper_edge3_max80_polymarket_5m_trades.csv
  paper_edge3_no10h_polymarket_5m_trades.csv
  paper_edge3_max80_no10h_polymarket_5m_trades.csv
  paper_edge3_down_only_polymarket_5m_trades.csv
  paper_edge3_limit60_polymarket_5m_trades.csv
  paper_ema_1s_trend_trades.csv
  paper_poly_odds_momentum_60s_trades.csv
  paper_poly_odds_momentum_90s_trades.csv
  paper_poly_75_breakout_trades.csv
)

echo "---STOPPING WEAK/SUPERSEDED PAPER STRATEGIES---"
for csv in "${csvs[@]}"; do
  echo "stopping ${csv}"
  pkill -f "paper_polymarket_5m_live.py.*${csv}" || true
done

echo "---REMAINING PAPER/REAL PROCESSES---"
pgrep -af "[p]aper_polymarket_5m_live.py" || true
