#!/usr/bin/env python3
"""Matrix backtests for BTC Up/Down 5m Polymarket ideas.

The matrix compares two families:
- edge-regime: the existing edge model with stricter regime filters.
- first-minute-selective: first-minute continuation that requires a second
  minute confirmation and rejects deep retraces.
"""

from __future__ import annotations

import argparse
import itertools
import math
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_polymarket_5m_edge import (
    PolymarketMarketHistory,
    add_features,
    binary_trade_pnl,
    estimate_momentum_score,
    estimate_sigma_remaining,
    estimate_up_probability,
    fetch_polymarket_market_history,
    fetch_binance_1m,
    max_drawdown,
    nearest_price_at,
    parse_float_list,
    run_backtest,
)


def first_minute_selective_direction(
    anchor: pd.Series,
    confirm: pd.Series,
    target_price: float,
    min_anchor_body_pct: float,
    max_retrace_pct: float,
    min_target_distance_pct: float,
    volume_multiplier: float,
) -> str | None:
    """Return continuation side when first candle breaks and second confirms."""
    anchor_open = float(anchor["open"])
    anchor_close = float(anchor["close"])
    confirm_close = float(confirm["close"])
    body = anchor_close - anchor_open
    body_abs = abs(body)
    body_pct = body_abs / max(anchor_open, 1e-9)
    if body_pct < min_anchor_body_pct:
        return None

    target_distance_pct = abs(anchor_close - target_price) / max(target_price, 1e-9)
    if target_distance_pct < min_target_distance_pct:
        return None

    volume_sma = float(anchor.get("volume_sma_20", 0.0) or 0.0)
    if not math.isfinite(volume_sma):
        volume_sma = 0.0
    if volume_multiplier > 0 and volume_sma > 0:
        if float(anchor["volume"]) < volume_sma * volume_multiplier:
            return None

    if body > 0 and anchor_close > target_price and confirm_close > target_price:
        retrace = max(0.0, anchor_close - confirm_close) / body_abs
        return "UP" if retrace <= max_retrace_pct else None

    if body < 0 and anchor_close < target_price and confirm_close < target_price:
        retrace = max(0.0, confirm_close - anchor_close) / body_abs
        return "DOWN" if retrace <= max_retrace_pct else None

    return None


def apply_entry_slippage(
    contract_price: float,
    entry_slippage: float,
    max_contract_price: float,
) -> float | None:
    """Apply a conservative buy-side slippage penalty."""
    adjusted_price = round(contract_price + max(entry_slippage, 0.0), 10)
    if adjusted_price > max_contract_price:
        return None
    return adjusted_price


def resolve_contract_price(
    direction: str,
    contract_price: float,
    use_polymarket_history: bool,
    history: PolymarketMarketHistory | None,
    entry_ts: int,
    max_polymarket_price_distance: int,
    entry_slippage: float,
    min_contract_price: float,
    max_contract_price: float,
) -> float | None:
    """Resolve entry price from simulated or historical Polymarket side price."""
    base_contract_price = contract_price
    if use_polymarket_history:
        if history is None:
            return None
        points = history.up_prices if direction == "UP" else history.down_prices
        historical_price = nearest_price_at(
            points,
            target_ts=entry_ts,
            max_distance_seconds=max_polymarket_price_distance,
        )
        if historical_price is None:
            return None
        base_contract_price = historical_price

    if base_contract_price < min_contract_price or base_contract_price > max_contract_price:
        return None
    return apply_entry_slippage(base_contract_price, entry_slippage, max_contract_price)


