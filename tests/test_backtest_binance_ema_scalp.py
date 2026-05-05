import pandas as pd

from backtest_binance_ema_scalp import (
    EmaScalpConfig,
    calculate_pnl,
    detect_ema_scalp_signal,
    run_backtest,
    trend_filter_allows,
)


def make_rows(prices: list[float]) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=len(prices), freq="s", tz="UTC")
    rows = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        rows.append(
            {
                "ts": ts[index],
                "open": open_price,
                "high": max(open_price, close),
                "low": min(open_price, close),
                "close": close,
                "volume": 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_detect_ema_scalp_signal_for_aligned_long():
    config = EmaScalpConfig(min_ema_gap_usd=1.0, min_slope_usd=0.2)
    prev = pd.Series({"close": 100.0, "ema_fast": 99.0})
    row = pd.Series(
        {
            "close": 105.0,
            "ema_fast": 104.0,
            "ema_mid": 101.0,
            "ema_slow": 99.0,
            "ema_slope": 0.5,
        }
    )

    assert detect_ema_scalp_signal(prev, row, config) == "LONG"


def test_detect_ema_scalp_signal_for_aligned_short():
    config = EmaScalpConfig(min_ema_gap_usd=1.0, min_slope_usd=0.2)
    prev = pd.Series({"close": 105.0, "ema_fast": 106.0})
    row = pd.Series(
        {
            "close": 100.0,
            "ema_fast": 101.0,
            "ema_mid": 104.0,
            "ema_slow": 106.0,
            "ema_slope": -0.5,
        }
    )

    assert detect_ema_scalp_signal(prev, row, config) == "SHORT"


def test_calculate_pnl_subtracts_round_trip_fees():
    gross, fees, pnl = calculate_pnl("LONG", entry_price=100.0, exit_price=101.0, notional=100.0, fee_rate=0.001)

    assert round(gross, 4) == 1.0
    assert round(fees, 4) == 0.2
    assert round(pnl, 4) == 0.8


def test_trend_filter_requires_adx_and_breakout():
    config = EmaScalpConfig(trend_filter="adx-breakout", min_adx=25.0, breakout_buffer_pct=0.0)
    row = pd.Series({"close": 105.0, "adx": 30.0, "breakout_high": 104.0, "breakout_low": 100.0})

    assert trend_filter_allows("LONG", row, config) is True

    weak_adx = pd.Series({"close": 105.0, "adx": 15.0, "breakout_high": 104.0, "breakout_low": 100.0})
    assert trend_filter_allows("LONG", weak_adx, config) is False

    no_breakout = pd.Series({"close": 103.0, "adx": 30.0, "breakout_high": 104.0, "breakout_low": 100.0})
    assert trend_filter_allows("LONG", no_breakout, config) is False


def test_run_backtest_opens_and_closes_take_profit():
    prices = [100.0 + index * 0.02 for index in range(720)]
    prices.extend([114.6, 114.7, 114.9, 115.2])
    config = EmaScalpConfig(
        ema_fast=5,
        ema_mid=10,
        ema_slow=20,
        slope_lookback=3,
        min_ema_gap_usd=0.01,
        min_slope_usd=0.01,
        max_price_ema_fast_distance_usd=10.0,
        stop_pct=0.001,
        take_profit_pct=0.001,
        fee_rate=0.0,
        cooldown_seconds=0,
        max_hold_seconds=30,
    )

    trades = run_backtest(make_rows(prices), config)

    assert not trades.empty
    assert trades["exit_reason"].isin(["TAKE_PROFIT"]).any()
