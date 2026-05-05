#!/usr/bin/env python3
"""Paper scanner para momentum em Binance Spot.

Monitora pares USDT, ranqueia movimentos curtos com filtros de liquidez e
simula entradas long spot. Nao envia ordens reais.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import requests


BINANCE_SPOT_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"


@dataclass(frozen=True)
class TickerSnapshot:
    symbol: str
    price: float
    bid: float
    ask: float
    quote_volume: float
    price_change_pct: float = 0.0

    @property
    def spread_pct(self) -> float:
        if self.price <= 0:
            return float("inf")
        return ((self.ask - self.bid) / self.price) * 100.0


@dataclass(frozen=True)
class MomentumCandidate:
    symbol: str
    price: float
    previous_price: float
    move_pct: float
    spread_pct: float
    quote_volume: float
    volume_multiplier: float = 0.0
    breakout_high: float = 0.0
    candle_open: float = 0.0
    price_change_pct: float = 0.0
    score: float = 0.0


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ScannerConfig:
    interval: str = "15m"
    breakout_lookback: int = 20
    kline_limit: int = 60
    prefilter_symbols: int = 40
    min_move_pct: float = 1.0
    max_move_pct: float = 8.0
    min_candle_move_pct: float = 2.0
    max_candle_move_pct: float = 18.0
    min_volume_multiplier: float = 3.0
    project_current_volume: bool = True
    min_volume_projection_fraction: float = 0.2
    breakout_buffer_pct: float = 0.001
    max_breakout_extension_pct: float = 12.0
    min_24h_change_pct: float = 5.0
    min_quote_volume: float = 1_000_000.0
    max_spread_pct: float = 0.35
    notional_usdc: float = 25.0
    stop_pct: float = 0.05
    take_profit_pct: float = 0.0
    trailing_activation_pct: float = 0.10
    trailing_pct: float = 0.05
    max_hold_seconds: int = 43_200
    fee_rate: float = 0.001
    cooldown_seconds: int = 86_400


@dataclass(frozen=True)
class PaperSpotPosition:
    symbol: str
    entry_time_utc: str
    entry_ts: int
    entry_price: float
    notional_usdc: float
    quantity: float
    stop_price: float
    take_profit_price: float
    max_hold_seconds: int
    fee_rate: float
    highest_price: float = 0.0
    trailing_activation_price: float = 0.0
    trailing_stop_price: float = 0.0
    trailing_pct: float = 0.0
    status: str = "OPEN"
    exit_time_utc: str = ""
    exit_ts: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    gross_pnl_usdc: float = 0.0
    fees_usdc: float = 0.0
    pnl_usdc: float = 0.0
    pnl_pct_notional: float = 0.0
    win: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_symbol_cooldowns(path: str | Path, now_ts: int) -> dict[str, int]:
    cooldown_path = Path(path)
    if not cooldown_path.exists():
        return {}
    try:
        raw = json.loads(cooldown_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    cooldowns: dict[str, int] = {}
    for symbol, until_ts in raw.items():
        try:
            until = int(until_ts)
        except (TypeError, ValueError):
            continue
        if until > now_ts:
            cooldowns[str(symbol)] = until
    return cooldowns


def save_symbol_cooldowns(path: str | Path, cooldowns: dict[str, int]) -> None:
    cooldown_path = Path(path)
    cooldown_path.write_text(json.dumps(cooldowns, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def load_open_position(path: str | Path) -> PaperSpotPosition | None:
    state_path = Path(path)
    if not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        if str(raw.get("status", "")).upper() != "OPEN":
            return None
        return PaperSpotPosition(**raw)
    except TypeError:
        return None


def save_open_position(path: str | Path, position: PaperSpotPosition | None) -> None:
    state_path = Path(path)
    if position is None:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        return

    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(asdict(position), ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(state_path)


def seed_cooldowns_from_trades(path: str | Path, cooldown_seconds: int, now_ts: int) -> dict[str, int]:
    trades_path = Path(path)
    if not trades_path.exists() or trades_path.stat().st_size == 0:
        return {}

    cooldowns: dict[str, int] = {}
    try:
        with trades_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                symbol = str(row.get("symbol", "")).strip()
                if not symbol:
                    continue
                status = str(row.get("status", "")).strip().upper()
                if status and status != "CLOSED":
                    continue
                raw_ts = row.get("exit_ts") or row.get("entry_ts")
                try:
                    base_ts = int(float(str(raw_ts)))
                except (TypeError, ValueError):
                    continue
                until_ts = base_ts + cooldown_seconds
                if until_ts > now_ts:
                    cooldowns[symbol] = max(cooldowns.get(symbol, 0), until_ts)
    except (OSError, csv.Error):
        return {}
    return cooldowns


def normalize_ticker(row: dict) -> TickerSnapshot | None:
    symbol = str(row.get("symbol", ""))
    if not symbol.endswith("USDT"):
        return None

    try:
        price = float(row["lastPrice"])
        bid = float(row["bidPrice"])
        ask = float(row["askPrice"])
        quote_volume = float(row["quoteVolume"])
        price_change_pct = float(row.get("priceChangePercent", 0.0))
    except (KeyError, TypeError, ValueError):
        return None

    if price <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        return None

    return TickerSnapshot(
        symbol=symbol,
        price=price,
        bid=bid,
        ask=ask,
        quote_volume=quote_volume,
        price_change_pct=price_change_pct,
    )


def normalize_kline(row: list) -> Candle | None:
    try:
        return Candle(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    except (IndexError, TypeError, ValueError):
        return None


def fetch_spot_tickers() -> dict[str, TickerSnapshot]:
    response = requests.get(BINANCE_SPOT_TICKER_URL, timeout=15)
    response.raise_for_status()
    snapshots: dict[str, TickerSnapshot] = {}
    for row in response.json():
        snapshot = normalize_ticker(row)
        if snapshot is not None:
            snapshots[snapshot.symbol] = snapshot
    return snapshots


def fetch_spot_klines(symbol: str, interval: str, limit: int) -> list[Candle]:
    response = requests.get(
        BINANCE_SPOT_KLINES_URL,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    response.raise_for_status()
    candles: list[Candle] = []
    for row in response.json():
        candle = normalize_kline(row)
        if candle is not None:
            candles.append(candle)
    return candles


def interval_to_milliseconds(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    if unit not in multipliers:
        raise ValueError(f"intervalo nao suportado: {interval}")
    return value * multipliers[unit]


def rank_candidates(
    previous: dict[str, TickerSnapshot],
    current: dict[str, TickerSnapshot],
    config: ScannerConfig,
    now_ts: int,
    cooldowns: dict[str, int],
) -> list[MomentumCandidate]:
    candidates: list[MomentumCandidate] = []

    for symbol, snapshot in current.items():
        prev = previous.get(symbol)
        if prev is None or prev.price <= 0:
            continue
        if cooldowns.get(symbol, 0) > now_ts:
            continue

        move_pct = ((snapshot.price - prev.price) / prev.price) * 100.0
        if move_pct < config.min_move_pct or move_pct > config.max_move_pct:
            continue
        if snapshot.quote_volume < config.min_quote_volume:
            continue
        if snapshot.spread_pct > config.max_spread_pct:
            continue

        candidates.append(
            MomentumCandidate(
                symbol=symbol,
                price=snapshot.price,
                previous_price=prev.price,
                move_pct=move_pct,
                spread_pct=snapshot.spread_pct,
                quote_volume=snapshot.quote_volume,
            )
        )

    return sorted(candidates, key=lambda candidate: (candidate.move_pct, candidate.quote_volume), reverse=True)


def basic_snapshot_filter(snapshot: TickerSnapshot, config: ScannerConfig, now_ts: int, cooldowns: dict[str, int]) -> bool:
    if cooldowns.get(snapshot.symbol, 0) > now_ts:
        return False
    if snapshot.quote_volume < config.min_quote_volume:
        return False
    if snapshot.spread_pct > config.max_spread_pct:
        return False
    if snapshot.price_change_pct < config.min_24h_change_pct:
        return False
    return True


def prefilter_breakout_symbols(
    snapshots: dict[str, TickerSnapshot],
    config: ScannerConfig,
    now_ts: int,
    cooldowns: dict[str, int],
) -> list[TickerSnapshot]:
    filtered = [
        snapshot
        for snapshot in snapshots.values()
        if basic_snapshot_filter(snapshot, config, now_ts, cooldowns)
    ]
    filtered.sort(key=lambda snapshot: (snapshot.price_change_pct, snapshot.quote_volume), reverse=True)
    return filtered[: config.prefilter_symbols]


def detect_breakout_candidate(
    snapshot: TickerSnapshot,
    candles: list[Candle],
    config: ScannerConfig,
    now_ms: int | None = None,
) -> MomentumCandidate | None:
    required = config.breakout_lookback + 1
    if len(candles) < required:
        return None

    current = candles[-1]
    history = candles[-required:-1]
    if current.open <= 0:
        return None

    breakout_high = max(candle.high for candle in history)
    median_volume = statistics.median(candle.volume for candle in history)
    if median_volume <= 0:
        return None

    candle_move_pct = ((current.close - current.open) / current.open) * 100.0
    effective_volume = current.volume
    if config.project_current_volume and now_ms is not None and now_ms > current.open_time:
        elapsed_fraction = (now_ms - current.open_time) / interval_to_milliseconds(config.interval)
        elapsed_fraction = min(1.0, max(config.min_volume_projection_fraction, elapsed_fraction))
        effective_volume = current.volume / elapsed_fraction
    volume_multiplier = effective_volume / median_volume
    breakout_price = breakout_high * (1.0 + config.breakout_buffer_pct)
    breakout_extension_pct = ((current.close - breakout_high) / breakout_high) * 100.0 if breakout_high > 0 else 0.0

    if current.close <= breakout_price:
        return None
    if breakout_extension_pct > config.max_breakout_extension_pct:
        return None
    if candle_move_pct < config.min_candle_move_pct or candle_move_pct > config.max_candle_move_pct:
        return None
    if volume_multiplier < config.min_volume_multiplier:
        return None
    if snapshot.price_change_pct < config.min_24h_change_pct:
        return None

    score = candle_move_pct + (volume_multiplier * 0.5) + (snapshot.price_change_pct * 0.1)
    return MomentumCandidate(
        symbol=snapshot.symbol,
        price=snapshot.price,
        previous_price=current.open,
        move_pct=candle_move_pct,
        spread_pct=snapshot.spread_pct,
        quote_volume=snapshot.quote_volume,
        volume_multiplier=volume_multiplier,
        breakout_high=breakout_high,
        candle_open=current.open,
        price_change_pct=snapshot.price_change_pct,
        score=score,
    )


def rank_breakout_candidates(
    snapshots: dict[str, TickerSnapshot],
    candles_by_symbol: dict[str, list[Candle]],
    config: ScannerConfig,
    now_ts: int,
    cooldowns: dict[str, int],
) -> list[MomentumCandidate]:
    candidates: list[MomentumCandidate] = []
    for snapshot in snapshots.values():
        if not basic_snapshot_filter(snapshot, config, now_ts, cooldowns):
            continue
        candidate = detect_breakout_candidate(
            snapshot,
            candles_by_symbol.get(snapshot.symbol, []),
            config,
            now_ms=now_ts * 1000,
        )
        if candidate is not None:
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.move_pct,
            candidate.volume_multiplier,
            candidate.score,
            candidate.quote_volume,
        ),
        reverse=True,
    )


def build_live_breakout_candidates(
    snapshots: dict[str, TickerSnapshot],
    config: ScannerConfig,
    now_ts: int,
    cooldowns: dict[str, int],
) -> list[MomentumCandidate]:
    candles_by_symbol: dict[str, list[Candle]] = {}
    for snapshot in prefilter_breakout_symbols(snapshots, config, now_ts, cooldowns):
        try:
            candles_by_symbol[snapshot.symbol] = fetch_spot_klines(
                snapshot.symbol,
                config.interval,
                config.kline_limit,
            )
        except Exception:
            continue
    return rank_breakout_candidates(snapshots, candles_by_symbol, config, now_ts, cooldowns)


def position_from_candidate(candidate: MomentumCandidate, config: ScannerConfig, now_ts: int) -> PaperSpotPosition:
    quantity = config.notional_usdc / candidate.price
    take_profit_price = candidate.price * (1.0 + config.take_profit_pct) if config.take_profit_pct > 0 else 0.0
    return PaperSpotPosition(
        symbol=candidate.symbol,
        entry_time_utc=datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        entry_ts=now_ts,
        entry_price=round(candidate.price, 8),
        notional_usdc=round(config.notional_usdc, 4),
        quantity=round(quantity, 10),
        stop_price=round(candidate.price * (1.0 - config.stop_pct), 8),
        take_profit_price=round(take_profit_price, 8),
        max_hold_seconds=config.max_hold_seconds,
        fee_rate=config.fee_rate,
        highest_price=round(candidate.price, 8),
        trailing_activation_price=round(candidate.price * (1.0 + config.trailing_activation_pct), 8),
        trailing_pct=config.trailing_pct,
    )


def update_position_trailing(position: PaperSpotPosition, current_price: float) -> PaperSpotPosition:
    highest_price = max(position.highest_price or position.entry_price, current_price)
    trailing_stop_price = position.trailing_stop_price
    if position.trailing_pct > 0 and highest_price >= position.trailing_activation_price > 0:
        trailing_stop_price = highest_price * (1.0 - position.trailing_pct)
    return replace(
        position,
        highest_price=round(highest_price, 8),
        trailing_stop_price=round(trailing_stop_price, 8),
    )


def check_exit(position: PaperSpotPosition, current_price: float, now_ts: int) -> str | None:
    if current_price <= position.stop_price:
        return "STOP_LOSS"
    if position.trailing_stop_price > 0 and current_price <= position.trailing_stop_price:
        return "TRAILING_STOP"
    if position.take_profit_price > 0 and current_price >= position.take_profit_price:
        return "TAKE_PROFIT"
    if now_ts >= position.entry_ts + position.max_hold_seconds:
        return "TIME_EXIT"
    return None


def close_position(position: PaperSpotPosition, exit_price: float, reason: str) -> PaperSpotPosition:
    now = utc_now()
    gross = (exit_price - position.entry_price) * position.quantity
    fees = (position.entry_price * position.quantity * position.fee_rate) + (
        exit_price * position.quantity * position.fee_rate
    )
    pnl = gross - fees
    return replace(
        position,
        status="CLOSED",
        exit_time_utc=now.isoformat(),
        exit_ts=int(now.timestamp()),
        exit_price=round(exit_price, 8),
        exit_reason=reason,
        gross_pnl_usdc=round(gross, 8),
        fees_usdc=round(fees, 8),
        pnl_usdc=round(pnl, 8),
        pnl_pct_notional=round((pnl / position.notional_usdc) * 100.0, 8),
        win=pnl > 0,
    )


def append_trade(path: str, position: PaperSpotPosition) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(position).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(position))


def latest_price(symbol: str, snapshots: dict[str, TickerSnapshot]) -> float | None:
    snapshot = snapshots.get(symbol)
    if snapshot is None:
        return None
    return snapshot.price


def run_loop(
    config: ScannerConfig,
    poll_seconds: int,
    trades_csv: str,
    cycles: int,
    once: bool,
    cooldowns_json: str,
    state_json: str,
) -> None:
    print("=== PAPER BINANCE SPOT MOMENTUM SCANNER ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"CSV: {trades_csv}")
    print(
        f"Config: breakout {config.interval} lookback={config.breakout_lookback}, "
        f"candle={config.min_candle_move_pct:.2f}%..{config.max_candle_move_pct:.2f}%, "
        f"vol_mult>={config.min_volume_multiplier:.2f}, 24h>={config.min_24h_change_pct:.2f}%, "
        f"notional={config.notional_usdc:.2f}, stop={config.stop_pct:.2%}, "
        f"tp={config.take_profit_pct:.2%}, trail={config.trailing_pct:.2%} apos "
        f"{config.trailing_activation_pct:.2%}, max_hold={config.max_hold_seconds // 3600}h"
    )

    open_position = load_open_position(state_json)
    start_ts = int(utc_now().timestamp())
    cooldowns: dict[str, int] = load_symbol_cooldowns(cooldowns_json, start_ts)
    seeded_cooldowns = seed_cooldowns_from_trades(trades_csv, config.cooldown_seconds, start_ts)
    if seeded_cooldowns:
        cooldowns.update({symbol: max(cooldowns.get(symbol, 0), until_ts) for symbol, until_ts in seeded_cooldowns.items()})
        save_symbol_cooldowns(cooldowns_json, cooldowns)
        print(f"Cooldown inicial reconstruido do CSV: {len(seeded_cooldowns)} simbolos bloqueados por 24h.")
    if open_position is not None:
        print(
            f"Posicao aberta restaurada: {open_position.symbol} "
            f"entry={open_position.entry_price:.8f} stop={open_position.stop_price:.8f}"
        )
    iterations = 0

    while True:
        now = utc_now()
        now_ts = int(now.timestamp())
        try:
            current = fetch_spot_tickers()
            if open_position is not None:
                price = latest_price(open_position.symbol, current)
                if price is None:
                    print(f"[{now.strftime('%H:%M:%S')}] ABERTA {open_position.symbol} sem preco atual")
                else:
                    open_position = update_position_trailing(open_position, price)
                    save_open_position(state_json, open_position)
                    reason = check_exit(open_position, price, now_ts)
                    if reason is None:
                        print(
                            f"[{now.strftime('%H:%M:%S')}] ABERTA {open_position.symbol} "
                            f"price={price:.8f} stop={open_position.stop_price:.8f} "
                            f"trail={open_position.trailing_stop_price:.8f}"
                        )
                    else:
                        closed = close_position(open_position, price, reason)
                        append_trade(trades_csv, closed)
                        cooldowns[closed.symbol] = closed.exit_ts + config.cooldown_seconds
                        save_symbol_cooldowns(cooldowns_json, cooldowns)
                        open_position = None
                        save_open_position(state_json, None)
                        print(
                            f"[{now.strftime('%H:%M:%S')}] FECHOU {closed.symbol} {reason} "
                            f"entry={closed.entry_price:.8f} exit={closed.exit_price:.8f} pnl={closed.pnl_usdc:.6f}"
                        )
            else:
                candidates = build_live_breakout_candidates(current, config, now_ts, cooldowns)
                if not candidates:
                    print(f"[{now.strftime('%H:%M:%S')}] HOLD sem candidato")
                else:
                    top = candidates[0]
                    open_position = position_from_candidate(top, config, now_ts)
                    save_open_position(state_json, open_position)
                    print(
                        f"[{now.strftime('%H:%M:%S')}] ABRIU PAPER {top.symbol} "
                        f"candle={top.move_pct:.2f}% 24h={top.price_change_pct:.2f}% "
                        f"vol_mult={top.volume_multiplier:.2f} price={top.price:.8f} "
                        f"breakout={top.breakout_high:.8f} trail_at={open_position.trailing_activation_price:.8f}"
                    )
        except Exception as exc:
            print(f"[{now.strftime('%H:%M:%S')}] ERRO: {exc}")

        iterations += 1
        if once or (cycles and iterations >= cycles):
            break
        time.sleep(poll_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper Binance Spot momentum scanner.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--breakout-lookback", type=int, default=20)
    parser.add_argument("--kline-limit", type=int, default=60)
    parser.add_argument("--prefilter-symbols", type=int, default=40)
    parser.add_argument("--min-move-pct", type=float, default=1.0)
    parser.add_argument("--max-move-pct", type=float, default=8.0)
    parser.add_argument("--min-candle-move-pct", type=float, default=2.0)
    parser.add_argument("--max-candle-move-pct", type=float, default=18.0)
    parser.add_argument("--min-volume-multiplier", type=float, default=3.0)
    parser.add_argument("--no-project-current-volume", action="store_true")
    parser.add_argument("--min-volume-projection-fraction", type=float, default=0.2)
    parser.add_argument("--breakout-buffer-pct", type=float, default=0.001)
    parser.add_argument("--max-breakout-extension-pct", type=float, default=12.0)
    parser.add_argument("--min-24h-change-pct", type=float, default=5.0)
    parser.add_argument("--min-quote-volume", type=float, default=1_000_000.0)
    parser.add_argument("--max-spread-pct", type=float, default=0.35)
    parser.add_argument("--notional", type=float, default=25.0)
    parser.add_argument("--stop-pct", type=float, default=0.05)
    parser.add_argument("--take-profit-pct", type=float, default=0.0)
    parser.add_argument("--trailing-activation-pct", type=float, default=0.10)
    parser.add_argument("--trailing-pct", type=float, default=0.05)
    parser.add_argument("--max-hold-seconds", type=int, default=43_200)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--cooldown-seconds", type=int, default=86_400)
    parser.add_argument("--cooldowns-json", default="paper_binance_spot_momentum_scanner_cooldowns.json")
    parser.add_argument("--state-json", default="paper_binance_spot_momentum_scanner_state.json")
    parser.add_argument("--trades-csv", default="paper_binance_spot_momentum_scanner_trades.csv")
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true")
    return parser


def default_config(args: argparse.Namespace) -> ScannerConfig:
    return ScannerConfig(
        interval=args.interval,
        breakout_lookback=args.breakout_lookback,
        kline_limit=args.kline_limit,
        prefilter_symbols=args.prefilter_symbols,
        min_move_pct=args.min_move_pct,
        max_move_pct=args.max_move_pct,
        min_candle_move_pct=args.min_candle_move_pct,
        max_candle_move_pct=args.max_candle_move_pct,
        min_volume_multiplier=args.min_volume_multiplier,
        project_current_volume=not args.no_project_current_volume,
        min_volume_projection_fraction=args.min_volume_projection_fraction,
        breakout_buffer_pct=args.breakout_buffer_pct,
        max_breakout_extension_pct=args.max_breakout_extension_pct,
        min_24h_change_pct=args.min_24h_change_pct,
        min_quote_volume=args.min_quote_volume,
        max_spread_pct=args.max_spread_pct,
        notional_usdc=args.notional,
        stop_pct=args.stop_pct,
        take_profit_pct=args.take_profit_pct,
        trailing_activation_pct=args.trailing_activation_pct,
        trailing_pct=args.trailing_pct,
        max_hold_seconds=args.max_hold_seconds,
        fee_rate=args.fee_rate,
        cooldown_seconds=args.cooldown_seconds,
    )


def main() -> None:
    args = build_arg_parser().parse_args()
    run_loop(
        config=default_config(args),
        poll_seconds=args.poll_seconds,
        trades_csv=args.trades_csv,
        cycles=args.cycles,
        once=args.once,
        cooldowns_json=args.cooldowns_json,
        state_json=args.state_json,
    )


if __name__ == "__main__":
    main()
