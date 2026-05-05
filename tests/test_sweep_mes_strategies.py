import pandas as pd

from sweep_mes_strategies import (
    StrategyParams,
    SweepCosts,
    backtest_open_reversal,
    backtest_orb,
    backtest_vwap_pullback,
    prepare_rth,
    run_sweep,
)


def make_rth_rows(prices: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2026-01-05 14:30", periods=len(prices), freq="5min", tz="UTC")
    rows = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        rows.append(
            {
                "ts": ts[index],
                "open": open_price,
                "high": max(open_price, close) + 0.25,
                "low": min(open_price, close) - 0.25,
                "close": close,
                "volume": 100 + index,
            }
        )
    return pd.DataFrame(rows)


def test_backtest_orb_opens_long_after_opening_range_breakout():
    df = prepare_rth(make_rth_rows([5000, 5000.5, 5001, 5001.5, 5005, 5008, 5010, 5012]))
    params = StrategyParams(strategy="orb", window_minutes=15, stop_points=4, take_profit_points=6, max_hold_bars=4)

    trades = backtest_orb(df, params, SweepCosts(commission_per_side_usd=0, slippage_ticks=0))

    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["exit_reason"] == "TAKE_PROFIT"


def test_backtest_open_reversal_fades_large_opening_push():
    df = prepare_rth(make_rth_rows([5000, 5005, 5010, 5012, 5004, 4998, 4994, 4990]))
    params = StrategyParams(
        strategy="open_reversal",
        window_minutes=15,
        min_open_move_points=8,
        stop_points=6,
        take_profit_points=8,
        max_hold_bars=4,
    )

    trades = backtest_open_reversal(df, params, SweepCosts(commission_per_side_usd=0, slippage_ticks=0))

    assert len(trades) == 1
    assert trades[0]["direction"] == "SHORT"


def test_backtest_vwap_pullback_opens_with_trend_and_pullback():
    df = prepare_rth(make_rth_rows([5000, 5002, 5004, 5003, 5007, 5010, 5012, 5014]))
    params = StrategyParams(
        strategy="vwap_pullback",
        vwap_proximity_points=4,
        min_vwap_distance_points=0.5,
        stop_points=4,
        take_profit_points=6,
        max_hold_bars=4,
    )

    trades = backtest_vwap_pullback(df, params, SweepCosts(commission_per_side_usd=0, slippage_ticks=0))

    assert len(trades) >= 1
    assert trades[0]["direction"] == "LONG"


def test_run_sweep_returns_ranked_summary_and_trades():
    prices = [5000 + index * 0.75 for index in range(80)]
    df = make_rth_rows(prices)

    summary, trades = run_sweep(df, SweepCosts(commission_per_side_usd=0, slippage_ticks=0), min_trades=1)

    assert not trades.empty
    assert not summary.empty
    assert summary.iloc[0]["net_pnl"] >= summary.iloc[-1]["net_pnl"]
