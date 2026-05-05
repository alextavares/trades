#!/usr/bin/env python3
"""Paper live para Binance EMA scalp 5m com ADX.

Simula entradas e saidas em tempo real usando o melhor setup encontrado no
backtest inicial. Nao envia ordens reais.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from backtest_binance_ema_scalp import (
    EmaScalpConfig,
    add_ema_indicators,
    calculate_pnl,
    detect_ema_scalp_signal,
)


BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUTURES_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"


@dataclass(frozen=True)
class PaperEmaScalpPosition:
    symbol: str
    direction: str
    entry_time_utc: str
    entry_ts: int
    entry_price: float
    notional_usdc: float
    stop_price: float
    take_profit_price: float
    max_hold_seconds: int
    fee_rate: float
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


def fetch_futures_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    response = requests.get(
        BINANCE_FUTURES_KLINES_URL,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
            "ignore",
        ],
    )
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_ts"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["ts", "close_ts", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)


def fetch_futures_price(symbol: str) -> float:
    response = requests.get(BINANCE_FUTURES_PRICE_URL, params={"symbol": symbol}, timeout=10)
    response.raise_for_status()
    return float(response.json()["price"])


def position_from_signal(symbol: str, direction: str, entry_price: float, config: EmaScalpConfig) -> PaperEmaScalpPosition:
    now = utc_now()
    if direction == "LONG":
        stop_price = entry_price * (1.0 - config.stop_pct)
        take_profit_price = entry_price * (1.0 + config.take_profit_pct)
    else:
        stop_price = entry_price * (1.0 + config.stop_pct)
        take_profit_price = entry_price * (1.0 - config.take_profit_pct)
    return PaperEmaScalpPosition(
        symbol=symbol,
        direction=direction,
        entry_time_utc=now.isoformat(),
        entry_ts=int(now.timestamp()),
        entry_price=round(entry_price, 4),
        notional_usdc=round(config.notional_usdc, 4),
        stop_price=round(stop_price, 4),
        take_profit_price=round(take_profit_price, 4),
        max_hold_seconds=config.max_hold_seconds,
        fee_rate=config.fee_rate,
    )


def check_exit(position: PaperEmaScalpPosition, current_price: float, now_ts: int) -> str | None:
    if position.direction == "LONG":
        if current_price <= position.stop_price:
            return "STOP_LOSS"
        if current_price >= position.take_profit_price:
            return "TAKE_PROFIT"
    else:
        if current_price >= position.stop_price:
            return "STOP_LOSS"
        if current_price <= position.take_profit_price:
            return "TAKE_PROFIT"

    if now_ts >= position.entry_ts + position.max_hold_seconds:
        return "TIME_EXIT"
    return None


def close_position(position: PaperEmaScalpPosition, exit_price: float, reason: str) -> PaperEmaScalpPosition:
    now = utc_now()
    gross, fees, pnl = calculate_pnl(
        position.direction,
        position.entry_price,
        exit_price,
        position.notional_usdc,
        position.fee_rate,
    )
    return replace(
        position,
        status="CLOSED",
        exit_time_utc=now.isoformat(),
        exit_ts=int(now.timestamp()),
        exit_price=round(exit_price, 4),
        exit_reason=reason,
        gross_pnl_usdc=round(gross, 6),
        fees_usdc=round(fees, 6),
        pnl_usdc=round(pnl, 6),
        pnl_pct_notional=round((pnl / position.notional_usdc) * 100.0, 6),
        win=pnl > 0,
    )


def append_trade(path: str, position: PaperEmaScalpPosition) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(position).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(position))


def load_open_position(path: str | Path) -> PaperEmaScalpPosition | None:
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
        return PaperEmaScalpPosition(**raw)
    except TypeError:
        return None


def save_open_position(path: str | Path, position: PaperEmaScalpPosition | None) -> None:
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


def build_candidate(config: EmaScalpConfig, kline_limit: int) -> tuple[PaperEmaScalpPosition | None, str, str]:
    df = add_ema_indicators(fetch_futures_klines(config.symbol, config.interval, kline_limit), config)
    if len(df) < max(config.ema_slow, config.breakout_lookback, config.adx_period) + 5:
        return None, "", "HOLD dados insuficientes"

    prev_row = df.iloc[-3]
    signal_row = df.iloc[-2]
    signal_close_ts = pd.Timestamp(signal_row["close_ts"]).isoformat()
    signal = detect_ema_scalp_signal(prev_row, signal_row, config)
    if signal is None:
        return (
            None,
            signal_close_ts,
            f"HOLD close={float(signal_row['close']):.2f} adx={float(signal_row['adx']):.2f} "
            f"ema={float(signal_row['ema_fast']):.2f}/{float(signal_row['ema_mid']):.2f}/{float(signal_row['ema_slow']):.2f}",
        )

    entry_price = fetch_futures_price(config.symbol)
    position = position_from_signal(config.symbol, signal, entry_price, config)
    return (
        position,
        signal_close_ts,
        f"SIGNAL {signal} price={entry_price:.2f} adx={float(signal_row['adx']):.2f} "
        f"slope={float(signal_row['ema_slope']):.2f}",
    )


def default_strategy_config(args: argparse.Namespace) -> EmaScalpConfig:
    return EmaScalpConfig(
        symbol=args.symbol,
        market="futures",
        interval=args.interval,
        ema_fast=args.ema_fast,
        ema_mid=args.ema_mid,
        ema_slow=args.ema_slow,
        slope_lookback=args.slope_lookback,
        min_ema_gap_usd=args.min_ema_gap_usd,
        min_slope_usd=args.min_slope_usd,
        max_price_ema_fast_distance_usd=args.max_price_ema_fast_distance_usd,
        entry_mode=args.entry_mode,
        trend_filter=args.trend_filter,
        adx_period=args.adx_period,
        min_adx=args.min_adx,
        breakout_lookback=args.breakout_lookback,
        breakout_buffer_pct=args.breakout_buffer_pct,
        notional_usdc=args.notional,
        fee_rate=args.fee_rate,
        stop_pct=args.stop_pct,
        take_profit_pct=args.take_profit_pct,
        trailing_pct=0.0,
        max_hold_seconds=args.max_hold_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )


def run_loop(
    config: EmaScalpConfig,
    kline_limit: int,
    poll_seconds: int,
    trades_csv: str,
    cycles: int,
    once: bool,
    state_json: str,
) -> None:
    print("=== PAPER BINANCE EMA SCALP LIVE ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"CSV: {trades_csv}")
    print(
        f"Config: {config.symbol} {config.interval}, EMA={config.ema_fast}/{config.ema_mid}/{config.ema_slow}, "
        f"filter={config.trend_filter}, ADX>={config.min_adx}, notional={config.notional_usdc:.2f}, "
        f"stop={config.stop_pct:.2%}, tp={config.take_profit_pct:.2%}, max_hold={config.max_hold_seconds // 3600}h"
    )

    open_position = load_open_position(state_json)
    last_signal_close_ts = ""
    cooldown_until_ts = 0
    if open_position is not None:
        print(
            f"Posicao aberta restaurada: {open_position.direction} "
            f"entry={open_position.entry_price:.2f} stop={open_position.stop_price:.2f}"
        )
    iterations = 0

    while True:
        now = utc_now()
        try:
            if open_position is not None:
                price = fetch_futures_price(config.symbol)
                save_open_position(state_json, open_position)
                reason = check_exit(open_position, price, int(now.timestamp()))
                if reason is None:
                    print(
                        f"[{now.strftime('%H:%M:%S')}] ABERTA {open_position.direction} "
                        f"price={price:.2f} stop={open_position.stop_price:.2f} tp={open_position.take_profit_price:.2f}"
                    )
                else:
                    closed = close_position(open_position, price, reason)
                    append_trade(trades_csv, closed)
                    cooldown_until_ts = closed.exit_ts + config.cooldown_seconds
                    open_position = None
                    save_open_position(state_json, None)
                    print(
                        f"[{now.strftime('%H:%M:%S')}] FECHOU {closed.direction} {reason} "
                        f"entry={closed.entry_price:.2f} exit={closed.exit_price:.2f} pnl={closed.pnl_usdc:.4f}"
                    )
            elif int(now.timestamp()) < cooldown_until_ts:
                remaining = cooldown_until_ts - int(now.timestamp())
                print(f"[{now.strftime('%H:%M:%S')}] COOL_DOWN restante={remaining}s")
            else:
                candidate, signal_close_ts, message = build_candidate(config, kline_limit)
                if signal_close_ts and signal_close_ts == last_signal_close_ts:
                    print(f"[{now.strftime('%H:%M:%S')}] Aguardando novo candle {config.interval}")
                else:
                    if signal_close_ts:
                        last_signal_close_ts = signal_close_ts
                    print(f"[{now.strftime('%H:%M:%S')}] {message}")
                    if candidate is not None:
                        open_position = candidate
                        save_open_position(state_json, open_position)
                        print(
                            f"[{now.strftime('%H:%M:%S')}] ABRIU PAPER {candidate.direction} "
                            f"entry={candidate.entry_price:.2f} stop={candidate.stop_price:.2f} "
                            f"tp={candidate.take_profit_price:.2f} notional={candidate.notional_usdc:.2f}"
                        )
        except Exception as exc:
            print(f"[{now.strftime('%H:%M:%S')}] ERRO: {exc}")

        iterations += 1
        if once or (cycles and iterations >= cycles):
            break
        time.sleep(poll_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper live Binance futures EMA scalp.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--kline-limit", type=int, default=500)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--ema-fast", type=int, default=9)
    parser.add_argument("--ema-mid", type=int, default=21)
    parser.add_argument("--ema-slow", type=int, default=60)
    parser.add_argument("--slope-lookback", type=int, default=9)
    parser.add_argument("--entry-mode", default="trend", choices=["trend", "pullback"])
    parser.add_argument("--trend-filter", default="adx", choices=["none", "adx", "breakout", "adx-breakout"])
    parser.add_argument("--adx-period", type=int, default=14)
    parser.add_argument("--min-adx", type=float, default=22.0)
    parser.add_argument("--breakout-lookback", type=int, default=12)
    parser.add_argument("--breakout-buffer-pct", type=float, default=0.0002)
    parser.add_argument("--min-ema-gap-usd", type=float, default=15.0)
    parser.add_argument("--min-slope-usd", type=float, default=5.0)
    parser.add_argument("--max-price-ema-fast-distance-usd", type=float, default=500.0)
    parser.add_argument("--notional", type=float, default=100.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--stop-pct", type=float, default=0.001)
    parser.add_argument("--take-profit-pct", type=float, default=0.0015)
    parser.add_argument("--max-hold-seconds", type=int, default=180)
    parser.add_argument("--cooldown-seconds", type=int, default=30)
    parser.add_argument("--state-json", default="paper_binance_ema_scalp_state.json")
    parser.add_argument("--trades-csv", default="paper_binance_ema_scalp_live_trades.csv")
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = default_strategy_config(args)
    run_loop(
        config=config,
        kline_limit=args.kline_limit,
        poll_seconds=args.poll_seconds,
        trades_csv=args.trades_csv,
        cycles=args.cycles,
        once=args.once,
        state_json=args.state_json,
    )


if __name__ == "__main__":
    main()