def run_first_minute_selective_backtest(
    df: pd.DataFrame,
    contract_prices: list[float],
    lookback_minutes: int,
    momentum_minutes: int,
    momentum_weight: float,
    edge_min: float,
    min_contract_price: float,
    max_contract_price: float,
    stake_usdc: float,
    min_anchor_body_pct: float,
    max_retrace_pct: float,
    min_target_distance_pct: float,
    volume_multiplier: float,
    entry_slippage: float = 0.0,
    use_polymarket_history: bool = False,
    max_polymarket_price_distance: int = 75,
) -> pd.DataFrame:
    """Backtest first-minute continuation with a second-minute confirmation."""
    candles = add_features(df)
    trades: list[dict[str, Any]] = []
    polymarket_cache: dict[int, PolymarketMarketHistory | None] = {}

    for block_start in range(lookback_minutes + 1, len(candles) - 5):
        ts = candles["ts"].iloc[block_start]
        if int(ts.minute) % 5 != 0:
            continue
        if block_start + 4 >= len(candles):
            continue

        target_price = float(candles["close"].iloc[block_start - 1])
        anchor = candles.iloc[block_start]
        confirm = candles.iloc[block_start + 1]
        direction = first_minute_selective_direction(
            anchor=anchor,
            confirm=confirm,
            target_price=target_price,
            min_anchor_body_pct=min_anchor_body_pct,
            max_retrace_pct=max_retrace_pct,
            min_target_distance_pct=min_target_distance_pct,
            volume_multiplier=volume_multiplier,
        )
        if direction is None:
            continue

        entry_idx = block_start + 1
        entry_ts = int(candles["close_time"].iloc[entry_idx].timestamp())
        current_price = float(candles["close"].iloc[entry_idx])
        final_price = float(candles["close"].iloc[block_start + 4])
        sigma_remaining = estimate_sigma_remaining(
            df=candles,
            entry_idx=entry_idx,
            lookback_minutes=lookback_minutes,
            remaining_minutes=3,
        )
        if sigma_remaining <= 0 or not math.isfinite(sigma_remaining):
            continue

        momentum_score = estimate_momentum_score(
            df=candles,
            entry_idx=entry_idx,
            momentum_minutes=momentum_minutes,
            lookback_minutes=lookback_minutes,
        )
        prob_up = estimate_up_probability(
            current_price=current_price,
            target_price=target_price,
            sigma_remaining=sigma_remaining,
            momentum_score=momentum_score,
            momentum_weight=momentum_weight,
        )
        side_probability = prob_up if direction == "UP" else 1.0 - prob_up

        event_start_ts = int(ts.timestamp())
        history: PolymarketMarketHistory | None = None
        if use_polymarket_history:
            if event_start_ts not in polymarket_cache:
                polymarket_cache[event_start_ts] = fetch_polymarket_market_history(event_start_ts)
            history = polymarket_cache[event_start_ts]

        price_candidates = [float("nan")] if use_polymarket_history else contract_prices
        for contract_price in price_candidates:
            adjusted_contract_price = resolve_contract_price(
                direction=direction,
                contract_price=contract_price,
                use_polymarket_history=use_polymarket_history,
                history=history,
                entry_ts=entry_ts,
                max_polymarket_price_distance=max_polymarket_price_distance,
                entry_slippage=entry_slippage,
                min_contract_price=min_contract_price,
                max_contract_price=max_contract_price,
            )
            if adjusted_contract_price is None:
                continue
            edge = round(side_probability - adjusted_contract_price, 10)
            if edge < edge_min:
                continue

            pnl = binary_trade_pnl(
                direction=direction,
                target_price=target_price,
                final_price=final_price,
                contract_price=adjusted_contract_price,
                stake_usdc=stake_usdc,
            )
            trades.append(
                {
                    "market_start": ts,
                    "entry_time": candles["close_time"].iloc[entry_idx],
                    "entry_offset_min": 2,
                    "seconds_remaining": 180,
                    "direction": direction,
                    "target_price": target_price,
                    "entry_price": current_price,
                    "final_price": final_price,
                    "sigma_remaining": sigma_remaining,
                    "z_score": (current_price - target_price) / sigma_remaining,
                    "momentum_score": momentum_score,
                    "model_probability": side_probability,
                    "contract_price": adjusted_contract_price,
                    "price_source": "polymarket_history" if use_polymarket_history else "simulated",
                    "entry_slippage": entry_slippage,
                    "edge": edge,
                    "win": pnl > 0,
                    "pnl_usdc": pnl,
                    "roi_pct": pnl / stake_usdc * 100.0,
                }
            )

    return pd.DataFrame(trades)


