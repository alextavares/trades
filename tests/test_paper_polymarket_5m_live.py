import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paper_polymarket_5m_live as live
from paper_polymarket_5m_live import (
    LiveConfig,
    PaperPosition,
    bollinger_rsi_reversal_down_signal,
    calculate_order_shares,
    current_event_start,
    current_5m_event_start,
    decide_poly_75_breakout,
    decide_poly_odds_momentum,
    decide_mispricing_contrarian,
    ema_1s_trend_direction,
    first_minute_continuation_direction,
    fetch_polymarket_event_for_config,
    is_entry_offset_allowed,
    late_window_direction,
    load_credentials_env,
    momentum_confirmed_direction,
    parse_allowed_directions,
    parse_excluded_entry_hours,
    place_real_buy_order,
    prepare_paper_limit_entry,
    prepare_paper_realistic_entry,
    recent_abs_move_pct,
    real_order_limit_price,
    RealOrderSubmissionUncertain,
    release_real_shared_position,
    reserve_real_shared_position,
    settle_position_by_contract_price,
    settle_position,
    should_skip_entry_hour,
    target_rejection_direction,
    try_fill_paper_limit_entry,
)


def test_current_5m_event_start_rounds_down_to_boundary():
    now = datetime(2026, 4, 29, 21, 37, 42, tzinfo=timezone.utc)

    assert current_5m_event_start(now) == 1777498500


def test_fetch_polymarket_event_for_config_uses_asset_slug_for_non_btc(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"slug": "eth-updown-5m-123", "markets": []}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("paper_polymarket_5m_live.requests.get", fake_get)

    event = fetch_polymarket_event_for_config(123, LiveConfig(symbol="ETHUSDT", event_slug_asset="eth"))

    assert event["slug"] == "eth-updown-5m-123"
    assert captured["url"].endswith("/events/slug/eth-updown-5m-123")


def test_current_event_start_supports_15m_boundary():
    now = datetime(2026, 4, 29, 21, 37, 42, tzinfo=timezone.utc)

    assert current_event_start(now, duration_seconds=900) == 1777498200


def test_is_entry_offset_allowed_uses_one_based_minute_offsets():
    config = LiveConfig(entry_offsets=(1, 4))

    assert is_entry_offset_allowed(10, config)
    assert not is_entry_offset_allowed(61, config)
    assert not is_entry_offset_allowed(150, config)
    assert is_entry_offset_allowed(180, config)
    assert not is_entry_offset_allowed(245, config)

    late_config = LiveConfig(entry_offsets=(5,))
    assert is_entry_offset_allowed(245, late_config)

    fifteen_config = LiveConfig(entry_offsets=(6, 10), event_duration_minutes=15)
    assert not is_entry_offset_allowed(245, fifteen_config)
    assert is_entry_offset_allowed(305, fifteen_config)
    assert is_entry_offset_allowed(599, fifteen_config)
    assert not is_entry_offset_allowed(900, fifteen_config)


def test_recent_abs_move_pct_uses_previous_closed_minute():
    import pandas as pd

    df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})

    assert round(recent_abs_move_pct(df, current_price=103.0, seconds=60), 6) == 0.019802


def test_recent_abs_move_pct_returns_zero_when_disabled_or_missing_data():
    import pandas as pd

    df = pd.DataFrame({"close": [100.0]})

    assert recent_abs_move_pct(df, current_price=103.0, seconds=0) == 0.0
    assert recent_abs_move_pct(df, current_price=103.0, seconds=60) == 0.0


def test_real_order_limit_price_defaults_to_signal_price():
    config = LiveConfig(max_contract_price=0.85)

    assert real_order_limit_price(0.76, config) == 0.76


def test_real_order_limit_price_adds_slippage_and_caps_at_max_price():
    config = LiveConfig(max_contract_price=0.85, real_price_slippage=0.01)

    assert real_order_limit_price(0.76, config) == 0.77
    assert real_order_limit_price(0.845, config) == 0.85


