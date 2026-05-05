#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import pandas as pd

from backtest_mt5_us500 import Us500Config, add_indicators, detect_signal, resample_mt5_rates
from export_mt5_rates import infer_broker_utc_offset_hours, normalize_mt5_times


TIMEFRAME_MAP = {
    "1min": mt5.TIMEFRAME_M1,
    "5min": mt5.TIMEFRAME_M5,
    "15min": mt5.TIMEFRAME_M15,
    "30min": mt5.TIMEFRAME_M30,
    "1h": mt5.TIMEFRAME_H1,
}
TIMEFRAME_DURATION = {
    "1min": pd.Timedelta(minutes=1),
    "5min": pd.Timedelta(minutes=5),
    "15min": pd.Timedelta(minutes=15),
    "30min": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
}


@dataclass
class OpenUs500Position:
    ticket: int
    symbol: str
    direction: str
    entry_time_utc: str
    entry_price: float
    lot_size: float
    bars_held: int
    stop_loss: float
    take_profit: float


@dataclass
class Mt5Us500State:
    last_processed_ts: str = ""
    cooldown_until_ts: str = ""
    open_position: OpenUs500Position | None = None


def parse_ts(value: str) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(value)


def load_state(path: str) -> Mt5Us500State:
    state_path = Path(path)
    if not state_path.exists() or state_path.stat().st_size == 0:
        return Mt5Us500State()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    open_position = raw.get("open_position")
    return Mt5Us500State(
        last_processed_ts=raw.get("last_processed_ts", ""),
        cooldown_until_ts=raw.get("cooldown_until_ts", ""),
        open_position=OpenUs500Position(**open_position) if open_position else None,
    )


def save_state(path: str, state: Mt5Us500State) -> None:
    Path(path).write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


def append_trade(path: str, trade: dict[str, Any]) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trade.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(trade)


def build_entry_levels(direction: str, entry_price: float, config: Us500Config) -> tuple[float, float]:
    if direction == "LONG":
        return entry_price - config.stop_points, entry_price + config.take_profit_points
    return entry_price + config.stop_points, entry_price - config.take_profit_points


def build_signal_snapshot(row: pd.Series) -> dict[str, float]:
    return {
        "close": round(float(row["close"]), 4),
        "spread_points": round(float(row["spread_points"]), 4),
        "adx": round(float(row.get("adx", 0.0)), 4),
        "ema_fast": round(float(row["ema_fast"]), 4),
        "ema_mid": round(float(row["ema_mid"]), 4),
        "ema_slow": round(float(row["ema_slow"]), 4),
        "ema_slope": round(float(row["ema_slope"]), 4),
    }


def build_hold_message(closed_bar_ts: pd.Timestamp, close_price: float, polled_at_utc: pd.Timestamp) -> str:
    return (
        f"HOLD closed_bar_ts={closed_bar_ts.isoformat()} "
        f"close={close_price:.2f} polled_at_utc={polled_at_utc.isoformat()}"
    )


def increment_bars_held(state: Mt5Us500State) -> None:
    if state.open_position is not None:
        state.open_position.bars_held += 1


def cooldown_active(row_ts: pd.Timestamp, cooldown_until_ts: str) -> bool:
    cooldown = parse_ts(cooldown_until_ts)
    return cooldown is not None and row_ts <= cooldown


def reconcile_state_timestamp(state: Mt5Us500State, latest_closed_bar_ts: pd.Timestamp, timeframe: str) -> bool:
    last_processed = parse_ts(state.last_processed_ts)
    if last_processed is None or state.open_position is not None:
        return False
    max_reasonable_ts = latest_closed_bar_ts + TIMEFRAME_DURATION[timeframe]
    if last_processed > max_reasonable_ts:
        state.last_processed_ts = latest_closed_bar_ts.isoformat()
        return True
    return False


def bootstrap_state_to_latest_closed_bar(state: Mt5Us500State, latest_closed_bar_ts: pd.Timestamp) -> bool:
    if state.last_processed_ts or state.open_position is not None:
        return False
    state.last_processed_ts = latest_closed_bar_ts.isoformat()
    return True


