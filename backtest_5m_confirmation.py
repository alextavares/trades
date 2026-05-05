#!/usr/bin/env python3
"""Backtest para estrategia de confirmacao em candles de 5 minutos.

Modos:
- second:
  - Candle A fecha em uma direcao.
  - Candle B fecha na direcao oposta.
  - Entrada acontece na abertura do candle C.
  - A saida acontece no fechamento do proprio candle C.

- third:
  - Candle A fecha em uma direcao.
  - Candle B fecha na direcao oposta.
  - Candle C fecha na mesma direcao do candle B.
  - Entrada acontece na abertura do candle D.
  - A saida acontece no fechamento do proprio candle D.

Este backtest mede acerto direcional no candle de entrada. Ele nao depende
de odds historicas da Polymarket, entao o resultado principal aqui e win rate
da leitura de direcao.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
import requests


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


@dataclass(frozen=True)
class TradeResult:
    entry_time: pd.Timestamp
    direction: str
    trigger_a_color: str
    trigger_b_color: str
    trigger_c_color: str
    entry_open: float
    exit_close: float
    entry_range_pct: float
    pnl_points: float
    pnl_pct: float
    outcome: str
    win: bool


def candle_color(open_price: float, close_price: float) -> int:
    if close_price > open_price:
        return 1
    if close_price < open_price:
        return -1
    return 0


def color_label(color: int) -> str:
    if color > 0:
        return "GREEN"
    if color < 0:
        return "RED"
    return "DOJI"


def detect_confirmation_signal(color_a: int, color_b: int, color_c: int, entry_mode: str) -> str | None:
    if entry_mode == "second":
        if 0 in (color_a, color_b):
            return None
        if color_a == -1 and color_b == 1:
            return "UP"
        if color_a == 1 and color_b == -1:
            return "DOWN"
        return None

    if entry_mode != "third":
        raise ValueError("entry_mode must be 'second' or 'third'")

    if 0 in (color_a, color_b, color_c):
        return None
    if color_a == -1 and color_b == 1 and color_c == 1:
        return "UP"
    if color_a == 1 and color_b == -1 and color_c == -1:
        return "DOWN"
    return None


def fetch_binance_5m(symbol: str, limit: int) -> pd.DataFrame:
    remaining = max(1, limit)
    end_time = None
    chunks: list[pd.DataFrame] = []

    while remaining > 0:
        batch_limit = min(remaining, 1000)
        params = {
            "symbol": symbol,
            "interval": "5m",
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
    df["color"] = np.where(df["close"] > df["open"], 1, np.where(df["close"] < df["open"], -1, 0))
    return df.tail(limit).reset_index(drop=True)


def run_backtest(df: pd.DataFrame, entry_mode: str) -> pd.DataFrame:
    trades: list[dict] = []

    start_index = 2 if entry_mode == "second" else 3
    for entry_idx in range(start_index, len(df)):
        color_a = int(df["color"].iloc[entry_idx - 2])
        color_b = int(df["color"].iloc[entry_idx - 1])
        color_c = int(df["color"].iloc[entry_idx]) if entry_mode == "second" else int(df["color"].iloc[entry_idx - 1])
        if entry_mode == "third":
            color_a = int(df["color"].iloc[entry_idx - 3])
            color_b = int(df["color"].iloc[entry_idx - 2])
            color_c = int(df["color"].iloc[entry_idx - 1])

        signal = detect_confirmation_signal(color_a, color_b, color_c, entry_mode=entry_mode)
        if signal is None:
            continue

        entry_open = float(df["open"].iloc[entry_idx])
        exit_close = float(df["close"].iloc[entry_idx])
        pnl_points = exit_close - entry_open if signal == "UP" else entry_open - exit_close
        pnl_pct = pnl_points / entry_open * 100.0
        candle_range_pct = abs(exit_close - entry_open) / entry_open * 100.0
        if signal == "UP":
            win = exit_close > entry_open
        else:
            win = exit_close < entry_open

        outcome = "TIE"
        if exit_close > entry_open:
            outcome = "UP"
        elif exit_close < entry_open:
            outcome = "DOWN"

        result = TradeResult(
            entry_time=df["ts"].iloc[entry_idx],
            direction=signal,
            trigger_a_color=color_label(color_a),
            trigger_b_color=color_label(color_b),
            trigger_c_color=color_label(color_c),
            entry_open=entry_open,
            exit_close=exit_close,
            entry_range_pct=candle_range_pct,
            pnl_points=pnl_points,
            pnl_pct=pnl_pct,
            outcome=outcome,
            win=win,
        )
        trades.append(asdict(result))

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, entry_mode: str) -> None:
    if trades.empty:
        print("Nenhum sinal encontrado.")
        return

    ties = int((trades["outcome"] == "TIE").sum())
    resolved = trades[trades["outcome"] != "TIE"].copy()
    wins = int(resolved["win"].sum()) if not resolved.empty else 0
    losses = len(resolved) - wins
    win_rate = (wins / len(resolved) * 100.0) if len(resolved) else 0.0

    print(f"\n=== BACKTEST 5M CONFIRMATION ({entry_mode.upper()}) ===")
    print(f"Total de sinais: {len(trades)}")
    print(f"Trades resolvidos: {len(resolved)}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Ties: {ties}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Media do movimento do candle de entrada: {trades['entry_range_pct'].mean():.4f}%")
    print(f"Media do PnL percentual: {trades['pnl_pct'].mean():.4f}%")

    by_direction = (
        trades.groupby("direction")
        .agg(
            trades=("direction", "size"),
            wins=("win", "sum"),
            avg_pnl_pct=("pnl_pct", "mean"),
        )
        .reset_index()
    )
    if not by_direction.empty:
        by_direction["win_rate"] = by_direction["wins"] / by_direction["trades"] * 100.0
        print("\nPor direcao:")
        print(by_direction.to_string(index=False, float_format=lambda value: f"{value:.4f}"))

    print("\nUltimos 10 sinais:")
    preview = trades[
        [
            "entry_time",
            "direction",
            "trigger_a_color",
            "trigger_b_color",
            "trigger_c_color",
            "entry_open",
            "exit_close",
            "outcome",
            "win",
            "pnl_pct",
        ]
    ].tail(10)
    print(preview.to_string(index=False, float_format=lambda value: f"{value:.4f}"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest da estrategia 5m de confirmacao por dois candles.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000, help="Numero de candles 5m para testar.")
    parser.add_argument("--entry-mode", default="third", choices=["second", "third"])
    parser.add_argument("--save-csv", default="", help="Opcional: salva os trades em CSV.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    df = fetch_binance_5m(args.symbol, args.limit)
    trades = run_backtest(df, entry_mode=args.entry_mode)
    summarize(trades, entry_mode=args.entry_mode)
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nCSV salvo em: {args.save_csv}")


if __name__ == "__main__":
    main()