def test_settle_position_calculates_binary_pnl_for_winner():
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.65,
        model_probability=0.80,
        edge=0.15,
        stake_usdc=10.0,
    )

    settled = settle_position(position, final_btc_price=102.0)

    assert settled.status == "CLOSED"
    assert settled.win is True
    assert round(settled.pnl_usdc, 4) == 5.3846


def test_calculate_order_shares_uses_stake_divided_by_price():
    assert calculate_order_shares(stake_usdc=5.0, price=0.80) == 6.25
    shares = calculate_order_shares(stake_usdc=5.0, price=0.72)
    assert round(shares, 4) == shares
    assert round(shares * 0.72, 2) == shares * 0.72


def test_decide_poly_odds_momentum_buys_up_when_up_strengthens_and_down_weakens():
    decision = decide_poly_odds_momentum(
        anchor_up=0.48,
        anchor_down=0.52,
        current_up=0.62,
        current_down=0.39,
        config=LiveConfig(
            strategy="poly-odds-momentum",
            odds_momentum_min_move=0.08,
            odds_momentum_opposite_move=0.05,
            min_contract_price=0.55,
            max_contract_price=0.75,
        ),
    )

    assert decision.direction == "UP"
    assert decision.contract_price == 0.62
    assert round(decision.edge, 4) == 0.14


def test_decide_poly_odds_momentum_holds_without_opposite_confirmation():
    decision = decide_poly_odds_momentum(
        anchor_up=0.48,
        anchor_down=0.52,
        current_up=0.62,
        current_down=0.50,
        config=LiveConfig(
            strategy="poly-odds-momentum",
            odds_momentum_min_move=0.08,
            odds_momentum_opposite_move=0.05,
        ),
    )

    assert decision.direction == "HOLD"


def test_decide_poly_75_breakout_buys_side_that_reaches_trigger():
    decision = decide_poly_75_breakout(
        current_up=0.76,
        current_down=0.23,
        config=LiveConfig(
            strategy="poly-75-breakout",
            poly_breakout_trigger_price=0.75,
            max_contract_price=0.78,
        ),
    )

    assert decision.direction == "UP"
    assert decision.contract_price == 0.76


def test_decide_poly_75_breakout_holds_when_price_is_above_max_entry():
    decision = decide_poly_75_breakout(
        current_up=0.84,
        current_down=0.15,
        config=LiveConfig(
            strategy="poly-75-breakout",
            poly_breakout_trigger_price=0.75,
            max_contract_price=0.78,
        ),
    )

    assert decision.direction == "HOLD"


def test_settle_position_by_contract_price_uses_polymarket_contract_not_binance():
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=0.0,
        target_price=0.5,
        contract_price=0.62,
        model_probability=0.62,
        edge=0.14,
        stake_usdc=10.0,
    )

    settled = settle_position_by_contract_price(position, final_contract_price=0.99, closed_ts=400)

    assert settled.status == "CLOSED"
    assert settled.win is True
    assert round(settled.pnl_usdc, 4) == 6.129
    assert settled.final_btc_price == 0.99


def test_prepare_paper_limit_entry_keeps_signal_pending_when_price_above_limit():
    now = datetime(2026, 5, 5, 12, 1, 10, tzinfo=timezone.utc)
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.68,
        model_probability=0.75,
        edge=0.07,
        stake_usdc=10.0,
    )

    pending = prepare_paper_limit_entry(position, LiveConfig(paper_limit_entry_price=0.55), now)

    assert pending.status == "PENDING_LIMIT"
    assert pending.contract_price == 0.55
    assert pending.signal_contract_price == 0.68
    assert pending.limit_entry_price == 0.55
    assert pending.edge == 0.20


def test_prepare_paper_limit_entry_fills_immediately_when_signal_price_is_inside_limit():
    now = datetime(2026, 5, 5, 12, 1, 10, tzinfo=timezone.utc)
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="DOWN",
        token_id="down-token",
        entry_ts=100,
        entry_btc_price=99.0,
        target_price=100.0,
        contract_price=0.54,
        model_probability=0.65,
        edge=0.11,
        stake_usdc=10.0,
    )

    opened = prepare_paper_limit_entry(position, LiveConfig(paper_limit_entry_price=0.55), now)

    assert opened.status == "OPEN"
    assert opened.contract_price == 0.55
    assert opened.order_status == "FILLED_LIMIT_IMMEDIATE"


