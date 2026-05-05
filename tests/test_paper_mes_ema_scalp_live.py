import pandas as pd

from backtest_mes_ema_scalp import IndexFuturesConfig
from paper_mes_ema_scalp_live import (
    MesPaperState,
    PaperMesPosition,
    close_position,
    interval_to_timedelta,
    latest_closed_bar_is_stale,
    process_closed_rows,
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


def test_close_position_calculates_mes_pnl_with_costs():
    config = IndexFuturesConfig(commission_per_side_usd=0.62, slippage_ticks=1.0)
    position = PaperMesPosition(
        symbol="MES=F",
        direction="LONG",
        entry_time_utc="2026-01-01T14:30:00+00:00",
        entry_price=5000.0,
        bars_held=0,
        contracts=1,
    )

    closed = close_position(position, "2026-01-01T14:35:00+00:00", 5002.0, "TAKE_PROFIT", config)

    assert closed["gross_pnl_usd"] == 10.0
    assert closed["costs_usd"] == 3.74
    assert closed["pnl_usd"] == 6.26
    assert closed["win"] is True


def test_process_closed_rows_opens_paper_position_from_signal():
    prices = [5000.0 + index * 0.5 for index in range(26)]
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
    )
    state = MesPaperState(last_processed_ts=pd.Timestamp("2026-01-01 16:15", tz="UTC").isoformat())

    next_state, trades, messages = process_closed_rows(make_rows(prices), state, config)

    assert next_state.open_position is not None
    assert next_state.open_position.direction == "LONG"
    assert trades == []
    assert any("ABRIU LONG" in message for message in messages)


def test_interval_to_timedelta_supports_minute_intervals():
    assert interval_to_timedelta("5m") == pd.Timedelta(minutes=5)
    assert interval_to_timedelta("1m") == pd.Timedelta(minutes=1)


def test_latest_closed_bar_is_stale_for_old_closed_candle():
    now = pd.Timestamp("2026-05-03 12:00:00+00:00")
    latest_ts = pd.Timestamp("2026-05-03 11:45:00+00:00")

    assert latest_closed_bar_is_stale(latest_ts, now, "5m") is True


def test_latest_closed_bar_is_not_stale_when_within_expected_delay():
    now = pd.Timestamp("2026-05-03 12:10:00+00:00")
    latest_ts = pd.Timestamp("2026-05-03 12:05:00+00:00")

    assert latest_closed_bar_is_stale(latest_ts, now, "5m") is False