def summarize_strategy_result(
    strategy: str,
    params: dict[str, Any],
    trades: pd.DataFrame,
    stake_usdc: float,
) -> dict[str, Any]:
    """Build one comparable matrix row."""
    row: dict[str, Any] = {"strategy": strategy, **params}
    if trades.empty:
        row.update(
            {
                "trades": 0,
                "unique_signals": 0,
                "win_rate_pct": 0.0,
                "pnl_usdc": 0.0,
                "avg_roi_pct": 0.0,
                "max_drawdown_usdc": 0.0,
                "brier": None,
            }
        )
        return row

    ordered = trades.sort_values("entry_time").reset_index(drop=True)
    unique_cols = ["market_start", "entry_offset_min", "direction"]
    unique_signals = ordered.drop_duplicates(subset=unique_cols)
    cumulative = ordered["pnl_usdc"].cumsum()
    row.update(
        {
            "trades": int(len(ordered)),
            "unique_signals": int(len(unique_signals)),
            "win_rate_pct": float(ordered["win"].mean() * 100.0),
            "pnl_usdc": float(ordered["pnl_usdc"].sum()),
            "avg_roi_pct": float(ordered["roi_pct"].mean()),
            "max_drawdown_usdc": float(max_drawdown(cumulative)),
            "brier": float(((ordered["model_probability"] - ordered["win"].astype(float)) ** 2).mean()),
            "stake_usdc": stake_usdc,
        }
    )
    return row


