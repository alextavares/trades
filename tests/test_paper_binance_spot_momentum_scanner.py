from paper_binance_spot_momentum_scanner import (
    Candle,
    PaperSpotPosition,
    ScannerConfig,
    TickerSnapshot,
    build_arg_parser,
    check_exit,
    close_position,
    detect_breakout_candidate,
    load_open_position,
    load_symbol_cooldowns,
    normalize_kline,
    normalize_ticker,
    position_from_candidate,
    rank_breakout_candidates,
    rank_candidates,
    save_open_position,
    save_symbol_cooldowns,
    seed_cooldowns_from_trades,
    update_position_trailing,
)


def make_snapshot(
    symbol: str,
    price: float,
    bid: float | None = None,
    ask: float | None = None,
    quote_volume: float = 2_000_000.0,
    price_change_pct: float = 12.0,
) -> TickerSnapshot:
    return TickerSnapshot(
        symbol=symbol,
        price=price,
        bid=bid if bid is not None else price * 0.999,
        ask=ask if ask is not None else price * 1.001,
        quote_volume=quote_volume,
        price_change_pct=price_change_pct,
    )


def make_candle(open_price: float, high: float, low: float, close: float, volume: float) -> Candle:
    return Candle(
        open_time=1,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_normalize_ticker_keeps_usdt_spot_rows_with_valid_prices():
    row = {
        "symbol": "ETHUSDT",
        "lastPrice": "3200.50",
        "bidPrice": "3200.00",
        "askPrice": "3201.00",
        "quoteVolume": "15000000",
    }

    snapshot = normalize_ticker(row)

    assert snapshot == TickerSnapshot(
        symbol="ETHUSDT",
        price=3200.50,
        bid=3200.00,
        ask=3201.00,
        quote_volume=15_000_000.0,
        price_change_pct=0.0,
    )


def test_normalize_kline_maps_binance_rows_to_candles():
    row = [1770000000000, "1.00", "1.20", "0.95", "1.15", "12345", 1770000899999]

    candle = normalize_kline(row)

    assert candle == Candle(
        open_time=1770000000000,
        open=1.0,
        high=1.2,
        low=0.95,
        close=1.15,
        volume=12345.0,
    )


def test_detect_breakout_candidate_requires_range_break_and_volume():
    snapshot = make_snapshot("TSTUSDT", 0.0132, bid=0.01318, ask=0.01322, price_change_pct=18.0)
    previous_range = [make_candle(0.0108, 0.0112, 0.0107, 0.0110, 1000.0) for _ in range(20)]
    breakout = make_candle(0.0110, 0.0134, 0.0109, 0.0132, 5000.0)
    config = ScannerConfig(
        breakout_lookback=20,
        min_candle_move_pct=2.0,
        max_candle_move_pct=25.0,
        max_breakout_extension_pct=25.0,
        min_volume_multiplier=3.0,
        min_24h_change_pct=5.0,
    )

    candidate = detect_breakout_candidate(snapshot, previous_range + [breakout], config)

    assert candidate is not None
    assert candidate.symbol == "TSTUSDT"
    assert round(candidate.move_pct, 4) == 20.0
    assert round(candidate.volume_multiplier, 4) == 5.0
    assert candidate.breakout_high == 0.0112


def test_detect_breakout_candidate_rejects_overextended_current_candle():
    snapshot = make_snapshot("PUMPUSDT", 0.0145, price_change_pct=45.0)
    previous_range = [make_candle(0.0108, 0.0112, 0.0107, 0.0110, 1000.0) for _ in range(20)]
    overextended = make_candle(0.0110, 0.0150, 0.0109, 0.0145, 6000.0)
    config = ScannerConfig(max_candle_move_pct=18.0, min_volume_multiplier=3.0)

    candidate = detect_breakout_candidate(snapshot, previous_range + [overextended], config)

    assert candidate is None


def test_detect_breakout_candidate_rejects_price_too_far_above_breakout_line():
    snapshot = make_snapshot("LATEUSDT", 0.0130, price_change_pct=25.0)
    previous_range = [make_candle(0.0100, 0.0102, 0.0099, 0.0100, 1000.0) for _ in range(20)]
    late_breakout = make_candle(0.0102, 0.0132, 0.0102, 0.0130, 5000.0)
    config = ScannerConfig(
        min_candle_move_pct=2.0,
        max_candle_move_pct=35.0,
        max_breakout_extension_pct=12.0,
        min_volume_multiplier=3.0,
        min_24h_change_pct=5.0,
    )

    candidate = detect_breakout_candidate(snapshot, previous_range + [late_breakout], config)

    assert candidate is None


def test_detect_breakout_candidate_can_project_incomplete_candle_volume():
    snapshot = make_snapshot("EARLYUSDT", 0.0122, price_change_pct=6.0)
    previous_range = [make_candle(0.0100, 0.0102, 0.0099, 0.0100, 1000.0) for _ in range(20)]
    early_breakout = Candle(open_time=0, open=0.0100, high=0.0123, low=0.0100, close=0.0122, volume=1200.0)
    config = ScannerConfig(
        interval="15m",
        min_candle_move_pct=2.0,
        max_candle_move_pct=25.0,
        max_breakout_extension_pct=25.0,
        min_volume_multiplier=3.0,
        min_24h_change_pct=5.0,
        project_current_volume=True,
    )

    candidate = detect_breakout_candidate(snapshot, previous_range + [early_breakout], config, now_ms=300_000)

    assert candidate is not None
    assert round(candidate.volume_multiplier, 4) == 3.6


def test_rank_breakout_candidates_orders_by_breakout_score():
    snapshots = {
        "SLOWUSDT": make_snapshot("SLOWUSDT", 1.04, quote_volume=3_000_000.0, price_change_pct=8.0),
        "FASTUSDT": make_snapshot("FASTUSDT", 1.08, quote_volume=4_000_000.0, price_change_pct=15.0),
        "WIDEUSDT": make_snapshot("WIDEUSDT", 1.08, bid=1.0, ask=1.16, quote_volume=4_000_000.0, price_change_pct=15.0),
    }
    base = [make_candle(1.0, 1.01, 0.99, 1.0, 1000.0) for _ in range(20)]
    candles_by_symbol = {
        "SLOWUSDT": base + [make_candle(1.0, 1.05, 1.0, 1.04, 3500.0)],
        "FASTUSDT": base + [make_candle(1.0, 1.09, 1.0, 1.08, 5500.0)],
        "WIDEUSDT": base + [make_candle(1.0, 1.09, 1.0, 1.08, 5500.0)],
    }
    config = ScannerConfig(min_quote_volume=1_000_000.0, max_spread_pct=0.5, min_volume_multiplier=3.0)

    candidates = rank_breakout_candidates(snapshots, candles_by_symbol, config, now_ts=1000, cooldowns={})

    assert [candidate.symbol for candidate in candidates] == ["FASTUSDT", "SLOWUSDT"]


def test_rank_candidates_prefers_filtered_short_term_momentum():
    previous = {
        "ETHUSDT": make_snapshot("ETHUSDT", 100.0),
        "SOLUSDT": make_snapshot("SOLUSDT", 100.0),
        "THINUSDT": make_snapshot("THINUSDT", 100.0, quote_volume=10_000.0),
        "WIDEUSDT": make_snapshot("WIDEUSDT", 100.0),
        "CHASEUSDT": make_snapshot("CHASEUSDT", 100.0),
    }
    current = {
        "ETHUSDT": make_snapshot("ETHUSDT", 102.0),
        "SOLUSDT": make_snapshot("SOLUSDT", 103.0),
        "THINUSDT": make_snapshot("THINUSDT", 110.0, quote_volume=10_000.0),
        "WIDEUSDT": make_snapshot("WIDEUSDT", 103.0, bid=100.0, ask=106.0),
        "CHASEUSDT": make_snapshot("CHASEUSDT", 120.0),
    }
    config = ScannerConfig(
        min_move_pct=1.0,
        max_move_pct=8.0,
        min_quote_volume=500_000.0,
        max_spread_pct=0.5,
    )

    candidates = rank_candidates(previous, current, config, now_ts=1000, cooldowns={})

    assert [candidate.symbol for candidate in candidates] == ["SOLUSDT", "ETHUSDT"]
    assert round(candidates[0].move_pct, 4) == 3.0


def test_rank_candidates_respects_symbol_cooldown():
    previous = {"ETHUSDT": make_snapshot("ETHUSDT", 100.0)}
    current = {"ETHUSDT": make_snapshot("ETHUSDT", 102.0)}
    config = ScannerConfig(min_move_pct=1.0, min_quote_volume=1.0)

    candidates = rank_candidates(previous, current, config, now_ts=1000, cooldowns={"ETHUSDT": 1100})

    assert candidates == []


def test_symbol_cooldowns_are_persisted_and_pruned(tmp_path):
    path = tmp_path / "cooldowns.json"

    save_symbol_cooldowns(path, {"OLDUSDT": 900, "TSTUSDT": 2000})
    loaded = load_symbol_cooldowns(path, now_ts=1000)

    assert loaded == {"TSTUSDT": 2000}


def test_symbol_cooldowns_handles_missing_or_invalid_file(tmp_path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{ invalido", encoding="utf-8")

    assert load_symbol_cooldowns(missing, now_ts=1000) == {}
    assert load_symbol_cooldowns(invalid, now_ts=1000) == {}


def test_open_position_state_round_trip_and_clear(tmp_path):
    path = tmp_path / "spot_state.json"
    position = PaperSpotPosition(
        symbol="ETHUSDT",
        entry_time_utc="2026-01-01T00:00:00+00:00",
        entry_ts=1000,
        entry_price=100.0,
        notional_usdc=100.0,
        quantity=1.0,
        stop_price=95.0,
        take_profit_price=0.0,
        max_hold_seconds=3600,
        fee_rate=0.001,
        highest_price=103.0,
        trailing_activation_price=110.0,
        trailing_stop_price=97.5,
        trailing_pct=0.05,
    )

    save_open_position(path, position)
    loaded = load_open_position(path)

    assert loaded == position

    save_open_position(path, None)
    assert load_open_position(path) is None


def test_open_position_state_ignores_closed_or_invalid_payload(tmp_path):
    closed = tmp_path / "closed.json"
    closed.write_text('{"symbol":"ETHUSDT","status":"CLOSED"}', encoding="utf-8")
    invalid = tmp_path / "invalid_state.json"
    invalid.write_text("{ bad", encoding="utf-8")

    assert load_open_position(closed) is None
    assert load_open_position(invalid) is None


def test_seed_cooldowns_from_recent_closed_trades(tmp_path):
    path = tmp_path / "trades.csv"
    path.write_text(
        "\n".join(
            [
                "symbol,status,entry_ts,exit_ts",
                "OLDUSDT,CLOSED,100,200",
                "TSTUSDT,CLOSED,1000,1900",
                "OPENUSDT,OPEN,1800,",
                "BADUSDT,CLOSED,abc,",
            ]
        ),
        encoding="utf-8",
    )

    cooldowns = seed_cooldowns_from_trades(path, cooldown_seconds=500, now_ts=2000)

    assert cooldowns == {"TSTUSDT": 2400}


def test_position_exit_rules_for_long_spot_paper_trade():
    position = PaperSpotPosition(
        symbol="ETHUSDT",
        entry_time_utc="2026-01-01T00:00:00+00:00",
        entry_ts=1000,
        entry_price=100.0,
        notional_usdc=100.0,
        quantity=1.0,
        stop_price=98.0,
        take_profit_price=104.0,
        max_hold_seconds=60,
        fee_rate=0.001,
    )

    assert check_exit(position, 97.9, 1001) == "STOP_LOSS"
    assert check_exit(position, 104.1, 1001) == "TAKE_PROFIT"
    assert check_exit(position, 100.5, 1060) == "TIME_EXIT"
    assert check_exit(position, 100.5, 1059) is None


def test_close_position_calculates_spot_net_pnl():
    position = PaperSpotPosition(
        symbol="ETHUSDT",
        entry_time_utc="2026-01-01T00:00:00+00:00",
        entry_ts=1000,
        entry_price=100.0,
        notional_usdc=100.0,
        quantity=1.0,
        stop_price=98.0,
        take_profit_price=104.0,
        max_hold_seconds=60,
        fee_rate=0.001,
    )

    closed = close_position(position, 105.0, "TAKE_PROFIT")

    assert closed.status == "CLOSED"
    assert round(closed.gross_pnl_usdc, 4) == 5.0
    assert round(closed.fees_usdc, 4) == 0.205
    assert round(closed.pnl_usdc, 4) == 4.795
    assert closed.win is True


def test_update_position_trailing_activates_after_profit_target():
    position = PaperSpotPosition(
        symbol="ETHUSDT",
        entry_time_utc="2026-01-01T00:00:00+00:00",
        entry_ts=1000,
        entry_price=100.0,
        notional_usdc=100.0,
        quantity=1.0,
        stop_price=95.0,
        take_profit_price=0.0,
        max_hold_seconds=3600,
        fee_rate=0.001,
        highest_price=100.0,
        trailing_activation_price=110.0,
        trailing_pct=0.05,
    )

    updated = update_position_trailing(position, 112.0)

    assert updated.highest_price == 112.0
    assert updated.trailing_stop_price == 106.4
    assert check_exit(updated, 106.3, 1001) == "TRAILING_STOP"


def test_position_from_candidate_defaults_to_trailing_runner_without_fixed_take_profit():
    previous = {"ETHUSDT": make_snapshot("ETHUSDT", 100.0)}
    current = {"ETHUSDT": make_snapshot("ETHUSDT", 102.0)}
    config = ScannerConfig(
        notional_usdc=50.0,
        stop_pct=0.05,
        take_profit_pct=0.0,
        trailing_activation_pct=0.10,
        trailing_pct=0.05,
        max_hold_seconds=21_600,
    )
    candidate = rank_candidates(previous, current, config, now_ts=1000, cooldowns={})[0]

    position = position_from_candidate(candidate, config, now_ts=1000)

    assert position.take_profit_price == 0.0
    assert position.trailing_activation_price == 112.2
    assert position.trailing_pct == 0.05
    assert position.max_hold_seconds == 21_600


def test_position_from_candidate_sets_fixed_risk_prices():
    previous = {"ETHUSDT": make_snapshot("ETHUSDT", 100.0)}
    current = {"ETHUSDT": make_snapshot("ETHUSDT", 102.0)}
    config = ScannerConfig(notional_usdc=50.0, stop_pct=0.02, take_profit_pct=0.04, max_hold_seconds=90)
    candidate = rank_candidates(previous, current, config, now_ts=1000, cooldowns={})[0]

    position = position_from_candidate(candidate, config, now_ts=1000)

    assert position.symbol == "ETHUSDT"
    assert position.entry_price == 102.0
    assert round(position.quantity, 8) == round(50.0 / 102.0, 8)
    assert position.stop_price == 99.96
    assert position.take_profit_price == 106.08
    assert position.max_hold_seconds == 90


def test_cli_defaults_are_spot_paper_scanner_defaults():
    args = build_arg_parser().parse_args([])

    assert args.poll_seconds == 60
    assert args.interval == "15m"
    assert args.breakout_lookback == 20
    assert args.min_candle_move_pct == 2.0
    assert args.max_candle_move_pct == 18.0
    assert args.min_volume_multiplier == 3.0
    assert args.min_24h_change_pct == 5.0
    assert args.min_quote_volume == 1_000_000.0
    assert args.max_spread_pct == 0.35
    assert args.notional == 25.0
    assert args.stop_pct == 0.05
    assert args.take_profit_pct == 0.0
    assert args.trailing_activation_pct == 0.10
    assert args.trailing_pct == 0.05
    assert args.max_breakout_extension_pct == 12.0
    assert args.max_hold_seconds == 43200
    assert args.cooldown_seconds == 86400
    assert args.cooldowns_json == "paper_binance_spot_momentum_scanner_cooldowns.json"
    assert args.state_json == "paper_binance_spot_momentum_scanner_state.json"
    assert args.trades_csv == "paper_binance_spot_momentum_scanner_trades.csv"
