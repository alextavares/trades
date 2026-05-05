#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import time

import MetaTrader5 as mt5
import pandas as pd


ROOT = Path(__file__).resolve().parent
LOCAL_TZ = "America/Sao_Paulo"
SUPPORTED_TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
MAX_BARS_PER_REQUEST = 30_000
MAX_RETRIES = 3
TIMEFRAME_BARS_PER_DAY = {
    "M1": 24 * 60,
    "M5": 24 * 12,
    "M15": 24 * 4,
    "M30": 24 * 2,
    "H1": 24,
    "H4": 6,
    "D1": 1,
}
TIMEFRAME_TO_PANDAS_FREQ = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}


def parse_timeframe(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_TIMEFRAMES:
        allowed = ", ".join(sorted(SUPPORTED_TIMEFRAMES))
        raise ValueError(f"Unsupported timeframe '{value}'. Allowed: {allowed}")
    return normalized


def normalize_mt5_times(frame: pd.DataFrame, broker_utc_offset_hours: int = 0) -> pd.Series:
    raw_times = pd.to_datetime(frame["time"], unit="s", utc=True)
    return raw_times - pd.to_timedelta(broker_utc_offset_hours, unit="h")


def infer_broker_utc_offset_hours(
    frame: pd.DataFrame,
    timeframe: str,
    now_utc: pd.Timestamp | None = None,
) -> int:
    if frame.empty:
        return 0
    raw_times = pd.to_datetime(frame["time"], unit="s", utc=True)
    latest_bar_open = pd.Timestamp(raw_times.iloc[-1])
    now = now_utc if now_utc is not None else pd.Timestamp.utcnow()
    current_bar_open = now.floor(TIMEFRAME_TO_PANDAS_FREQ[timeframe])
    diff_hours = (latest_bar_open - current_bar_open).total_seconds() / 3600.0
    return int(round(diff_hours))


def normalize_rates_frame(frame: pd.DataFrame, broker_utc_offset_hours: int = 0) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["time"] = normalize_mt5_times(normalized, broker_utc_offset_hours=broker_utc_offset_hours)
    normalized["time_utc"] = normalized["time"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    normalized["time_utc"] = normalized["time_utc"].str.replace(
        r"([+-]\d{2})(\d{2})$",
        r"\1:\2",
        regex=True,
    )
    local_times = normalized["time"].dt.tz_convert(LOCAL_TZ)
    normalized["time_brt"] = local_times.dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    normalized["time_brt"] = normalized["time_brt"].str.replace(
        r"([+-]\d{2})(\d{2})$",
        r"\1:\2",
        regex=True,
    )
    columns = [
        "time_utc",
        "time_brt",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    return normalized[columns]


def fetch_rates_in_chunks(symbol: str, timeframe: str, total_bars: int) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    fetched = 0

    while fetched < total_bars:
        batch_size = min(MAX_BARS_PER_REQUEST, total_bars - fetched)
        rates = None
        for attempt in range(MAX_RETRIES):
            rates = mt5.copy_rates_from_pos(symbol, SUPPORTED_TIMEFRAMES[timeframe], fetched, batch_size)
            if rates is not None:
                break
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.25)
        if rates is None:
            raise RuntimeError(f"copy_rates_from_pos failed at start_pos={fetched}: {mt5.last_error()}")

        frame = pd.DataFrame(rates)
        if frame.empty:
            break

        frames.append(frame)
        fetched += len(frame)
        if len(frame) < batch_size:
            break
        time.sleep(0.05)

    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return merged


def fetch_rates(
    symbol: str,
    timeframe: str,
    days: int,
    broker_utc_offset_hours: int | None = None,
    terminal_path: str | None = None,
) -> pd.DataFrame:
    init_kwargs = {"path": terminal_path} if terminal_path else {}
    if not mt5.initialize(**init_kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")

        count = max(days * TIMEFRAME_BARS_PER_DAY[timeframe], 1)
        frame = fetch_rates_in_chunks(symbol, timeframe, count)
        if frame.empty:
            raise RuntimeError(f"No rates returned for {symbol} {timeframe} in the last {days} days")
        inferred_offset = (
            infer_broker_utc_offset_hours(frame, timeframe)
            if broker_utc_offset_hours is None
            else broker_utc_offset_hours
        )
        return normalize_rates_frame(frame, broker_utc_offset_hours=inferred_offset)
    finally:
        mt5.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export rates from the running MT5 terminal to CSV.")
    parser.add_argument("--symbol", default="US500", help="MT5 symbol, for example US500")
    parser.add_argument("--timeframe", default="M1", help="Timeframe: M1, M5, M15, M30, H1, H4, D1")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days")
    parser.add_argument(
        "--output",
        default=str(ROOT / "us500_mt5_m1.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--terminal-path",
        default=None,
        help="Optional explicit terminal executable path for MT5 initialize()",
    )
    parser.add_argument(
        "--broker-utc-offset-hours",
        type=int,
        default=None,
        help="Optional MT5 server offset in hours to convert raw broker bar times into UTC. "
        "If omitted, inferred from the latest bar.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    timeframe = parse_timeframe(args.timeframe)
    frame = fetch_rates(
        symbol=args.symbol,
        timeframe=timeframe,
        days=args.days,
        broker_utc_offset_hours=args.broker_utc_offset_hours,
        terminal_path=args.terminal_path,
    )
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    print(
        f"Exported {len(frame)} rows for {args.symbol} {timeframe} "
        f"to {output_path}"
    )
    print(f"Range UTC: {frame.iloc[0]['time_utc']} -> {frame.iloc[-1]['time_utc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
