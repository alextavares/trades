#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Us500Config:
    csv: str = "us500_mt5_m1.csv"
    timeframe: str = "1min"
    lot_size: float = 0.01
    tick_size: float = 0.01
    tick_value_usd: float = 0.01
    commission_per_side_usd: float = 0.0
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 60
    slope_lookback: int = 5
    min_ema_gap_points: float = 0.50
    min_slope_points: float = 0.25
    max_price_ema_fast_distance_points: float = 4.0
    entry_mode: str = "trend"
    trend_filter: str = "adx"
    adx_period: int = 14
    min_adx: float = 18.0
    stop_points: float = 6.0
    take_profit_points: float = 8.0
    max_hold_bars: int = 30
    cooldown_bars: int = 5
    session_start_hour_brt: int = 10
    session_end_hour_brt: int = 18


def load_mt5_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time_utc", "open", "high", "low", "close", "tick_volume", "spread"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV MT5 sem colunas obrigatorias: {sorted(missing)}")
    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(df["time_utc"], utc=True),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["tick_volume"], errors="coerce").fillna(0),
            "spread_points": pd.to_numeric(df["spread"], errors="coerce").fillna(0),
        }
    )
    return out.dropna(subset=["ts", "open", "high", "low", "close"]).reset_index(drop=True)


