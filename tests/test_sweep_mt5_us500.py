import pandas as pd

from sweep_mt5_us500 import (
    SweepParams,
    compact_params,
    run_sweep,
)


def make_rows(prices: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01 14:30", periods=len(prices), freq="5min", tz="UTC")
    rows = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        rows.append(
            {
                "ts": ts[index],
                "open": open_price,
                "high": max(open_price, close) + 0.2,
                "low": min(open_price, close) - 0.2,
                "close": close,
                "volume": 100 + index,
                "spread_points": 43,
            }
        )
    return pd.DataFrame(rows)


def test_compact_params_includes_core_fields():
    params = SweepParams(timeframe="5min", entry_mode="trend", trend_filter="adx", stop_points=6.0, take_profit_points=8.0)

    compact = compact_params(params)

    assert "tf=5min" in compact
    assert "mode=trend" in compact
    assert "stop=6.0" in compact


def test_run_sweep_returns_ranked_summary_and_trades():
    prices = [7000 + index * 0.4 for index in range(240)]
    df = make_rows(prices)
    grid = [
        SweepParams(
            timeframe="5min",
            ema_fast=5,
            ema_mid=10,
            ema_slow=20,
            slope_lookback=3,
            min_ema_gap_points=0.05,
            min_slope_points=0.05,
            max_price_ema_fast_distance_points=5.0,
            trend_filter="none",
            entry_mode="trend",
            stop_points=1.0,
            take_profit_points=1.0,
            max_hold_bars=12,
            cooldown_bars=0,
        ),
        SweepParams(
            timeframe="5min",
            ema_fast=5,
            ema_mid=10,
            ema_slow=20,
            slope_lookback=3,
            min_ema_gap_points=0.05,
            min_slope_points=0.05,
            max_price_ema_fast_distance_points=5.0,
            trend_filter="none",
            entry_mode="pullback",
            stop_points=1.0,
            take_profit_points=1.0,
            max_hold_bars=12,
            cooldown_bars=0,
        ),
    ]

    summary, trades = run_sweep(df, grid, lot_size=0.01, min_trades=1)

    assert not summary.empty
    assert not trades.empty
    assert summary.iloc[0]["net_pnl"] >= summary.iloc[-1]["net_pnl"]
