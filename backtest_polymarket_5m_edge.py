#!/usr/bin/env python3
"""Backtest de edge para mercados BTC Up/Down de 5 minutos.

Este script usa candles de 1 minuto da Binance como proxy do preço do BTC e
simula entradas dentro de cada janela de 5 minutos. Como o histórico de odds da
Polymarket não vem junto com os candles, os preços dos contratos são cenários
hipotéticos: 0.55, 0.60, 0.65 etc.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
GAMMA_EVENT_SLUG_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
POLYMARKET_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"
DEFAULT_CONTRACT_PRICES = "0.55,0.60,0.65,0.70,0.75,0.80,0.85,0.90"
DEFAULT_ENTRY_OFFSETS = "1,2,3,4"


@dataclass(frozen=True)
class TradeDecision:
    direction: str
    probability: float = 0.0
    contract_price: float = 0.0
    edge: float = 0.0


@dataclass(frozen=True)
class PricePoint:
    timestamp: int
    price: float


@dataclass(frozen=True)
class PolymarketMarketHistory:
    event_start_ts: int
    up_token_id: str
    down_token_id: str
    up_prices: tuple[PricePoint, ...]
    down_prices: tuple[PricePoint, ...]


def normal_cdf(x: float) -> float:
    """Standard normal CDF without scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def estimate_up_probability(
    current_price: float,
    target_price: float,
    sigma_remaining: float,
    momentum_score: float = 0.0,
    momentum_weight: float = 0.10,
) -> float:
    """Estimate probability that final BTC price ends above the target."""
    if sigma_remaining <= 0 or not math.isfinite(sigma_remaining):
        if current_price > target_price:
            return 1.0
        if current_price < target_price:
            return 0.0
        return 0.5

    z_score = (current_price - target_price) / sigma_remaining
    adjusted_score = z_score + (momentum_weight * momentum_score)
    return min(1.0, max(0.0, normal_cdf(adjusted_score)))


def decide_trade(
    prob_up: float,
    ask_up: float,
    ask_down: float,
    edge_min: float,
    max_contract_price: float,
    min_contract_price: float = 0.0,
) -> TradeDecision:
    """Pick the side with enough modeled edge after contract price."""
    candidates: list[TradeDecision] = []
    prob_down = 1.0 - prob_up

    if min_contract_price <= ask_up <= max_contract_price:
        edge_up = round(prob_up - ask_up, 10)
        if edge_up >= edge_min:
            candidates.append(
                TradeDecision(
                    direction="UP",
                    probability=prob_up,
                    contract_price=ask_up,
                    edge=edge_up,
                )
            )

    if min_contract_price <= ask_down <= max_contract_price:
        edge_down = round(prob_down - ask_down, 10)
        if edge_down >= edge_min:
            candidates.append(
                TradeDecision(
                    direction="DOWN",
                    probability=prob_down,
                    contract_price=ask_down,
                    edge=edge_down,
                )
            )

    if not candidates:
        return TradeDecision(direction="HOLD")
    return max(candidates, key=lambda item: item.edge)


def decide_trade_for_side_price(
    prob_up: float,
    contract_price: float,
    edge_min: float,
    max_contract_price: float,
    min_contract_price: float = 0.0,
) -> TradeDecision:
    """Evaluate UP and DOWN against the same hypothetical side price."""
    return decide_trade(
        prob_up=prob_up,
        ask_up=contract_price,
        ask_down=contract_price,
        edge_min=edge_min,
        max_contract_price=max_contract_price,
        min_contract_price=min_contract_price,
    )


def binary_trade_pnl(
    direction: str,
    target_price: float,
    final_price: float,
    contract_price: float,
    stake_usdc: float,
) -> float:
    """Return USDC P&L for a binary share paying 1 USDC if it wins."""
    if contract_price <= 0:
        raise ValueError("contract_price must be positive")

    if direction == "UP":
        win = final_price > target_price
    elif direction == "DOWN":
        win = final_price < target_price
    else:
        raise ValueError("direction must be UP or DOWN")

    if not win:
        return -stake_usdc

    shares = stake_usdc / contract_price
    payout = shares * 1.0
    return payout - stake_usdc


