import pandas as pd

from backtest_ema_slope_grid import (
    BacktestConfig,
    add_indicators,
    pip_size_for_symbol,
    run_backtest,
)


def make_rows(prices: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=len(prices), freq="min", tz="UTC")
    rows = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        rows.append(
            {
                "ts": ts[index],
                "open": open_price,
                "high": max(open_price, close) + 0.00005,
                "low": min(open_price, close) - 0.00005,
                "close": close,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


def test_pip_size_for_common_forex_symbols():
    assert pip_size_for_symbol("EURUSD=X") == 0.0001
    assert pip_size_for_symbol("GBPUSD") == 0.0001
    assert pip_size_for_symbol("USDJPY=X") == 0.01


def test_add_indicators_creates_normalized_slope():
    df = make_rows([1.1000, 1.1002, 1.1004, 1.1006, 1.1008, 1.1010])
    out = add_indicators(df, ema_period=3, slope_lookback=2, atr_period=3)

    assert "ema" in out.columns
    assert "slope_atr" in out.columns
    assert out["slope_atr"].iloc[-1] > 0


def test_run_backtest_adds_grid_order_against_buy_move_and_closes_tp():
    prices = [
        1.1000,
        1.1002,
        1.1004,
        1.1006,
        1.1008,
        1.1010,
        1.0994,
        1.1006,
        1.1012,
    ]
    df = add_indicators(make_rows(prices), ema_period=3, slope_lookback=2, atr_period=3)
    config = BacktestConfig(
        symbol="EURUSD=X",
        ema_period=3,
        slope_lookback=2,
        atr_period=3,
        min_slope_atr=0.05,
        grid_spacing_pips=10,
        take_profit_pips=8,
        max_orders=100,
        spread_pips=0,
        basket_take_profit_usd=999,
        initial_balance=100,
    )

    result = run_backtest(df, config)

    assert result.closed_trades.shape[0] >= 1
    assert result.max_open_orders >= 2
    assert result.closed_trades["close_reason"].isin(["TP"]).any()


def test_run_backtest_basket_close_closes_open_positions():
    prices = [
        1.1000,
        1.1002,
        1.1004,
        1.1006,
        1.1008,
        1.1010,
        1.0994,
        1.1004,
    ]
    df = add_indicators(make_rows(prices), ema_period=3, slope_lookback=2, atr_period=3)
    config = BacktestConfig(
        symbol="EURUSD=X",
        ema_period=3,
        slope_lookback=2,
        atr_period=3,
        min_slope_atr=0.05,
        grid_spacing_pips=10,
        take_profit_pips=50,
        max_orders=100,
        spread_pips=0,
        basket_take_profit_usd=0.05,
        initial_balance=100,
    )

    result = run_backtest(df, config)

    assert result.closed_trades["close_reason"].isin(["BASKET"]).any()
    assert result.closed_trades.loc[result.closed_trades["close_reason"] == "BASKET", "pnl_usd"].sum() > 0
