import pandas as pd

from backtest_polymarket_5m_matrix import (
    apply_entry_slippage,
    first_minute_selective_direction,
    resolve_contract_price,
    summarize_strategy_result,
)
from backtest_polymarket_5m_edge import PolymarketMarketHistory, PricePoint


def make_candle(open_price, high_price, low_price, close_price, volume=100.0, volume_sma_20=100.0):
    return pd.Series(
        {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "volume_sma_20": volume_sma_20,
        }
    )


def test_first_minute_selective_accepts_confirmed_up_breakout():
    anchor = make_candle(100.0, 100.8, 99.9, 100.6)
    confirm = make_candle(100.6, 100.9, 100.45, 100.7)

    assert (
        first_minute_selective_direction(
            anchor=anchor,
            confirm=confirm,
            target_price=100.0,
            min_anchor_body_pct=0.003,
            max_retrace_pct=0.35,
            min_target_distance_pct=0.002,
            volume_multiplier=0.0,
        )
        == "UP"
    )


def test_first_minute_selective_rejects_deep_retrace():
    anchor = make_candle(100.0, 100.8, 99.9, 100.6)
    confirm = make_candle(100.6, 100.7, 100.1, 100.2)

    assert (
        first_minute_selective_direction(
            anchor=anchor,
            confirm=confirm,
            target_price=100.0,
            min_anchor_body_pct=0.003,
            max_retrace_pct=0.35,
            min_target_distance_pct=0.002,
            volume_multiplier=0.0,
        )
        is None
    )


def test_summarize_strategy_result_handles_empty_trades():
    row = summarize_strategy_result(
        strategy="edge-regime",
        params={"edge_min": 0.08},
        trades=pd.DataFrame(),
        stake_usdc=10.0,
    )

    assert row["strategy"] == "edge-regime"
    assert row["trades"] == 0
    assert row["pnl_usdc"] == 0.0
    assert row["edge_min"] == 0.08


def test_apply_entry_slippage_adds_penalty_to_contract_price():
    assert apply_entry_slippage(0.55, 0.02, 0.85) == 0.57


def test_apply_entry_slippage_rejects_prices_above_cap():
    assert apply_entry_slippage(0.84, 0.02, 0.85) is None


def test_resolve_contract_price_uses_directional_polymarket_history():
    history = PolymarketMarketHistory(
        event_start_ts=1000,
        up_token_id="up",
        down_token_id="down",
        up_prices=(PricePoint(timestamp=1120, price=0.61),),
        down_prices=(PricePoint(timestamp=1120, price=0.39),),
    )

    price = resolve_contract_price(
        direction="UP",
        contract_price=0.55,
        use_polymarket_history=True,
        history=history,
        entry_ts=1122,
        max_polymarket_price_distance=10,
        entry_slippage=0.01,
        min_contract_price=0.50,
        max_contract_price=0.75,
    )

    assert price == 0.62


def test_resolve_contract_price_returns_none_when_history_side_is_missing():
    history = PolymarketMarketHistory(
        event_start_ts=1000,
        up_token_id="up",
        down_token_id="down",
        up_prices=(),
        down_prices=(PricePoint(timestamp=1120, price=0.39),),
    )

    price = resolve_contract_price(
        direction="UP",
        contract_price=0.55,
        use_polymarket_history=True,
        history=history,
        entry_ts=1122,
        max_polymarket_price_distance=10,
        entry_slippage=0.01,
        min_contract_price=0.50,
        max_contract_price=0.75,
    )

    assert price is None
