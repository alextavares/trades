#!/usr/bin/env python3
"""Backtest agressivo de EMA slope + grid contra o preco para Forex.

Modelo:
- EMA slope define BUY/SELL.
- Primeira ordem abre quando o slope passa do limiar.
- Reentradas sao contra a posicao a cada X pips.
- Cada ordem tem TP individual.
- Se a cesta aberta volta a lucro de X USD, fecha tudo.
- Sem SL; o backtest marca stop-out quando equity <= 0.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import requests


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str = "EURUSD=X"
    ema_period: int = 20
    slope_lookback: int = 3
    atr_period: int = 14
    min_slope_atr: float = 0.15
    grid_spacing_pips: float = 15.0
    take_profit_pips: float = 10.0
    basket_take_profit_usd: float = 2.0
    lot_size: float = 0.01
    max_orders: int = 100
    spread_pips: float = 1.0
    initial_balance: float = 100.0
    pip_value_per_lot_usd: float = 10.0


@dataclass(frozen=True)
class OpenOrder:
    order_id: int
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    lot_size: float


@dataclass(frozen=True)
class ClosedOrder:
    order_id: int
    side: str
    entry_time: pd.Timestamp
    close_time: pd.Timestamp
    entry_price: float
    close_price: float
    lot_size: float
    pnl_pips: float
    pnl_usd: float
    close_reason: str


@dataclass(frozen=True)
class BacktestResult:
    closed_trades: pd.DataFrame
    equity_curve: pd.DataFrame
    final_balance: float
    max_equity: float
    min_equity: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    max_open_orders: int
    open_orders: int
    stop_out: bool


def pip_size_for_symbol(symbol: str) -> float:
    normalized = symbol.upper().replace("=X", "")
    return 0.01 if "JPY" in normalized else 0.0001


def pip_value_usd(config: BacktestConfig) -> float:
    return config.pip_value_per_lot_usd * config.lot_size


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    rename = {column: column.strip().lower() for column in df.columns}
    df = df.rename(columns=rename)
    if "time" in df.columns and "ts" not in df.columns:
        df = df.rename(columns={"time": "ts"})
    required = {"ts", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV sem colunas obrigatorias: {sorted(missing)}")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["ts", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )


def fetch_yahoo_forex(symbol: str, interval: str = "1m", range_: str = "7d", retries: int = 3) -> pd.DataFrame:
    params = {
        "interval": interval,
        "range": range_,
        "includePrePost": "false",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()["chart"]["result"][0]
            timestamps = payload.get("timestamp") or []
            quote = payload["indicators"]["quote"][0]
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
            df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
            if df.empty:
                raise RuntimeError("Yahoo retornou serie vazia")
            return df
        except Exception as exc:  # pragma: no cover - depends on network/provider
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"falha baixando dados do Yahoo: {last_error}")


def add_indicators(df: pd.DataFrame, ema_period: int, slope_lookback: int, atr_period: int) -> pd.DataFrame:
    out = df.copy().sort_values("ts").reset_index(drop=True)
    out["ema"] = out["close"].ewm(span=ema_period, adjust=False).mean()
    out["ema_slope"] = out["ema"] - out["ema"].shift(slope_lookback)

    prev_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.rolling(atr_period).mean()
    out["slope_atr"] = out["ema_slope"] / out["atr"].replace(0, pd.NA)
    return out


def floating_pnl_usd(orders: list[OpenOrder], price: float, config: BacktestConfig) -> float:
    pip_size = pip_size_for_symbol(config.symbol)
    value = pip_value_usd(config)
    total = 0.0
    for order in orders:
        if order.side == "BUY":
            pips = (price - order.entry_price) / pip_size - config.spread_pips
        else:
            pips = (order.entry_price - price) / pip_size - config.spread_pips
        total += pips * value
    return total


def close_order(order: OpenOrder, close_time: pd.Timestamp, close_price: float, reason: str, config: BacktestConfig) -> ClosedOrder:
    pip_size = pip_size_for_symbol(config.symbol)
    if order.side == "BUY":
        pnl_pips = (close_price - order.entry_price) / pip_size - config.spread_pips
    else:
        pnl_pips = (order.entry_price - close_price) / pip_size - config.spread_pips
    pnl_usd = pnl_pips * pip_value_usd(config)
    return ClosedOrder(
        order_id=order.order_id,
        side=order.side,
        entry_time=order.entry_time,
        close_time=close_time,
        entry_price=order.entry_price,
        close_price=close_price,
        lot_size=order.lot_size,
        pnl_pips=pnl_pips,
        pnl_usd=pnl_usd,
        close_reason=reason,
    )


def signal_from_slope(slope_atr: float, config: BacktestConfig) -> str | None:
    if pd.isna(slope_atr):
        return None
    if slope_atr >= config.min_slope_atr:
        return "BUY"
    if slope_atr <= -config.min_slope_atr:
        return "SELL"
    return None


def run_backtest(df: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
    pip_size = pip_size_for_symbol(config.symbol)
    grid_distance = config.grid_spacing_pips * pip_size
    tp_distance = config.take_profit_pips * pip_size

    balance = config.initial_balance
    max_equity = balance
    min_equity = balance
    max_drawdown_usd = 0.0
    orders: list[OpenOrder] = []
    closed: list[ClosedOrder] = []
    equity_rows: list[dict] = []
    next_order_id = 1
    max_open_orders = 0
    stop_out = False

    for row in df.itertuples(index=False):
        ts = pd.Timestamp(row.ts)
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        closed_basket_this_bar = False

        remaining: list[OpenOrder] = []
        for order in orders:
            if order.side == "BUY" and high >= order.entry_price + tp_distance:
                closed_order = close_order(order, ts, order.entry_price + tp_distance, "TP", config)
                closed.append(closed_order)
                balance += closed_order.pnl_usd
            elif order.side == "SELL" and low <= order.entry_price - tp_distance:
                closed_order = close_order(order, ts, order.entry_price - tp_distance, "TP", config)
                closed.append(closed_order)
                balance += closed_order.pnl_usd
            else:
                remaining.append(order)
        orders = remaining

        basket_pnl = floating_pnl_usd(orders, close, config)
        if orders and basket_pnl >= config.basket_take_profit_usd:
            for order in orders:
                closed_order = close_order(order, ts, close, "BASKET", config)
                closed.append(closed_order)
                balance += closed_order.pnl_usd
            orders = []
            closed_basket_this_bar = True

        equity = balance + floating_pnl_usd(orders, close, config)
        max_equity = max(max_equity, equity)
        min_equity = min(min_equity, equity)
        max_drawdown_usd = max(max_drawdown_usd, max_equity - equity)
        equity_rows.append(
            {
                "ts": ts,
                "balance": balance,
                "equity": equity,
                "open_orders": len(orders),
                "floating_pnl_usd": equity - balance,
            }
        )
        if equity <= 0:
            stop_out = True
            break

        if closed_basket_this_bar:
            continue

        signal = signal_from_slope(row.slope_atr, config)
        open_side = orders[0].side if orders else None
        if len(orders) >= config.max_orders:
            continue

        should_open = False
        if not orders and signal is not None:
            should_open = True
            side = signal
        elif orders and open_side == "BUY":
            side = "BUY"
            last_entry = min(order.entry_price for order in orders)
            should_open = close <= last_entry - grid_distance
        elif orders and open_side == "SELL":
            side = "SELL"
            last_entry = max(order.entry_price for order in orders)
            should_open = close >= last_entry + grid_distance
        else:
            side = ""

        if should_open:
            orders.append(
                OpenOrder(
                    order_id=next_order_id,
                    side=side,
                    entry_time=ts,
                    entry_price=close,
                    lot_size=config.lot_size,
                )
            )
            next_order_id += 1
            max_open_orders = max(max_open_orders, len(orders))

    closed_df = pd.DataFrame([asdict(item) for item in closed])
    equity_df = pd.DataFrame(equity_rows)
    max_drawdown_pct = (max_drawdown_usd / max_equity * 100.0) if max_equity > 0 else 0.0
    return BacktestResult(
        closed_trades=closed_df,
        equity_curve=equity_df,
        final_balance=balance,
        max_equity=max_equity,
        min_equity=min_equity,
        max_drawdown_usd=max_drawdown_usd,
        max_drawdown_pct=max_drawdown_pct,
        max_open_orders=max_open_orders,
        open_orders=len(orders),
        stop_out=stop_out,
    )


def summarize(result: BacktestResult, config: BacktestConfig) -> None:
    trades = result.closed_trades
    print("\n=== BACKTEST EMA SLOPE GRID CONTRA ===")
    print(f"Symbol: {config.symbol}")
    print(f"Balance inicial: {config.initial_balance:.2f}")
    print(f"Balance final: {result.final_balance:.2f}")
    print(f"PnL realizado: {result.final_balance - config.initial_balance:.2f}")
    print(f"Min equity: {result.min_equity:.2f}")
    print(f"Max drawdown: {result.max_drawdown_usd:.2f} ({result.max_drawdown_pct:.2f}%)")
    print(f"Max ordens abertas: {result.max_open_orders}")
    print(f"Ordens abertas no final: {result.open_orders}")
    print(f"Stop-out equity<=0: {result.stop_out}")
    if trades.empty:
        print("Nenhuma ordem fechada.")
        return

    wins = int((trades["pnl_usd"] > 0).sum())
    losses = int((trades["pnl_usd"] <= 0).sum())
    print(f"Ordens fechadas: {len(trades)}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win rate: {(wins / len(trades) * 100):.2f}%")
    print(f"PnL medio por ordem: {trades['pnl_usd'].mean():.4f}")
    print("\nPor motivo de fechamento:")
    print(trades.groupby("close_reason").agg(orders=("order_id", "size"), pnl_usd=("pnl_usd", "sum")).to_string())
    print("\nUltimas 10 ordens fechadas:")
    print(
        trades[
            ["side", "entry_time", "close_time", "pnl_pips", "pnl_usd", "close_reason"]
        ].tail(10).to_string(index=False, float_format=lambda value: f"{value:.4f}")
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest Forex EMA slope + grid contra o preco.")
    parser.add_argument("--csv", default="", help="CSV local com ts/time, open, high, low, close, volume.")
    parser.add_argument("--symbol", default="EURUSD=X")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--range", default="7d")
    parser.add_argument("--save-data-csv", default="")
    parser.add_argument("--save-trades-csv", default="")
    parser.add_argument("--save-equity-csv", default="")
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--slope-lookback", type=int, default=3)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--min-slope-atr", type=float, default=0.15)
    parser.add_argument("--grid-spacing-pips", type=float, default=15.0)
    parser.add_argument("--take-profit-pips", type=float, default=10.0)
    parser.add_argument("--basket-take-profit-usd", type=float, default=2.0)
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--max-orders", type=int, default=100)
    parser.add_argument("--spread-pips", type=float, default=1.0)
    parser.add_argument("--initial-balance", type=float, default=100.0)
    parser.add_argument("--pip-value-per-lot-usd", type=float, default=10.0)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = BacktestConfig(
        symbol=args.symbol,
        ema_period=args.ema_period,
        slope_lookback=args.slope_lookback,
        atr_period=args.atr_period,
        min_slope_atr=args.min_slope_atr,
        grid_spacing_pips=args.grid_spacing_pips,
        take_profit_pips=args.take_profit_pips,
        basket_take_profit_usd=args.basket_take_profit_usd,
        lot_size=args.lot_size,
        max_orders=args.max_orders,
        spread_pips=args.spread_pips,
        initial_balance=args.initial_balance,
        pip_value_per_lot_usd=args.pip_value_per_lot_usd,
    )

    if args.csv:
        df = load_csv(args.csv)
    else:
        df = fetch_yahoo_forex(args.symbol, args.interval, args.range)

    if args.save_data_csv:
        df.to_csv(args.save_data_csv, index=False)
        print(f"Dados salvos em: {args.save_data_csv}")

    df = add_indicators(df, args.ema_period, args.slope_lookback, args.atr_period)
    result = run_backtest(df, config)
    summarize(result, config)

    if args.save_trades_csv:
        result.closed_trades.to_csv(args.save_trades_csv, index=False)
        print(f"\nTrades salvos em: {args.save_trades_csv}")
    if args.save_equity_csv:
        result.equity_curve.to_csv(args.save_equity_csv, index=False)
        print(f"Equity salva em: {args.save_equity_csv}")


if __name__ == "__main__":
    main()
