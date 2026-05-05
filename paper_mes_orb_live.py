#!/usr/bin/env python3
"""Paper live para MES/ES Opening Range Breakout.

Nao envia ordens reais. Processa candles fechados da sessao regular de NY,
monta o range inicial e simula uma entrada por sessao.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from backtest_mes_ema_scalp import DATABENTO_DATASET, IndexFuturesConfig, calculate_contract_pnl, get_ohlcv, normalize_ohlcv
from paper_mes_ema_scalp_live import interval_to_timedelta, latest_closed_bar_is_stale
from sweep_mes_strategies import NY_TZ, RTH_CLOSE, RTH_OPEN


@dataclass(frozen=True)
class OrbConfig:
    symbol: str = "MES=F"
    period: str = "5d"
    interval: str = "5m"
    contracts: int = 1
    point_value_usd: float = 5.0
    tick_size: float = 0.25
    commission_per_side_usd: float = 0.62
    slippage_ticks: float = 1.0
    window_minutes: int = 30
    stop_points: float = 8.0
    take_profit_points: float = 8.0
    max_hold_bars: int = 24
    buffer_points: float = 0.0


@dataclass
class OrbPosition:
    symbol: str
    direction: str
    entry_time_utc: str
    entry_price: float
    bars_held: int
    contracts: int
    session_date_ny: str


@dataclass
class PendingSignal:
    direction: str
    signal_time_utc: str


@dataclass
class MesOrbState:
    last_processed_ts: str = ""
    session_date_ny: str = ""
    range_high: float | None = None
    range_low: float | None = None
    range_ready: bool = False
    traded_session_date_ny: str = ""
    pending_signal: PendingSignal | None = None
    open_position: OrbPosition | None = None


def parse_ts(value: str) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(value)


def load_state(path: str) -> MesOrbState:
    state_path = Path(path)
    if not state_path.exists() or state_path.stat().st_size == 0:
        return MesOrbState()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    position = raw.get("open_position")
    pending = raw.get("pending_signal")
    return MesOrbState(
        last_processed_ts=raw.get("last_processed_ts", ""),
        session_date_ny=raw.get("session_date_ny", ""),
        range_high=raw.get("range_high"),
        range_low=raw.get("range_low"),
        range_ready=bool(raw.get("range_ready", False)),
        traded_session_date_ny=raw.get("traded_session_date_ny", ""),
        pending_signal=PendingSignal(**pending) if pending else None,
        open_position=OrbPosition(**position) if position else None,
    )


def save_state(path: str, state: MesOrbState) -> None:
    Path(path).write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def append_trade(path: str, trade: dict) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trade.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(trade)


def reset_session(state: MesOrbState, session_date_ny: str) -> None:
    if state.session_date_ny == session_date_ny:
        return
    pending = state.pending_signal
    if pending is not None:
        pending_date = pd.Timestamp(pending.signal_time_utc).tz_convert(NY_TZ).date().isoformat()
        if pending_date != session_date_ny:
            pending = None
    state.session_date_ny = session_date_ny
    state.range_high = None
    state.range_low = None
    state.range_ready = False
    state.pending_signal = pending


def add_session_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_ohlcv(df).sort_values("ts").reset_index(drop=True)
    out["ts_ny"] = out["ts"].dt.tz_convert(NY_TZ)
    out["date_ny"] = out["ts_ny"].dt.date.astype(str)
    out["time_ny"] = out["ts_ny"].dt.strftime("%H:%M")
    return out[(out["time_ny"] >= RTH_OPEN) & (out["time_ny"] < RTH_CLOSE)].reset_index(drop=True)


def range_window_end(row_ts_ny: pd.Timestamp, config: OrbConfig) -> pd.Timestamp:
    session_open = row_ts_ny.normalize() + pd.Timedelta(hours=9, minutes=30)
    return session_open + pd.Timedelta(minutes=config.window_minutes)


def close_position(position: OrbPosition, row: pd.Series, exit_price: float, exit_reason: str, config: OrbConfig) -> dict:
    cost_config = IndexFuturesConfig(
        symbol=config.symbol,
        contracts=config.contracts,
        point_value_usd=config.point_value_usd,
        tick_size=config.tick_size,
        commission_per_side_usd=config.commission_per_side_usd,
        slippage_ticks=config.slippage_ticks,
        stop_points=config.stop_points,
        take_profit_points=config.take_profit_points,
        max_hold_bars=config.max_hold_bars,
    )
    gross, costs, net = calculate_contract_pnl(position.direction, position.entry_price, exit_price, cost_config)
    return {
        "symbol": position.symbol,
        "strategy": "MES ORB 30m",
        "session_date_ny": position.session_date_ny,
        "direction": position.direction,
        "entry_time_utc": position.entry_time_utc,
        "exit_time_utc": pd.Timestamp(row["ts"]).isoformat(),
        "entry_price": round(position.entry_price, 4),
        "exit_price": round(float(exit_price), 4),
        "exit_reason": exit_reason,
        "bars_held": int(position.bars_held),
        "contracts": int(position.contracts),
        "gross_pnl_usd": gross,
        "costs_usd": costs,
        "pnl_usd": net,
        "win": net > 0,
    }


def exit_for_row(position: OrbPosition, row: pd.Series, config: OrbConfig) -> tuple[float, str] | None:
    if position.direction == "LONG":
        stop = position.entry_price - config.stop_points
        take_profit = position.entry_price + config.take_profit_points
        if float(row["low"]) <= stop:
            return stop, "STOP"
        if float(row["high"]) >= take_profit:
            return take_profit, "TAKE_PROFIT"
    else:
        stop = position.entry_price + config.stop_points
        take_profit = position.entry_price - config.take_profit_points
        if float(row["high"]) >= stop:
            return stop, "STOP"
        if float(row["low"]) <= take_profit:
            return take_profit, "TAKE_PROFIT"
    return None


def process_closed_rows(
    df: pd.DataFrame,
    state: MesOrbState,
    config: OrbConfig,
) -> tuple[MesOrbState, list[dict], list[str]]:
    data = add_session_columns(df)
    last_processed = parse_ts(state.last_processed_ts)
    trades: list[dict] = []
    messages: list[str] = []

    for _, row in data.iterrows():
        row_ts = pd.Timestamp(row["ts"])
        row_ts_ny = pd.Timestamp(row["ts_ny"])
        session_date = str(row["date_ny"])
        if last_processed is not None and row_ts <= last_processed:
            continue

        reset_session(state, session_date)

        if state.pending_signal is not None and state.open_position is None:
            state.open_position = OrbPosition(
                symbol=config.symbol,
                direction=state.pending_signal.direction,
                entry_time_utc=row_ts.isoformat(),
                entry_price=round(float(row["open"]), 4),
                bars_held=0,
                contracts=config.contracts,
                session_date_ny=session_date,
            )
            state.traded_session_date_ny = session_date
            messages.append(f"ABRIU {state.open_position.direction} entry={state.open_position.entry_price:.2f}")
            state.pending_signal = None

        if state.open_position is not None:
            state.open_position.bars_held += 1
            exit_hit = exit_for_row(state.open_position, row, config)
            if exit_hit is None and state.open_position.bars_held >= config.max_hold_bars:
                exit_hit = (float(row["close"]), "TIME")
            if exit_hit is not None:
                exit_price, exit_reason = exit_hit
                trade = close_position(state.open_position, row, exit_price, exit_reason, config)
                trades.append(trade)
                messages.append(
                    f"FECHOU {trade['direction']} {exit_reason} entry={trade['entry_price']:.2f} "
                    f"exit={trade['exit_price']:.2f} pnl={trade['pnl_usd']:.2f}"
                )
                state.open_position = None

        window_end = range_window_end(row_ts_ny, config)
        if row_ts_ny < window_end:
            state.range_high = float(row["high"]) if state.range_high is None else max(state.range_high, float(row["high"]))
            state.range_low = float(row["low"]) if state.range_low is None else min(state.range_low, float(row["low"]))
        elif not state.range_ready and state.range_high is not None and state.range_low is not None:
            state.range_ready = True
            messages.append(f"ORB pronto high={state.range_high:.2f} low={state.range_low:.2f}")

        if (
            state.range_ready
            and state.open_position is None
            and state.pending_signal is None
            and state.traded_session_date_ny != session_date
            and state.range_high is not None
            and state.range_low is not None
        ):
            close = float(row["close"])
            if close > state.range_high + config.buffer_points:
                state.pending_signal = PendingSignal("LONG", row_ts.isoformat())
                messages.append(f"SINAL LONG close={close:.2f} range_high={state.range_high:.2f}")
            elif close < state.range_low - config.buffer_points:
                state.pending_signal = PendingSignal("SHORT", row_ts.isoformat())
                messages.append(f"SINAL SHORT close={close:.2f} range_low={state.range_low:.2f}")

        state.last_processed_ts = row_ts.isoformat()
        last_processed = row_ts

    return state, trades, messages


def build_config(args: argparse.Namespace) -> OrbConfig:
    return OrbConfig(
        symbol=args.symbol,
        period=args.period,
        interval=args.interval,
        contracts=args.contracts,
        point_value_usd=args.point_value_usd,
        tick_size=args.tick_size,
        commission_per_side_usd=args.commission_per_side_usd,
        slippage_ticks=args.slippage_ticks,
        window_minutes=args.window_minutes,
        stop_points=args.stop_points,
        take_profit_points=args.take_profit_points,
        max_hold_bars=args.max_hold_bars,
        buffer_points=args.buffer_points,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper live MES/ES ORB 30m.")
    parser.add_argument("--symbol", default="MES=F")
    parser.add_argument("--period", default="5d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--data-source", choices=["auto", "yahoo", "databento"], default="yahoo")
    parser.add_argument("--databento-dataset", default=DATABENTO_DATASET)
    parser.add_argument("--databento-symbol", default="")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-stale-minutes", type=int, default=30)
    parser.add_argument("--trades-csv", default="paper_mes_orb_30m_trades.csv")
    parser.add_argument("--state-file", default="paper_mes_orb_30m_state.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cycles", type=int, default=0)
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--point-value-usd", type=float, default=5.0)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--commission-per-side-usd", type=float, default=0.62)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--window-minutes", type=int, default=30)
    parser.add_argument("--stop-points", type=float, default=8.0)
    parser.add_argument("--take-profit-points", type=float, default=8.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--buffer-points", type=float, default=0.0)
    return parser.parse_args()


def run_loop(args: argparse.Namespace) -> None:
    config = build_config(args)
    print("=== PAPER MES/ES ORB LIVE ===")
    print("Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"Dados: {args.data_source} | Symbol: {config.symbol} | Intervalo: {config.interval}")
    print(f"CSV: {args.trades_csv} | Estado: {args.state_file}")
    print(
        f"Setup: ORB {config.window_minutes}m, stop={config.stop_points}pts, "
        f"tp={config.take_profit_points}pts, hold={config.max_hold_bars} candles"
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
                max_stale = pd.Timedelta(minutes=args.max_stale_minutes)
                stale = latest_closed_bar_is_stale(latest_closed_ts, now_ts, config.interval)
                if stale and (now_ts - latest_closed_ts) > max_stale:
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
                        f"HOLD ts={pd.Timestamp(last['ts']).isoformat()} close={float(last['close']):.2f}"
                        f" range={state.range_low}/{state.range_high}{open_txt}"
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
