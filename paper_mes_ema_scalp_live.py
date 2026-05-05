#!/usr/bin/env python3
"""Paper live para MES/ES EMA scalp usando dados delayed do Yahoo.

Nao envia ordens reais. Processa candles fechados, simula uma posicao por vez
e registra apenas trades fechados em CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from backtest_mes_ema_scalp import (
    DATABENTO_DATASET,
    IndexFuturesConfig,
    add_indicators,
    calculate_contract_pnl,
    detect_signal,
    exit_for_bar,
    get_ohlcv,
)


@dataclass
class PaperMesPosition:
    symbol: str
    direction: str
    entry_time_utc: str
    entry_price: float
    bars_held: int
    contracts: int


@dataclass
class MesPaperState:
    last_processed_ts: str = ""
    cooldown_until_ts: str = ""
    open_position: PaperMesPosition | None = None


def load_state(path: str) -> MesPaperState:
    state_path = Path(path)
    if not state_path.exists() or state_path.stat().st_size == 0:
        return MesPaperState()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    position = raw.get("open_position")
    return MesPaperState(
        last_processed_ts=raw.get("last_processed_ts", ""),
        cooldown_until_ts=raw.get("cooldown_until_ts", ""),
        open_position=PaperMesPosition(**position) if position else None,
    )


def save_state(path: str, state: MesPaperState) -> None:
    raw = asdict(state)
    Path(path).write_text(json.dumps(raw, indent=2), encoding="utf-8")


def append_trade(path: str, trade: dict) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trade.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(trade)


def close_position(
    position: PaperMesPosition,
    exit_time_utc: str,
    exit_price: float,
    exit_reason: str,
    config: IndexFuturesConfig,
) -> dict:
    gross, costs, net = calculate_contract_pnl(position.direction, position.entry_price, exit_price, config)
    return {
        "symbol": position.symbol,
        "direction": position.direction,
        "entry_time_utc": position.entry_time_utc,
        "exit_time_utc": exit_time_utc,
        "entry_price": round(position.entry_price, 4),
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "bars_held": int(position.bars_held),
        "contracts": int(position.contracts),
        "gross_pnl_usd": gross,
        "costs_usd": costs,
        "pnl_usd": net,
        "win": net > 0,
    }


def parse_ts(value: str) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(value)


def cooldown_active(row_ts: pd.Timestamp, cooldown_until_ts: str) -> bool:
    cooldown = parse_ts(cooldown_until_ts)
    return cooldown is not None and row_ts <= cooldown


def interval_to_timedelta(interval: str) -> pd.Timedelta:
    text = interval.strip().lower()
    if text.endswith("m"):
        return pd.Timedelta(minutes=int(text[:-1]))
    if text.endswith("h"):
        return pd.Timedelta(hours=int(text[:-1]))
    if text.endswith("d"):
        return pd.Timedelta(days=int(text[:-1]))
    raise ValueError(f"Intervalo nao suportado: {interval}")


def latest_closed_bar_is_stale(latest_ts: pd.Timestamp, now: pd.Timestamp, interval: str) -> bool:
    interval_delta = interval_to_timedelta(interval)
    # aceita atraso normal de ate um candle + pequena folga de rede/clock
    stale_after = interval_delta + pd.Timedelta(minutes=1)
    return (now - latest_ts) > stale_after


def process_closed_rows(
    df: pd.DataFrame,
    state: MesPaperState,
    config: IndexFuturesConfig,
) -> tuple[MesPaperState, list[dict], list[str]]:
    data = add_indicators(df, config)
    warmup = max(config.ema_slow, config.adx_period + config.slope_lookback) + 2
    last_processed = parse_ts(state.last_processed_ts)
    trades: list[dict] = []
    messages: list[str] = []

    for index in range(warmup, len(data)):
        row = data.iloc[index]
        row_ts = pd.Timestamp(row["ts"])
        if last_processed is not None and row_ts <= last_processed:
            continue

        if state.open_position is not None:
            state.open_position.bars_held += 1
            exit_hit = exit_for_bar(state.open_position.direction, row, state.open_position.entry_price, config)
            if exit_hit is None and state.open_position.bars_held >= config.max_hold_bars:
                exit_hit = (float(row["close"]), "TIME")
            if exit_hit is not None:
                exit_price, exit_reason = exit_hit
                trade = close_position(state.open_position, row_ts.isoformat(), exit_price, exit_reason, config)
                trades.append(trade)
                messages.append(
                    f"FECHOU {trade['direction']} {exit_reason} entry={trade['entry_price']:.2f} "
                    f"exit={trade['exit_price']:.2f} pnl={trade['pnl_usd']:.2f}"
                )
                state.open_position = None
                cooldown_index = min(index + config.cooldown_bars, len(data) - 1)
                state.cooldown_until_ts = pd.Timestamp(data.iloc[cooldown_index]["ts"]).isoformat()

        if state.open_position is None and not cooldown_active(row_ts, state.cooldown_until_ts):
            prev_row = data.iloc[index - 1]
            signal = detect_signal(prev_row, row, config)
            if signal is not None:
                state.open_position = PaperMesPosition(
                    symbol=config.symbol,
                    direction=signal,
                    entry_time_utc=row_ts.isoformat(),
                    entry_price=round(float(row["close"]), 4),
                    bars_held=0,
                    contracts=config.contracts,
                )
                messages.append(
                    f"ABRIU {signal} entry={state.open_position.entry_price:.2f} "
                    f"adx={float(row['adx']):.2f} slope={float(row['ema_slope']):.2f}"
                )

        state.last_processed_ts = row_ts.isoformat()

    return state, trades, messages


def build_config(args: argparse.Namespace) -> IndexFuturesConfig:
    return IndexFuturesConfig(
        symbol=args.symbol,
        period=args.period,
        interval=args.interval,
        contracts=args.contracts,
        point_value_usd=args.point_value_usd,
        tick_size=args.tick_size,
        commission_per_side_usd=args.commission_per_side_usd,
        slippage_ticks=args.slippage_ticks,
        ema_fast=args.ema_fast,
        ema_mid=args.ema_mid,
        ema_slow=args.ema_slow,
        slope_lookback=args.slope_lookback,
        min_ema_gap_points=args.min_ema_gap_points,
        min_slope_points=args.min_slope_points,
        max_price_ema_fast_distance_points=args.max_price_ema_fast_distance_points,
        trend_filter=args.trend_filter,
        adx_period=args.adx_period,
        min_adx=args.min_adx,
        entry_mode=args.entry_mode,
        stop_points=args.stop_points,
        take_profit_points=args.take_profit_points,
        max_hold_bars=args.max_hold_bars,
        cooldown_bars=args.cooldown_bars,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper live MES/ES EMA scalp com Yahoo ou Databento.")
    parser.add_argument("--symbol", default="MES=F")
    parser.add_argument("--period", default="5d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--data-source", choices=["auto", "yahoo", "databento"], default="yahoo")
    parser.add_argument("--databento-dataset", default=DATABENTO_DATASET)
    parser.add_argument("--databento-symbol", default="")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--trades-csv", default="paper_mes_ema_scalp_trades.csv")
    parser.add_argument("--state-file", default="paper_mes_ema_scalp_state.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cycles", type=int, default=0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--point-value-usd", type=float, default=5.0)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--commission-per-side-usd", type=float, default=0.62)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--ema-fast", type=int, default=9)
    parser.add_argument("--ema-mid", type=int, default=21)
    parser.add_argument("--ema-slow", type=int, default=60)
    parser.add_argument("--slope-lookback", type=int, default=5)
    parser.add_argument("--min-ema-gap-points", type=float, default=1.0)
    parser.add_argument("--min-slope-points", type=float, default=0.5)
    parser.add_argument("--max-price-ema-fast-distance-points", type=float, default=12.0)
    parser.add_argument("--trend-filter", choices=["none", "adx"], default="adx")
    parser.add_argument("--adx-period", type=int, default=14)
    parser.add_argument("--min-adx", type=float, default=22.0)
    parser.add_argument("--entry-mode", choices=["trend", "pullback"], default="trend")
    parser.add_argument("--stop-points", type=float, default=12.0)
    parser.add_argument("--take-profit-points", type=float, default=18.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--cooldown-bars", type=int, default=6)
    return parser.parse_args()


def run_loop(args: argparse.Namespace) -> None:
    config = build_config(args)
    print("=== PAPER MES/ES EMA SCALP LIVE ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"Dados: {args.data_source} | Symbol: {config.symbol} | Intervalo: {config.interval}")
    print(f"CSV: {args.trades_csv} | Estado: {args.state_file}")
    print(
        f"Setup: EMA={config.ema_fast}/{config.ema_mid}/{config.ema_slow}, ADX>={config.min_adx}, "
        f"stop={config.stop_points}pts, tp={config.take_profit_points}pts, contracts={config.contracts}"
    )

    iterations = 0
    while True:
        try:
            state = load_state(args.state_file)
            df = get_ohlcv(
                args.data_source,
                config.symbol,
                config.period,
                config.interval,
                databento_dataset=args.databento_dataset,
                databento_symbol=args.databento_symbol,
            )
            if len(df) < 3:
                print("HOLD dados insuficientes")
            else:
                closed_df = df.iloc[:-1].copy()
                now_ts = pd.Timestamp.utcnow()
                latest_closed_ts = pd.Timestamp(closed_df.iloc[-1]["ts"])
                if latest_closed_bar_is_stale(latest_closed_ts, now_ts, config.interval):
                    print(
                        f"STALE ultimo candle fechado em {latest_closed_ts.isoformat()} "
                        f"(agora={now_ts.isoformat()})"
                    )
                    if args.once:
                        break
                    iterations += 1
                    if args.cycles and iterations >= args.cycles:
                        break
                    time.sleep(args.poll_seconds)
                    continue
                if not state.last_processed_ts:
                    latest_ts = pd.Timestamp(closed_df.iloc[-1]["ts"]).isoformat()
                    state.last_processed_ts = latest_ts
                    save_state(args.state_file, state)
                    print(f"Inicializado em {latest_ts}; aguardando proximo candle fechado.")
                else:
                    state, trades, messages = process_closed_rows(closed_df, state, config)
                    for trade in trades:
                        append_trade(args.trades_csv, trade)
                    save_state(args.state_file, state)
                    if messages:
                        for message in messages:
                            print(message)
                    else:
                        last = closed_df.iloc[-1]
                        open_txt = f" | aberta={state.open_position.direction}" if state.open_position else ""
                        print(
                            f"HOLD ts={pd.Timestamp(last['ts']).isoformat()} close={float(last['close']):.2f}{open_txt}"
                        )
        except Exception as exc:
            print(f"ERRO {type(exc).__name__}: {exc}")

        iterations += 1
        if args.once or (args.cycles and iterations >= args.cycles):
            break
        time.sleep(args.poll_seconds)


def main() -> None:
    run_loop(parse_args())


if __name__ == "__main__":
    main()