def resample_mt5_rates(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    normalized = timeframe.strip().lower()
    if normalized in {"1m", "1min", "m1"}:
        return df.copy()
    out = (
        df.set_index("ts")
        .resample(normalized)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "spread_points": "last",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return out


def add_indicators(df: pd.DataFrame, config: Us500Config) -> pd.DataFrame:
    out = df.copy().sort_values("ts").reset_index(drop=True)
    out["ema_fast"] = out["close"].ewm(span=config.ema_fast, adjust=False).mean()
    out["ema_mid"] = out["close"].ewm(span=config.ema_mid, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=config.ema_slow, adjust=False).mean()
    out["ema_slope"] = out["ema_fast"] - out["ema_fast"].shift(config.slope_lookback)
    out["price_ema_fast_distance"] = out["close"] - out["ema_fast"]

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
    out["hour_brt"] = out["ts"].dt.tz_convert("America/Sao_Paulo").dt.hour
    return out


def within_session(row: pd.Series, config: Us500Config) -> bool:
    if "hour_brt" not in row.index:
        return True
    hour = int(row["hour_brt"])
    return config.session_start_hour_brt <= hour <= config.session_end_hour_brt


def detect_signal(prev_row: pd.Series, row: pd.Series, config: Us500Config) -> str | None:
    if not within_session(row, config):
        return None
    fast = float(row["ema_fast"])
    mid = float(row["ema_mid"])
    slow = float(row["ema_slow"])
    close = float(row["close"])
    slope = float(row["ema_slope"])
    distance = close - fast
    if pd.isna(slope):
        return None
    if min(abs(fast - mid), abs(mid - slow)) < config.min_ema_gap_points:
        return None
    if abs(distance) > config.max_price_ema_fast_distance_points:
        return None
    if config.trend_filter == "adx":
        adx = float(row.get("adx", 0.0))
        if pd.isna(adx) or adx < config.min_adx:
            return None
    elif config.trend_filter != "none":
        raise ValueError("trend_filter deve ser none ou adx")

    long_ok = fast > mid > slow and slope >= config.min_slope_points and close >= fast
    short_ok = fast < mid < slow and slope <= -config.min_slope_points and close <= fast

    if config.entry_mode == "pullback":
        prev_close = float(prev_row["close"])
        prev_fast = float(prev_row["ema_fast"])
        long_ok = long_ok and prev_close <= prev_fast
        short_ok = short_ok and prev_close >= prev_fast
    elif config.entry_mode != "trend":
        raise ValueError("entry_mode deve ser trend ou pullback")

    if long_ok:
        return "LONG"
    if short_ok:
        return "SHORT"
    return None


def point_value_per_lot(config: Us500Config) -> float:
    return config.tick_value_usd / config.tick_size


def calculate_trade_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    spread_points: float,
    config: Us500Config,
) -> tuple[float, float, float]:
    points = exit_price - entry_price if direction == "LONG" else entry_price - exit_price
    gross = points * point_value_per_lot(config) * config.lot_size
    spread_cost = spread_points * config.tick_value_usd * config.lot_size
    commission_cost = config.commission_per_side_usd * 2.0
    costs = spread_cost + commission_cost
    return round(gross, 4), round(costs, 4), round(gross - costs, 4)


def exit_for_bar(direction: str, row: pd.Series, entry_price: float, config: Us500Config) -> tuple[float, str] | None:
    if direction == "LONG":
        stop = entry_price - config.stop_points
        take_profit = entry_price + config.take_profit_points
        if float(row["low"]) <= stop:
            return stop, "STOP"
        if float(row["high"]) >= take_profit:
            return take_profit, "TAKE_PROFIT"
    else:
        stop = entry_price + config.stop_points
        take_profit = entry_price - config.take_profit_points
        if float(row["high"]) >= stop:
            return stop, "STOP"
        if float(row["low"]) <= take_profit:
            return take_profit, "TAKE_PROFIT"
    return None


def run_backtest(df: pd.DataFrame, config: Us500Config) -> pd.DataFrame:
    data = add_indicators(df, config)
    trades: list[dict] = []
    warmup = max(config.ema_slow, config.adx_period + config.slope_lookback) + 2
    index = warmup
    cooldown_until = -1

    while index < len(data) - 1:
        if index < cooldown_until:
            index += 1
            continue
        prev_row = data.iloc[index - 1]
        row = data.iloc[index]
        direction = detect_signal(prev_row, row, config)
        if direction is None:
            index += 1
            continue

        entry_index = index + 1
        entry_row = data.iloc[entry_index]
        spread_price = float(entry_row["spread_points"]) * config.tick_size
        raw_open = float(entry_row["open"])
        entry_price = raw_open + spread_price if direction == "LONG" else raw_open

        exit_price = float(entry_row["close"])
        exit_reason = "TIME"
        exit_index = min(entry_index + config.max_hold_bars, len(data) - 1)

        for test_index in range(entry_index, min(entry_index + config.max_hold_bars, len(data) - 1) + 1):
            candidate = data.iloc[test_index]
            exit_hit = exit_for_bar(direction, candidate, entry_price, config)
            if exit_hit is not None:
                exit_price, exit_reason = exit_hit
                if direction == "SHORT":
                    exit_price += float(candidate["spread_points"]) * config.tick_size
                exit_index = test_index
                break
            exit_price = float(candidate["close"])
            exit_index = test_index

        exit_row = data.iloc[exit_index]
        if direction == "SHORT" and exit_reason == "TIME":
            exit_price += float(exit_row["spread_points"]) * config.tick_size

        gross, costs, net = calculate_trade_pnl(
            direction,
            entry_price,
            exit_price,
            float(entry_row["spread_points"]),
            config,
        )
        trades.append(
            {
                "entry_time_utc": entry_row["ts"].isoformat(),
                "exit_time_utc": exit_row["ts"].isoformat(),
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "exit_reason": exit_reason,
                "bars_held": int(exit_index - entry_index + 1),
                "lot_size": config.lot_size,
                "gross_pnl_usd": gross,
                "costs_usd": costs,
                "pnl_usd": net,
                "win": net > 0,
                "ema_fast": round(float(row["ema_fast"]), 4),
                "ema_mid": round(float(row["ema_mid"]), 4),
                "ema_slow": round(float(row["ema_slow"]), 4),
                "ema_slope": round(float(row["ema_slope"]), 4),
                "adx": round(float(row.get("adx", 0.0)), 4),
                "spread_points": float(entry_row["spread_points"]),
            }
        )
        cooldown_until = exit_index + config.cooldown_bars
        index = exit_index + 1

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "costs": 0.0,
            "net_pnl": 0.0,
            "avg_trade": 0.0,
            "max_drawdown": 0.0,
        }
    equity = trades["pnl_usd"].cumsum()
    drawdown = equity - equity.cummax()
    wins = int(trades["win"].sum())
    total = int(len(trades))
    return {
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": wins / total * 100.0,
        "gross_pnl": float(trades["gross_pnl_usd"].sum()),
        "costs": float(trades["costs_usd"].sum()),
        "net_pnl": float(trades["pnl_usd"].sum()),
        "avg_trade": float(trades["pnl_usd"].mean()),
        "max_drawdown": float(drawdown.min()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest EMA para US500 MT5 CSV.")
    parser.add_argument("--csv", default="us500_mt5_m1.csv")
    parser.add_argument("--timeframe", default="1min")
    parser.add_argument("--save-csv", default="backtest_us500_mt5_trades.csv")
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--tick-value-usd", type=float, default=0.01)
    parser.add_argument("--commission-per-side-usd", type=float, default=0.0)
    parser.add_argument("--ema-fast", type=int, default=9)
    parser.add_argument("--ema-mid", type=int, default=21)
    parser.add_argument("--ema-slow", type=int, default=60)
    parser.add_argument("--slope-lookback", type=int, default=5)
    parser.add_argument("--min-ema-gap-points", type=float, default=0.50)
    parser.add_argument("--min-slope-points", type=float, default=0.25)
    parser.add_argument("--max-price-ema-fast-distance-points", type=float, default=4.0)
    parser.add_argument("--entry-mode", choices=["trend", "pullback"], default="trend")
    parser.add_argument("--trend-filter", choices=["none", "adx"], default="adx")
    parser.add_argument("--adx-period", type=int, default=14)
    parser.add_argument("--min-adx", type=float, default=18.0)
    parser.add_argument("--stop-points", type=float, default=6.0)
    parser.add_argument("--take-profit-points", type=float, default=8.0)
    parser.add_argument("--max-hold-bars", type=int, default=30)
    parser.add_argument("--cooldown-bars", type=int, default=5)
    parser.add_argument("--session-start-hour-brt", type=int, default=10)
    parser.add_argument("--session-end-hour-brt", type=int, default=18)
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> Us500Config:
    return Us500Config(
        csv=args.csv,
        timeframe=args.timeframe,
        lot_size=args.lot_size,
        tick_size=args.tick_size,
        tick_value_usd=args.tick_value_usd,
        commission_per_side_usd=args.commission_per_side_usd,
        ema_fast=args.ema_fast,
        ema_mid=args.ema_mid,
        ema_slow=args.ema_slow,
        slope_lookback=args.slope_lookback,
        min_ema_gap_points=args.min_ema_gap_points,
        min_slope_points=args.min_slope_points,
        max_price_ema_fast_distance_points=args.max_price_ema_fast_distance_points,
        entry_mode=args.entry_mode,
        trend_filter=args.trend_filter,
        adx_period=args.adx_period,
        min_adx=args.min_adx,
        stop_points=args.stop_points,
        take_profit_points=args.take_profit_points,
        max_hold_bars=args.max_hold_bars,
        cooldown_bars=args.cooldown_bars,
        session_start_hour_brt=args.session_start_hour_brt,
        session_end_hour_brt=args.session_end_hour_brt,
    )


def main() -> int:
    args = parse_args()
    config = config_from_args(args)
    data = load_mt5_csv(config.csv)
    data = resample_mt5_rates(data, config.timeframe)
    trades = run_backtest(data, config)
    summary = summarize(trades)
    print("=== CONFIG ===")
    for key, value in asdict(config).items():
        print(f"{key}: {value}")
    print("=== SUMMARY ===")
    for key, value in summary.items():
        print(f"{key}: {value}")
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"Saved trades to {args.save_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
