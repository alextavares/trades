import pandas as pd

from backtest_mt5_us500 import (
    Us500Config,
    calculate_trade_pnl,
    detect_signal,
    load_mt5_csv,
    resample_mt5_rates,
    run_backtest,
)


def test_load_mt5_csv_keeps_required_columns(tmp_path):
    csv_path = tmp_path / "us500.csv"
    csv_path.write_text(
        "\n".join(
            [
                "time_utc,time_brt,open,high,low,close,tick_volume,spread,real_volume",
                "2026-01-22T01:06:00+00:00,2026-01-21T22:06:00-03:00,6887.53,6889.90,6887.53,6889.75,151,43,0",
                "2026-01-22T01:07:00+00:00,2026-01-21T22:07:00-03:00,6889.78,6890.53,6889.03,6889.18,148,43,0",
            ]
        ),
        encoding="utf-8",
    )

    df = load_mt5_csv(str(csv_path))

    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume", "spread_points"]
    assert len(df) == 2
    assert df.loc[0, "spread_points"] == 43


def test_resample_mt5_rates_from_m1_to_m5():
    ts = pd.date_range("2026-01-01 14:30", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [10, 20, 30, 40, 50],
            "spread_points": [40, 41, 42, 43, 44],
        }
    )

    out = resample_mt5_rates(df, "5min")

    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 105.0
    assert row["low"] == 99.0
    assert row["close"] == 104.5
    assert row["volume"] == 150
    assert row["spread_points"] == 44


def test_calculate_trade_pnl_long_uses_lot_and_spread():
    config = Us500Config(lot_size=0.01, tick_size=0.01, tick_value_usd=0.01, commission_per_side_usd=0.0)

    gross, costs, net = calculate_trade_pnl(
        "LONG",
        entry_price=7000.50,
        exit_price=7005.50,
        spread_points=43,
        config=config,
    )

    assert round(gross, 4) == 0.05
    assert round(costs, 4) == 0.0043
    assert round(net, 4) == 0.0457


def test_detect_signal_finds_long_and_short():
    config = Us500Config(min_ema_gap_points=0.2, min_slope_points=0.1, trend_filter="none")
    prev = pd.Series({"close": 100.0, "ema_fast": 99.0})

    long_row = pd.Series(
        {"close": 101.0, "ema_fast": 100.8, "ema_mid": 100.4, "ema_slow": 100.0, "ema_slope": 0.2}
    )
    short_row = pd.Series(
        {"close": 99.0, "ema_fast": 99.2, "ema_mid": 99.6, "ema_slow": 100.0, "ema_slope": -0.2}
    )

    assert detect_signal(prev, long_row, config) == "LONG"
    assert detect_signal(prev, short_row, config) == "SHORT"


def test_run_backtest_produces_trades():
    ts = pd.date_range("2026-01-01 14:30", periods=120, freq="1min", tz="UTC")
    prices = [7000 + index * 0.15 for index in range(120)]
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": prices,
            "high": [price + 0.2 for price in prices],
            "low": [price - 0.2 for price in prices],
            "close": [price + 0.1 for price in prices],
            "volume": [100] * len(prices),
            "spread_points": [43] * len(prices),
        }
    )
    config = Us500Config(
        ema_fast=5,
        ema_mid=10,
        ema_slow=20,
        slope_lookback=3,
        min_ema_gap_points=0.05,
        min_slope_points=0.05,
        max_price_ema_fast_distance_points=5.0,
        stop_points=1.0,
        take_profit_points=1.0,
        max_hold_bars=20,
        cooldown_bars=0,
        commission_per_side_usd=0.0,
    )

    trades = run_backtest(df, config)

    assert not trades.empty
    assert trades["exit_reason"].isin(["TAKE_PROFIT", "TIME"]).any()
