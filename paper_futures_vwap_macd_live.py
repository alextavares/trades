#!/usr/bin/env python3
"""Paper trader live para BTC futures usando VWAP + MACD em candles de 1 minuto."""

from __future__ import annotations

import argparse
import csv
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUTURES_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"


@dataclass(frozen=True)
class FuturesConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    kline_limit: int = 240
    poll_seconds: int = 5
    margin_usdc: float = 10.0
    leverage: float = 3.0
    fee_rate: float = 0.0004
    stop_atr_mult: float = 1.0
    reward_r_mult: float = 1.5
    atr_period: int = 14
    max_hold_minutes: int = 20
    min_stop_pct: float = 0.001
    trades_csv: str = "paper_futures_vwap_macd_trades.csv"


@dataclass(frozen=True)
class FuturesPosition:
    symbol: str
    direction: str
    entry_time_utc: str
    entry_ts: int
    entry_price: float
    margin_usdc: float
    leverage: float
    notional_usdc: float
    stop_price: float
    take_profit_price: float
    max_hold_minutes: int
    status: str = "OPEN"
    exit_time_utc: str = ""
    exit_ts: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_usdc: float = 0.0
    pnl_pct_margin: float = 0.0
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


def add_indicators(df: pd.DataFrame, config: FuturesConfig) -> pd.DataFrame:
    out = df.copy()
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    session = out["ts"].dt.floor("D")
    volume_sum = out["volume"].groupby(session).cumsum()
    out["vwap"] = (typical * out["volume"]).groupby(session).cumsum() / volume_sum

    out["ema_fast"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=21, adjust=False).mean()
    macd_fast = out["close"].ewm(span=12, adjust=False).mean()
    macd_slow = out["close"].ewm(span=26, adjust=False).mean()
    out["macd_line"] = macd_fast - macd_slow
    out["macd_signal"] = out["macd_line"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd_line"] - out["macd_signal"]

    prev_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.rolling(config.atr_period).mean()
    return out


def detect_vwap_macd_signal(prev_row: pd.Series, curr_row: pd.Series, config: FuturesConfig) -> str | None:
    del config
    prev_hist = float(prev_row["macd_hist"])
    curr_hist = float(curr_row["macd_hist"])
    close = float(curr_row["close"])
    vwap = float(curr_row["vwap"])
    ema_fast = float(curr_row["ema_fast"])
    ema_slow = float(curr_row["ema_slow"])

    if close > vwap and ema_fast > ema_slow and prev_hist <= 0 and curr_hist > 0:
        return "LONG"
    if close < vwap and ema_fast < ema_slow and prev_hist >= 0 and curr_hist < 0:
        return "SHORT"
    return None


def position_from_signal(
    symbol: str,
    direction: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    atr: float,
    config: FuturesConfig,
) -> FuturesPosition:
    min_stop = entry_price * config.min_stop_pct
    stop_distance = max(float(atr) * config.stop_atr_mult, min_stop)
    if direction == "LONG":
        stop_price = entry_price - stop_distance
        take_profit = entry_price + stop_distance * config.reward_r_mult
    else:
        stop_price = entry_price + stop_distance
        take_profit = entry_price - stop_distance * config.reward_r_mult

    entry_dt = pd.Timestamp(entry_time).tz_convert("UTC")
    return FuturesPosition(
        symbol=symbol,
        direction=direction,
        entry_time_utc=entry_dt.isoformat(),
        entry_ts=int(entry_dt.timestamp()),
        entry_price=round(entry_price, 2),
        margin_usdc=round(config.margin_usdc, 4),
        leverage=round(config.leverage, 4),
        notional_usdc=round(config.margin_usdc * config.leverage, 4),
        stop_price=round(stop_price, 2),
        take_profit_price=round(take_profit, 2),
        max_hold_minutes=config.max_hold_minutes,
    )


def calculate_futures_pnl(position: FuturesPosition, exit_price: float, fee_rate: float) -> float:
    if position.direction == "LONG":
        gross = position.notional_usdc * ((exit_price - position.entry_price) / position.entry_price)
    else:
        gross = position.notional_usdc * ((position.entry_price - exit_price) / position.entry_price)
    fees = position.notional_usdc * fee_rate * 2.0
    return gross - fees


def check_position_exit(position: FuturesPosition, current_price: float, now_ts: int) -> str | None:
    if position.direction == "LONG":
        if current_price >= position.take_profit_price:
            return "TAKE_PROFIT"
        if current_price <= position.stop_price:
            return "STOP_LOSS"
    else:
        if current_price <= position.take_profit_price:
            return "TAKE_PROFIT"
        if current_price >= position.stop_price:
            return "STOP_LOSS"

    if now_ts >= position.entry_ts + position.max_hold_minutes * 60:
        return "TIME_EXIT"
    return None


def close_position(position: FuturesPosition, exit_price: float, reason: str, config: FuturesConfig) -> FuturesPosition:
    now = utc_now()
    pnl = calculate_futures_pnl(position, exit_price, config.fee_rate)
    return replace(
        position,
        status="CLOSED",
        exit_time_utc=now.isoformat(),
        exit_ts=int(now.timestamp()),
        exit_price=round(exit_price, 2),
        exit_reason=reason,
        pnl_usdc=round(pnl, 4),
        pnl_pct_margin=round((pnl / position.margin_usdc) * 100.0, 4),
        win=pnl > 0,
    )


def append_trade(path: str, position: FuturesPosition) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(position).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(position))


