import pandas as pd

from paper_mes_orb_live import MesOrbState, OrbConfig, PendingSignal, process_closed_rows


def make_rows(prices: list[float]) -> pd.DataFrame:
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
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


def test_process_closed_rows_builds_range_then_opens_next_bar_after_breakout():
    df = make_rows([5000, 5001, 5002, 5003, 5004, 5005, 5010, 5018, 5020])
    config = OrbConfig(
        window_minutes=30,
        stop_points=8,
        take_profit_points=8,
        max_hold_bars=24,
        commission_per_side_usd=0,
        slippage_ticks=0,
    )

    state, trades, messages = process_closed_rows(df, MesOrbState(), config)

    assert trades
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["entry_price"] == 5010
    assert trades[0]["exit_reason"] == "TAKE_PROFIT"
    assert any("ORB pronto" in message for message in messages)


def test_process_closed_rows_enters_pending_signal_on_next_bar_open():
    df = make_rows([5000, 5001, 5002, 5003, 5004, 5005, 5010, 5011])
    config = OrbConfig(window_minutes=30, stop_points=8, take_profit_points=8, max_hold_bars=24)
    state = MesOrbState(
        last_processed_ts="2026-01-05T15:00:00+00:00",
        pending_signal=PendingSignal("LONG", "2026-01-05T15:00:00+00:00"),
    )

    state, trades, messages = process_closed_rows(df, state, config)

    assert state.open_position is not None
    assert state.open_position.direction == "LONG"
    assert state.open_position.entry_price == 5010
    assert trades == []
    assert any("ABRIU LONG" in message for message in messages)


def test_process_closed_rows_only_trades_once_per_session():
    df = make_rows([5000, 5001, 5002, 5003, 5004, 5005, 5010, 5018, 5020, 5030])
    config = OrbConfig(
        window_minutes=30,
        stop_points=8,
        take_profit_points=8,
        max_hold_bars=24,
        commission_per_side_usd=0,
        slippage_ticks=0,
    )
    state, trades, _ = process_closed_rows(df, MesOrbState(), config)

    state, more_trades, _ = process_closed_rows(df, state, config)

    assert len(trades) == 1
    assert more_trades == []
