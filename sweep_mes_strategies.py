#!/usr/bin/env python3
"""Varredura de estrategias intraday para MES/ES usando OHLCV historico."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from backtest_mes_ema_scalp import (
    DATABENTO_DATASET,
    IndexFuturesConfig,
    calculate_contract_pnl,
    get_ohlcv,
    load_csv,
    normalize_ohlcv,
)


NY_TZ = "America/New_York"
RTH_OPEN = "09:30"
RTH_CLOSE = "16:00"


@dataclass(frozen=True)
class SweepCosts:
    contracts: int = 1
    point_value_usd: float = 5.0
    tick_size: float = 0.25
    commission_per_side_usd: float = 0.62
    slippage_ticks: float = 1.0


@dataclass(frozen=True)
class StrategyParams:
    strategy: str
    window_minutes: int = 15
    stop_points: float = 6.0
    take_profit_points: float = 10.0
    max_hold_bars: int = 12
    buffer_points: float = 0.0
    min_open_move_points: float = 6.0
    vwap_proximity_points: float = 2.0
    min_vwap_distance_points: float = 1.0


def prepare_rth(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_ohlcv(df).sort_values("ts").reset_index(drop=True)
    out["ts_ny"] = out["ts"].dt.tz_convert(NY_TZ)
    out["date_ny"] = out["ts_ny"].dt.date
    out["time_ny"] = out["ts_ny"].dt.strftime("%H:%M")
    out = out[(out["time_ny"] >= RTH_OPEN) & (out["time_ny"] < RTH_CLOSE)].copy()
    return out.reset_index(drop=True)


def add_session_vwap(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    volume = out["volume"].replace(0, 1.0)
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    out["_pv"] = typical * volume
    out["_volume_safe"] = volume
    out["vwap"] = out.groupby("date_ny")["_pv"].cumsum() / out.groupby("date_ny")["_volume_safe"].cumsum()
    out["vwap_slope"] = out.groupby("date_ny")["vwap"].diff()
    return out.drop(columns=["_pv", "_volume_safe"])


def _cost_config(costs: SweepCosts, params: StrategyParams) -> IndexFuturesConfig:
    return IndexFuturesConfig(
        contracts=costs.contracts,
        point_value_usd=costs.point_value_usd,
        tick_size=costs.tick_size,
        commission_per_side_usd=costs.commission_per_side_usd,
        slippage_ticks=costs.slippage_ticks,
        stop_points=params.stop_points,
        take_profit_points=params.take_profit_points,
        max_hold_bars=params.max_hold_bars,
    )


def exit_trade(
    day: pd.DataFrame,
    entry_pos: int,
    direction: str,
    entry_price: float,
    params: StrategyParams,
) -> tuple[int, float, str]:
    exit_pos = min(entry_pos + params.max_hold_bars - 1, len(day) - 1)
    exit_price = float(day.iloc[exit_pos]["close"])
    exit_reason = "TIME"

    for pos in range(entry_pos, exit_pos + 1):
        row = day.iloc[pos]
        if direction == "LONG":
            stop = entry_price - params.stop_points
            take_profit = entry_price + params.take_profit_points
            if float(row["low"]) <= stop:
                return pos, stop, "STOP"
            if float(row["high"]) >= take_profit:
                return pos, take_profit, "TAKE_PROFIT"
        else:
            stop = entry_price + params.stop_points
            take_profit = entry_price - params.take_profit_points
            if float(row["high"]) >= stop:
                return pos, stop, "STOP"
            if float(row["low"]) <= take_profit:
                return pos, take_profit, "TAKE_PROFIT"

    return exit_pos, exit_price, exit_reason


def make_trade(
    day: pd.DataFrame,
    signal_pos: int,
    direction: str,
    params: StrategyParams,
    costs: SweepCosts,
    extra: dict,
) -> dict | None:
    entry_pos = signal_pos + 1
    if entry_pos >= len(day):
        return None

    entry_row = day.iloc[entry_pos]
    entry_price = float(entry_row["open"])
    exit_pos, exit_price, exit_reason = exit_trade(day, entry_pos, direction, entry_price, params)
    exit_row = day.iloc[exit_pos]
    gross, trade_costs, net = calculate_contract_pnl(direction, entry_price, exit_price, _cost_config(costs, params))
    return {
        "strategy": params.strategy,
        "params": compact_params(params),
        "date_ny": str(entry_row["date_ny"]),
        "direction": direction,
        "entry_time_utc": pd.Timestamp(entry_row["ts"]).isoformat(),
        "exit_time_utc": pd.Timestamp(exit_row["ts"]).isoformat(),
        "entry_price": round(entry_price, 4),
        "exit_price": round(float(exit_price), 4),
        "exit_reason": exit_reason,
        "bars_held": int(exit_pos - entry_pos + 1),
        "gross_pnl_usd": gross,
        "costs_usd": trade_costs,
        "pnl_usd": net,
        "win": net > 0,
        **extra,
    }


def compact_params(params: StrategyParams) -> str:
    if params.strategy == "orb":
        return (
            f"win={params.window_minutes};stop={params.stop_points};tp={params.take_profit_points};"
            f"hold={params.max_hold_bars};buf={params.buffer_points}"
        )
    if params.strategy == "open_reversal":
        return (
            f"win={params.window_minutes};minmove={params.min_open_move_points};stop={params.stop_points};"
            f"tp={params.take_profit_points};hold={params.max_hold_bars}"
        )
    return (
        f"prox={params.vwap_proximity_points};dist={params.min_vwap_distance_points};"
        f"stop={params.stop_points};tp={params.take_profit_points};hold={params.max_hold_bars}"
    )


def opening_window(day: pd.DataFrame, window_minutes: int) -> pd.DataFrame:
    first_ts = pd.Timestamp(day.iloc[0]["ts_ny"])
    end_ts = first_ts + pd.Timedelta(minutes=window_minutes)
    return day[day["ts_ny"] < end_ts]


def backtest_orb(df: pd.DataFrame, params: StrategyParams, costs: SweepCosts) -> list[dict]:
    trades: list[dict] = []
    for _, day in df.groupby("date_ny", sort=True):
        day = day.reset_index(drop=True)
        window = opening_window(day, params.window_minutes)
        if len(window) < 1:
            continue
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())
        for pos in range(len(window), len(day) - 1):
            close = float(day.iloc[pos]["close"])
            direction = None
            if close > range_high + params.buffer_points:
                direction = "LONG"
            elif close < range_low - params.buffer_points:
                direction = "SHORT"
            if direction is None:
                continue
            trade = make_trade(
                day,
                pos,
                direction,
                params,
                costs,
                {"range_high": round(range_high, 4), "range_low": round(range_low, 4)},
            )
            if trade:
                trades.append(trade)
            break
    return trades


def backtest_open_reversal(df: pd.DataFrame, params: StrategyParams, costs: SweepCosts) -> list[dict]:
    trades: list[dict] = []
    for _, day in df.groupby("date_ny", sort=True):
        day = day.reset_index(drop=True)
        window = opening_window(day, params.window_minutes)
        if len(window) < 1:
            continue
        open_price = float(window.iloc[0]["open"])
        window_close = float(window.iloc[-1]["close"])
        move = window_close - open_price
        if abs(move) < params.min_open_move_points:
            continue
        midpoint = (float(window["high"].max()) + float(window["low"].min())) / 2.0
        target_direction = "SHORT" if move > 0 else "LONG"

        for pos in range(len(window), len(day) - 1):
            close = float(day.iloc[pos]["close"])
            if target_direction == "SHORT" and close >= midpoint:
                continue
            if target_direction == "LONG" and close <= midpoint:
                continue
            trade = make_trade(
                day,
                pos,
                target_direction,
                params,
                costs,
                {"opening_move_points": round(move, 4), "opening_midpoint": round(midpoint, 4)},
            )
            if trade:
                trades.append(trade)
            break
    return trades


def backtest_vwap_pullback(df: pd.DataFrame, params: StrategyParams, costs: SweepCosts) -> list[dict]:
    trades: list[dict] = []
    with_vwap = add_session_vwap(df)
    for _, day in with_vwap.groupby("date_ny", sort=True):
        day = day.reset_index(drop=True)
        if len(day) < 4:
            continue
        for pos in range(2, len(day) - 1):
            prev = day.iloc[pos - 1]
            row = day.iloc[pos]
            close = float(row["close"])
            vwap = float(row["vwap"])
            prev_vwap = float(prev["vwap"])
            slope = float(row["vwap_slope"])
            direction = None
            if (
                close >= vwap + params.min_vwap_distance_points
                and slope > 0
                and float(prev["low"]) <= prev_vwap + params.vwap_proximity_points
            ):
                direction = "LONG"
            elif (
                close <= vwap - params.min_vwap_distance_points
                and slope < 0
                and float(prev["high"]) >= prev_vwap - params.vwap_proximity_points
            ):
                direction = "SHORT"
            if direction is None:
                continue
            trade = make_trade(
                day,
                pos,
                direction,
                params,
                costs,
                {"vwap": round(vwap, 4), "vwap_slope": round(slope, 4)},
            )
            if trade:
                trades.append(trade)
            break
    return trades


def strategy_grid() -> list[StrategyParams]:
    rows: list[StrategyParams] = []
    for window in (5, 15, 30):
        for stop in (4.0, 6.0, 8.0, 10.0):
            for tp in (6.0, 8.0, 12.0, 16.0):
                for hold in (6, 12, 24):
                    for buffer in (0.0, 1.0):
                        rows.append(
                            StrategyParams(
                                strategy="orb",
                                window_minutes=window,
                                stop_points=stop,
                                take_profit_points=tp,
                                max_hold_bars=hold,
                                buffer_points=buffer,
                            )
                        )
                    for min_move in (4.0, 8.0, 12.0):
                        rows.append(
                            StrategyParams(
                                strategy="open_reversal",
                                window_minutes=window,
                                stop_points=stop,
                                take_profit_points=tp,
                                max_hold_bars=hold,
                                min_open_move_points=min_move,
                            )
                        )
    for proximity in (1.0, 2.0, 3.0):
        for distance in (0.5, 1.0, 2.0):
            for stop in (4.0, 6.0, 8.0, 10.0):
                for tp in (6.0, 8.0, 12.0, 16.0):
                    for hold in (6, 12, 24):
                        rows.append(
                            StrategyParams(
                                strategy="vwap_pullback",
                                stop_points=stop,
                                take_profit_points=tp,
                                max_hold_bars=hold,
                                vwap_proximity_points=proximity,
                                min_vwap_distance_points=distance,
                            )
                        )
    return rows


def run_strategy(df: pd.DataFrame, params: StrategyParams, costs: SweepCosts) -> list[dict]:
    if params.strategy == "orb":
        return backtest_orb(df, params, costs)
    if params.strategy == "open_reversal":
        return backtest_open_reversal(df, params, costs)
    if params.strategy == "vwap_pullback":
        return backtest_vwap_pullback(df, params, costs)
    raise ValueError(f"Estrategia nao suportada: {params.strategy}")


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for (strategy, params), group in trades.groupby(["strategy", "params"], sort=False):
        pnl = group["pnl_usd"].astype(float)
        wins = group[group["pnl_usd"] > 0]
        losses = group[group["pnl_usd"] <= 0]
        equity = pnl.cumsum()
        drawdown = equity - equity.cummax()
        gross_win = float(wins["pnl_usd"].sum())
        gross_loss = abs(float(losses["pnl_usd"].sum()))
        max_drawdown = float(drawdown.min())
        rows.append(
            {
                "strategy": strategy,
                "params": params,
                "trades": int(len(group)),
                "wins": int(len(wins)),
                "losses": int(len(losses)),
                "win_rate": round(len(wins) / len(group) * 100.0, 2),
                "net_pnl": round(float(pnl.sum()), 4),
                "avg_trade": round(float(pnl.mean()), 4),
                "max_drawdown": round(max_drawdown, 4),
                "pnl_to_dd": round(float(pnl.sum()) / abs(max_drawdown), 4) if max_drawdown < 0 else 999.0,
                "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else 999.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["net_pnl", "profit_factor"], ascending=[False, False]).reset_index(drop=True)


def run_sweep(df: pd.DataFrame, costs: SweepCosts, min_trades: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    prepared = prepare_rth(df)
    trades: list[dict] = []
    for params in strategy_grid():
        trades.extend(run_strategy(prepared, params, costs))
    trades_df = pd.DataFrame(trades)
    summary = summarize_trades(trades_df)
    if not summary.empty:
        summary = summary[summary["trades"] >= min_trades].reset_index(drop=True)
    return summary, trades_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Varre estrategias MES/ES com dados historicos.")
    parser.add_argument("--symbol", default="MES=F")
    parser.add_argument("--period", default="30d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--csv", default="")
    parser.add_argument("--data-source", choices=["auto", "yahoo", "databento"], default="databento")
    parser.add_argument("--databento-dataset", default=DATABENTO_DATASET)
    parser.add_argument("--databento-symbol", default="")
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--point-value-usd", type=float, default=5.0)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--commission-per-side-usd", type=float, default=0.62)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--save-summary", default="mes_strategy_sweep_summary.csv")
    parser.add_argument("--save-trades", default="mes_strategy_sweep_trades.csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df = (
        load_csv(args.csv)
        if args.csv
        else get_ohlcv(
            args.data_source,
            args.symbol,
            args.period,
            args.interval,
            databento_dataset=args.databento_dataset,
            databento_symbol=args.databento_symbol,
        )
    )
    costs = SweepCosts(
        contracts=args.contracts,
        point_value_usd=args.point_value_usd,
        tick_size=args.tick_size,
        commission_per_side_usd=args.commission_per_side_usd,
        slippage_ticks=args.slippage_ticks,
    )
    summary, trades = run_sweep(df, costs, min_trades=args.min_trades)
    if args.save_summary:
        summary.to_csv(args.save_summary, index=False)
    if args.save_trades:
        trades.to_csv(args.save_trades, index=False)

    print("=== MES/ES STRATEGY SWEEP ===")
    print(f"Fonte: {'csv' if args.csv else args.data_source} | Symbol: {args.symbol} | Linhas: {len(df)}")
    print(f"Custos: {asdict(costs)}")
    print(f"Configs com min_trades: {len(summary)} | Trades simulados: {len(trades)}")
    if summary.empty:
        print("Nenhuma configuracao passou o filtro minimo de trades.")
        return
    print(summary.head(args.top).to_string(index=False))
    print(f"Resumo salvo: {Path(args.save_summary).resolve()}")
    print(f"Trades salvos: {Path(args.save_trades).resolve()}")


if __name__ == "__main__":
    main()