def test_try_fill_paper_limit_entry_opens_when_contract_touches_limit(monkeypatch):
    now = datetime(2026, 5, 5, 12, 2, 0, tzinfo=timezone.utc)
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=int(now.timestamp()) - 120,
        event_end_ts=int(now.timestamp()) + 120,
        direction="UP",
        token_id="up-token",
        entry_ts=int(now.timestamp()) - 60,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.55,
        model_probability=0.75,
        edge=0.20,
        stake_usdc=10.0,
        status="PENDING_LIMIT",
        limit_entry_price=0.55,
        signal_contract_price=0.68,
    )
    monkeypatch.setattr(live, "fetch_buy_price", lambda token_id: 0.55)
    monkeypatch.setattr(live, "fetch_binance_price", lambda symbol: 102.0)

    opened = try_fill_paper_limit_entry(position, LiveConfig(paper_limit_entry_price=0.55), now)

    assert opened is not None
    assert opened.status == "OPEN"
    assert opened.entry_btc_price == 102.0
    assert opened.order_status == "FILLED_LIMIT"


def test_prepare_paper_realistic_entry_uses_current_price_plus_slippage(monkeypatch):
    now = datetime(2026, 5, 6, 12, 1, 10, tzinfo=timezone.utc)
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.60,
        model_probability=0.72,
        edge=0.12,
        stake_usdc=5.0,
    )
    monkeypatch.setattr(live, "fetch_buy_price", lambda token_id: 0.64)

    opened = prepare_paper_realistic_entry(
        position,
        LiveConfig(
            paper_realistic_entry=True,
            paper_realistic_price_slippage=0.01,
            paper_realistic_max_entry_price=0.67,
        ),
        now,
    )

    assert opened is not None
    assert opened.status == "OPEN"
    assert opened.contract_price == 0.65
    assert opened.signal_contract_price == 0.60
    assert opened.limit_entry_price == 0.67
    assert opened.edge == 0.07
    assert opened.shares == 7.6
    assert opened.order_status == "PAPER_REALISTIC_FILL"
    assert opened.order_response == "current_price=0.640; simulated_slippage=0.010; max_entry=0.670"


def test_prepare_paper_realistic_entry_skips_when_simulated_price_exceeds_cap(monkeypatch):
    now = datetime(2026, 5, 6, 12, 1, 10, tzinfo=timezone.utc)
    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="DOWN",
        token_id="down-token",
        entry_ts=100,
        entry_btc_price=99.0,
        target_price=100.0,
        contract_price=0.64,
        model_probability=0.74,
        edge=0.10,
        stake_usdc=5.0,
    )
    monkeypatch.setattr(live, "fetch_buy_price", lambda token_id: 0.66)

    opened = prepare_paper_realistic_entry(
        position,
        LiveConfig(
            paper_realistic_entry=True,
            paper_realistic_price_slippage=0.02,
            paper_realistic_max_entry_price=0.67,
        ),
        now,
    )

    assert opened is None


def test_real_shared_lock_blocks_second_strategy_in_same_market(tmp_path):
    lock_file = tmp_path / "real_shared_position_lock.json"
    config = LiveConfig(real_shared_lock_file=str(lock_file), real_shared_lock_scope="market")
    first = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.65,
        model_probability=0.80,
        edge=0.15,
        stake_usdc=5.0,
    )
    second = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="DOWN",
        token_id="down-token",
        entry_ts=101,
        entry_btc_price=99.0,
        target_price=100.0,
        contract_price=0.65,
        model_probability=0.80,
        edge=0.15,
        stake_usdc=5.0,
    )

    assert reserve_real_shared_position(first, config, now_ts=100)[0] is True
    reserved, key, existing = reserve_real_shared_position(second, config, now_ts=101)

    assert reserved is False
    assert key == "btc-updown-5m-1"
    assert existing["direction"] == "UP"

    release_real_shared_position(first, config)
    assert reserve_real_shared_position(second, config, now_ts=102)[0] is True


