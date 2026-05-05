#!/usr/bin/env python3
"""Backtest for late cheap-side lottery entries on BTC Up/Down 5m."""

from __future__ import annotations

import argparse
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from backtest_polymarket_5m_edge import (
    PolymarketMarketHistory,
    fetch_binance_1m,
    fetch_polymarket_market_history,
    max_drawdown,
    nearest_price_at,
)
from paper_polymarket_late_lottery import (
    LotteryConfig,
    choose_lottery_entry,
    parse_allowed_directions,
    settle_lottery_position,
)


@dataclass(frozen=True)
class BacktestLotteryConfig(LotteryConfig):
    limit: int = 3000
    scan_step_seconds: int = 5
    max_price_distance_seconds: int = 5
    save_csv: str = ""


def price_at_or_before(df: pd.DataFrame, target_ts: int) -> float | None:
    ts = pd.to_datetime(target_ts, unit="s", utc=True)
    eligible = df[df["close_time"] <= ts]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["close"])


def sigma_remaining_at(df: pd.DataFrame, target_ts: int, lookback_minutes: int, seconds_remaining: int) -> float:
    ts = pd.to_datetime(target_ts, unit="s", utc=True)
    eligible = df[df["close_time"] <= ts]
    if eligible.empty:
        return 0.0
    returns = eligible["log_return"].dropna().tail(lookback_minutes)
    std_1m = float(returns.std(ddof=0)) if not returns.empty else 0.0
    if std_1m <= 0 or not math.isfinite(std_1m):
        return 0.0
    current_price = float(eligible.iloc[-1]["close"])
    return current_price * std_1m * math.sqrt(max(seconds_remaining, 1) / 60.0)


def find_lottery_trade_for_event(
    df: pd.DataFrame,
    history: PolymarketMarketHistory,
    config: BacktestLotteryConfig,
) -> dict | None:
    event_start_ts = history.event_start_ts
    event_end_ts = event_start_ts + 300
    target_price = price_at_or_before(df, event_start_ts - 1)
    final_price = price_at_or_before(df, event_end_ts)
    if target_price is None or final_price is None:
        return None

    start_ts = event_end_ts - config.max_seconds_remaining
    stop_ts = event_end_ts - config.min_seconds_remaining
    for entry_ts in range(start_ts, stop_ts + 1, max(config.scan_step_seconds, 1)):
        seconds_remaining = event_end_ts - entry_ts
        current_price = price_at_or_before(df, entry_ts)
        if current_price is None:
            continue
        sigma_remaining = sigma_remaining_at(df, entry_ts, config.lookback_minutes, seconds_remaining)
        up_price = nearest_price_at(history.up_prices, entry_ts, config.max_price_distance_seconds)
        down_price = nearest_price_at(history.down_prices, entry_ts, config.max_price_distance_seconds)
        if up_price is None or down_price is None:
            continue

        entry = choose_lottery_entry(
            current_price=current_price,
            target_price=target_price,
            sigma_remaining=sigma_remaining,
            up_price=up_price,
            down_price=down_price,
            seconds_remaining=seconds_remaining,
            config=config,
        )
        if entry is None:
            continue

        settled = settle_lottery_position(
            position=dict_to_position(
                market_slug=f"btc-updown-5m-{event_start_ts}",
                event_start_ts=event_start_ts,
                event_end_ts=event_end_ts,
                direction=entry.direction,
                entry_ts=entry_ts,
                entry_btc_price=current_price,
                target_price=target_price,
                contract_price=entry.contract_price,
                favorite_price=entry.favorite_price,
                stake_usdc=config.stake_usdc,
                seconds_remaining=seconds_remaining,
                distance_usd=entry.distance_usd,
                z_score=entry.z_score,
                sigma_remaining=entry.sigma_remaining,
            ),
            final_btc_price=final_price,
            closed_ts=event_end_ts,
        )
        row = asdict(settled)
        row["market_start"] = pd.to_datetime(event_start_ts, unit="s", utc=True)
        row["entry_time"] = pd.to_datetime(entry_ts, unit="s", utc=True)
        row["price_source"] = "polymarket_history"
        row["scan_step_seconds"] = config.scan_step_seconds
        return row
    return None


