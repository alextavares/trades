import pandas as pd

from paper_futures_vwap_macd_live import (
    FuturesConfig,
    FuturesPosition,
    calculate_futures_pnl,
    check_position_exit,
    detect_vwap_macd_signal,
    position_from_signal,
)


def test_detect_vwap_macd_signal_requires_vwap_macd_and_ema_alignment():
    config = FuturesConfig()
    prev = pd.Series({"close": 100.0, "vwap": 101.0, "macd_hist": -1.0, "ema_fast": 99.0, "ema_slow": 100.0})
    curr = pd.Series({"close": 102.0, "vwap": 101.0, "macd_hist": 0.2, "ema_fast": 101.5, "ema_slow": 101.0})

    assert detect_vwap_macd_signal(prev, curr, config) == "LONG"


def test_detect_vwap_macd_signal_returns_short_for_bearish_alignment():
    config = FuturesConfig()
    prev = pd.Series({"close": 101.0, "vwap": 100.0, "macd_hist": 1.0, "ema_fast": 101.0, "ema_slow": 100.0})
    curr = pd.Series({"close": 99.0, "vwap": 100.0, "macd_hist": -0.2, "ema_fast": 99.0, "ema_slow": 99.5})

    assert detect_vwap_macd_signal(prev, curr, config) == "SHORT"


def test_position_from_signal_uses_atr_stop_and_reward_multiple():
    config = FuturesConfig(margin_usdc=10.0, leverage=5.0, stop_atr_mult=1.0, reward_r_mult=1.5)

    position = position_from_signal(
        symbol="BTCUSDT",
        direction="LONG",
        entry_time=pd.Timestamp("2026-05-01T12:00:00Z"),
        entry_price=100.0,
        atr=2.0,
        config=config,
    )

    assert position.notional_usdc == 50.0
    assert position.stop_price == 98.0
    assert position.take_profit_price == 103.0


def test_calculate_futures_pnl_subtracts_entry_and_exit_fees():
    position = FuturesPosition(
        symbol="BTCUSDT",
        direction="LONG",
        entry_time_utc="2026-05-01T12:00:00+00:00",
        entry_ts=1,
        entry_price=100.0,
        margin_usdc=10.0,
        leverage=5.0,
        notional_usdc=50.0,
        stop_price=98.0,
        take_profit_price=103.0,
        max_hold_minutes=20,
    )

    pnl = calculate_futures_pnl(position, exit_price=102.0, fee_rate=0.0004)

    assert round(pnl, 4) == 0.96


def test_check_position_exit_detects_stop_and_take_profit():
    long_position = FuturesPosition(
        symbol="BTCUSDT",
        direction="LONG",
        entry_time_utc="2026-05-01T12:00:00+00:00",
        entry_ts=1,
        entry_price=100.0,
        margin_usdc=10.0,
        leverage=5.0,
        notional_usdc=50.0,
        stop_price=98.0,
        take_profit_price=103.0,
        max_hold_minutes=20,
    )

    assert check_position_exit(long_position, current_price=103.1, now_ts=100) == "TAKE_PROFIT"
    assert check_position_exit(long_position, current_price=97.9, now_ts=100) == "STOP_LOSS"
