#!/usr/bin/env python3
"""Backtest de scalp cripto com EMA 1s trend.

O backtest usa klines publicos da Binance. Por padrao, usa Spot 1s porque
o endpoint futures REST nao aceita candles de 1 segundo. O PnL e calculado
como scalp de derivativo/spot alavancado via notional, com taxa de ida e volta.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests


SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"
FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


@dataclass(frozen=True)
class EmaScalpConfig:
    symbol: str = "BTCUSDT"
    market: str = "spot"
    interval: str = "1s"
    lookback_hours: float = 12.0
    ema_fast: int = 60
    ema_mid: int = 300
    ema_slow: int = 600
    slope_lookback: int = 15
    min_ema_gap_usd: float = 2.0
    min_slope_usd: float = 0.2
    max_price_ema_fast_distance_usd: float = 80.0
    entry_mode: str = "trend"
    trend_filter: str = "none"
    adx_period: int = 14
    min_adx: float = 20.0
    breakout_lookback: int = 20
    breakout_buffer_pct: float = 0.0
    notional_usdc: float = 100.0
    fee_rate: float = 0.0004
    stop_pct: float = 0.0010
    take_profit_pct: float = 0.0015
    trailing_pct: float = 0.0
    max_hold_seconds: int = 180
    cooldown_seconds: int = 30


@dataclass(frozen=True)
class EmaScalpTrade:
    entry_time_utc: str
    exit_time_utc: str
    direction: str
    entry_price: float
    exit_price: float
    stop_price: float
    take_profit_price: float
    exit_reason: str
    seconds_held: int
    notional_usdc: float
    gross_pnl_usdc: float
    fees_usdc: float
    pnl_usdc: float
    pnl_pct_notional: float
    win: bool
    ema_fast: float
    ema_mid: float
    ema_slow: float
    ema_slope: float
    price_ema_fast_distance: float


def interval_to_milliseconds(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    if unit not in multipliers:
        raise ValueError(f"intervalo nao suportado: {interval}")
    return value * multipliers[unit]


def klines_url_for_market(market: str) -> str:
    if market == "spot":
        return SPOT_KLINES_URL
    if market == "futures":
        return FUTURES_KLINES_URL
    raise ValueError("market deve ser spot ou futures")


def fetch_binance_klines(
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    market: str = "spot",
    pause_seconds: float = 0.05,
) -> pd.DataFrame:
    url = klines_url_for_market(market)
    step_ms = interval_to_milliseconds(interval)
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    rows: list[list] = []

    while start_ms < end_ms:
        response = requests.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=15,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        last_open_ms = int(batch[-1][0])
        next_start_ms = last_open_ms + step_ms
        if next_start_ms <= start_ms:
            break
        start_ms = next_start_ms
        if len(batch) < 1000:
            break
        time.sleep(pause_seconds)

    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

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
    return df[["ts", "open", "high", "low", "close", "volume"]].dropna().drop_duplicates("ts").reset_index(drop=True)


def add_ema_indicators(df: pd.DataFrame, config: EmaScalpConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=config.ema_fast, adjust=False).mean()
    out["ema_mid"] = out["close"].ewm(span=config.ema_mid, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=config.ema_slow, adjust=False).mean()
    out["ema_slope"] = out["ema_fast"] - out["ema_fast"].shift(config.slope_lookback)
    out["price_ema_fast_distance"] = out["close"] - out["ema_fast"]
    out["breakout_high"] = out["high"].shift(1).rolling(config.breakout_lookback).max()
    out["breakout_low"] = out["low"].shift(1).rolling(config.breakout_lookback).min()

    prev_close = out["close"].shift(1)
    plus_dm = (out["high"] - out["high"].shift(1)).clip(lower=0)
    minus_dm = (out["low"].shift(1) - out["low"]).clip(lower=0)
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / config.adx_period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / config.adx_period, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / config.adx_period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100.0
    out["adx"] = dx.ewm(alpha=1 / config.adx_period, adjust=False).mean()
    return out


def trend_filter_allows(direction: str, row: pd.Series, config: EmaScalpConfig) -> bool:
    close = float(row["close"])
    checks = config.trend_filter.split("-")

    if "adx" in checks:
        adx = float(row.get("adx", 0.0))
        if pd.isna(adx) or adx < config.min_adx:
            return False

    if "breakout" in checks:
        high = float(row.get("breakout_high", float("nan")))
        low = float(row.get("breakout_low", float("nan")))
        if pd.isna(high) or pd.isna(low):
            return False
        if direction == "LONG" and close <= high * (1.0 + config.breakout_buffer_pct):
            return False
        if direction == "SHORT" and close >= low * (1.0 - config.breakout_buffer_pct):
            return False

    return True


def detect_ema_scalp_signal(prev_row: pd.Series, row: pd.Series, config: EmaScalpConfig) -> str | None:
    fast = float(row["ema_fast"])
    mid = float(row["ema_mid"])
    slow = float(row["ema_slow"])
    slope = float(row["ema_slope"])
    close = float(row["close"])
    distance = close - fast
    gap_fast_mid = abs(fast - mid)
    gap_mid_slow = abs(mid - slow)

    if min(gap_fast_mid, gap_mid_slow) < config.min_ema_gap_usd:
        return None
    if abs(distance) > config.max_price_ema_fast_distance_usd:
        return None

    long_trend = fast > mid > slow and slope >= config.min_slope_usd and close >= fast
    short_trend = fast < mid < slow and slope <= -config.min_slope_usd and close <= fast

    if config.entry_mode == "pullback":
        prev_close = float(prev_row["close"])
        prev_fast = float(prev_row["ema_fast"])
        long_trend = long_trend and prev_close <= prev_fast
        short_trend = short_trend and prev_close >= prev_fast

    if long_trend and trend_filter_allows("LONG", row, config):
        return "LONG"
    if short_trend and trend_filter_allows("SHORT", row, config):
        return "SHORT"
    return None


def calculate_pnl(direction: str, entry_price: float, exit_price: float, notional: float, fee_rate: float) -> tuple[float, float, float]:
    if direction == "LONG":
        gross = notional * ((exit_price - entry_price) / entry_price)
    else:
        gross = notional * ((entry_price - exit_price) / entry_price)
    fees = notional * fee_rate * 2.0
    return gross, fees, gross - fees


def run_backtest(df: pd.DataFrame, config: EmaScalpConfig) -> pd.DataFrame:
    data = add_ema_indicators(df, config)
    trades: list[dict] = []
    warmup = max(config.ema_slow, config.slope_lookback) + 2
    index = warmup
    cooldown_until: pd.Timestamp | None = None

    while index < len(data) - 1:
        signal_row = data.iloc[index]
        signal_time = pd.Timestamp(signal_row["ts"])
        if cooldown_until is not None and signal_time < cooldown_until:
            index += 1
            continue

        signal = detect_ema_scalp_signal(data.iloc[index - 1], signal_row, config)
        if signal is None:
            index += 1
            continue

        entry_index = index + 1
        entry_row = data.iloc[entry_index]
        entry_time = pd.Timestamp(entry_row["ts"])
        entry_price = float(entry_row["open"])
        if signal == "LONG":
            stop_price = entry_price * (1.0 - config.stop_pct)
            take_profit_price = entry_price * (1.0 + config.take_profit_pct)
        else:
            stop_price = entry_price * (1.0 + config.stop_pct)
            take_profit_price = entry_price * (1.0 - config.take_profit_pct)

        best_price = entry_price
        exit_price = float(data.iloc[-1]["close"])
        exit_time = pd.Timestamp(data.iloc[-1]["ts"])
        exit_reason = "END_OF_DATA"
        exit_index = len(data) - 1
        max_exit_time = entry_time + pd.Timedelta(seconds=config.max_hold_seconds)

        for scan_index in range(entry_index, len(data)):
            row = data.iloc[scan_index]
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            now = pd.Timestamp(row["ts"])

            if signal == "LONG":
                best_price = max(best_price, high)
                if config.trailing_pct > 0 and best_price > entry_price:
                    stop_price = max(stop_price, best_price * (1.0 - config.trailing_pct))
                if low <= stop_price:
                    exit_price, exit_time, exit_reason, exit_index = stop_price, now, "STOP_LOSS", scan_index
                    break
                if high >= take_profit_price:
                    exit_price, exit_time, exit_reason, exit_index = take_profit_price, now, "TAKE_PROFIT", scan_index
                    break
            else:
                best_price = min(best_price, low)
                if config.trailing_pct > 0 and best_price < entry_price:
                    stop_price = min(stop_price, best_price * (1.0 + config.trailing_pct))
                if high >= stop_price:
                    exit_price, exit_time, exit_reason, exit_index = stop_price, now, "STOP_LOSS", scan_index
                    break
                if low <= take_profit_price:
                    exit_price, exit_time, exit_reason, exit_index = take_profit_price, now, "TAKE_PROFIT", scan_index
                    break

            if now >= max_exit_time:
                exit_price, exit_time, exit_reason, exit_index = close, now, "TIME_EXIT", scan_index
                break

        gross, fees, pnl = calculate_pnl(signal, entry_price, exit_price, config.notional_usdc, config.fee_rate)
        seconds_held = int((exit_time - entry_time).total_seconds())
        trades.append(
            asdict(
                EmaScalpTrade(
                    entry_time_utc=entry_time.isoformat(),
                    exit_time_utc=exit_time.isoformat(),
                    direction=signal,
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    stop_price=round(stop_price, 4),
                    take_profit_price=round(take_profit_price, 4),
                    exit_reason=exit_reason,
                    seconds_held=seconds_held,
                    notional_usdc=round(config.notional_usdc, 4),
                    gross_pnl_usdc=round(gross, 6),
                    fees_usdc=round(fees, 6),
                    pnl_usdc=round(pnl, 6),
                    pnl_pct_notional=round((pnl / config.notional_usdc) * 100.0, 6),
                    win=pnl > 0,
                    ema_fast=round(float(signal_row["ema_fast"]), 4),
                    ema_mid=round(float(signal_row["ema_mid"]), 4),
                    ema_slow=round(float(signal_row["ema_slow"]), 4),
                    ema_slope=round(float(signal_row["ema_slope"]), 4),
                    price_ema_fast_distance=round(float(signal_row["price_ema_fast_distance"]), 4),
                )
            )
        )
        cooldown_until = exit_time + pd.Timedelta(seconds=config.cooldown_seconds)
        index = max(exit_index + 1, index + 1)

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, config: EmaScalpConfig) -> None:
    print("\n=== BACKTEST BINANCE EMA SCALP ===")
    print(
        f"{config.symbol} {config.market} {config.interval} | notional={config.notional_usdc:.2f} "
        f"fee={config.fee_rate:.4%} stop={config.stop_pct:.3%} tp={config.take_profit_pct:.3%}"
    )
    if trades.empty:
        print("Nenhum trade encontrado.")
        return

    wins = int(trades["win"].sum())
    losses = len(trades) - wins
    pnl = float(trades["pnl_usdc"].sum())
    gross = float(trades["gross_pnl_usdc"].sum())
    fees = float(trades["fees_usdc"].sum())
    win_rate = wins / len(trades) * 100.0
    avg = pnl / len(trades)
    equity = trades["pnl_usdc"].cumsum()
    drawdown = equity - equity.cummax()

    print(f"Trades: {len(trades)}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Gross PnL: {gross:.4f}")
    print(f"Fees: {fees:.4f}")
    print(f"Net PnL: {pnl:.4f}")
    print(f"Media/trade: {avg:.4f}")
    print(f"Max drawdown: {float(drawdown.min()):.4f}")

    by_reason = trades.groupby("exit_reason").agg(
        trades=("exit_reason", "size"),
        wins=("win", "sum"),
        pnl=("pnl_usdc", "sum"),
    )
    by_reason["win_rate"] = by_reason["wins"] / by_reason["trades"] * 100.0
    print("\nPor saida:")
    print(by_reason.reset_index().to_string(index=False, float_format=lambda value: f"{value:.4f}"))

    print("\nUltimos 10 trades:")
    columns = ["entry_time_utc", "direction", "entry_price", "exit_price", "exit_reason", "seconds_held", "win", "pnl_usdc"]
    print(trades[columns].tail(10).to_string(index=False, float_format=lambda value: f"{value:.4f}"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest Binance EMA scalp.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--market", default="spot", choices=["spot", "futures"])
    parser.add_argument("--interval", default="1s")
    parser.add_argument("--lookback-hours", type=float, default=12.0)
    parser.add_argument("--ema-fast", type=int, default=60)
    parser.add_argument("--ema-mid", type=int, default=300)
    parser.add_argument("--ema-slow", type=int, default=600)
    parser.add_argument("--slope-lookback", type=int, default=15)
    parser.add_argument("--min-ema-gap-usd", type=float, default=2.0)
    parser.add_argument("--min-slope-usd", type=float, default=0.2)
    parser.add_argument("--max-price-ema-fast-distance-usd", type=float, default=80.0)
    parser.add_argument("--entry-mode", default="trend", choices=["trend", "pullback"])
    parser.add_argument("--trend-filter", default="none", choices=["none", "adx", "breakout", "adx-breakout"])
    parser.add_argument("--adx-period", type=int, default=14)
    parser.add_argument("--min-adx", type=float, default=20.0)
    parser.add_argument("--breakout-lookback", type=int, default=20)
    parser.add_argument("--breakout-buffer-pct", type=float, default=0.0)
    parser.add_argument("--notional", type=float, default=100.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--stop-pct", type=float, default=0.0010)
    parser.add_argument("--take-profit-pct", type=float, default=0.0015)
    parser.add_argument("--trailing-pct", type=float, default=0.0)
    parser.add_argument("--max-hold-seconds", type=int, default=180)
    parser.add_argument("--cooldown-seconds", type=int, default=30)
    parser.add_argument("--save-csv", default="backtest_binance_ema_scalp_trades.csv")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = EmaScalpConfig(
        symbol=args.symbol,
        market=args.market,
        interval=args.interval,
        lookback_hours=args.lookback_hours,
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
        trailing_pct=args.trailing_pct,
        max_hold_seconds=args.max_hold_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=config.lookback_hours)
    df = fetch_binance_klines(
        symbol=config.symbol,
        interval=config.interval,
        start_time=start_time,
        end_time=end_time,
        market=config.market,
    )
    trades = run_backtest(df, config)
    summarize(trades, config)
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nCSV salvo em: {args.save_csv}")


if __name__ == "__main__":
    main()
