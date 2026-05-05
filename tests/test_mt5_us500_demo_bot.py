from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_us500_demo_bot import (
    Mt5Us500State,
    OpenUs500Position,
    build_hold_message,
    build_entry_levels,
    build_signal_snapshot,
    bootstrap_state_to_latest_closed_bar,
    build_config,
    increment_bars_held,
    position_matches,
    process_closed_rows,
    reconcile_state_timestamp,
)
from backtest_mt5_us500 import Us500Config


def make_rows(prices: list[float], spread_points: float = 43.0) -> pd.DataFrame:
    ts = pd.date_range("2026-01-01 14:30", periods=len(prices), freq="15min", tz="UTC")
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
                "volume": 100,
                "spread_points": spread_points,
            }
        )
    return pd.DataFrame(rows)


def test_build_entry_levels_for_long_and_short():
    config = Us500Config(stop_points=7.0, take_profit_points=10.0)

    long_levels = build_entry_levels("LONG", 7200.0, config)
    short_levels = build_entry_levels("SHORT", 7200.0, config)

    assert long_levels == (7193.0, 7210.0)
    assert short_levels == (7207.0, 7190.0)


def test_process_closed_rows_emits_open_signal():
    prices = [7000.0 + index * 0.4 for index in range(100)]
    config = Us500Config(
        timeframe="15min",
        ema_fast=5,
        ema_mid=10,
        ema_slow=20,
        slope_lookback=3,
        min_ema_gap_points=0.05,
        min_slope_points=0.05,
        max_price_ema_fast_distance_points=5.0,
        trend_filter="none",
        entry_mode="trend",
        cooldown_bars=0,
    )
    state = Mt5Us500State(last_processed_ts=pd.Timestamp("2026-01-02 12:00:00+00:00").isoformat())

    next_state, actions = process_closed_rows(make_rows(prices), state, config)

    assert next_state.last_processed_ts
    assert any(action["kind"] == "OPEN_SIGNAL" for action in actions)


def test_increment_bars_held_advances_open_position():
    state = Mt5Us500State(
        open_position=OpenUs500Position(
            ticket=123,
            symbol="US500",
            direction="LONG",
            entry_time_utc="2026-01-01T14:30:00+00:00",
            entry_price=7200.0,
            lot_size=0.01,
            bars_held=0,
            stop_loss=7193.0,
            take_profit=7210.0,
        )
    )

    increment_bars_held(state)

    assert state.open_position is not None
    assert state.open_position.bars_held == 1


def test_build_signal_snapshot_copies_indicator_fields():
    row = pd.Series(
        {
            "adx": 22.5,
            "ema_fast": 7200.2,
            "ema_mid": 7199.8,
            "ema_slow": 7199.1,
            "ema_slope": 0.6,
            "spread_points": 43.0,
            "close": 7201.0,
        }
    )

    snapshot = build_signal_snapshot(row)

    assert snapshot["adx"] == 22.5
    assert snapshot["ema_fast"] == 7200.2
    assert snapshot["spread_points"] == 43.0


def test_build_hold_message_includes_closed_bar_and_poll_time():
    closed_bar_ts = pd.Timestamp("2026-05-05T23:30:00+00:00")
    polled_at_utc = pd.Timestamp("2026-05-05T23:44:53+00:00")

    message = build_hold_message(closed_bar_ts, 7269.47, polled_at_utc)

    assert "closed_bar_ts=2026-05-05T23:30:00+00:00" in message
    assert "close=7269.47" in message
    assert "polled_at_utc=2026-05-05T23:44:53+00:00" in message


def test_build_config_respects_min_adx_and_cooldown_overrides():
    args = SimpleNamespace(
        timeframe="15min",
        lot_size=0.01,
        min_adx=18.0,
        cooldown_bars=5,
    )

    config = build_config(args)

    assert config.min_adx == 18.0
    assert config.cooldown_bars == 5


def test_reconcile_state_timestamp_rebases_future_state_without_open_position():
    state = Mt5Us500State(last_processed_ts="2026-05-05T23:30:00+00:00")

    changed = reconcile_state_timestamp(
        state,
        latest_closed_bar_ts=pd.Timestamp("2026-05-05T21:30:00+00:00"),
        timeframe="15min",
    )

    assert changed is True
    assert state.last_processed_ts == "2026-05-05T21:30:00+00:00"


def test_bootstrap_state_to_latest_closed_bar_sets_initial_checkpoint():
    state = Mt5Us500State()

    changed = bootstrap_state_to_latest_closed_bar(
        state,
        latest_closed_bar_ts=pd.Timestamp("2026-05-05T21:30:00+00:00"),
    )

    assert changed is True
    assert state.last_processed_ts == "2026-05-05T21:30:00+00:00"


def test_position_matches_filters_magic_and_comment():
    class DummyPosition:
        magic = 505001
        comment = "codex-us500-demo-open"

    assert position_matches(DummyPosition(), magic=505001, comment_prefix="codex-us500-demo")
    assert not position_matches(DummyPosition(), magic=123, comment_prefix="codex-us500-demo")
    assert not position_matches(DummyPosition(), magic=505001, comment_prefix="other-bot")