def matrix_values(raw: str, cast):
    return [cast(item.strip()) for item in raw.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Matrix backtest for BTC Polymarket 5m ideas.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--contract-prices", default="0.55,0.60,0.65,0.70,0.75,0.80,0.85")
    parser.add_argument("--lookback", type=int, default=30)
    parser.add_argument("--momentum-minutes", type=int, default=2)
    parser.add_argument("--momentum-weight", type=float, default=0.10)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument("--min-contract-price", type=float, default=0.50)
    parser.add_argument("--edge-mins", default="0.06,0.08,0.10")
    parser.add_argument("--min-abs-zs", default="0.8,1.0,1.2")
    parser.add_argument("--volume-multipliers", default="0.0,1.0")
    parser.add_argument("--max-contract-prices", default="0.75,0.85")
    parser.add_argument("--entry-offset-sets", default="1;1,2;2")
    parser.add_argument("--first-minute-body-pcts", default="0.0003,0.0005")
    parser.add_argument("--first-minute-retrace-pcts", default="0.25,0.35,0.50")
    parser.add_argument("--first-minute-distance-pcts", default="0.0002,0.0004")
    parser.add_argument("--first-minute-slippages", default="0.00,0.01,0.02,0.03")
    parser.add_argument(
        "--use-polymarket-history",
        action="store_true",
        help="Usa /prices-history da Polymarket tambem na familia first-minute-selective.",
    )
    parser.add_argument(
        "--max-polymarket-price-distance",
        type=int,
        default=75,
        help="Distancia maxima em segundos entre entrada e preco historico da Polymarket.",
    )
    parser.add_argument("--save-summary-csv", default="backtest_polymarket_5m_matrix_summary.csv")
    parser.add_argument("--save-trades-dir", default="")
    return parser


def parse_entry_offset_sets(raw: str) -> list[list[int]]:
    sets = []
    for group in raw.split(";"):
        offsets = [int(item.strip()) for item in group.split(",") if item.strip()]
        if offsets:
            sets.append(offsets)
    return sets


def main() -> None:
    args = build_arg_parser().parse_args()
    contract_prices = parse_float_list(args.contract_prices)

    print(f"Baixando {args.limit} candles 1m de {args.symbol} na Binance...")
    df = fetch_binance_1m(symbol=args.symbol, limit=args.limit)
    print(f"Periodo: {df['ts'].iloc[0]} ate {df['ts'].iloc[-1]}")

    summary_rows: list[dict[str, Any]] = []
    trades_dir = Path(args.save_trades_dir) if args.save_trades_dir else None
    if trades_dir:
        trades_dir.mkdir(parents=True, exist_ok=True)

    edge_mins = matrix_values(args.edge_mins, float)
    min_abs_zs = matrix_values(args.min_abs_zs, float)
    volume_multipliers = matrix_values(args.volume_multipliers, float)
    max_contract_prices = matrix_values(args.max_contract_prices, float)
    entry_offset_sets = parse_entry_offset_sets(args.entry_offset_sets)

    run_index = 0
    for edge_min, min_abs_z, volume_multiplier, max_contract_price, entry_offsets in itertools.product(
        edge_mins,
        min_abs_zs,
        volume_multipliers,
        max_contract_prices,
        entry_offset_sets,
    ):
        params = {
            "edge_min": edge_min,
            "min_abs_z": min_abs_z,
            "volume_multiplier": volume_multiplier,
            "max_contract_price": max_contract_price,
            "entry_offsets": ",".join(str(item) for item in entry_offsets),
            "price_source": "simulated",
        }
        trades = run_backtest(
            df=df,
            contract_prices=contract_prices,
            entry_offsets=entry_offsets,
            lookback_minutes=args.lookback,
            momentum_minutes=args.momentum_minutes,
            momentum_weight=args.momentum_weight,
            edge_min=edge_min,
            max_contract_price=max_contract_price,
            min_contract_price=args.min_contract_price,
            min_abs_z=min_abs_z,
            stake_usdc=args.stake,
            volume_multiplier=volume_multiplier,
        )
        summary_rows.append(summarize_strategy_result("edge-regime", params, trades, args.stake))
        if trades_dir and not trades.empty:
            trades.to_csv(trades_dir / f"edge_regime_{run_index:03d}.csv", index=False)
        run_index += 1

    body_pcts = matrix_values(args.first_minute_body_pcts, float)
    retrace_pcts = matrix_values(args.first_minute_retrace_pcts, float)
    distance_pcts = matrix_values(args.first_minute_distance_pcts, float)
    entry_slippages = matrix_values(args.first_minute_slippages, float)
    for edge_min, body_pct, retrace_pct, distance_pct, volume_multiplier, max_contract_price, entry_slippage in itertools.product(
        edge_mins,
        body_pcts,
        retrace_pcts,
        distance_pcts,
        volume_multipliers,
        max_contract_prices,
        entry_slippages,
    ):
        params = {
            "edge_min": edge_min,
            "min_anchor_body_pct": body_pct,
            "max_retrace_pct": retrace_pct,
            "min_target_distance_pct": distance_pct,
            "volume_multiplier": volume_multiplier,
            "max_contract_price": max_contract_price,
            "entry_slippage": entry_slippage,
            "entry_offsets": "2",
            "price_source": "polymarket_history" if args.use_polymarket_history else "simulated",
        }
        trades = run_first_minute_selective_backtest(
            df=df,
            contract_prices=contract_prices,
            lookback_minutes=args.lookback,
            momentum_minutes=args.momentum_minutes,
            momentum_weight=args.momentum_weight,
            edge_min=edge_min,
            min_contract_price=args.min_contract_price,
            max_contract_price=max_contract_price,
            stake_usdc=args.stake,
            min_anchor_body_pct=body_pct,
            max_retrace_pct=retrace_pct,
            min_target_distance_pct=distance_pct,
            volume_multiplier=volume_multiplier,
            entry_slippage=entry_slippage,
            use_polymarket_history=args.use_polymarket_history,
            max_polymarket_price_distance=args.max_polymarket_price_distance,
        )
        summary_rows.append(summarize_strategy_result("first-minute-selective", params, trades, args.stake))
        if trades_dir and not trades.empty:
            trades.to_csv(trades_dir / f"first_minute_selective_{run_index:03d}.csv", index=False)
        run_index += 1

    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values(["pnl_usdc", "win_rate_pct", "trades"], ascending=[False, False, False])
    print("\n=== TOP 20 MATRIX RESULTS ===")
    print(summary.head(20).round(4).to_string(index=False))

    if args.save_summary_csv:
        summary.to_csv(args.save_summary_csv, index=False)
        print(f"\nResumo salvo em: {args.save_summary_csv}")


if __name__ == "__main__":
    main()
