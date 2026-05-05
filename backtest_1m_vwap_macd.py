#!/usr/bin/env python3
"""Backtest de VWAP + MACD em 1 minuto com saida no vencimento do bloco 5m.

Regras:
- UP quando o candle 1m fecha acima da VWAP e o MACD cruza para cima.
- DOWN quando o candle 1m fecha abaixo da VWAP e o MACD cruza para baixo.
- Entrada na abertura do proximo candle 1m.
- Saida no fechamento do bloco de 5 minutos que contem a entrada.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass

import pandas as pd

from backtest_polymarket_5m_edge import fetch_binance_1m


@dataclass(frozen=True)
class VwapMacdTrade:
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    signal_close: float
    signal_vwap: float
    macd_line: float
    macd_signal: float
    entry_open: float
    exit_close: float
    minutes_held: int
    outcome: str
    win: bool
    pnl_points: float
    pnl_pct: float


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    typical_price = (out["high"] + out["low"] + out["close"]) / 3.0
    session = out["ts"].dt.floor("D")
    out["vwap"] = (typical_price * out["volume"]).groupby(session).cumsum() / out["volume"].groupby(session).cumsum()

    ema_fast = out["close"].ewm(span=12, adjust=False).mean()
    ema_slow = out["close"].ewm(span=26, adjust=False).mean()
    out["macd_line"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd_line"].ewm(span=9, adjust=False).mean()
    return out


def detect_vwap_macd_signal(prev_row: pd.Series, curr_row: pd.Series) -> str | None:
    macd_prev = float(prev_row["macd_line"] - prev_row["macd_signal"])
    macd_curr = float(curr_row["macd_line"] - curr_row["macd_signal"])

    if float(curr_row["close"]) > float(curr_row["vwap"]) and macd_prev <= 0 and macd_curr > 0:
        return "UP"
    if float(curr_row["close"]) < float(curr_row["vwap"]) and macd_prev >= 0 and macd_curr < 0:
        return "DOWN"
    return None


def next_5m_block_end(entry_time: pd.Timestamp) -> pd.Timestamp:
    minute = int(entry_time.minute)
    remainder = minute % 5
    start_minute = minute - remainder
    block_start = entry_time.replace(minute=start_minute, second=0, microsecond=0)
    return block_start + pd.Timedelta(minutes=4)


def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    trades: list[dict] = []

    for signal_idx in range(1, len(df) - 1):
        prev_row = df.iloc[signal_idx - 1]
        curr_row = df.iloc[signal_idx]
        signal = detect_vwap_macd_signal(prev_row, curr_row)
        if signal is None:
            continue

        entry_idx = signal_idx + 1
        entry_time = pd.Timestamp(df["ts"].iloc[entry_idx])
        exit_time = next_5m_block_end(entry_time)
        exit_rows = df[df["ts"] == exit_time]
        if exit_rows.empty:
            continue
        exit_row = exit_rows.iloc[0]

        entry_open = float(df["open"].iloc[entry_idx])
        exit_close = float(exit_row["close"])
        pnl_points = exit_close - entry_open if signal == "UP" else entry_open - exit_close
        pnl_pct = pnl_points / entry_open * 100.0
        outcome = "TIE"
        if exit_close > entry_open:
            outcome = "UP"
        elif exit_close < entry_open:
            outcome = "DOWN"
        win = outcome == signal

        trades.append(
            asdict(
                VwapMacdTrade(
                    signal_time=pd.Timestamp(curr_row["ts"]),
                    entry_time=entry_time,
                    exit_time=exit_time,
                    direction=signal,
                    signal_close=float(curr_row["close"]),
                    signal_vwap=float(curr_row["vwap"]),
                    macd_line=float(curr_row["macd_line"]),
                    macd_signal=float(curr_row["macd_signal"]),
                    entry_open=entry_open,
                    exit_close=exit_close,
                    minutes_held=int((exit_time - entry_time).total_seconds() // 60) + 1,
                    outcome=outcome,
                    win=win,
                    pnl_points=pnl_points,
                    pnl_pct=pnl_pct,
                )
            )
        )

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame) -> None:
    if trades.empty:
        print("Nenhum sinal encontrado.")
        return

    ties = int((trades["outcome"] == "TIE").sum())
    resolved = trades[trades["outcome"] != "TIE"].copy()
    wins = int(resolved["win"].sum()) if not resolved.empty else 0
    losses = len(resolved) - wins
    win_rate = (wins / len(resolved) * 100.0) if len(resolved) else 0.0

    print("\n=== BACKTEST 1M VWAP + MACD ===")
    print(f"Total de sinais: {len(trades)}")
    print(f"Trades resolvidos: {len(resolved)}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Ties: {ties}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Media do PnL percentual: {trades['pnl_pct'].mean():.4f}%")

    by_direction = (
        trades.groupby("direction")
        .agg(trades=("direction", "size"), wins=("win", "sum"), avg_pnl_pct=("pnl_pct", "mean"))
        .reset_index()
    )
    if not by_direction.empty:
        by_direction["win_rate"] = by_direction["wins"] / by_direction["trades"] * 100.0
        print("\nPor direcao:")
        print(by_direction.to_string(index=False, float_format=lambda value: f"{value:.4f}"))

    print("\nUltimos 10 sinais:")
    preview = trades[
        ["signal_time", "entry_time", "exit_time", "direction", "entry_open", "exit_close", "outcome", "win", "pnl_pct"]
    ].tail(10)
    print(preview.to_string(index=False, float_format=lambda value: f"{value:.4f}"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest de VWAP + MACD em 1m com saida no bloco 5m.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--save-csv", default="")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    df = add_indicators(fetch_binance_1m(args.symbol, args.limit))
    trades = run_backtest(df)
    summarize(trades)
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nCSV salvo em: {args.save_csv}")


if __name__ == "__main__":
    main()