def test_first_minute_continuation_requires_direction_and_body():
    config = LiveConfig(strategy="first-minute-continuation", min_anchor_body_pct=0.0003)

    assert first_minute_continuation_direction(
        anchor_open=100.0,
        anchor_close=100.08,
        target_price=100.0,
        anchor_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) == "UP"

    assert first_minute_continuation_direction(
        anchor_open=100.0,
        anchor_close=99.98,
        target_price=100.0,
        anchor_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) is None


def test_first_minute_continuation_respects_allowed_directions():
    config = LiveConfig(
        strategy="first-minute-continuation",
        min_anchor_body_pct=0.0003,
        allowed_directions=("DOWN",),
    )

    assert first_minute_continuation_direction(
        anchor_open=100.0,
        anchor_close=100.08,
        target_price=100.0,
        anchor_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) is None

    assert first_minute_continuation_direction(
        anchor_open=100.0,
        anchor_close=99.90,
        target_price=100.0,
        anchor_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) == "DOWN"


def test_parse_allowed_directions_defaults_when_invalid():
    assert parse_allowed_directions("DOWN") == ("DOWN",)
    assert parse_allowed_directions("UP,DOWN") == ("UP", "DOWN")
    assert parse_allowed_directions("foo") == ("UP", "DOWN")


def test_parse_excluded_entry_hours_accepts_comma_separated_hours():
    assert parse_excluded_entry_hours("8,10,18,23") == (8, 10, 18, 23)
    assert parse_excluded_entry_hours(" 10 ") == (10,)
    assert parse_excluded_entry_hours("") == ()


def test_parse_excluded_entry_hours_rejects_invalid_hours():
    import pytest

    with pytest.raises(ValueError, match="0..23"):
        parse_excluded_entry_hours("24")

    with pytest.raises(ValueError, match="integer"):
        parse_excluded_entry_hours("10h")


def test_should_skip_entry_hour_uses_brt_hour():
    now = datetime(2026, 5, 5, 13, 11, 0, tzinfo=timezone.utc)
    config = LiveConfig(excluded_entry_hours_brt=(10,))

    assert should_skip_entry_hour(now, config)
    assert not should_skip_entry_hour(now, LiveConfig(excluded_entry_hours_brt=(9, 11)))