def max_drawdown(values: Iterable[float]) -> float:
    """Maximum drawdown from a cumulative P&L series."""
    peak = 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = min(max_dd, value - peak)
    return max_dd


def parse_float_list(raw: str) -> list[float]:
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def parse_int_list(raw: str) -> list[int]:
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def nearest_price_at(
    points: list[PricePoint] | tuple[PricePoint, ...],
    target_ts: int,
    max_distance_seconds: int,
) -> float | None:
    """Return the closest historical price to target_ts within tolerance."""
    if not points:
        return None

    point = min(points, key=lambda item: abs(item.timestamp - target_ts))
    if abs(point.timestamp - target_ts) > max_distance_seconds:
        return None
    return point.price


def decode_json_list(raw: object) -> list:
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return raw
    return []


def fetch_polymarket_event(event_start_ts: int) -> dict | None:
    slug = f"btc-updown-5m-{event_start_ts}"
    response = requests.get(GAMMA_EVENT_SLUG_URL.format(slug=slug), timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def fetch_token_price_history(
    token_id: str,
    start_ts: int,
    end_ts: int,
) -> tuple[PricePoint, ...]:
    params = {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": 1,
    }
    response = requests.get(POLYMARKET_PRICE_HISTORY_URL, params=params, timeout=15)
    response.raise_for_status()
    history = response.json().get("history", [])
    points = []
    for item in history:
        try:
            points.append(PricePoint(timestamp=int(item["t"]), price=float(item["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(points)


def fetch_polymarket_market_history(event_start_ts: int) -> PolymarketMarketHistory | None:
    event = fetch_polymarket_event(event_start_ts)
    if not event:
        return None

    markets = event.get("markets") or []
    if not markets:
        return None

    market = markets[0]
    outcomes = decode_json_list(market.get("outcomes"))
    token_ids = decode_json_list(market.get("clobTokenIds"))
    if len(outcomes) < 2 or len(token_ids) < 2:
        return None

    outcome_to_token = {str(outcome).lower(): str(token_id) for outcome, token_id in zip(outcomes, token_ids)}
    up_token_id = outcome_to_token.get("up")
    down_token_id = outcome_to_token.get("down")
    if not up_token_id or not down_token_id:
        return None

    end_ts = event_start_ts + 300
    return PolymarketMarketHistory(
        event_start_ts=event_start_ts,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        up_prices=fetch_token_price_history(up_token_id, event_start_ts, end_ts),
        down_prices=fetch_token_price_history(down_token_id, event_start_ts, end_ts),
    )


def fetch_binance_1m(symbol: str, limit: int) -> pd.DataFrame:
    """Fetch recent 1m candles from Binance, paginating when limit > 1000."""
    remaining = max(1, limit)
    end_time = None
    chunks: list[pd.DataFrame] = []

    while remaining > 0:
        batch_limit = min(remaining, 1000)
        params = {
            "symbol": symbol,
            "interval": "1m",
            "limit": batch_limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        response = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        if not data:
            break

        chunk = pd.DataFrame(
            data,
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "num_trades",
                "taker_buy_base_volume",
                "taker_buy_quote_volume",
                "ignore",
            ],
        )
        chunks.append(chunk)
        remaining -= len(chunk)

        first_ts = int(chunk["ts"].iloc[0])
        end_time = first_ts - 1
        if len(chunk) < batch_limit:
            break

    if not chunks:
        raise RuntimeError("Binance returned no candle data")

    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.tail(limit).reset_index(drop=True)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["log_return"] = np.log(out["close"]).diff()
    out["volume_sma_20"] = out["volume"].rolling(20).mean()
    return out


def estimate_sigma_remaining(
    df: pd.DataFrame,
    entry_idx: int,
    lookback_minutes: int,
    remaining_minutes: int,
) -> float:
    returns = df["log_return"].iloc[entry_idx - lookback_minutes + 1 : entry_idx + 1]
    std_1m = float(returns.std(ddof=0))
    current_price = float(df["close"].iloc[entry_idx])
    return current_price * std_1m * math.sqrt(max(remaining_minutes, 1))


def estimate_momentum_score(
    df: pd.DataFrame,
    entry_idx: int,
    momentum_minutes: int,
    lookback_minutes: int,
) -> float:
    start_idx = max(0, entry_idx - momentum_minutes)
    if start_idx == entry_idx:
        return 0.0

    recent_return = math.log(float(df["close"].iloc[entry_idx]) / float(df["close"].iloc[start_idx]))
    returns = df["log_return"].iloc[entry_idx - lookback_minutes + 1 : entry_idx + 1]
    std_1m = float(returns.std(ddof=0))
    denom = std_1m * math.sqrt(max(entry_idx - start_idx, 1))
    if denom <= 0 or not math.isfinite(denom):
        return 0.0
    return recent_return / denom


def run_backtest(
    df: pd.DataFrame,
    contract_prices: list[float],
    entry_offsets: list[int],
    lookback_minutes: int,
    momentum_minutes: int,
    momentum_weight: float,
    edge_min: float,
    max_contract_price: float,
    min_contract_price: float,
    min_abs_z: float,
    stake_usdc: float,
    volume_multiplier: float,
    use_polymarket_history: bool = False,
    max_polymarket_price_distance: int = 75,
) -> pd.DataFrame:
    df = add_features(df)
    trades: list[dict] = []
    polymarket_cache: dict[int, PolymarketMarketHistory | None] = {}

    max_offset = max(entry_offsets)
    for block_start in range(lookback_minutes + 1, len(df) - 5):
        ts = df["ts"].iloc[block_start]
        if int(ts.minute) % 5 != 0:
            continue
        if block_start + 4 >= len(df):
            continue

        target_price = float(df["close"].iloc[block_start - 1])
        final_price = float(df["close"].iloc[block_start + 4])

        for offset in entry_offsets:
            if offset < 1 or offset > 4:
                continue
            if offset > max_offset:
                continue

            entry_idx = block_start + offset - 1
            remaining_minutes = 5 - offset
            current_price = float(df["close"].iloc[entry_idx])
            sigma_remaining = estimate_sigma_remaining(
                df=df,
                entry_idx=entry_idx,
                lookback_minutes=lookback_minutes,
                remaining_minutes=remaining_minutes,
            )
            if sigma_remaining <= 0 or not math.isfinite(sigma_remaining):
                continue

            z_score = (current_price - target_price) / sigma_remaining
            if abs(z_score) < min_abs_z:
                continue

            if volume_multiplier > 0:
                volume_sma = float(df["volume_sma_20"].iloc[entry_idx])
                if math.isfinite(volume_sma) and volume_sma > 0:
                    if float(df["volume"].iloc[entry_idx]) < volume_sma * volume_multiplier:
                        continue

            momentum_score = estimate_momentum_score(
                df=df,
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

            event_start_ts = int(ts.timestamp())
            entry_ts = int(df["close_time"].iloc[entry_idx].timestamp())

            decisions: list[TradeDecision] = []
            price_source = "simulated"

            if use_polymarket_history:
                if event_start_ts not in polymarket_cache:
                    polymarket_cache[event_start_ts] = fetch_polymarket_market_history(event_start_ts)

                history = polymarket_cache[event_start_ts]
                if history is None:
                    continue

                up_price = nearest_price_at(
                    history.up_prices,
                    target_ts=entry_ts,
                    max_distance_seconds=max_polymarket_price_distance,
                )
                down_price = nearest_price_at(
                    history.down_prices,
                    target_ts=entry_ts,
                    max_distance_seconds=max_polymarket_price_distance,
                )
                if up_price is None or down_price is None:
                    continue

                decision = decide_trade(
                    prob_up=prob_up,
                    ask_up=up_price,
                    ask_down=down_price,
                    edge_min=edge_min,
                    max_contract_price=max_contract_price,
                    min_contract_price=min_contract_price,
                )
                if decision.direction != "HOLD":
                    decisions.append(decision)
                price_source = "polymarket_history"
            else:
                for contract_price in contract_prices:
                    decision = decide_trade_for_side_price(
                        prob_up=prob_up,
                        contract_price=contract_price,
                        edge_min=edge_min,
                        max_contract_price=max_contract_price,
                        min_contract_price=min_contract_price,
                    )
                    if decision.direction != "HOLD":
                        decisions.append(decision)

            for decision in decisions:
                contract_price = decision.contract_price

                pnl = binary_trade_pnl(
                    direction=decision.direction,
                    target_price=target_price,
                    final_price=final_price,
                    contract_price=decision.contract_price,
                    stake_usdc=stake_usdc,
                )
                win = pnl > 0
                trades.append(
                    {
                        "market_start": ts,
                        "entry_time": df["close_time"].iloc[entry_idx],
                        "entry_offset_min": offset,
                        "seconds_remaining": remaining_minutes * 60,
                        "direction": decision.direction,
                        "target_price": target_price,
                        "entry_price": current_price,
                        "final_price": final_price,
                        "sigma_remaining": sigma_remaining,
                        "z_score": z_score,
                        "momentum_score": momentum_score,
                        "model_probability": decision.probability,
                        "contract_price": decision.contract_price,
                        "price_source": price_source,
                        "edge": decision.edge,
                        "win": win,
                        "pnl_usdc": pnl,
                        "roi_pct": pnl / stake_usdc * 100.0,
                    }
                )

    return pd.DataFrame(trades)


def summarize_trades(trades: pd.DataFrame, stake_usdc: float) -> None:
    if trades.empty:
        print("Nenhum trade encontrado com estes filtros.")
        return

    ordered = trades.sort_values("entry_time").reset_index(drop=True)
    unique_signal_cols = ["market_start", "entry_offset_min", "direction"]
    unique_signals = ordered.drop_duplicates(subset=unique_signal_cols)
    cumulative = ordered["pnl_usdc"].cumsum()
    total_pnl = float(ordered["pnl_usdc"].sum())
    win_rate = float(ordered["win"].mean() * 100.0)
    avg_pnl = float(ordered["pnl_usdc"].mean())
    avg_roi = float(ordered["roi_pct"].mean())
    brier = float(((ordered["model_probability"] - ordered["win"].astype(float)) ** 2).mean())

    print("\n=== BACKTEST BTC UP/DOWN 5M - EDGE MODEL ===")
    print("Fonte: Binance BTCUSDT 1m")
    sources = ", ".join(sorted(ordered["price_source"].dropna().unique()))
    print(f"Fonte de preco do contrato: {sources}")
    if "simulated" in sources:
        print("Nota: contract_price simulado nao representa odds historicas da Polymarket.")
    if "polymarket_history" in sources:
        print("Nota: preco Polymarket vem de /prices-history; e historico de chart/trade, nao garantia de fill no ask.")
    print(f"Cenarios avaliados: {len(ordered)} sinal-preco")
    print(f"Sinais unicos: {len(unique_signals)}")
    print(f"Win rate agregado dos cenarios: {win_rate:.2f}%")
    print(f"PnL total dos cenarios: {total_pnl:.2f} USDC")
    print(f"EV medio por cenario: {avg_pnl:.4f} USDC ({avg_roi:.2f}% sobre stake)")
    print(f"Stake por trade: {stake_usdc:.2f} USDC")
    print(f"Max drawdown: {max_drawdown(cumulative):.2f} USDC")
    print(f"Brier score dos trades: {brier:.4f}")

    print("\n--- Por direcao ---")
    by_direction = ordered.groupby("direction").agg(
        trades=("win", "size"),
        win_rate=("win", "mean"),
        pnl_usdc=("pnl_usdc", "sum"),
        avg_roi_pct=("roi_pct", "mean"),
    )
    by_direction["win_rate"] *= 100.0
    print(by_direction.round(2).to_string())

    print("\n--- Por minuto de entrada ---")
    by_offset = ordered.groupby("entry_offset_min").agg(
        trades=("win", "size"),
        win_rate=("win", "mean"),
        pnl_usdc=("pnl_usdc", "sum"),
        avg_edge=("edge", "mean"),
        avg_prob=("model_probability", "mean"),
    )
    by_offset["win_rate"] *= 100.0
    print(by_offset.round(4).to_string())

    print("\n--- Por preco do contrato ---")
    by_price = ordered.groupby("contract_price").agg(
        trades=("win", "size"),
        win_rate=("win", "mean"),
        pnl_usdc=("pnl_usdc", "sum"),
        avg_roi_pct=("roi_pct", "mean"),
        avg_edge=("edge", "mean"),
    )
    by_price["win_rate"] *= 100.0
    print(by_price.round(4).to_string())

    print("\n--- Ultimos 10 trades ---")
    columns = [
        "entry_time",
        "entry_offset_min",
        "direction",
        "contract_price",
        "model_probability",
        "edge",
        "win",
        "pnl_usdc",
    ]
    print(ordered[columns].tail(10).round(4).to_string(index=False))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backtest de edge para BTC Up/Down 5m com candles Binance 1m."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000, help="Quantidade de candles 1m recentes.")
    parser.add_argument("--contract-prices", default=DEFAULT_CONTRACT_PRICES)
    parser.add_argument("--entry-offsets", default=DEFAULT_ENTRY_OFFSETS, help="Minutos apos inicio da rodada: 1,2,3,4.")
    parser.add_argument("--lookback", type=int, default=30, help="Minutos para estimar volatilidade.")
    parser.add_argument("--momentum-minutes", type=int, default=2)
    parser.add_argument("--momentum-weight", type=float, default=0.10)
    parser.add_argument("--edge-min", type=float, default=0.06)
    parser.add_argument("--min-contract-price", type=float, default=0.0)
    parser.add_argument("--max-contract-price", type=float, default=0.85)
    parser.add_argument("--min-abs-z", type=float, default=1.0)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument(
        "--use-polymarket-history",
        action="store_true",
        help="Usa historico real /prices-history dos tokens Up/Down em vez de contract-prices simulados.",
    )
    parser.add_argument(
        "--max-polymarket-price-distance",
        type=int,
        default=75,
        help="Distancia maxima em segundos entre entrada simulada e ponto de preco da Polymarket.",
    )
    parser.add_argument(
        "--volume-multiplier",
        type=float,
        default=0.0,
        help="0 desliga filtro; 1.0 exige volume >= media 20.",
    )
    parser.add_argument("--save-csv", default="", help="Caminho opcional para salvar trades.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    contract_prices = parse_float_list(args.contract_prices)
    entry_offsets = parse_int_list(args.entry_offsets)

    print(f"Baixando {args.limit} candles 1m de {args.symbol} na Binance...")
    df = fetch_binance_1m(symbol=args.symbol, limit=args.limit)
    print(f"Periodo: {df['ts'].iloc[0]} ate {df['ts'].iloc[-1]}")

    trades = run_backtest(
        df=df,
        contract_prices=contract_prices,
        entry_offsets=entry_offsets,
        lookback_minutes=args.lookback,
        momentum_minutes=args.momentum_minutes,
        momentum_weight=args.momentum_weight,
        edge_min=args.edge_min,
        max_contract_price=args.max_contract_price,
        min_contract_price=args.min_contract_price,
        min_abs_z=args.min_abs_z,
        stake_usdc=args.stake,
        volume_multiplier=args.volume_multiplier,
        use_polymarket_history=args.use_polymarket_history,
        max_polymarket_price_distance=args.max_polymarket_price_distance,
    )
    summarize_trades(trades, stake_usdc=args.stake)

    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nTrades salvos em: {args.save_csv}")


if __name__ == "__main__":
    main()
