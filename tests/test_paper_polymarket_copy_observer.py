from paper_polymarket_copy_observer import (
    CopyConfig,
    CopyObservation,
    CopyPaperTrade,
    normalize_trade,
    observation_to_paper_trade,
    settle_copy_trade,
    should_copy_trade,
    trade_key,
    winning_outcome_from_event,
)


def test_trade_key_prefers_transaction_hash_and_asset():
    trade = {
        "transactionHash": "0xabc",
        "asset": "123",
        "timestamp": 100,
        "proxyWallet": "0xwallet",
    }

    assert trade_key(trade) == "0xabc:123"


def test_normalize_trade_extracts_expected_fields():
    trade = {
        "proxyWallet": "0xwallet",
        "side": "BUY",
        "asset": "123",
        "conditionId": "0xcondition",
        "size": 10,
        "price": 0.55,
        "timestamp": 100,
        "title": "Market title",
        "slug": "market-slug",
        "outcome": "Yes",
        "name": "Trader",
        "transactionHash": "0xabc",
    }

    normalized = normalize_trade(trade)

    assert normalized["wallet"] == "0xwallet"
    assert normalized["side"] == "BUY"
    assert normalized["asset"] == "123"
    assert normalized["market_slug"] == "market-slug"
    assert normalized["trader_price"] == 0.55


def test_should_copy_trade_accepts_fresh_buy_when_current_price_is_close():
    config = CopyConfig(max_lag_seconds=60, max_price_worse=0.03, max_contract_price=0.85)

    decision, reason = should_copy_trade(
        side="BUY",
        trader_price=0.55,
        current_buy_price=0.57,
        lag_seconds=20,
        config=config,
    )

    assert decision is True
    assert reason == "copy_ok"


def test_should_copy_trade_rejects_stale_expensive_or_non_buy_trades():
    config = CopyConfig(max_lag_seconds=60, max_price_worse=0.03, max_contract_price=0.85)

    assert should_copy_trade("SELL", 0.55, 0.54, 20, config) == (False, "not_buy")
    assert should_copy_trade("BUY", 0.55, 0.54, 90, config) == (False, "stale")
    assert should_copy_trade("BUY", 0.55, None, 20, config) == (False, "no_current_price")
    assert should_copy_trade("BUY", 0.55, 0.90, 20, config) == (False, "price_above_max")
    assert should_copy_trade("BUY", 0.55, 0.60, 20, config) == (False, "price_moved")


def test_winning_outcome_from_event_uses_resolved_outcome_prices():
    event = {
        "closed": True,
        "markets": [
            {
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["0", "1"]',
            }
        ],
    }

    assert winning_outcome_from_event(event) == "Down"


def test_observation_to_paper_trade_maps_copy_signal():
    observation = CopyObservation(
        observed_time_utc="2026-05-01T20:11:00+00:00",
        observed_ts=1000,
        wallet="0xwallet",
        trader_name="Trader",
        leaderboard_rank="1",
        leaderboard_pnl=100.0,
        leaderboard_volume=1000.0,
        category="CRYPTO",
        time_period="WEEK",
        side="BUY",
        asset="123",
        condition_id="0xcondition",
        market_slug="btc-updown-5m-1",
        title="BTC",
        outcome="Up",
        trade_ts=990,
        lag_seconds=10,
        trader_price=0.50,
        current_buy_price=0.52,
        price_diff=0.02,
        copy_decision="COPY",
        reason="copy_ok",
        trader_size=20.0,
        simulated_stake_usdc=5.0,
        simulated_shares=9.615385,
        transaction_hash="0xabc",
    )

    trade = observation_to_paper_trade(observation)

    assert trade.trade_id == "0xabc:123"
    assert trade.direction == "Up"
    assert trade.contract_price == 0.52
    assert trade.status == "OPEN"


def test_settle_copy_trade_calculates_binary_pnl():
    trade = CopyPaperTrade(
        trade_id="1",
        market_slug="btc-updown-5m-1",
        direction="Up",
        contract_price=0.50,
        stake_usdc=5.0,
        shares=10.0,
        entry_time_utc="2026-05-01T20:11:00+00:00",
        entry_ts=1000,
        wallet="0xwallet",
        trader_name="Trader",
        title="BTC",
        outcome="Up",
        asset="123",
        transaction_hash="0xabc",
    )

    won = settle_copy_trade(trade, winning_outcome="Up", closed_time_utc="2026-05-01T20:15:00+00:00")
    lost = settle_copy_trade(trade, winning_outcome="Down", closed_time_utc="2026-05-01T20:15:00+00:00")

    assert won.status == "CLOSED"
    assert won.win is True
    assert won.pnl_usdc == 5.0
    assert lost.win is False
    assert lost.pnl_usdc == -5.0
