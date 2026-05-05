#!/usr/bin/env python3
"""Paper observer para reversoes baratas no ultimo minuto do BTC Up/Down 5m."""

from __future__ import annotations

import argparse
import csv
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtest_polymarket_5m_edge import binary_trade_pnl
from paper_polymarket_5m_live import (
    fetch_binance_price,
    fetch_buy_price,
    fetch_live_market,
    fetch_recent_binance_1m,
    target_price_for_event,
    utc_now,
)


@dataclass(frozen=True)
class LotteryConfig:
    symbol: str = "BTCUSDT"
    poll_seconds: int = 2
    stake_usdc: float = 1.0
    min_seconds_remaining: int = 5
    max_seconds_remaining: int = 60
    min_cheap_price: float = 0.01
    max_cheap_price: float = 0.10
    favorite_min_price: float = 0.90
    max_abs_distance_usd: float = 80.0
    max_abs_z: float = 5.0
    lookback_minutes: int = 30
    settle_delay_seconds: int = 10
    trades_csv: str = "paper_late_lottery_trades.csv"
    allowed_directions: tuple[str, ...] = ("UP", "DOWN")


@dataclass(frozen=True)
class LotteryEntry:
    direction: str
    contract_price: float
    favorite_price: float
    distance_usd: float
    z_score: float
    sigma_remaining: float


@dataclass(frozen=True)
class LotteryPosition:
    market_slug: str
    event_start_ts: int
    event_end_ts: int
    direction: str
    entry_ts: int
    entry_btc_price: float
    target_price: float
    contract_price: float
    favorite_price: float
    stake_usdc: float
    seconds_remaining: int
    distance_usd: float
    z_score: float
    sigma_remaining: float
    status: str = "OPEN"
    final_btc_price: float | None = None
    win: bool | None = None
    pnl_usdc: float = 0.0
    closed_ts: int | None = None
    closed_time_utc: str = ""


def current_5m_event_start(now: datetime | None = None) -> int:
    now = now or utc_now()
    ts = int(now.timestamp())
    return ts - (ts % 300)


def estimate_sigma_remaining(df: pd.DataFrame, current_price: float, seconds_remaining: int, lookback_minutes: int) -> float:
    returns = df["log_return"].dropna().tail(lookback_minutes)
    std_1m = float(returns.std(ddof=0)) if not returns.empty else 0.0
    if std_1m <= 0 or not math.isfinite(std_1m):
        return 0.0
    return current_price * std_1m * math.sqrt(max(seconds_remaining, 1) / 60.0)


def choose_lottery_entry(
    current_price: float,
    target_price: float,
    sigma_remaining: float,
    up_price: float,
    down_price: float,
    seconds_remaining: int,
    config: LotteryConfig,
) -> LotteryEntry | None:
    if seconds_remaining < config.min_seconds_remaining or seconds_remaining > config.max_seconds_remaining:
        return None

    distance = current_price - target_price
    abs_distance = abs(distance)
    z_score = 0.0 if sigma_remaining <= 0 else distance / sigma_remaining
    if abs_distance > config.max_abs_distance_usd:
        return None
    if abs(z_score) > config.max_abs_z:
        return None

    if config.min_cheap_price <= up_price <= config.max_cheap_price and down_price >= config.favorite_min_price:
        direction = "UP"
        cheap_price = up_price
        favorite_price = down_price
    elif config.min_cheap_price <= down_price <= config.max_cheap_price and up_price >= config.favorite_min_price:
        direction = "DOWN"
        cheap_price = down_price
        favorite_price = up_price
    else:
        return None

    if direction not in config.allowed_directions:
        return None

    return LotteryEntry(
        direction=direction,
        contract_price=round(cheap_price, 4),
        favorite_price=round(favorite_price, 4),
        distance_usd=round(abs_distance, 4),
        z_score=round(z_score, 6),
        sigma_remaining=round(sigma_remaining, 4),
    )