def test_load_credentials_env_uses_explicit_file(tmp_path, monkeypatch):
    for name in ("PK", "FUNDER", "API_KEY", "API_SECRET", "API_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env.real_first_minute"
    env_file.write_text(
        "\n".join(
            [
                "PK=0xfirst",
                "FUNDER=0xfunder",
                "API_KEY=key-first",
                "API_SECRET=secret-first",
                "API_PASSPHRASE=pass-first",
            ]
        ),
        encoding="utf-8",
    )

    load_credentials_env(LiveConfig(env_file=str(env_file)))

    assert os.getenv("PK") == "0xfirst"
    assert os.getenv("FUNDER") == "0xfunder"
    assert os.getenv("API_KEY") == "key-first"


def test_build_clob_client_uses_polynode_for_signature_type_3(monkeypatch):
    config = LiveConfig(real_signature_type=3)
    sentinel = object()
    monkeypatch.setattr(live, "load_credentials_env", lambda config: None)
    monkeypatch.setattr(live, "build_polynode_real_client", lambda config: sentinel)
    monkeypatch.setattr(live, "build_py_clob_real_client", lambda config: None)

    assert live.build_clob_client(config) is sentinel


def test_build_clob_client_uses_py_clob_for_non_deposit_wallet(monkeypatch):
    config = LiveConfig(real_signature_type=1)
    sentinel = object()
    monkeypatch.setattr(live, "load_credentials_env", lambda config: None)
    monkeypatch.setattr(live, "build_polynode_real_client", lambda config: None)
    monkeypatch.setattr(live, "build_py_clob_real_client", lambda config: sentinel)

    assert live.build_clob_client(config) is sentinel


def test_build_polynode_real_client_uses_ensure_ready_when_polynode_key_exists(monkeypatch):
    config = LiveConfig(trades_csv="real_poly_odds_momentum_60s_trades.csv")
    sentinel = object()

    class FakeReadyStatus:
        funder_address = "0xfunder"

    class FakeTrader:
        def __init__(self, trader_config):
            self.config = trader_config

        def ensure_ready(self, signer):
            assert signer == "0xpk"
            return FakeReadyStatus()

        def refresh_balance_allowance(self):
            return (True, 200)

    monkeypatch.setenv("PK", "0xpk")
    monkeypatch.setenv("FUNDER", "0xfunder")
    monkeypatch.setenv("POLYNODE_KEY", "pn_live_test")
    monkeypatch.setattr(live, "PolyNodeTrader", FakeTrader)
    monkeypatch.setattr(live, "PolyNodeRealOrderClient", lambda trader: sentinel)
    monkeypatch.setattr(live.asyncio, "run", lambda value: value)

    assert live.build_polynode_real_client(config) is sentinel


def test_build_polynode_real_client_falls_back_to_manual_link_without_polynode_key(monkeypatch):
    config = LiveConfig(trades_csv="real_poly_odds_momentum_60s_trades.csv")
    sentinel = object()

    class FakeSigner:
        address = "0xwallet"

    class FakeLinkResult:
        funder_address = "0xfunder"

    class FakeTrader:
        def __init__(self, trader_config):
            self.config = trader_config
            self.unlinked = None

        def unlink_wallet(self, address):
            self.unlinked = address

        def link_wallet(self, signer, type=None):
            assert signer == "0xpk"
            return FakeLinkResult()

    monkeypatch.setenv("PK", "0xpk")
    monkeypatch.setenv("FUNDER", "0xfunder")
    monkeypatch.delenv("POLYNODE_KEY", raising=False)
    monkeypatch.setattr(live, "PolyNodeTrader", FakeTrader)
    monkeypatch.setattr(live, "PolyNodeRealOrderClient", lambda trader: sentinel)
    monkeypatch.setattr(live, "polynode_normalize_signer", lambda pk, sig_type: FakeSigner())
    monkeypatch.setattr(live.asyncio, "run", lambda value: value)

    assert live.build_polynode_real_client(config) is sentinel


def test_place_real_buy_order_accepts_backend_wrapper_response():
    class FakeBackend:
        def post_limit_buy(self, token_id, limit_price, shares, market, order_type_name):
            assert token_id == "up-token"
            assert round(limit_price, 3) == 0.65
            assert shares > 0
            assert order_type_name == "FOK"
            return {"success": True, "orderID": "abc123", "status": "LIVE"}

    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.65,
        model_probability=0.80,
        edge=0.15,
        stake_usdc=10.0,
    )
    market = live.LiveMarket(
        slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        up_token_id="up-token",
        down_token_id="down-token",
        accepting_orders=True,
        tick_size="0.01",
        neg_risk=False,
        order_min_size=5.0,
    )
    config = LiveConfig(real_mode=True, real_confirmed=True, real_order_type="FOK")

    opened = place_real_buy_order(FakeBackend(), position, market, config)

    assert opened.execution_mode == "REAL"
    assert opened.order_id == "abc123"
    assert opened.order_status == "POSTED"




def test_place_real_buy_order_marks_submission_exception_uncertain():
    import pytest

    class FailingBackend:
        def post_limit_buy(self, token_id, limit_price, shares, market, order_type_name):
            raise TimeoutError("request timed out")

    position = PaperPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="UP",
        token_id="up-token",
        entry_ts=100,
        entry_btc_price=101.0,
        target_price=100.0,
        contract_price=0.65,
        model_probability=0.80,
        edge=0.15,
        stake_usdc=10.0,
    )
    market = live.LiveMarket(
        slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        up_token_id="up-token",
        down_token_id="down-token",
        accepting_orders=True,
        tick_size="0.01",
        neg_risk=False,
        order_min_size=5.0,
    )
    config = LiveConfig(real_mode=True, real_confirmed=True, real_order_type="FAK")

    with pytest.raises(RealOrderSubmissionUncertain, match="ordem real sem confirmacao"):
        place_real_buy_order(FailingBackend(), position, market, config)


