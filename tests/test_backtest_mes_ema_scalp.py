import pandas as pd

from backtest_mes_ema_scalp import (
    IndexFuturesConfig,
    calculate_contract_pnl,
    detect_signal,
    get_ohlcv,
    parse_databento_retry_end,
    parse_period_to_start,
    resample_ohlcv,
    run_backtest,
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
                "high": max(open_price, close) + 0.25,
                "low": min(open_price, close) - 0.25,
                "close": close,
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


def test_calculate_contract_pnl_subtracts_commission_and_slippage():
    config = IndexFuturesConfig(
        point_value_usd=5.0,
        contracts=1,
        tick_size=0.25,
        slippage_ticks=1.0,
        commission_per_side_usd=0.62,
    )

    gross, costs, net = calculate_contract_pnl("LONG", 5000.0, 5002.0, config)

    assert gross == 10.0
    assert costs == 3.74
    assert net == 6.26


def test_detect_signal_for_aligned_long_and_short():
    config = IndexFuturesConfig(min_ema_gap_points=0.5, min_slope_points=0.25, trend_filter="none")
    prev = pd.Series({"close": 5000.0, "ema_fast": 4999.0})

    long_row = pd.Series(
        {
            "close": 5004.0,
            "ema_fast": 5003.0,
            "ema_mid": 5001.5,
            "ema_slow": 5000.5,
            "ema_slope": 0.75,
        }
    )
    assert detect_signal(prev, long_row, config) == "LONG"

    short_row = pd.Series(
        {
            "close": 4996.0,
            "ema_fast": 4997.0,
            "ema_mid": 4998.5,
            "ema_slow": 4999.5,
            "ema_slope": -0.75,
        }
    )
    assert detect_signal(prev, short_row, config) == "SHORT"


def test_run_backtest_opens_and_closes_take_profit():
    prices = [5000.0 + index * 0.5 for index in range(80)]
    prices.extend([5040.5, 5041.0, 5042.0, 5044.0, 5046.0])
    config = IndexFuturesConfig(
        ema_fast=5,
        ema_mid=10,
        ema_slow=20,
        slope_lookback=3,
        min_ema_gap_points=0.10,
        min_slope_points=0.10,
        max_price_ema_fast_distance_points=20.0,
        trend_filter="none",
        stop_points=4.0,
        take_profit_points=4.0,
        max_hold_bars=12,
        cooldown_bars=0,
        commission_per_side_usd=0.0,
        slippage_ticks=0.0,
    )

    trades = run_backtest(make_rows(prices), config)

    assert not trades.empty
    assert trades["exit_reason"].isin(["TAKE_PROFIT"]).any()


def test_resample_ohlcv_from_one_minute_to_five_minutes():
    ts = pd.date_range("2026-01-01 14:30", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [10, 20, 30, 40, 50],
        }
    )

    out = resample_ohlcv(df, "5m")

    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 105.0
    assert row["low"] == 99.0
    assert row["close"] == 104.5
    assert row["volume"] == 150


def test_parse_period_to_start_supports_common_suffixes():
    end = pd.Timestamp("2026-01-10T15:00:00Z")

    assert parse_period_to_start("5d", end) == pd.Timestamp("2026-01-05T15:00:00Z")
    assert parse_period_to_start("12h", end) == pd.Timestamp("2026-01-10T03:00:00Z")
    assert parse_period_to_start("30m", end) == pd.Timestamp("2026-01-10T14:30:00Z")


def test_parse_databento_retry_end_supports_common_error_formats():
    assert parse_databento_retry_end(
        "The dataset GLBX.MDP3 has data available up to '2026-05-04 14:50:00+00:00'."
    ) == pd.Timestamp("2026-05-04T14:49:00Z")
    assert parse_databento_retry_end(
        "Try again with an end time before 2026-05-04T07:06:57.428069000Z."
    ) == pd.Timestamp("2026-05-04T07:05:57.428069Z")


def test_get_ohlcv_uses_requested_source(monkeypatch):
    expected = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01 14:30", periods=2, freq="5min", tz="UTC"),
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [10, 20],
        }
    )

    monkeypatch.setattr("backtest_mes_ema_scalp.fetch_databento_ohlcv", lambda *args, **kwargs: expected)

    result = get_ohlcv("databento", "MES1!", "5d", "5m")

    pd.testing.assert_frame_equal(result, expected)


def test_get_ohlcv_auto_falls_back_to_yahoo(monkeypatch):
    expected = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01 14:30", periods=2, freq="5min", tz="UTC"),
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [10, 20],
        }
    )

    def fail_databento(*args, **kwargs):
        raise RuntimeError("sem credito/licenca")

    monkeypatch.setattr("backtest_mes_ema_scalp.fetch_databento_ohlcv", fail_databento)
    monkeypatch.setattr("backtest_mes_ema_scalp.fetch_yahoo_ohlcv", lambda *args, **kwargs: expected)

    result = get_ohlcv("auto", "MES=F", "5d", "5m")

    pd.testing.assert_frame_equal(result, expected)
