#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import product

import pandas as pd

from backtest_mt5_us500 import (
    Us500Config,
    load_mt5_csv,
    resample_mt5_rates,
    run_backtest,
    summarize,
)


@dataclass(frozen=True)
class SweepParams:
    timeframe: str = "5min"
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 60
    slope_lookback: int = 5
    min_ema_gap_points: float = 0.5
    min_slope_points: float = 0.25
    max_price_ema_fast_distance_points: float = 4.0
    entry_mode: str = "trend"
    trend_filter: str = "adx"
    adx_period: int = 14
    min_adx: float = 18.0
    stop_points: float = 6.0
    take_profit_points: float = 8.0
    max_hold_bars: int = 30
    cooldown_bars: int = 5
    session_start_hour_brt: int = 10
    session_end_hour_brt: int = 18


def compact_params(params: SweepParams) -> str:
    return (
        f"tf={params.timeframe};mode={params.entry_mode};filter={params.trend_filter};"
        f"ema={params.ema_fast}/{params.ema_mid}/{params.ema_slow};slope={params.slope_lookback};"
        f"gap={params.min_ema_gap_points};minslope={params.min_slope_points};"
        f"dist={params.max_price_ema_fast_distance_points};adx={params.min_adx};"
        f"stop={params.stop_points};tp={params.take_profit_points};hold={params.max_hold_bars};"
        f"cooldown={params.cooldown_bars};session={params.session_start_hour_brt}-{params.session_end_hour_brt}"
    )


def config_from_params(base_csv: str, lot_size: float, params: SweepParams) -> Us500Config:
    return Us500Config(
        csv=base_csv,
        timeframe=params.timeframe,
        lot_size=lot_size,
        ema_fast=params.ema_fast,
        ema_mid=params.ema_mid,
        ema_slow=params.ema_slow,
        slope_lookback=params.slope_lookback,
        min_ema_gap_points=params.min_ema_gap_points,
        min_slope_points=params.min_slope_points,
        max_price_ema_fast_distance_points=params.max_price_ema_fast_distance_points,
        entry_mode=params.entry_mode,
        trend_filter=params.trend_filter,
        adx_period=params.adx_period,
        min_adx=params.min_adx,
        stop_points=params.stop_points,
        take_profit_points=params.take_profit_points,
        max_hold_bars=params.max_hold_bars,
        cooldown_bars=params.cooldown_bars,
        session_start_hour_brt=params.session_start_hour_brt,
        session_end_hour_brt=params.session_end_hour_brt,
    )


def strategy_grid() -> list[SweepParams]:
    grid: list[SweepParams] = []
    for timeframe, entry_mode, trend_filter, stop_points, take_profit_points, max_hold_bars, min_adx, cooldown_bars in product(
        ("5min", "15min"),
        ("trend", "pullback"),
        ("none", "adx"),
        (4.0, 6.0),
        (6.0, 8.0),
        (12, 24),
        (18.0,),
        (3, 5),
    ):
        grid.append(
            SweepParams(
                timeframe=timeframe,
                entry_mode=entry_mode,
                trend_filter=trend_filter,
                stop_points=stop_points,
                take_profit_points=take_profit_points,
                max_hold_bars=max_hold_bars,
                min_adx=min_adx,
                cooldown_bars=cooldown_bars,
            )
        )
    return grid


def run_sweep(
    raw_df: pd.DataFrame,
    grid: list[SweepParams],
    lot_size: float,
    min_trades: int = 20,
    csv_name: str = "us500_mt5_m1.csv",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_timeframe: dict[str, pd.DataFrame] = {}
    summary_rows: list[dict] = []
    trades_frames: list[pd.DataFrame] = []

    for params in grid:
        if params.timeframe not in by_timeframe:
            by_timeframe[params.timeframe] = resample_mt5_rates(raw_df, params.timeframe)
        frame = by_timeframe[params.timeframe]
        config = config_from_params(csv_name, lot_size, params)
        trades = run_backtest(frame, config)
        stats = summarize(trades)
        if stats["trades"] < min_trades:
            continue
        label = compact_params(params)
        summary_rows.append(
            {
                "params": label,
                "timeframe": params.timeframe,
                "entry_mode": params.entry_mode,
                "trend_filter": params.trend_filter,
                **stats,
            }
        )
        if not trades.empty:
            tagged = trades.copy()
            tagged.insert(0, "params", label)
            trades_frames.append(tagged)

    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary = summary.sort_values(["net_pnl", "win_rate", "trades"], ascending=[False, False, False]).reset_index(drop=True)
    trades_out = pd.concat(trades_frames, ignore_index=True) if trades_frames else pd.DataFrame()
    return summary, trades_out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep de parametros para US500 no MT5 CSV.")
    parser.add_argument("--csv", default="us500_mt5_m1.csv")
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--summary-csv", default="us500_mt5_sweep_summary.csv")
    parser.add_argument("--trades-csv", default="us500_mt5_sweep_trades.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = load_mt5_csv(args.csv)
    grid = strategy_grid()
    summary, trades = run_sweep(raw, grid, lot_size=args.lot_size, min_trades=args.min_trades, csv_name=args.csv)
    print(f"grid_size: {len(grid)}")
    print(f"qualified_setups: {len(summary)}")
    if summary.empty:
        print("No setups matched the minimum trade count.")
        return 0
    summary.to_csv(args.summary_csv, index=False)
    trades.to_csv(args.trades_csv, index=False)
    print("=== TOP 10 ===")
    print(summary.head(10).to_string(index=False))
    print(f"Saved summary to {args.summary_csv}")
    print(f"Saved trades to {args.trades_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