def test_target_rejection_detects_wick_reclaim_and_rejection():
    config = LiveConfig(strategy="target-rejection", min_rejection_wick_ratio=0.35)

    assert target_rejection_direction(
        candle_open=100.10,
        candle_high=100.50,
        candle_low=99.70,
        candle_close=100.40,
        target_price=100.0,
        candle_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) == "UP"

    assert target_rejection_direction(
        candle_open=100.10,
        candle_high=100.40,
        candle_low=99.60,
        candle_close=99.85,
        target_price=100.0,
        candle_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) == "DOWN"

    assert target_rejection_direction(
        candle_open=99.95,
        candle_high=100.05,
        candle_low=99.90,
        candle_close=100.02,
        target_price=100.0,
        candle_volume=10.0,
        volume_sma=10.0,
        config=config,
    ) is None


def test_momentum_confirmed_requires_ema_body_and_target_side():
    import pandas as pd

    event_start = datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc)
    rows = []
    price = 99.0
    for index in range(25):
        ts = event_start - pd.Timedelta(minutes=24 - index)
        open_price = price
        close_price = price + 0.08
        rows.append(
            {
                "ts": ts,
                "close_time": ts + pd.Timedelta(seconds=59),
                "open": open_price,
                "high": close_price + 0.02,
                "low": open_price - 0.02,
                "close": close_price,
                "volume": 10.0,
                "volume_sma_20": 10.0,
            }
        )
        price = close_price

    df = pd.DataFrame(rows)
    config = LiveConfig(strategy="momentum-confirmed", min_anchor_body_pct=0.0003)

    assert (
        momentum_confirmed_direction(
            df=df,
            event_start_ts=int(event_start.timestamp()),
            now=event_start + pd.Timedelta(minutes=1, seconds=5),
            target_price=100.0,
            config=config,
        )
        == "UP"
    )

    assert (
        momentum_confirmed_direction(
            df=df,
            event_start_ts=int(event_start.timestamp()),
            now=event_start + pd.Timedelta(minutes=1, seconds=5),
            target_price=200.0,
            config=config,
        )
        is None
    )


def test_ema_1s_trend_detects_aligned_uptrend_and_downtrend():
    import pandas as pd

    up_df = pd.DataFrame({"close": [100.0 + index * 0.05 for index in range(650)]})
    down_df = pd.DataFrame({"close": [140.0 - index * 0.05 for index in range(650)]})
    config = LiveConfig(
        strategy="ema-1s-trend",
        ema_fast_seconds=60,
        ema_mid_seconds=300,
        ema_slow_seconds=600,
        ema_slope_lookback_seconds=15,
        min_ema_gap_usd=0.5,
        min_ema_slope_usd=0.1,
        max_price_ema_fast_distance_usd=10.0,
    )

    assert ema_1s_trend_direction(up_df, current_price=132.50, config=config) == "UP"
    assert ema_1s_trend_direction(down_df, current_price=107.50, config=config) == "DOWN"


def test_ema_1s_trend_rejects_chop_and_overextended_price():
    import pandas as pd

    chop_df = pd.DataFrame({"close": [100.0 + ((index % 2) * 0.1) for index in range(650)]})
    trend_df = pd.DataFrame({"close": [100.0 + index * 0.05 for index in range(650)]})
    config = LiveConfig(
        strategy="ema-1s-trend",
        ema_fast_seconds=60,
        ema_mid_seconds=300,
        ema_slow_seconds=600,
        ema_slope_lookback_seconds=15,
        min_ema_gap_usd=0.5,
        min_ema_slope_usd=0.1,
        max_price_ema_fast_distance_usd=1.0,
    )

    assert ema_1s_trend_direction(chop_df, current_price=100.05, config=config) is None
    assert ema_1s_trend_direction(trend_df, current_price=150.0, config=config) is None


