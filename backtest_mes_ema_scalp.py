#!/usr/bin/env python3
"""Backtest de scalp EMA/ADX para micro futuros de indice (MES/ES).

Usa Yahoo ou Databento para triagem de estrategia. Isso serve para validacao
de setup e nao para medir execucao real de scalping com precisao de livro de
ofertas.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
import requests

try:  # Fallback opcional; o caminho principal usa requests direto no Yahoo Chart.
    import yfinance as yf
except ImportError:  # pragma: no cover - depende do ambiente local/servidor
    yf = None

try:
    import databento as db
except ImportError:  # pragma: no cover - depende do ambiente local/servidor
    db = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - depende do ambiente local/servidor
    load_dotenv = None


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DATABENTO_DATASET = "GLBX.MDP3"
DATABENTO_DEFAULT_SCHEMA = "ohlcv-1m"
DATABENTO_DELAY_MINUTES = 20
DATABENTO_SYMBOL_MAP = {
    "MES=F": "MES.v.0",
    "ES=F": "ES.v.0",
}


@dataclass(frozen=True)
class IndexFuturesConfig:
    symbol: str = "MES=F"
    period: str = "60d"
    interval: str = "5m"
    contracts: int = 1
    point_value_usd: float = 5.0
    tick_size: float = 0.25
    commission_per_side_usd: float = 0.62
    slippage_ticks: float = 1.0
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 60
    slope_lookback: int = 5
    min_ema_gap_points: float = 0.50
    min_slope_points: float = 0.25
    max_price_ema_fast_distance_points: float = 12.0
    trend_filter: str = "adx"
    adx_period: int = 14
    min_adx: float = 18.0
    entry_mode: str = "trend"
    stop_points: float = 5.0
    take_profit_points: float = 8.0
    max_hold_bars: int = 12
    cooldown_bars: int = 3


def maybe_load_dotenv() -> None:
    if load_dotenv is None:
        return
    load_dotenv(".env.local", override=False)
    load_dotenv(".env", override=False)


def fetch_yahoo_ohlcv(symbol: str, period: str, interval: str) -> pd.DataFrame:
    direct_error: Exception | None = None
    try:
        response = requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": period, "interval": interval, "includePrePost": "false"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()["chart"]["result"][0]
        timestamps = payload.get("timestamp") or []
        quote = payload["indicators"]["quote"][0]
        if timestamps:
            df = pd.DataFrame(
                {
                    "ts": pd.to_datetime(timestamps, unit="s", utc=True),
                    "open": quote["open"],
                    "high": quote["high"],
                    "low": quote["low"],
                    "close": quote["close"],
                    "volume": quote.get("volume") or [0] * len(timestamps),
                }
            )
            return normalize_ohlcv(df)
    except Exception as exc:
        direct_error = exc

    df = pd.DataFrame()
    if yf is not None:
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=False,
                progress=False,
                threads=False,
            )
        except Exception:
            df = pd.DataFrame()

    if not df.empty:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() for col in df.columns]
        else:
            df.columns = [str(col).lower() for col in df.columns]
        df = df.reset_index()
        time_col = "datetime" if "datetime" in df.columns else "date"
        df = df.rename(columns={time_col: "ts", "adj close": "adj_close"})
        return normalize_ohlcv(df)

    raise RuntimeError(f"Yahoo retornou serie vazia para {symbol} {period} {interval}; erro direto={direct_error}")


def parse_period_to_start(period: str, end: pd.Timestamp | None = None) -> pd.Timestamp:
    text = period.strip().lower()
    end_ts = pd.Timestamp.utcnow() if end is None else pd.Timestamp(end)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    else:
        end_ts = end_ts.tz_convert("UTC")

    for suffix, delta_factory in (
        ("mo", lambda value: pd.DateOffset(months=value)),
        ("wk", lambda value: pd.DateOffset(weeks=value)),
        ("w", lambda value: pd.DateOffset(weeks=value)),
        ("y", lambda value: pd.DateOffset(years=value)),
        ("d", lambda value: pd.Timedelta(days=value)),
        ("h", lambda value: pd.Timedelta(hours=value)),
        ("m", lambda value: pd.Timedelta(minutes=value)),
    ):
        if text.endswith(suffix):
            value = int(text[: -len(suffix)])
            return end_ts - delta_factory(value)
    raise ValueError(f"Periodo nao suportado: {period}")


def yahoo_symbol_to_databento(symbol: str) -> str:
    return DATABENTO_SYMBOL_MAP.get(symbol, symbol)


def interval_to_pandas_freq(interval: str) -> str:
    text = interval.strip().lower()
    if text.endswith("m"):
        return f"{int(text[:-1])}min"
    return interval


def parse_databento_retry_end(error_text: str) -> pd.Timestamp | None:
    patterns = [
        r"available up to '([^']+)'",
        r"before ([0-9T:\-:.]+Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_text)
        if match:
            return pd.Timestamp(match.group(1)).tz_convert("UTC") - pd.Timedelta(minutes=1)
    return None


def normalize_databento_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        index_name = out.index.name or "ts_event"
        out = out.reset_index().rename(columns={index_name: "ts"})
    elif "ts_event" in out.columns and "ts" not in out.columns:
        out = out.rename(columns={"ts_event": "ts"})
    elif "ts_recv" in out.columns and "ts" not in out.columns:
        out = out.rename(columns={"ts_recv": "ts"})
    return normalize_ohlcv(out)


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    out = normalize_ohlcv(df)
    if interval == "1m":
        return out

    pandas_freq = interval_to_pandas_freq(interval)
    resampled = (
        out.set_index("ts")
        .resample(pandas_freq)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return normalize_ohlcv(resampled)


def fetch_databento_ohlcv(
    symbol: str,
    period: str,
    interval: str,
    dataset: str = DATABENTO_DATASET,
    db_symbol: str = "",
) -> pd.DataFrame:
    maybe_load_dotenv()
    if db is None:
        raise RuntimeError("Pacote databento nao instalado")
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY nao configurada")

    end = pd.Timestamp.utcnow().floor("1min") - pd.Timedelta(minutes=DATABENTO_DELAY_MINUTES)
    start = parse_period_to_start(period, end)
    request_symbol = db_symbol or yahoo_symbol_to_databento(symbol)

    client = db.Historical()
    request_kwargs = {
        "dataset": dataset,
        "schema": DATABENTO_DEFAULT_SCHEMA,
        "symbols": request_symbol,
        "stype_in": "continuous",
        "start": start,
    }
    if end > start:
        request_kwargs["end"] = end

    try:
        data = client.timeseries.get_range(**request_kwargs)
    except Exception as exc:
        retry_end = parse_databento_retry_end(str(exc))
        if retry_end is None or retry_end <= start:
            raise
        request_kwargs["end"] = retry_end
        data = client.timeseries.get_range(**request_kwargs)
    normalized = normalize_databento_ohlcv(data.to_df())
    return resample_ohlcv(normalized, interval)


def get_ohlcv(
    data_source: str,
    symbol: str,
    period: str,
    interval: str,
    databento_dataset: str = DATABENTO_DATASET,
    databento_symbol: str = "",
) -> pd.DataFrame:
    if data_source == "yahoo":
        return fetch_yahoo_ohlcv(symbol, period, interval)
    if data_source == "databento":
        return fetch_databento_ohlcv(symbol, period, interval, databento_dataset, databento_symbol)
    if data_source == "auto":
        try:
            return fetch_databento_ohlcv(symbol, period, interval, databento_dataset, databento_symbol)
        except Exception:
            return fetch_yahoo_ohlcv(symbol, period, interval)
    raise ValueError(f"Fonte de dados nao suportada: {data_source}")


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = ["ts", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"Yahoo sem colunas obrigatorias: {missing}")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df[required].dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={col: col.strip().lower() for col in df.columns})
    if "time" in df.columns and "ts" not in df.columns:
        df = df.rename(columns={"time": "ts"})
    required = {"ts", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV sem colunas obrigatorias: {sorted(missing)}")
    if "volume" not in df.columns:
        df["volume"] = 0
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df[["ts", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )


def add_indicators(df: pd.DataFrame, config: IndexFuturesConfig) -> pd.DataFrame:
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
    return out


def trend_filter_allows(row: pd.Series, config: IndexFuturesConfig) -> bool:
    if config.trend_filter == "none":
        return True
    if config.trend_filter != "adx":
        raise ValueError("trend_filter deve ser none ou adx")
    adx = float(row.get("adx", 0.0))
    return not pd.isna(adx) and adx >= config.min_adx


def detect_signal(prev_row: pd.Series, row: pd.Series, config: IndexFuturesConfig) -> str | None:
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
    if not trend_filter_allows(row, config):
        return None

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


def calculate_contract_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    config: IndexFuturesConfig,
) -> tuple[float, float, float]:
    if direction == "LONG":
        points = exit_price - entry_price
    else:
        points = entry_price - exit_price
    gross = points * config.point_value_usd * config.contracts
    slippage_cost = config.tick_size * config.slippage_ticks * config.point_value_usd * config.contracts * 2.0
    commission_cost = config.commission_per_side_usd * config.contracts * 2.0
    costs = slippage_cost + commission_cost
    return round(gross, 4), round(costs, 4), round(gross - costs, 4)


def exit_for_bar(direction: str, row: pd.Series, entry_price: float, config: IndexFuturesConfig) -> tuple[float, str] | None:
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


def run_backtest(df: pd.DataFrame, config: IndexFuturesConfig) -> pd.DataFrame:
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
        entry_price = float(entry_row["open"])
        exit_price = float(entry_row["close"])
        exit_reason = "TIME"
        exit_index = min(entry_index + config.max_hold_bars, len(data) - 1)

        for test_index in range(entry_index, min(entry_index + config.max_hold_bars, len(data) - 1) + 1):
            candidate = data.iloc[test_index]
            exit_hit = exit_for_bar(direction, candidate, entry_price, config)
            if exit_hit is not None:
                exit_price, exit_reason = exit_hit
                exit_index = test_index
                break
            exit_price = float(candidate["close"])
            exit_index = test_index

        gross, costs, net = calculate_contract_pnl(direction, entry_price, exit_price, config)
        exit_row = data.iloc[exit_index]
        trades.append(
            {
                "entry_time_utc": entry_row["ts"].isoformat(),
                "exit_time_utc": exit_row["ts"].isoformat(),
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "exit_reason": exit_reason,
                "bars_held": int(exit_index - entry_index + 1),
                "contracts": config.contracts,
                "gross_pnl_usd": gross,
                "costs_usd": costs,
                "pnl_usd": net,
                "win": net > 0,
                "ema_fast": round(float(row["ema_fast"]), 4),
                "ema_mid": round(float(row["ema_mid"]), 4),
                "ema_slow": round(float(row["ema_slow"]), 4),
                "ema_slope": round(float(row["ema_slope"]), 4),
                "adx": round(float(row.get("adx", 0.0)), 4),
            }
        )
        cooldown_until = exit_index + config.cooldown_bars
        index = exit_index + 1

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "gross_pnl": 0.0, "costs": 0.0, "net_pnl": 0.0}
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
    parser = argparse.ArgumentParser(description="Backtest EMA/ADX para MES/ES com Yahoo ou Databento.")
    parser.add_argument("--symbol", default="MES=F", help="Ticker Yahoo. Ex.: MES=F ou ES=F")
    parser.add_argument("--period", default="60d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--csv", help="CSV local com ts,open,high,low,close,volume")
    parser.add_argument("--save-csv", default="")
    parser.add_argument("--data-source", choices=["auto", "yahoo", "databento"], default="auto")
    parser.add_argument("--databento-dataset", default=DATABENTO_DATASET)
    parser.add_argument("--databento-symbol", default="")
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--point-value-usd", type=float, default=5.0)
    parser.add_argument("--commission-per-side-usd", type=float, default=0.62)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--ema-fast", type=int, default=9)
    parser.add_argument("--ema-mid", type=int, default=21)
    parser.add_argument("--ema-slow", type=int, default=60)
    parser.add_argument("--slope-lookback", type=int, default=5)
    parser.add_argument("--min-ema-gap-points", type=float, default=0.5)
    parser.add_argument("--min-slope-points", type=float, default=0.25)
    parser.add_argument("--max-price-ema-fast-distance-points", type=float, default=12.0)
    parser.add_argument("--trend-filter", choices=["none", "adx"], default="adx")
    parser.add_argument("--min-adx", type=float, default=18.0)
    parser.add_argument("--entry-mode", choices=["trend", "pullback"], default="trend")
    parser.add_argument("--stop-points", type=float, default=5.0)
    parser.add_argument("--take-profit-points", type=float, default=8.0)
    parser.add_argument("--max-hold-bars", type=int, default=12)
    parser.add_argument("--cooldown-bars", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = IndexFuturesConfig(
        **{
            key: value
            for key, value in vars(args).items()
            if key not in {"csv", "save_csv", "data_source", "databento_dataset", "databento_symbol"}
        }
    )
    df = (
        load_csv(args.csv)
        if args.csv
        else get_ohlcv(
            args.data_source,
            config.symbol,
            config.period,
            config.interval,
            databento_dataset=args.databento_dataset,
            databento_symbol=args.databento_symbol,
        )
    )
    trades = run_backtest(df, config)
    summary = summarize(trades)

    print("=== MES/ES EMA SCALP BACKTEST ===")
    print(f"Config: {asdict(config)}")
    print(f"Fonte de dados: {'csv' if args.csv else args.data_source}")
    print(f"Linhas: {len(df)} | Trades: {summary['trades']}")
    print(
        "Wins: {wins} | Losses: {losses} | Win rate: {win_rate:.2f}% | "
        "Gross: {gross_pnl:.2f} | Costs: {costs:.2f} | Net: {net_pnl:.2f} | "
        "Avg/trade: {avg_trade:.2f} | Max DD: {max_drawdown:.2f}".format(**summary)
    )
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"CSV salvo: {args.save_csv}")


if __name__ == "__main__":
    main()