def process_closed_rows(df: pd.DataFrame, state: Mt5Us500State, config: Us500Config) -> tuple[Mt5Us500State, list[dict[str, Any]]]:
    data = add_indicators(df, config)
    warmup = max(config.ema_slow, config.adx_period + config.slope_lookback) + 2
    last_processed = parse_ts(state.last_processed_ts)
    actions: list[dict[str, Any]] = []

    for index in range(warmup, len(data)):
        row = data.iloc[index]
        row_ts = pd.Timestamp(row["ts"])
        if last_processed is not None and row_ts <= last_processed:
            continue

        if state.open_position is not None:
            increment_bars_held(state)
            if state.open_position.bars_held >= config.max_hold_bars:
                actions.append(
                    {
                        "kind": "FORCE_CLOSE",
                        "ticket": state.open_position.ticket,
                        "row_ts": row_ts.isoformat(),
                        "bars_held": state.open_position.bars_held,
                        "reason": "TIME",
                    }
                )

        if state.open_position is None and not cooldown_active(row_ts, state.cooldown_until_ts):
            prev_row = data.iloc[index - 1]
            direction = detect_signal(prev_row, row, config)
            if direction is not None:
                stop_loss, take_profit = build_entry_levels(direction, float(row["close"]), config)
                actions.append(
                    {
                        "kind": "OPEN_SIGNAL",
                        "row_ts": row_ts.isoformat(),
                        "direction": direction,
                        "stop_loss": round(stop_loss, 4),
                        "take_profit": round(take_profit, 4),
                        **build_signal_snapshot(row),
                    }
                )

        state.last_processed_ts = row_ts.isoformat()

    return state, actions


def fetch_recent_closed_bars(symbol: str, timeframe: str, bars: int, broker_utc_offset_hours: int | None = None) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], 0, bars + 1)
    if rates is None:
        raise RuntimeError(f"copy_rates_from_pos failed: {mt5.last_error()}")
    frame = pd.DataFrame(rates)
    if frame.empty or len(frame) < 3:
        raise RuntimeError("MT5 retornou poucos candles para processar")
    normalized_timeframe = {
        "1min": "M1",
        "5min": "M5",
        "15min": "M15",
        "30min": "M30",
        "1h": "H1",
    }[timeframe]
    inferred_offset = (
        infer_broker_utc_offset_hours(frame, normalized_timeframe)
        if broker_utc_offset_hours is None
        else broker_utc_offset_hours
    )
    frame["ts"] = normalize_mt5_times(frame, broker_utc_offset_hours=inferred_offset)
    out = pd.DataFrame(
        {
            "ts": frame["ts"],
            "open": frame["open"].astype(float),
            "high": frame["high"].astype(float),
            "low": frame["low"].astype(float),
            "close": frame["close"].astype(float),
            "volume": frame["tick_volume"].astype(float),
            "spread_points": frame["spread"].astype(float),
        }
    )
    # ultimo candle pode estar em formacao
    return out.iloc[:-1].reset_index(drop=True)


def position_matches(position: Any, magic: int | None = None, comment_prefix: str | None = None) -> bool:
    if magic is not None and int(getattr(position, "magic", 0)) != int(magic):
        return False
    if comment_prefix:
        comment = str(getattr(position, "comment", "") or "")
        if not comment.startswith(comment_prefix):
            return False
    return True


def current_symbol_position(symbol: str, magic: int | None = None, comment_prefix: str | None = None) -> Any | None:
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for position in positions:
        if position_matches(position, magic=magic, comment_prefix=comment_prefix):
            return position
    return None


def build_open_request(symbol: str, lot_size: float, direction: str, stop_loss: float, take_profit: float, magic: int, comment: str) -> dict[str, Any]:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Sem tick para {symbol}: {mt5.last_error()}")
    if direction == "LONG":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 20,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }


def build_close_request(position: Any, magic: int, comment: str) -> dict[str, Any]:
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        raise RuntimeError(f"Sem tick para {position.symbol}: {mt5.last_error()}")
    if position.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "position": position.ticket,
        "volume": position.volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }


def sync_closed_position(state: Mt5Us500State, now_utc: pd.Timestamp) -> dict[str, Any] | None:
    if state.open_position is None:
        return None
    ticket = state.open_position.ticket
    from_dt = parse_ts(state.open_position.entry_time_utc) - pd.Timedelta(days=2)
    deals = mt5.history_deals_get(from_dt.to_pydatetime(), now_utc.to_pydatetime())
    if deals is None:
        return None
    related = [deal for deal in deals if getattr(deal, "position_id", None) == ticket]
    if not related:
        return None
    exit_deals = [deal for deal in related if getattr(deal, "entry", None) in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    if not exit_deals:
        return None
    exit_deal = sorted(exit_deals, key=lambda item: item.time)[-1]
    pnl = sum(float(getattr(deal, "profit", 0.0)) + float(getattr(deal, "commission", 0.0)) + float(getattr(deal, "swap", 0.0)) for deal in related)
    trade = {
        "symbol": state.open_position.symbol,
        "direction": state.open_position.direction,
        "entry_time_utc": state.open_position.entry_time_utc,
        "exit_time_utc": pd.Timestamp(exit_deal.time, unit="s", tz="UTC").isoformat(),
        "entry_price": round(state.open_position.entry_price, 4),
        "exit_price": round(float(exit_deal.price), 4),
        "exit_reason": "BROKER_CLOSE",
        "bars_held": state.open_position.bars_held,
        "lot_size": state.open_position.lot_size,
        "stop_loss": state.open_position.stop_loss,
        "take_profit": state.open_position.take_profit,
        "gross_pnl_usd": round(pnl, 4),
        "costs_usd": 0.0,
        "pnl_usd": round(pnl, 4),
        "win": pnl > 0,
    }
    state.open_position = None
    state.cooldown_until_ts = now_utc.isoformat()
    return trade


def ensure_mt5(symbol: str) -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Could not select symbol {symbol}: {mt5.last_error()}")
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or not terminal.connected:
        raise RuntimeError("MT5 terminal nao conectado")
    if account is None:
        raise RuntimeError("Conta MT5 indisponivel")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="US500 MT5 demo bot using the validated 15min pullback+ADX setup.")
    parser.add_argument("--symbol", default="US500")
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAME_MAP), default="15min")
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--min-adx", type=float, default=20.0)
    parser.add_argument("--cooldown-bars", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--bars", type=int, default=600)
    parser.add_argument("--state-file", default="mt5_us500_demo_state.json")
    parser.add_argument("--trades-csv", default="mt5_us500_demo_trades.csv")
    parser.add_argument("--magic", type=int, default=505001)
    parser.add_argument("--comment", default="codex-us500-demo")
    parser.add_argument("--broker-utc-offset-hours", type=int, default=None)
    parser.add_argument("--execute", action="store_true", help="Actually send demo orders to MT5")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cycles", type=int, default=0)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Us500Config:
    return Us500Config(
        timeframe=args.timeframe,
        lot_size=args.lot_size,
        ema_fast=9,
        ema_mid=21,
        ema_slow=60,
        slope_lookback=5,
        min_ema_gap_points=0.5,
        min_slope_points=0.25,
        max_price_ema_fast_distance_points=4.0,
        entry_mode="pullback",
        trend_filter="adx",
        adx_period=14,
        min_adx=args.min_adx,
        stop_points=7.0,
        take_profit_points=10.0,
        max_hold_bars=12,
        cooldown_bars=args.cooldown_bars,
        session_start_hour_brt=10,
        session_end_hour_brt=18,
    )


def run_loop(args: argparse.Namespace) -> None:
    config = build_config(args)
    print("=== MT5 US500 DEMO BOT ===")
    print(f"Symbol={args.symbol} Timeframe={config.timeframe} Lot={config.lot_size} Execute={args.execute}")
    print(
        "Setup="
        f"15min pullback + ADX, stop=7, tp=10, min_adx={config.min_adx:g}, "
        f"cooldown={config.cooldown_bars}, session=10-18 BRT"
    )

    ensure_mt5(args.symbol)
    iterations = 0
    last_logged_hold_ts = ""

    try:
        while True:
            state = load_state(args.state_file)
            now_utc = pd.Timestamp.utcnow()

            live_position = current_symbol_position(args.symbol, args.magic, args.comment)
            any_symbol_position = current_symbol_position(args.symbol)

            if state.open_position is not None and live_position is None:
                trade = sync_closed_position(state, now_utc)
                if trade is not None:
                    append_trade(args.trades_csv, trade)
                    print(
                        f"SYNC CLOSE ticket={trade['symbol']} exit={trade['exit_price']:.2f} "
                        f"pnl={trade['pnl_usd']:.2f}"
                    )
                    save_state(args.state_file, state)
                else:
                    print(f"CLEAR STALE STATE ticket={state.open_position.ticket}")
                    state.open_position = None
                    state.cooldown_until_ts = now_utc.isoformat()
                    save_state(args.state_file, state)

            closed_df = fetch_recent_closed_bars(
                args.symbol,
                config.timeframe,
                args.bars,
                broker_utc_offset_hours=args.broker_utc_offset_hours,
            )
            latest_closed_bar_ts = pd.Timestamp(closed_df.iloc[-1]["ts"])
            if bootstrap_state_to_latest_closed_bar(state, latest_closed_bar_ts):
                print(f"BOOTSTRAP last_processed_ts={state.last_processed_ts}")
            if reconcile_state_timestamp(state, pd.Timestamp(closed_df.iloc[-1]["ts"]), config.timeframe):
                print(f"REBASED last_processed_ts={state.last_processed_ts}")
            state, actions = process_closed_rows(closed_df, state, config)

            if live_position is not None and state.open_position is None:
                state.open_position = OpenUs500Position(
                    ticket=int(live_position.ticket),
                    symbol=live_position.symbol,
                    direction="LONG" if live_position.type == mt5.POSITION_TYPE_BUY else "SHORT",
                    entry_time_utc=now_utc.isoformat(),
                    entry_price=float(live_position.price_open),
                    lot_size=float(live_position.volume),
                    bars_held=0,
                    stop_loss=float(live_position.sl),
                    take_profit=float(live_position.tp),
                )
            elif live_position is None and state.open_position is not None and any_symbol_position is not None:
                print(
                    f"IGNORING EXTERNAL POSITION ticket={any_symbol_position.ticket} "
                    f"magic={getattr(any_symbol_position, 'magic', 0)} comment={getattr(any_symbol_position, 'comment', '')}"
                )
                state.open_position = None

            for action in actions:
                if action["kind"] == "FORCE_CLOSE":
                    if live_position is None:
                        continue
                    if args.execute:
                        request = build_close_request(live_position, args.magic, f"{args.comment}-time-exit")
                        result = mt5.order_send(request)
                        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                            print(f"CLOSE FAILED retcode={None if result is None else result.retcode} err={mt5.last_error()}")
                        else:
                            print(f"CLOSE SENT ticket={live_position.ticket} reason=TIME")
                    else:
                        print(f"DRY CLOSE ticket={live_position.ticket} reason=TIME")
                elif action["kind"] == "OPEN_SIGNAL":
                    if live_position is not None or state.open_position is not None:
                        continue
                    if any_symbol_position is not None:
                        print(
                            f"SKIP OPEN external_position ticket={any_symbol_position.ticket} "
                            f"magic={getattr(any_symbol_position, 'magic', 0)}"
                        )
                        continue
                    if args.execute:
                        request = build_open_request(
                            args.symbol,
                            config.lot_size,
                            action["direction"],
                            action["stop_loss"],
                            action["take_profit"],
                            args.magic,
                            args.comment,
                        )
                        result = mt5.order_send(request)
                        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                            print(f"OPEN FAILED dir={action['direction']} retcode={None if result is None else result.retcode} err={mt5.last_error()}")
                        else:
                            position = current_symbol_position(args.symbol)
                            if position is not None:
                                state.open_position = OpenUs500Position(
                                    ticket=int(position.ticket),
                                    symbol=position.symbol,
                                    direction=action["direction"],
                                    entry_time_utc=now_utc.isoformat(),
                                    entry_price=float(position.price_open),
                                    lot_size=float(position.volume),
                                    bars_held=0,
                                    stop_loss=action["stop_loss"],
                                    take_profit=action["take_profit"],
                                )
                            print(
                                f"OPEN SENT dir={action['direction']} close={action['close']:.2f} "
                                f"adx={action['adx']:.2f} sl={action['stop_loss']:.2f} tp={action['take_profit']:.2f}"
                            )
                    else:
                        if any_symbol_position is not None:
                            print(
                                f"SKIP DRY OPEN external_position ticket={any_symbol_position.ticket} "
                                f"magic={getattr(any_symbol_position, 'magic', 0)}"
                            )
                            continue
                        print(
                            f"DRY OPEN dir={action['direction']} close={action['close']:.2f} "
                            f"adx={action['adx']:.2f} sl={action['stop_loss']:.2f} tp={action['take_profit']:.2f}"
                        )

            save_state(args.state_file, state)
            if not actions:
                last = closed_df.iloc[-1]
                closed_bar_ts = pd.Timestamp(last["ts"])
                closed_bar_key = closed_bar_ts.isoformat()
                if closed_bar_key != last_logged_hold_ts:
                    print(build_hold_message(closed_bar_ts, float(last["close"]), now_utc))
                    last_logged_hold_ts = closed_bar_key

            iterations += 1
            if args.once or (args.cycles and iterations >= args.cycles):
                break
            time.sleep(args.poll_seconds)
    finally:
        mt5.shutdown()


def main() -> None:
    run_loop(parse_args())


if __name__ == "__main__":
    main()