def dict_to_position(**kwargs):
    from paper_polymarket_late_lottery import LotteryPosition

    return LotteryPosition(
        market_slug=kwargs["market_slug"],
        event_start_ts=kwargs["event_start_ts"],
        event_end_ts=kwargs["event_end_ts"],
        direction=kwargs["direction"],
        entry_ts=kwargs["entry_ts"],
        entry_btc_price=round(kwargs["entry_btc_price"], 4),
        target_price=round(kwargs["target_price"], 4),
        contract_price=kwargs["contract_price"],
        favorite_price=kwargs["favorite_price"],
        stake_usdc=kwargs["stake_usdc"],
        seconds_remaining=kwargs["seconds_remaining"],
        distance_usd=kwargs["distance_usd"],
        z_score=kwargs["z_score"],
        sigma_remaining=kwargs["sigma_remaining"],
    )


def run_backtest(config: BacktestLotteryConfig) -> pd.DataFrame:
    df = fetch_binance_1m(config.symbol, config.limit).copy()
    df["log_return"] = np.log(df["close"]).diff()
    trades = []

    for block_start in range(config.lookback_minutes + 1, len(df) - 5):
        ts = df["ts"].iloc[block_start]
        if int(ts.minute) % 5 != 0:
            continue

        event_start_ts = int(ts.timestamp())
        history = fetch_polymarket_market_history(event_start_ts)
        if history is None:
            continue

        trade = find_lottery_trade_for_event(df, history, config)
        if trade is not None:
            trades.append(trade)

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> None:
    if trades.empty:
        print("Nenhum trade encontrado.")
        return
    ordered = trades.sort_values("entry_time").reset_index(drop=True)
    pnl = ordered["pnl_usdc"].fillna(0.0)
    cumulative = pnl.cumsum()
    print("=== BACKTEST LATE LOTTERY BTC UP/DOWN 5M ===")
    print(f"Trades: {len(ordered)}")
    print(f"Win rate: {ordered['win'].mean() * 100.0:.2f}%")
    print(f"PnL total: {pnl.sum():.4f} USDC")
    print(f"PnL medio: {pnl.mean():.4f} USDC")
    print(f"Max drawdown: {max_drawdown(cumulative):.4f} USDC")
    print("\n--- Por faixa de preco do contrato ---")
    bands = pd.cut(
        ordered["contract_price"],
        bins=[0.0, 0.03, 0.05, 0.10, 1.0],
        labels=["0-3c", "3-5c", "5-10c", "10c+"],
        include_lowest=True,
    )
    by_band = ordered.groupby(bands, observed=False).agg(
        trades=("win", "size"),
        win_rate=("win", "mean"),
        pnl_usdc=("pnl_usdc", "sum"),
    )
    by_band["win_rate"] *= 100.0
    print(by_band.round(4).to_string())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest da estrategia late lottery BTC 5m.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--stake", type=float, default=1.0)
    parser.add_argument("--min-seconds-remaining", type=int, default=5)
    parser.add_argument("--max-seconds-remaining", type=int, default=60)
    parser.add_argument("--min-cheap-price", type=float, default=0.01)
    parser.add_argument("--max-cheap-price", type=float, default=0.10)
    parser.add_argument("--favorite-min-price", type=float, default=0.90)
    parser.add_argument("--max-abs-distance-usd", type=float, default=80.0)
    parser.add_argument("--max-abs-z", type=float, default=5.0)
    parser.add_argument("--lookback-minutes", type=int, default=30)
    parser.add_argument("--allowed-directions", default="UP,DOWN")
    parser.add_argument("--scan-step-seconds", type=int, default=5)
    parser.add_argument("--max-price-distance-seconds", type=int, default=5)
    parser.add_argument("--save-csv", default="")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = BacktestLotteryConfig(
        symbol=args.symbol,
        stake_usdc=args.stake,
        min_seconds_remaining=args.min_seconds_remaining,
        max_seconds_remaining=args.max_seconds_remaining,
        min_cheap_price=args.min_cheap_price,
        max_cheap_price=args.max_cheap_price,
        favorite_min_price=args.favorite_min_price,
        max_abs_distance_usd=args.max_abs_distance_usd,
        max_abs_z=args.max_abs_z,
        lookback_minutes=args.lookback_minutes,
        allowed_directions=parse_allowed_directions(args.allowed_directions),
        limit=args.limit,
        scan_step_seconds=args.scan_step_seconds,
        max_price_distance_seconds=args.max_price_distance_seconds,
        save_csv=args.save_csv,
    )
    trades = run_backtest(config)
    summarize(trades)
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nTrades salvos em: {args.save_csv}")


if __name__ == "__main__":
    main()