def test_late_window_requires_time_window_and_distance():
    config = LiveConfig(
        strategy="late-window",
        min_abs_z=0.8,
        late_entry_min_remaining=45,
        late_entry_max_remaining=75,
    )

    assert (
        late_window_direction(
            current_price=101.0,
            target_price=100.0,
            seconds_remaining=60,
            z_score=0.9,
            config=config,
        )
        == "UP"
    )

    assert (
        late_window_direction(
            current_price=99.0,
            target_price=100.0,
            seconds_remaining=60,
            z_score=-0.9,
            config=config,
        )
        == "DOWN"
    )

    assert (
        late_window_direction(
            current_price=101.0,
            target_price=100.0,
            seconds_remaining=90,
            z_score=1.2,
            config=config,
        )
        is None
    )


def test_mispricing_contrarian_buys_only_cheap_side_with_edge():
    config = LiveConfig(
        strategy="mispricing-contrarian",
        min_contract_price=0.10,
        max_contract_price=0.49,
        edge_min=0.08,
        contrarian_favorite_min_price=0.70,
    )

    decision = decide_mispricing_contrarian(
        prob_up=0.32,
        ask_up=0.22,
        ask_down=0.76,
        config=config,
    )
    assert decision.direction == "UP"
    assert decision.edge == 0.10

    decision = decide_mispricing_contrarian(
        prob_up=0.78,
        ask_up=0.82,
        ask_down=0.14,
        config=config,
    )
    assert decision.direction == "DOWN"
    assert decision.edge == 0.08

    decision = decide_mispricing_contrarian(
        prob_up=0.30,
        ask_up=0.26,
        ask_down=0.60,
        config=config,
    )
    assert decision.direction == "HOLD"

    assert (
        late_window_direction(
            current_price=101.0,
            target_price=100.0,
            seconds_remaining=60,
            z_score=0.4,
            config=config,
        )
        is None
    )


def test_bollinger_rsi_reversal_detects_green_exhaustion_then_red_reversal():
    import pandas as pd

    rows = []
    for index in range(19):
        base = 100.0 + (index * 0.02)
        rows.append(
            {
                "open": base,
                "high": base + 0.04,
                "low": base - 0.03,
                "close": base + 0.03,
                "cor": 1,
            }
        )

    rows.append(
        {
            "open": 100.70,
            "high": 106.00,
            "low": 100.65,
            "close": 105.80,
            "cor": 1,
        }
    )
    rows.append(
        {
            "open": 105.80,
            "high": 105.90,
            "low": 101.90,
            "close": 102.10,
            "cor": -1,
        }
    )
    rows.append(
        {
            "open": 102.10,
            "high": 102.20,
            "low": 101.80,
            "close": 101.95,
            "cor": -1,
        }
    )

    df = pd.DataFrame(rows)
    assert bollinger_rsi_reversal_down_signal(df, rsi_threshold_sell=70.0) is True


def test_bollinger_rsi_reversal_rejects_when_no_red_confirmation():
    import pandas as pd

    rows = []
    for index in range(19):
        base = 100.0 + (index * 0.02)
        rows.append(
            {
                "open": base,
                "high": base + 0.04,
                "low": base - 0.03,
                "close": base + 0.03,
                "cor": 1,
            }
        )

    rows.append(
        {
            "open": 100.70,
            "high": 106.00,
            "low": 100.65,
            "close": 105.80,
            "cor": 1,
        }
    )
    rows.append(
        {
            "open": 105.80,
            "high": 106.10,
            "low": 105.70,
            "close": 105.95,
            "cor": 1,
        }
    )
    rows.append(
        {
            "open": 105.95,
            "high": 106.00,
            "low": 105.50,
            "close": 105.70,
            "cor": -1,
        }
    )

    df = pd.DataFrame(rows)
    assert bollinger_rsi_reversal_down_signal(df, rsi_threshold_sell=70.0) is False