def settle_lottery_position(position: LotteryPosition, final_btc_price: float, closed_ts: int | None = None) -> LotteryPosition:
    pnl = binary_trade_pnl(
        direction=position.direction,
        target_price=position.target_price,
        final_price=final_btc_price,
        contract_price=position.contract_price,
        stake_usdc=position.stake_usdc,
    )
    if position.direction == "UP":
        win = final_btc_price > position.target_price
    else:
        win = final_btc_price < position.target_price

    closed_ts = closed_ts or int(utc_now().timestamp())
    return LotteryPosition(
        **{
            **asdict(position),
            "status": "CLOSED",
            "final_btc_price": round(final_btc_price, 4),
            "win": win,
            "pnl_usdc": round(pnl, 4),
            "closed_ts": closed_ts,
            "closed_time_utc": datetime.fromtimestamp(closed_ts, tz=timezone.utc).isoformat(),
        }
    )


def append_position(path: str, position: LotteryPosition) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(position).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(position))


def evaluate_entry(config: LotteryConfig, now: datetime | None = None) -> LotteryPosition | None:
    now = now or utc_now()
    event_start_ts = current_5m_event_start(now)
    event_end_ts = event_start_ts + 300
    seconds_remaining = event_end_ts - int(now.timestamp())

    if seconds_remaining < config.min_seconds_remaining or seconds_remaining > config.max_seconds_remaining:
        print(f"[{now.strftime('%H:%M:%S')}] Aguardando ultimo minuto; remaining={seconds_remaining}s")
        return None

    market = fetch_live_market(event_start_ts)
    if market is None:
        print(f"[{now.strftime('%H:%M:%S')}] Mercado atual nao encontrado.")
        return None

    df = fetch_recent_binance_1m(config.symbol, max(config.lookback_minutes + 5, 40))
    current_price = fetch_binance_price(config.symbol)
    target_price = target_price_for_event(df, event_start_ts)
    sigma_remaining = estimate_sigma_remaining(
        df=df,
        current_price=current_price,
        seconds_remaining=seconds_remaining,
        lookback_minutes=config.lookback_minutes,
    )

    up_price = fetch_buy_price(market.up_token_id)
    down_price = fetch_buy_price(market.down_token_id)
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
        distance = abs(current_price - target_price)
        z_score = 0.0 if sigma_remaining <= 0 else (current_price - target_price) / sigma_remaining
        print(
            f"[{now.strftime('%H:%M:%S')}] HOLD lotto rem={seconds_remaining}s "
            f"btc={current_price:.2f} target={target_price:.2f} dist={distance:.2f} "
            f"z={z_score:.2f} up={up_price:.3f} down={down_price:.3f}"
        )
        return None

    return LotteryPosition(
        market_slug=market.slug,
        event_start_ts=event_start_ts,
        event_end_ts=event_end_ts,
        direction=entry.direction,
        entry_ts=int(now.timestamp()),
        entry_btc_price=round(current_price, 4),
        target_price=round(target_price, 4),
        contract_price=entry.contract_price,
        favorite_price=entry.favorite_price,
        stake_usdc=config.stake_usdc,
        seconds_remaining=seconds_remaining,
        distance_usd=entry.distance_usd,
        z_score=entry.z_score,
        sigma_remaining=entry.sigma_remaining,
    )


