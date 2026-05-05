#!/usr/bin/env python3
"""Backtest de wick reversal em candles de 5 minutos.

Regra:
- Wick inferior longo + candle verde -> entrar UP na abertura do proximo candle.
- Wick superior longo + candle vermelho -> entrar DOWN na abertura do proximo candle.
- Saida no fechamento desse proximo candle, equivalente ao vencimento do mercado
  Polymarket BTC Up/Down 5m correspondente.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass

import pandas as pd

from backtest_5m_confirmation import fetch_binance_5m


@dataclass(frozen=True)
class WickSignal:
    direction: str
    body: float
    lower_wick: float
    upper_wick: float


@dataclass(frozen=True)
class WickTrade:
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    direction: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    body_pct: float
    lower_wick_to_body: float
    upper_wick_to_body: float
    entry_open: float
    exit_close: float
    pnl_points: float
    pnl_pct: float
    outcome: str
    win: bool


def detect_wick_reversal_signal(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    wick_to_body_ratio: float,
    opposite_wick_max_body_ratio: float,
) -> WickSignal | None:
    body = abs(close_price - open_price)
    if body <= 0:
        return None

    lower_wick = min(open_price, close_price) - low_price
    upper_wick = high_price - max(open_price, close_price)
    lower_ratio = lower_wick / body
    upper_ratio = upper_wick / body

    if (
        close_price > open_price
        and lower_ratio >= wick_to_body_ratio
        and upper_ratio <= opposite_wick_max_body_ratio
    ):
        return WickSignal(
            direction="UP",
            body=body,
            lower_wick=lower_wick,
            upper_wick=upper_wick,
        )

    if (
        close_price < open_price
        and upper_ratio >= wick_to_body_ratio
        and lower_ratio <= opposite_wick_max_body_ratio
    ):
        return WickSignal(
            direction="DOWN",
            body=body,
            lower_wick=lower_wick,
            upper_wick=upper_wick,
        )

    return None


def run_backtest(
    df: pd.DataFrame,
    wick_to_body_ratio: float,
    opposite_wick_max_body_ratio: float,
) -> pd.DataFrame:
    trades: list[dict] = []

    for signal_idx in range(len(df) - 1):
        row = df.iloc[signal_idx]
        signal = detect_wick_reversal_signal(
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            wick_to_body_ratio=wick_to_body_ratio,
            opposite_wick_max_body_ratio=opposite_wick_max_body_ratio,
        )
        if signal is None:
            continue

        entry_idx = signal_idx + 1
        entry_open = float(df["open"].iloc[entry_idx])
        exit_close = float(df["close"].iloc[entry_idx])
        pnl_points = exit_close - entry_open if signal.direction == "UP" else entry_open - exit_close
        pnl_pct = pnl_points / entry_open * 100.0

        outcome = "TIE"
        if exit_close > entry_open:
            outcome = "UP"
        elif exit_close < entry_open:
            outcome = "DOWN"

        win = outcome == signal.direction

        trade = WickTrade(
            signal_time=df["ts"].iloc[signal_idx],
            entry_time=df["ts"].iloc[entry_idx],
            direction=signal.direction,
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            body_pct=signal.body / max(float(row["open"]), 1e-9) * 100.0,
            lower_wick_to_body=signal.lower_wick / signal.body,
            upper_wick_to_body=signal.upper_wick / signal.body,
            entry_open=entry_open,
            exit_close=exit_close,
            pnl_points=pnl_points,
            pnl_pct=pnl_pct,
            outcome=outcome,
            win=win,
        )
        trades.append(asdict(trade))

    return pd.DataFrame(trades)


def summarize(trades: pd.DataFrame, wick_to_body_ratio: float, opposite_wick_max_body_ratio: float) -> None:
    if trades.empty:
        print("Nenhum sinal encontrado.")
        return

    ties = int((trades["outcome"] == "TIE").sum())
    resolved = trades[trades["outcome"] != "TIE"].copy()
    wins = int(resolved["win"].sum()) if not resolved.empty else 0
    losses = len(resolved) - wins
    win_rate = (wins / len(resolved) * 100.0) if len(resolved) else 0.0

    print("\n=== BACKTEST 5M WICK REVERSAL ===")
    print(f"Threshold wick/body: {wick_to_body_ratio:.2f}")
    print(f"Opposite wick max/body: {opposite_wick_max_body_ratio:.2f}")
    print(f"Total de sinais: {len(trades)}")
    print(f"Trades resolvidos: {len(resolved)}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Ties: {ties}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Media do PnL percentual: {trades['pnl_pct'].mean():.4f}%")
    print(f"Media body% do candle de sinal: {trades['body_pct'].mean():.4f}%")

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
            "signal_time",
            "entry_time",
            "direction",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "lower_wick_to_body",
            "upper_wick_to_body",
            "entry_open",
            "exit_close",
            "outcome",
            "win",
            "pnl_pct",
        ]
    ].tail(10)
    print(preview.to_string(index=False, float_format=lambda value: f"{value:.4f}"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest da estrategia de wick reversal em 5m.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000, help="Numero de candles 5m para testar.")
    parser.add_argument("--wick-to-body-ratio", type=float, default=2.0)
    parser.add_argument("--opposite-wick-max-body-ratio", type=float, default=0.75)
    parser.add_argument("--save-csv", default="", help="Opcional: salva os trades em CSV.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    df = fetch_binance_5m(args.symbol, args.limit)
    trades = run_backtest(
        df=df,
        wick_to_body_ratio=args.wick_to_body_ratio,
        opposite_wick_max_body_ratio=args.opposite_wick_max_body_ratio,
    )
    summarize(
        trades=trades,
        wick_to_body_ratio=args.wick_to_body_ratio,
        opposite_wick_max_body_ratio=args.opposite_wick_max_body_ratio,
    )
    if args.save_csv:
        trades.to_csv(args.save_csv, index=False)
        print(f"\nCSV salvo em: {args.save_csv}")


if __name__ == "__main__":
    main()
