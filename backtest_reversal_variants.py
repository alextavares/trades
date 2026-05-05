#!/usr/bin/env python3
"""Compara variantes da estrategia de reversao DOWN em 5 minutos.

Baseado no bot atual:
- candle anterior verde e esticado
- candle atual vermelho confirma a reversao
- entrada no proximo candle
- saida no fechamento desse proximo candle, equivalente ao vencimento do
  mercado Polymarket BTC Up/Down 5m correspondente
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass

import pandas as pd

from backtest_5m_confirmation import fetch_binance_5m


@dataclass(frozen=True)
class VariantConfig:
    name: str
    rsi_threshold: float | None
    require_bollinger: bool


VARIANTS = (
    VariantConfig(name="rsi70_bollinger", rsi_threshold=70.0, require_bollinger=True),
    VariantConfig(name="rsi70_only", rsi_threshold=70.0, require_bollinger=False),
    VariantConfig(name="rsi65_only", rsi_threshold=65.0, require_bollinger=False),
)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["sma_20"] = out["close"].rolling(20).mean()
    out["std_20"] = out["close"].rolling(20).std()
    out["upper_band"] = out["sma_20"] + (out["std_20"] * 2)

    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["rsi"] = 100 - (100 / (1 + rs))
    out["color"] = out["color"].astype(int)
    return out


def detect_reversal_down_signal(prev_row: pd.Series, curr_row: pd.Series, config: VariantConfig) -> bool:
    if int(prev_row["color"]) != 1 or int(curr_row["color"]) != -1:
        return False

    if config.rsi_threshold is not None:
        rsi = float(prev_row["rsi"])
        if not pd.notna(rsi) or rsi <= config.rsi_threshold:
            return False

    if config.require_bollinger:
        upper_band = float(prev_row["upper_band"])
        if not pd.notna(upper_band) or float(prev_row["high"]) < upper_band:
            return False

    return True


def run_variant(df: pd.DataFrame, config: VariantConfig) -> pd.DataFrame:
    trades: list[dict] = []
    for signal_idx in range(1, len(df) - 1):
        prev_row = df.iloc[signal_idx - 1]
        curr_row = df.iloc[signal_idx]
        if not detect_reversal_down_signal(prev_row, curr_row, config):
            continue

        entry_idx = signal_idx + 1
        entry_open = float(df["open"].iloc[entry_idx])
        exit_close = float(df["close"].iloc[entry_idx])
        pnl_points = entry_open - exit_close
        pnl_pct = pnl_points / entry_open * 100.0
        win = exit_close < entry_open
        outcome = "DOWN" if exit_close < entry_open else "UP" if exit_close > entry_open else "TIE"

        trades.append(
            {
                "variant": config.name,
                "signal_time": df["ts"].iloc[signal_idx],
                "entry_time": df["ts"].iloc[entry_idx],
                "prev_open": float(prev_row["open"]),
                "prev_high": float(prev_row["high"]),
                "prev_low": float(prev_row["low"]),
                "prev_close": float(prev_row["close"]),
                "prev_rsi": float(prev_row["rsi"]),
                "prev_upper_band": float(prev_row["upper_band"]),
                "curr_open": float(curr_row["open"]),
                "curr_close": float(curr_row["close"]),
                "entry_open": entry_open,
                "exit_close": exit_close,
                "outcome": outcome,
                "win": win,
                "pnl_points": pnl_points,
                "pnl_pct": pnl_pct,
            }
        )
    return pd.DataFrame(trades)


def summarize(all_trades: pd.DataFrame) -> None:
    if all_trades.empty:
        print("Nenhum sinal encontrado.")
        return

    summary = (
        all_trades.groupby("variant")
        .agg(
            trades=("variant", "size"),
            wins=("win", "sum"),
            avg_pnl_pct=("pnl_pct", "mean"),
        )
        .reset_index()
    )
    summary["losses"] = summary["trades"] - summary["wins"]
    summary["win_rate"] = summary["wins"] / summary["trades"] * 100.0

    print("\n=== BACKTEST REVERSAL VARIANTS (DOWN) ===")
    print(summary[["variant", "trades", "wins", "losses", "win_rate", "avg_pnl_pct"]].to_string(
        index=False,
        float_format=lambda value: f"{value:.4f}",
    ))

    for variant in summary["variant"]:
        print(f"\nUltimos 5 sinais de {variant}:")
        preview = all_trades[all_trades["variant"] == variant][
            ["signal_time", "entry_time", "prev_rsi", "entry_open", "exit_close", "outcome", "win", "pnl_pct"]
        ].tail(5)
        print(preview.to_string(index=False, float_format=lambda value: f"{value:.4f}"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compara variantes da estrategia de reversao DOWN.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--save-csv", default="")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    df = add_indicators(fetch_binance_5m(args.symbol, args.limit))
    frames = [run_variant(df, variant) for variant in VARIANTS]
    all_trades = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if any(
        not frame.empty for frame in frames
    ) else pd.DataFrame()
    summarize(all_trades)
    if args.save_csv:
        all_trades.to_csv(args.save_csv, index=False)
        print(f"\nCSV salvo em: {args.save_csv}")


if __name__ == "__main__":
    main()