def run_loop(config: LotteryConfig, cycles: int = 0, once: bool = False) -> None:
    print("=== PAPER LATE LOTTERY BTC UP/DOWN 5M ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"CSV: {config.trades_csv}")
    print(
        f"Filtro: cheap={config.min_cheap_price:.2f}-{config.max_cheap_price:.2f}, "
        f"favorite>={config.favorite_min_price:.2f}, remaining={config.min_seconds_remaining}-{config.max_seconds_remaining}s, "
        f"dist<={config.max_abs_distance_usd:.2f}, abs_z<={config.max_abs_z:.2f}"
    )

    open_position: LotteryPosition | None = None
    traded_events: set[int] = set()
    iterations = 0

    while True:
        now = utc_now()
        try:
            if open_position is not None:
                if int(now.timestamp()) >= open_position.event_end_ts + config.settle_delay_seconds:
                    final_price = fetch_binance_price(config.symbol)
                    closed = settle_lottery_position(open_position, final_price, closed_ts=int(now.timestamp()))
                    append_position(config.trades_csv, closed)
                    print(
                        f"[{now.strftime('%H:%M:%S')}] FECHOU {closed.direction} "
                        f"win={closed.win} pnl={closed.pnl_usdc:.4f} final={closed.final_btc_price:.2f} "
                        f"target={closed.target_price:.2f}"
                    )
                    open_position = None
                else:
                    remaining = max(open_position.event_end_ts - int(now.timestamp()), 0)
                    print(
                        f"[{now.strftime('%H:%M:%S')}] POSICAO ABERTA {open_position.direction} "
                        f"restante={remaining}s contrato={open_position.contract_price:.3f}"
                    )
            else:
                event_start_ts = current_5m_event_start(now)
                if event_start_ts in traded_events:
                    print(f"[{now.strftime('%H:%M:%S')}] Evento ja testado; aguardando proximo.")
                else:
                    candidate = evaluate_entry(config, now=now)
                    if candidate is not None:
                        open_position = candidate
                        traded_events.add(candidate.event_start_ts)
                        print(
                            f"[{now.strftime('%H:%M:%S')}] ABRIU LOTTO {candidate.direction} "
                            f"contrato={candidate.contract_price:.3f} favorito={candidate.favorite_price:.3f} "
                            f"dist={candidate.distance_usd:.2f} z={candidate.z_score:.2f} "
                            f"stake={candidate.stake_usdc:.2f}"
                        )
        except Exception as exc:
            print(f"[{now.strftime('%H:%M:%S')}] ERRO: {exc}")

        iterations += 1
        if once or (cycles and iterations >= cycles):
            break
        time.sleep(config.poll_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper observer de reversao barata no ultimo minuto BTC 5m.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--poll-seconds", type=int, default=2)
    parser.add_argument("--stake", type=float, default=1.0)
    parser.add_argument("--min-seconds-remaining", type=int, default=5)
    parser.add_argument("--max-seconds-remaining", type=int, default=60)
    parser.add_argument("--min-cheap-price", type=float, default=0.01)
    parser.add_argument("--max-cheap-price", type=float, default=0.10)
    parser.add_argument("--favorite-min-price", type=float, default=0.90)
    parser.add_argument("--max-abs-distance-usd", type=float, default=80.0)
    parser.add_argument("--max-abs-z", type=float, default=5.0)
    parser.add_argument("--lookback-minutes", type=int, default=30)
    parser.add_argument("--settle-delay-seconds", type=int, default=10)
    parser.add_argument("--trades-csv", default="paper_late_lottery_trades.csv")
    parser.add_argument("--allowed-directions", default="UP,DOWN")
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true")
    return parser


def parse_allowed_directions(raw: str) -> tuple[str, ...]:
    items = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    allowed = tuple(item for item in items if item in {"UP", "DOWN"})
    return allowed or ("UP", "DOWN")


def main() -> None:
    args = build_arg_parser().parse_args()
    config = LotteryConfig(
        symbol=args.symbol,
        poll_seconds=args.poll_seconds,
        stake_usdc=args.stake,
        min_seconds_remaining=args.min_seconds_remaining,
        max_seconds_remaining=args.max_seconds_remaining,
        min_cheap_price=args.min_cheap_price,
        max_cheap_price=args.max_cheap_price,
        favorite_min_price=args.favorite_min_price,
        max_abs_distance_usd=args.max_abs_distance_usd,
        max_abs_z=args.max_abs_z,
        lookback_minutes=args.lookback_minutes,
        settle_delay_seconds=args.settle_delay_seconds,
        trades_csv=args.trades_csv,
        allowed_directions=parse_allowed_directions(args.allowed_directions),
    )
    run_loop(config, cycles=args.cycles, once=args.once)


if __name__ == "__main__":
    main()
