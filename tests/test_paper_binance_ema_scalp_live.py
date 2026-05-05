from paper_binance_ema_scalp_live import (
    PaperEmaScalpPosition,
    build_arg_parser,
    check_exit,
    close_position,
    default_strategy_config,
    load_open_position,
    save_open_position,
)


def make_position(direction: str = "LONG") -> PaperEmaScalpPosition:
    return PaperEmaScalpPosition(
        symbol="BTCUSDT",
        direction=direction,
        entry_time_utc="2026-01-01T00:00:00+00:00",
        entry_ts=1000,
        entry_price=100.0,
        notional_usdc=100.0,
        stop_price=98.0 if direction == "LONG" else 102.0,
        take_profit_price=104.0 if direction == "LONG" else 96.0,
        max_hold_seconds=3600,
        fee_rate=0.001,
    )


def test_check_exit_long_stop_and_take_profit():
    position = make_position("LONG")

    assert check_exit(position, 97.9, 1001) == "STOP_LOSS"
    assert check_exit(position, 104.1, 1001) == "TAKE_PROFIT"
    assert check_exit(position, 100.5, 1001) is None


def test_check_exit_short_stop_and_take_profit():
    position = make_position("SHORT")

    assert check_exit(position, 102.1, 1001) == "STOP_LOSS"
    assert check_exit(position, 95.9, 1001) == "TAKE_PROFIT"


def test_close_position_calculates_net_pnl():
    position = make_position("LONG")
    closed = close_position(position, 101.0, "TAKE_PROFIT")

    assert closed.status == "CLOSED"
    assert round(closed.gross_pnl_usdc, 4) == 1.0
    assert round(closed.fees_usdc, 4) == 0.2
    assert round(closed.pnl_usdc, 4) == 0.8
    assert closed.win is True


def test_open_position_state_round_trip_and_clear(tmp_path):
    path = tmp_path / "ema_scalp_state.json"
    position = make_position("SHORT")

    save_open_position(path, position)
    loaded = load_open_position(path)

    assert loaded == position

    save_open_position(path, None)
    assert load_open_position(path) is None


def test_open_position_state_ignores_closed_or_invalid_payload(tmp_path):
    closed = tmp_path / "closed.json"
    closed.write_text('{"symbol":"BTCUSDT","status":"CLOSED"}', encoding="utf-8")
    invalid = tmp_path / "invalid_state.json"
    invalid.write_text("{ bad", encoding="utf-8")

    assert load_open_position(closed) is None
    assert load_open_position(invalid) is None


def test_default_strategy_config_uses_scalp_sized_risk_params():
    class Args:
        symbol = "BTCUSDT"
        interval = "5m"
        ema_fast = 9
        ema_mid = 21
        ema_slow = 60
        slope_lookback = 9
        entry_mode = "trend"
        trend_filter = "adx"
        adx_period = 14
        min_adx = 22.0
        breakout_lookback = 12
        breakout_buffer_pct = 0.0002
        min_ema_gap_usd = 15.0
        min_slope_usd = 5.0
        max_price_ema_fast_distance_usd = 500.0
        notional = 100.0
        fee_rate = 0.0004
        stop_pct = 0.001
        take_profit_pct = 0.0015
        max_hold_seconds = 180
        cooldown_seconds = 30

    config = default_strategy_config(Args())

    assert config.stop_pct == 0.001
    assert config.take_profit_pct == 0.0015
    assert config.max_hold_seconds == 180
    assert config.cooldown_seconds == 30


def test_cli_defaults_match_scalp_profile():
    args = build_arg_parser().parse_args([])

    assert args.stop_pct == 0.001
    assert args.take_profit_pct == 0.0015
    assert args.max_hold_seconds == 180
    assert args.cooldown_seconds == 30
    assert args.state_json == "paper_binance_ema_scalp_state.json"
