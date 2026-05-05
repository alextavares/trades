import pandas as pd

from backtest_polymarket_5m_edge import PolymarketMarketHistory, PricePoint
from backtest_polymarket_late_lottery import (
    BacktestLotteryConfig,
    find_lottery_trade_for_event,
)


def make_market_df() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-05-01T09:59:00Z")
    prices = [9998.0, 10000.0, 10010.0, 10012.0, 10015.0, 10018.0, 10020.0]
    for index, close in enumerate(prices):
        ts = start + pd.Timedelta(minutes=index)
        rows.append(
            {
                "ts": ts,
                "close_time": ts + pd.Timedelta(seconds=59),
                "open": prices[index - 1] if index else close,
                "high": close + 5,
                "low": close - 5,
                "close": close,
                "volume": 100.0,
                "log_return": 0.001 if index else None,
            }
        )
    return pd.DataFrame(rows)


def test_find_lottery_trade_for_event_uses_first_cheap_signal():
    df = make_market_df()
    history = PolymarketMarketHistory(
        event_start_ts=int(pd.Timestamp("2026-05-01T10:00:00Z").timestamp()),
        up_token_id="up",
        down_token_id="down",
        up_prices=(
            PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.92),
            PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:25Z").timestamp()), price=0.95),
        ),
        down_prices=(
            PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.08),
            PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:25Z").timestamp()), price=0.05),
        ),
    )
    config = BacktestLotteryConfig(
        stake_usdc=1.0,
        min_seconds_remaining=5,
        max_seconds_remaining=60,
        min_cheap_price=0.01,
        max_cheap_price=0.10,
        favorite_min_price=0.90,
        max_abs_distance_usd=80.0,
        max_abs_z=5.0,
        lookback_minutes=3,
        scan_step_seconds=5,
    )

    trade = find_lottery_trade_for_event(df, history, config)

    assert trade is not None
    assert trade["direction"] == "DOWN"
    assert trade["entry_ts"] == int(pd.Timestamp("2026-05-01T10:04:05Z").timestamp())
    assert trade["contract_price"] == 0.08


def test_find_lottery_trade_for_event_returns_none_without_valid_signal():
    df = make_market_df()
    history = PolymarketMarketHistory(
        event_start_ts=int(pd.Timestamp("2026-05-01T10:00:00Z").timestamp()),
        up_token_id="up",
        down_token_id="down",
        up_prices=(PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.80),),
        down_prices=(PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.20),),
    )
    config = BacktestLotteryConfig(
        min_cheap_price=0.01,
        max_cheap_price=0.10,
        favorite_min_price=0.90,
        max_abs_distance_usd=80.0,
        max_abs_z=5.0,
        lookback_minutes=3,
        scan_step_seconds=5,
    )

    assert find_lottery_trade_for_event(df, history, config) is None


def test_find_lottery_trade_for_event_respects_allowed_directions():
    df = make_market_df()
    history = PolymarketMarketHistory(
        event_start_ts=int(pd.Timestamp("2026-05-01T10:00:00Z").timestamp()),
        up_token_id="up",
        down_token_id="down",
        up_prices=(PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.92),),
        down_prices=(PricePoint(timestamp=int(pd.Timestamp("2026-05-01T10:04:10Z").timestamp()), price=0.08),),
    )
    config = BacktestLotteryConfig(
        min_cheap_price=0.01,
        max_cheap_price=0.10,
        favorite_min_price=0.90,
        max_abs_distance_usd=80.0,
        max_abs_z=5.0,
        lookback_minutes=3,
        scan_step_seconds=5,
        allowed_directions=("UP",),
    )

    assert find_lottery_trade_for_event(df, history, config) is None