def build_candidate_position(config: FuturesConfig) -> tuple[FuturesPosition | None, str]:
    df = add_indicators(fetch_futures_klines(config.symbol, config.interval, config.kline_limit), config)
    if len(df) < max(30, config.atr_period + 2):
        return None, "HOLD dados insuficientes"

    prev_row = df.iloc[-3]
    signal_row = df.iloc[-2]
    signal = detect_vwap_macd_signal(prev_row, signal_row, config)
    if signal is None:
        hist = float(signal_row["macd_hist"])
        return None, (
            f"HOLD close={float(signal_row['close']):.2f} vwap={float(signal_row['vwap']):.2f} "
            f"hist={hist:.2f}"
        )

    atr = float(signal_row["atr"])
    if pd.isna(atr) or atr <= 0:
        return None, "HOLD ATR invalido"
    entry_price = fetch_futures_price(config.symbol)
    position = position_from_signal(
        symbol=config.symbol,
        direction=signal,
        entry_time=utc_now(),
        entry_price=entry_price,
        atr=atr,
        config=config,
    )
    return position, f"SIGNAL {signal} price={entry_price:.2f} atr={atr:.2f}"


def run_loop(config: FuturesConfig, cycles: int = 0, once: bool = False) -> None:
    print("=== PAPER BTC FUTURES VWAP + MACD 1M ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"CSV: {config.trades_csv}")
    print(
        f"Config: margin={config.margin_usdc:.2f}, leverage={config.leverage:.1f}x, "
        f"stop={config.stop_atr_mult:.2f} ATR, alvo={config.reward_r_mult:.2f}R"
    )

    open_position: FuturesPosition | None = None
    last_signal_close_ts = ""
    iterations = 0

    while True:
        now = utc_now()
        try:
            if open_position is not None:
                price = fetch_futures_price(config.symbol)
                reason = check_position_exit(open_position, price, int(now.timestamp()))
                if reason:
                    closed = close_position(open_position, price, reason, config)
                    append_trade(config.trades_csv, closed)
                    print(
                        f"[{now.strftime('%H:%M:%S')}] FECHOU {closed.direction} {reason} "
                        f"entry={closed.entry_price:.2f} exit={closed.exit_price:.2f} pnl={closed.pnl_usdc:.4f}"
                    )
                    open_position = None
                else:
                    print(
                        f"[{now.strftime('%H:%M:%S')}] ABERTA {open_position.direction} "
                        f"price={price:.2f} stop={open_position.stop_price:.2f} tp={open_position.take_profit_price:.2f}"
                    )
            else:
                df = add_indicators(fetch_futures_klines(config.symbol, config.interval, config.kline_limit), config)
                signal_close_ts = pd.Timestamp(df.iloc[-2]["close_ts"]).isoformat()
                if signal_close_ts == last_signal_close_ts:
                    print(f"[{now.strftime('%H:%M:%S')}] Aguardando novo candle 1m")
                else:
                    last_signal_close_ts = signal_close_ts
                    candidate, message = build_candidate_position(config)
                    print(f"[{now.strftime('%H:%M:%S')}] {message}")
                    if candidate is not None:
                        open_position = candidate
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
        time.sleep(config.poll_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper live BTC futures com VWAP + MACD em 1m.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--margin", type=float, default=10.0)
    parser.add_argument("--leverage", type=float, default=3.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--stop-atr-mult", type=float, default=1.0)
    parser.add_argument("--reward-r-mult", type=float, default=1.5)
    parser.add_argument("--max-hold-minutes", type=int, default=20)
    parser.add_argument("--trades-csv", default="paper_futures_vwap_macd_trades.csv")
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = FuturesConfig(
        symbol=args.symbol,
        poll_seconds=args.poll_seconds,
        margin_usdc=args.margin,
        leverage=args.leverage,
        fee_rate=args.fee_rate,
        stop_atr_mult=args.stop_atr_mult,
        reward_r_mult=args.reward_r_mult,
        max_hold_minutes=args.max_hold_minutes,
        trades_csv=args.trades_csv,
    )
    run_loop(config, cycles=args.cycles, once=args.once)


if __name__ == "__main__":
    main()
