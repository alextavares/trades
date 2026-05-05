#!/usr/bin/env python3
"""Paper trader em tempo real para BTC Up/Down 5m na Polymarket.

O script nunca envia ordens. Ele observa o mercado atual, calcula o mesmo edge
do backtest, abre uma posicao simulada quando os filtros passam e grava o
resultado quando a janela de 5 minutos fecha.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from backtest_polymarket_5m_edge import (
    BINANCE_KLINES_URL,
    TradeDecision,
    binary_trade_pnl,
    decide_trade,
    decode_json_list,
    estimate_up_probability,
    fetch_polymarket_event,
)

try:
    from py_clob_client_v2 import ApiCreds, ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, Side

    BUY = Side.BUY
except ImportError:  # pragma: no cover - exercised by environment, not unit tests
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY
    except ImportError:
        ClobClient = None
        ApiCreds = None
        OrderArgs = None
        OrderType = None
        PartialCreateOrderOptions = None
        BUY = "BUY"

try:
    from polynode.trading import ExchangeVersion as PolyNodeExchangeVersion
    from polynode.trading import OrderParams as PolyNodeOrderParams
    from polynode.trading import PolyNodeTrader
    from polynode.trading import SignatureType as PolyNodeSignatureType
    from polynode.trading import TraderConfig as PolyNodeTraderConfig
    from polynode.trading import normalize_signer as polynode_normalize_signer
except ImportError:  # pragma: no cover - exercised by environment, not unit tests
    PolyNodeTrader = None
    PolyNodeOrderParams = None
    PolyNodeSignatureType = None
    PolyNodeTraderConfig = None
    PolyNodeExchangeVersion = None
    polynode_normalize_signer = None


BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
POLYMARKET_PRICE_URL = "https://clob.polymarket.com/price"
GAMMA_EVENT_SLUG_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
BRT = timezone(timedelta(hours=-3))


@dataclass(frozen=True)
class LiveConfig:
    symbol: str = "BTCUSDT"
    event_slug_asset: str = "btc"
    poll_seconds: int = 10
    strategy: str = "edge"
    entry_offsets: tuple[int, ...] = (1,)
    edge_min: float = 0.06
    min_contract_price: float = 0.50
    max_contract_price: float = 0.85
    min_abs_z: float = 1.0
    min_anchor_body_pct: float = 0.0003
    anchor_volume_multiplier: float = 0.0
    target_touch_tolerance_pct: float = 0.0002
    min_rejection_wick_ratio: float = 0.35
    late_entry_min_remaining: int = 45
    late_entry_max_remaining: int = 75
    contrarian_favorite_min_price: float = 0.70
    rsi_threshold_sell: float = 70.0
    lookback_minutes: int = 30
    momentum_minutes: int = 2
    momentum_weight: float = 0.10
    recent_move_filter_seconds: int = 0
    max_recent_move_pct: float = 0.0
    ema_fast_seconds: int = 60
    ema_mid_seconds: int = 300
    ema_slow_seconds: int = 600
    ema_slope_lookback_seconds: int = 15
    min_ema_gap_usd: float = 2.0
    min_ema_slope_usd: float = 0.2
    max_price_ema_fast_distance_usd: float = 80.0
    stake_usdc: float = 10.0
    paper_limit_entry_price: float = 0.0
    paper_limit_entry_min_seconds_remaining: int = 20
    settle_delay_seconds: int = 10
    trades_csv: str = "paper_polymarket_5m_trades.csv"
    event_duration_minutes: int = 5
    event_slug_duration: str = "5m"
    real_mode: bool = False
    env_file: str = ""
    real_confirmed: bool = False
    real_order_type: str = "FOK"
    real_price_slippage: float = 0.0
    real_signature_type: int = 1
    max_real_trades: int = 1
    max_open_positions: int = 1
    max_real_loss_usdc: float = 0.0
    real_shared_lock_file: str = ""
    real_shared_lock_scope: str = "market"
    allowed_directions: tuple[str, ...] = ("UP", "DOWN")
    excluded_entry_hours_brt: tuple[int, ...] = ()


@dataclass(frozen=True)
class LiveMarket:
    slug: str
    event_start_ts: int
    event_end_ts: int
    up_token_id: str
    down_token_id: str
    accepting_orders: bool
    tick_size: str = "0.001"
    neg_risk: bool = False
    order_min_size: float = 5.0


@dataclass(frozen=True)
class PaperPosition:
    market_slug: str
    event_start_ts: int
    event_end_ts: int
    direction: str
    token_id: str
    entry_ts: int
    entry_btc_price: float
    target_price: float
    contract_price: float
    model_probability: float
    edge: float
    stake_usdc: float
    status: str = "OPEN"
    final_btc_price: float | None = None
    win: bool | None = None
    pnl_usdc: float = 0.0
    closed_ts: int | None = None
    execution_mode: str = "PAPER"
    shares: float = 0.0
    order_id: str = ""
    order_status: str = ""
    order_response: str = ""
    signal_contract_price: float = 0.0
    limit_entry_price: float = 0.0


class LegacyRealOrderClient:
    def __init__(self, client) -> None:
        self._client = client

    def post_limit_buy(self, token_id: str, limit_price: float, shares: float, market: LiveMarket, order_type_name: str) -> object:
        if OrderArgs is None or OrderType is None or PartialCreateOrderOptions is None:
            raise RuntimeError("py-clob-client nao esta disponivel")
        order_type = getattr(OrderType, order_type_name.upper(), OrderType.FOK)
        order_args = OrderArgs(
            token_id=token_id,
            price=limit_price,
            size=shares,
            side=BUY,
        )
        options = PartialCreateOrderOptions(tick_size=market.tick_size, neg_risk=market.neg_risk)
        signed_order = self._client.create_order(order_args, options=options)
        return self._client.post_order(signed_order, order_type)


class PolyNodeRealOrderClient:
    def __init__(self, trader) -> None:
        self._trader = trader

    def post_limit_buy(self, token_id: str, limit_price: float, shares: float, market: LiveMarket, order_type_name: str) -> object:
        if PolyNodeOrderParams is None:
            raise RuntimeError("polynode nao esta disponivel")

        async def _submit():
            result = await self._trader.order(
                PolyNodeOrderParams(
                    token_id=token_id,
                    side="BUY",
                    price=limit_price,
                    size=shares,
                    type=order_type_name.upper(),
                )
            )
            return {
                "success": result.success,
                "orderID": result.order_id or "",
                "status": result.status or "",
                "errorMsg": result.error or "",
                "makingAmount": result.making_amount or "",
                "takingAmount": result.taking_amount or "",
            }

        return asyncio.run(_submit())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_5m_event_start(now: datetime | None = None) -> int:
    return current_event_start(now, duration_seconds=300)


def current_event_start(now: datetime | None = None, duration_seconds: int = 300) -> int:
    if now is None:
        now = utc_now()
    ts = int(now.timestamp())
    return ts - (ts % duration_seconds)


def event_duration_seconds(config: LiveConfig) -> int:
    return max(int(config.event_duration_minutes), 1) * 60


def config_event_start(config: LiveConfig, now: datetime | None = None) -> int:
    return current_event_start(now, event_duration_seconds(config))


def seconds_into_event(now: datetime | None = None) -> int:
    if now is None:
        now = utc_now()
    return int(now.timestamp()) - current_5m_event_start(now)


def is_entry_offset_allowed(seconds_elapsed: int, config: LiveConfig) -> bool:
    """Offsets are one-based minutes: offset 1 means 0-59 seconds elapsed."""
    if seconds_elapsed < 0 or seconds_elapsed >= event_duration_seconds(config):
        return False
    current_offset = (seconds_elapsed // 60) + 1
    return current_offset in set(config.entry_offsets)


def fetch_binance_price(symbol: str) -> float:
    response = requests.get(BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=10)
    response.raise_for_status()
    return float(response.json()["price"])


def fetch_recent_binance_1m(symbol: str, limit: int) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": "1m", "limit": min(max(limit, 5), 1000)}
    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(
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
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df["log_return"] = np.log(df["close"]).diff()
    df["volume_sma_20"] = df["volume"].rolling(20).mean()
    return df


def fetch_recent_binance_1s(symbol: str, limit: int) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": "1s", "limit": min(max(limit, 10), 1000)}
    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(
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
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def fetch_recent_binance_5m(symbol: str, limit: int) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": "5m", "limit": min(max(limit, 20), 1000)}
    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(
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
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = df[column].astype(float)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df["cor"] = np.where(df["close"] > df["open"], 1, np.where(df["close"] < df["open"], -1, 0))
    return df


def target_price_for_event(df: pd.DataFrame, event_start_ts: int) -> float:
    event_start = pd.to_datetime(event_start_ts, unit="s", utc=True)
    previous = df[df["close_time"] < event_start]
    if not previous.empty:
        return float(previous.iloc[-1]["close"])

    same_minute = df[df["ts"] <= event_start]
    if not same_minute.empty:
        return float(same_minute.iloc[-1]["open"])

    return float(df.iloc[-1]["close"])


def bollinger_rsi_reversal_down_signal(df: pd.DataFrame, rsi_threshold_sell: float) -> bool:
    if len(df) < 20:
        return False

    candles = df.copy()
    candles["sma"] = candles["close"].rolling(20).mean()
    candles["std"] = candles["close"].rolling(20).std()
    candles["upper"] = candles["sma"] + (candles["std"] * 2)

    delta = candles["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    candles["rsi"] = 100 - (100 / (1 + rs))

    ultimo = candles.iloc[-2]
    penultimo = candles.iloc[-3]

    is_overextended = bool(
        penultimo["high"] >= penultimo["upper"] and penultimo["rsi"] > rsi_threshold_sell
    )
    is_reversal = bool(penultimo["cor"] == 1 and ultimo["cor"] == -1)
    return is_overextended and is_reversal


def rsi_reversal_down_signal(df: pd.DataFrame, rsi_threshold_sell: float) -> bool:
    if len(df) < 3:
        return False

    candles = df.copy()
    delta = candles["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    candles["rsi"] = 100 - (100 / (1 + rs))

    ultimo = candles.iloc[-2]
    penultimo = candles.iloc[-3]
    is_overextended = bool(penultimo["rsi"] > rsi_threshold_sell)
    is_reversal = bool(penultimo["cor"] == 1 and ultimo["cor"] == -1)
    return is_overextended and is_reversal


def estimate_live_probability(
    df: pd.DataFrame,
    current_price: float,
    target_price: float,
    seconds_remaining: int,
    config: LiveConfig,
) -> tuple[float, float, float]:
    returns = df["log_return"].dropna().tail(config.lookback_minutes)
    std_1m = float(returns.std(ddof=0)) if not returns.empty else 0.0
    sigma_remaining = current_price * std_1m * math.sqrt(max(seconds_remaining, 1) / 60.0)

    closes = df["close"].tail(config.momentum_minutes + 1)
    momentum_score = 0.0
    if len(closes) >= 2 and std_1m > 0:
        recent_return = math.log(float(closes.iloc[-1]) / float(closes.iloc[0]))
        denom = std_1m * math.sqrt(max(len(closes) - 1, 1))
        momentum_score = recent_return / denom if denom > 0 else 0.0

    prob_up = estimate_up_probability(
        current_price=current_price,
        target_price=target_price,
        sigma_remaining=sigma_remaining,
        momentum_score=momentum_score,
        momentum_weight=config.momentum_weight,
    )
    z_score = 0.0 if sigma_remaining <= 0 else (current_price - target_price) / sigma_remaining
    return prob_up, z_score, sigma_remaining


def recent_abs_move_pct(df: pd.DataFrame, current_price: float, seconds: int) -> float:
    if seconds <= 0 or current_price <= 0:
        return 0.0

    periods_back = max(math.ceil(seconds / 60.0), 1)
    reference_index = -(periods_back + 1)
    if len(df) < abs(reference_index):
        return 0.0

    reference_price = float(df["close"].iloc[reference_index])
    if reference_price <= 0:
        return 0.0
    return abs(current_price / reference_price - 1.0)


def fetch_polymarket_event_for_config(event_start_ts: int, config: LiveConfig) -> dict | None:
    asset = config.event_slug_asset.lower().strip()
    if asset == "btc" and config.event_slug_duration == "5m":
        return fetch_polymarket_event(event_start_ts)
    slug = f"{asset}-updown-{config.event_slug_duration}-{event_start_ts}"
    response = requests.get(GAMMA_EVENT_SLUG_URL.format(slug=slug), timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def fetch_live_market(event_start_ts: int, config: LiveConfig | None = None) -> LiveMarket | None:
    if config is None:
        config = LiveConfig()
    event = fetch_polymarket_event_for_config(event_start_ts, config)
    if not event:
        return None

    markets = event.get("markets") or []
    if not markets:
        return None

    market = markets[0]
    outcomes = decode_json_list(market.get("outcomes"))
    token_ids = decode_json_list(market.get("clobTokenIds"))
    if len(outcomes) < 2 or len(token_ids) < 2:
        return None

    outcome_to_token = {str(outcome).lower(): str(token_id) for outcome, token_id in zip(outcomes, token_ids)}
    up_token_id = outcome_to_token.get("up")
    down_token_id = outcome_to_token.get("down")
    if not up_token_id or not down_token_id:
        return None

    return LiveMarket(
        slug=str(
            event.get("slug")
            or market.get("slug")
            or f"{config.event_slug_asset.lower().strip()}-updown-{config.event_slug_duration}-{event_start_ts}"
        ),
        event_start_ts=event_start_ts,
        event_end_ts=event_start_ts + event_duration_seconds(config),
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        accepting_orders=bool(market.get("acceptingOrders", False)),
        tick_size=str(market.get("orderPriceMinTickSize") or "0.001"),
        neg_risk=bool(market.get("negRisk", False)),
        order_min_size=float(market.get("orderMinSize") or 5.0),
    )


def fetch_buy_price(token_id: str) -> float:
    response = requests.get(POLYMARKET_PRICE_URL, params={"token_id": token_id, "side": "BUY"}, timeout=10)
    response.raise_for_status()
    return float(response.json()["price"])


def first_minute_continuation_direction(
    anchor_open: float,
    anchor_close: float,
    target_price: float,
    anchor_volume: float,
    volume_sma: float,
    config: LiveConfig,
) -> str | None:
    body_pct = abs(anchor_close - anchor_open) / max(anchor_open, 1e-9)
    if body_pct < config.min_anchor_body_pct:
        return None

    if config.anchor_volume_multiplier > 0 and volume_sma > 0:
        if anchor_volume < volume_sma * config.anchor_volume_multiplier:
            return None

    if anchor_close > anchor_open and anchor_close > target_price:
        direction = "UP"
        return direction if direction in config.allowed_directions else None
    if anchor_close < anchor_open and anchor_close < target_price:
        direction = "DOWN"
        return direction if direction in config.allowed_directions else None
    return None


def first_minute_anchor_direction(df: pd.DataFrame, event_start_ts: int, config: LiveConfig) -> str | None:
    event_start = pd.to_datetime(event_start_ts, unit="s", utc=True)
    anchor_rows = df[df["ts"] == event_start]
    if anchor_rows.empty:
        return None

    anchor = anchor_rows.iloc[-1]
    volume_sma = float(anchor.get("volume_sma_20", 0.0) or 0.0)
    if not math.isfinite(volume_sma):
        volume_sma = 0.0

    target_price = target_price_for_event(df, event_start_ts)
    return first_minute_continuation_direction(
        anchor_open=float(anchor["open"]),
        anchor_close=float(anchor["close"]),
        target_price=target_price,
        anchor_volume=float(anchor["volume"]),
        volume_sma=volume_sma,
        config=config,
    )


def target_rejection_direction(
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    target_price: float,
    candle_volume: float,
    volume_sma: float,
    config: LiveConfig,
) -> str | None:
    candle_range = candle_high - candle_low
    if candle_range <= 0:
        return None

    if config.anchor_volume_multiplier > 0 and volume_sma > 0:
        if candle_volume < volume_sma * config.anchor_volume_multiplier:
            return None

    tolerance = target_price * config.target_touch_tolerance_pct
    lower_wick = min(candle_open, candle_close) - candle_low
    upper_wick = candle_high - max(candle_open, candle_close)
    lower_wick_ratio = lower_wick / candle_range
    upper_wick_ratio = upper_wick / candle_range

    if (
        candle_low <= target_price + tolerance
        and candle_close > target_price
        and candle_close > candle_open
        and lower_wick_ratio >= config.min_rejection_wick_ratio
    ):
        return "UP"

    if (
        candle_high >= target_price - tolerance
        and candle_close < target_price
        and candle_close < candle_open
        and upper_wick_ratio >= config.min_rejection_wick_ratio
    ):
        return "DOWN"

    return None


def target_rejection_latest_direction(
    df: pd.DataFrame,
    event_start_ts: int,
    now: datetime,
    config: LiveConfig,
) -> str | None:
    event_start = pd.to_datetime(event_start_ts, unit="s", utc=True)
    now_ts = pd.Timestamp(now)
    closed = df[(df["ts"] >= event_start) & (df["close_time"] < now_ts)]
    if closed.empty:
        return None

    candle = closed.iloc[-1]
    volume_sma = float(candle.get("volume_sma_20", 0.0) or 0.0)
    if not math.isfinite(volume_sma):
        volume_sma = 0.0

    target_price = target_price_for_event(df, event_start_ts)
    return target_rejection_direction(
        candle_open=float(candle["open"]),
        candle_high=float(candle["high"]),
        candle_low=float(candle["low"]),
        candle_close=float(candle["close"]),
        target_price=target_price,
        candle_volume=float(candle["volume"]),
        volume_sma=volume_sma,
        config=config,
    )


def momentum_confirmed_direction(
    df: pd.DataFrame,
    event_start_ts: int,
    now: datetime,
    target_price: float,
    config: LiveConfig,
) -> str | None:
    event_start = pd.to_datetime(event_start_ts, unit="s", utc=True)
    now_ts = pd.Timestamp(now)
    closed = df[df["close_time"] < now_ts].copy()
    if closed.empty:
        return None

    closed["ema_9"] = closed["close"].ewm(span=9, adjust=False).mean()
    closed["ema_21"] = closed["close"].ewm(span=21, adjust=False).mean()
    event_closed = closed[closed["ts"] >= event_start]
    if event_closed.empty:
        return None

    candle = event_closed.iloc[-1]
    candle_open = float(candle["open"])
    candle_high = float(candle["high"])
    candle_low = float(candle["low"])
    candle_close = float(candle["close"])
    candle_range = candle_high - candle_low
    if candle_range <= 0:
        return None

    body_pct = abs(candle_close - candle_open) / max(candle_open, 1e-9)
    if body_pct < config.min_anchor_body_pct:
        return None

    volume_sma = float(candle.get("volume_sma_20", 0.0) or 0.0)
    if not math.isfinite(volume_sma):
        volume_sma = 0.0
    if config.anchor_volume_multiplier > 0 and volume_sma > 0:
        if float(candle["volume"]) < volume_sma * config.anchor_volume_multiplier:
            return None

    ema_9 = float(candle["ema_9"])
    ema_21 = float(candle["ema_21"])
    upper_wick_ratio = (candle_high - max(candle_open, candle_close)) / candle_range
    lower_wick_ratio = (min(candle_open, candle_close) - candle_low) / candle_range

    recent_closes = event_closed["close"].tail(3).to_list()
    rising = len(recent_closes) < 2 or all(a <= b for a, b in zip(recent_closes, recent_closes[1:]))
    falling = len(recent_closes) < 2 or all(a >= b for a, b in zip(recent_closes, recent_closes[1:]))

    if (
        candle_close > target_price
        and candle_close > candle_open
        and ema_9 > ema_21
        and rising
        and upper_wick_ratio <= 0.50
    ):
        return "UP"

    if (
        candle_close < target_price
        and candle_close < candle_open
        and ema_9 < ema_21
        and falling
        and lower_wick_ratio <= 0.50
    ):
        return "DOWN"

    return None


def ema_1s_trend_direction(df_1s: pd.DataFrame, current_price: float, config: LiveConfig) -> str | None:
    required = max(
        config.ema_fast_seconds,
        config.ema_mid_seconds,
        config.ema_slow_seconds,
    ) + config.ema_slope_lookback_seconds
    if len(df_1s) < required:
        return None

    candles = df_1s.copy()
    candles["ema_fast"] = candles["close"].ewm(span=config.ema_fast_seconds, adjust=False).mean()
    candles["ema_mid"] = candles["close"].ewm(span=config.ema_mid_seconds, adjust=False).mean()
    candles["ema_slow"] = candles["close"].ewm(span=config.ema_slow_seconds, adjust=False).mean()

    latest = candles.iloc[-1]
    previous = candles.iloc[-1 - config.ema_slope_lookback_seconds]
    ema_fast = float(latest["ema_fast"])
    ema_mid = float(latest["ema_mid"])
    ema_slow = float(latest["ema_slow"])
    fast_slope = ema_fast - float(previous["ema_fast"])
    mid_slope = ema_mid - float(previous["ema_mid"])

    price_distance = abs(current_price - ema_fast)
    if price_distance > config.max_price_ema_fast_distance_usd:
        return None

    if (
        current_price > ema_fast
        and ema_fast > ema_mid > ema_slow
        and (ema_fast - ema_mid) >= config.min_ema_gap_usd
        and fast_slope >= config.min_ema_slope_usd
        and mid_slope >= -config.min_ema_slope_usd
    ):
        return "UP"

    if (
        current_price < ema_fast
        and ema_fast < ema_mid < ema_slow
        and (ema_mid - ema_fast) >= config.min_ema_gap_usd
        and fast_slope <= -config.min_ema_slope_usd
        and mid_slope <= config.min_ema_slope_usd
    ):
        return "DOWN"

    return None


def late_window_direction(
    current_price: float,
    target_price: float,
    seconds_remaining: int,
    z_score: float,
    config: LiveConfig,
) -> str | None:
    if seconds_remaining < config.late_entry_min_remaining:
        return None
    if seconds_remaining > config.late_entry_max_remaining:
        return None

    if current_price > target_price and z_score >= config.min_abs_z:
        return "UP"
    if current_price < target_price and z_score <= -config.min_abs_z:
        return "DOWN"
    return None


def decide_mispricing_contrarian(
    prob_up: float,
    ask_up: float,
    ask_down: float,
    config: LiveConfig,
) -> TradeDecision:
    prob_down = 1.0 - prob_up
    candidates: list[TradeDecision] = []

    if (
        ask_down >= config.contrarian_favorite_min_price
        and config.min_contract_price <= ask_up <= config.max_contract_price
    ):
        edge_up = round(prob_up - ask_up, 10)
        if edge_up >= config.edge_min:
            candidates.append(
                TradeDecision(
                    direction="UP",
                    probability=prob_up,
                    contract_price=ask_up,
                    edge=edge_up,
                )
            )

    if (
        ask_up >= config.contrarian_favorite_min_price
        and config.min_contract_price <= ask_down <= config.max_contract_price
    ):
        edge_down = round(prob_down - ask_down, 10)
        if edge_down >= config.edge_min:
            candidates.append(
                TradeDecision(
                    direction="DOWN",
                    probability=prob_down,
                    contract_price=ask_down,
                    edge=edge_down,
                )
            )

    if not candidates:
        return TradeDecision(direction="HOLD")
    return max(candidates, key=lambda item: item.edge)


def parse_allowed_directions(raw: str) -> tuple[str, ...]:
    items = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    allowed = tuple(item for item in items if item in {"UP", "DOWN"})
    return allowed or ("UP", "DOWN")


def parse_excluded_entry_hours(raw: str) -> tuple[int, ...]:
    hours: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            hour = int(value)
        except ValueError as exc:
            raise ValueError("excluded entry hours must be integer values") from exc
        if hour < 0 or hour > 23:
            raise ValueError("excluded entry hours must be in 0..23")
        if hour not in hours:
            hours.append(hour)
    return tuple(hours)


def should_skip_entry_hour(now: datetime, config: LiveConfig) -> bool:
    if not config.excluded_entry_hours_brt:
        return False
    return now.astimezone(BRT).hour in config.excluded_entry_hours_brt


def calculate_order_shares(stake_usdc: float, price: float) -> float:
    if stake_usdc <= 0:
        raise ValueError("stake_usdc must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    stake_decimal = Decimal(str(stake_usdc))
    price_decimal = Decimal(str(price))
    share_step = Decimal("0.0001")
    max_share_units = int((stake_decimal / price_decimal / share_step).to_integral_value(rounding=ROUND_DOWN))

    for share_units in range(max_share_units, 0, -1):
        shares_decimal = Decimal(share_units) * share_step
        maker_amount = shares_decimal * price_decimal
        if maker_amount == maker_amount.quantize(Decimal("0.01")):
            return float(shares_decimal)

    raise ValueError("could not fit order shares to Polymarket precision constraints")


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} nao configurado no .env")
    return value


def load_credentials_env(config: LiveConfig) -> None:
    if config.env_file:
        env_path = Path(config.env_file)
        if not env_path.exists():
            raise RuntimeError(f"env file nao encontrado: {env_path}")
        load_dotenv(dotenv_path=env_path, override=True)
        return

    load_dotenv()


def build_py_clob_real_client(config: LiveConfig):
    if ClobClient is None or ApiCreds is None:
        raise RuntimeError("py-clob-client nao instalado. Rode: python -m pip install py-clob-client")

    creds = ApiCreds(
        api_key=required_env("API_KEY"),
        api_secret=required_env("API_SECRET"),
        api_passphrase=required_env("API_PASSPHRASE"),
    )
    kwargs = {
        "host": "https://clob.polymarket.com",
        "chain_id": 137,
        "key": required_env("PK"),
        "creds": creds,
    }
    funder = os.getenv("FUNDER")
    if config.real_signature_type != 0:
        kwargs["signature_type"] = config.real_signature_type
        if funder:
            kwargs["funder"] = funder
    return LegacyRealOrderClient(ClobClient(**kwargs))


def build_polynode_real_client(config: LiveConfig):
    if (
        PolyNodeTrader is None
        or PolyNodeOrderParams is None
        or PolyNodeSignatureType is None
        or PolyNodeTraderConfig is None
        or PolyNodeExchangeVersion is None
        or polynode_normalize_signer is None
    ):
        raise RuntimeError("polynode nao instalado. Rode: python -m pip install polynode==0.10.3")

    pk = required_env("PK")
    env_funder = os.getenv("FUNDER", "").strip()
    wallet_signer = asyncio.run(polynode_normalize_signer(pk, PolyNodeSignatureType.POLY_1271))

    trader = PolyNodeTrader(
        PolyNodeTraderConfig(
            default_signature_type=PolyNodeSignatureType.POLY_1271,
            exchange_version=PolyNodeExchangeVersion.V2,
            db_path=str(Path(config.trades_csv).with_suffix(".polynode.db")),
        )
    )
    trader.unlink_wallet(wallet_signer.address)
    link_result = asyncio.run(trader.link_wallet(pk, type=PolyNodeSignatureType.POLY_1271))
    if env_funder and link_result.funder_address.lower() != env_funder.lower():
        print(
            f"AVISO POLYNODE: FUNDER do .env ({env_funder}) difere do funder derivado ({link_result.funder_address}). "
            "Usando o funder derivado."
        )
    return PolyNodeRealOrderClient(trader)


def build_clob_client(config: LiveConfig):
    load_credentials_env(config)
    if config.real_signature_type == 3:
        return build_polynode_real_client(config)
    return build_py_clob_real_client(config)


def compact_order_response(response: object) -> str:
    try:
        return json.dumps(response, ensure_ascii=True, separators=(",", ":"))[:1000]
    except TypeError:
        return str(response)[:1000]


def extract_order_id(response: object) -> str:
    if isinstance(response, dict):
        return str(response.get("orderID") or response.get("orderId") or response.get("id") or "")
    return ""


def order_was_accepted(response: object) -> bool:
    if not isinstance(response, dict):
        return True
    if response.get("success") is False:
        return False
    status = str(response.get("status") or response.get("errorMsg") or "").lower()
    if "error" in status or "rejected" in status or "failed" in status:
        return False
    return True


def real_order_limit_price(signal_price: float, config: LiveConfig) -> float:
    limit_price = min(signal_price + max(config.real_price_slippage, 0.0), config.max_contract_price)
    return round(limit_price, 3)


def real_shared_lock_key(position: PaperPosition, config: LiveConfig) -> str:
    if config.real_shared_lock_scope == "outcome":
        return position.token_id
    return position.market_slug


def load_real_shared_lock_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def write_real_shared_lock_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
    tmp_path.write_text(json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


class RealSharedFileLock:
    def __init__(self, state_path: Path, timeout_seconds: float = 5.0):
        self.lock_path = state_path.with_suffix(state_path.suffix + ".lock")
        self.timeout_seconds = timeout_seconds
        self.fd: int | None = None

    def __enter__(self) -> "RealSharedFileLock":
        start = time.monotonic()
        while True:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, str(os.getpid()).encode("ascii", errors="ignore"))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > 30:
                        self.lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() - start > self.timeout_seconds:
                    raise RuntimeError(f"timeout aguardando lock compartilhado: {self.lock_path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass


def prune_real_shared_lock_state(
    state: dict[str, dict[str, object]],
    now_ts: int,
    settle_delay_seconds: int,
) -> dict[str, dict[str, object]]:
    expiry_grace = settle_delay_seconds + 60
    pruned: dict[str, dict[str, object]] = {}
    for key, item in state.items():
        try:
            event_end_ts = int(item.get("event_end_ts", 0))
        except (TypeError, ValueError):
            continue
        if event_end_ts + expiry_grace >= now_ts:
            pruned[key] = item
    return pruned


def reserve_real_shared_position(
    position: PaperPosition,
    config: LiveConfig,
    now_ts: int | None = None,
) -> tuple[bool, str, dict[str, object] | None]:
    if not config.real_shared_lock_file:
        return True, "", None

    now_ts = int(time.time()) if now_ts is None else now_ts
    state_path = Path(config.real_shared_lock_file)
    key = real_shared_lock_key(position, config)
    with RealSharedFileLock(state_path):
        state = prune_real_shared_lock_state(
            load_real_shared_lock_state(state_path),
            now_ts=now_ts,
            settle_delay_seconds=config.settle_delay_seconds,
        )
        existing = state.get(key)
        if existing is not None:
            write_real_shared_lock_state(state_path, state)
            return False, key, existing

        state[key] = {
            "market_slug": position.market_slug,
            "token_id": position.token_id,
            "direction": position.direction,
            "event_start_ts": position.event_start_ts,
            "event_end_ts": position.event_end_ts,
            "reserved_at_ts": now_ts,
            "strategy": config.strategy,
            "trades_csv": config.trades_csv,
            "scope": config.real_shared_lock_scope,
        }
        write_real_shared_lock_state(state_path, state)
    return True, key, None


def release_real_shared_position(position: PaperPosition, config: LiveConfig) -> None:
    if not config.real_shared_lock_file:
        return

    state_path = Path(config.real_shared_lock_file)
    key = real_shared_lock_key(position, config)
    with RealSharedFileLock(state_path):
        state = load_real_shared_lock_state(state_path)
        if key in state:
            del state[key]
            write_real_shared_lock_state(state_path, state)


def place_real_buy_order(client, position: PaperPosition, market: LiveMarket, config: LiveConfig) -> PaperPosition:
    if not config.real_confirmed:
        raise RuntimeError("modo real exige --i-understand-real-money")

    limit_price = real_order_limit_price(position.contract_price, config)
    shares = calculate_order_shares(config.stake_usdc, limit_price)
    if shares < market.order_min_size:
        raise RuntimeError(
            f"stake pequeno demais: {config.stake_usdc:.2f} USDC compra {shares:.4f} shares, "
            f"minimo do mercado e {market.order_min_size:.4f}"
        )
    response = client.post_limit_buy(position.token_id, limit_price, shares, market, config.real_order_type)
    if not order_was_accepted(response):
        raise RuntimeError(f"ordem nao aceita: {compact_order_response(response)}")

    return replace(
        position,
        execution_mode="REAL",
        contract_price=limit_price,
        edge=round(position.model_probability - limit_price, 10),
        shares=shares,
        order_id=extract_order_id(response),
        order_status="POSTED",
        order_response=compact_order_response(response),
    )


def paper_limit_entry_enabled(config: LiveConfig) -> bool:
    return config.paper_limit_entry_price > 0 and not config.real_mode


def prepare_paper_limit_entry(position: PaperPosition, config: LiveConfig, now: datetime) -> PaperPosition:
    limit_price = round(config.paper_limit_entry_price, 3)
    current_signal_price = position.contract_price
    base = replace(
        position,
        contract_price=limit_price,
        edge=round(position.model_probability - limit_price, 10),
        signal_contract_price=current_signal_price,
        limit_entry_price=limit_price,
    )
    if current_signal_price <= limit_price:
        return replace(
            base,
            status="OPEN",
            order_status="FILLED_LIMIT_IMMEDIATE",
            order_response=f"signal_price={current_signal_price:.3f}",
        )

    return replace(
        base,
        status="PENDING_LIMIT",
        order_status="PENDING_LIMIT",
        order_response=f"signal_price={current_signal_price:.3f}",
        entry_ts=int(now.timestamp()),
    )


def try_fill_paper_limit_entry(position: PaperPosition, config: LiveConfig, now: datetime) -> PaperPosition | None:
    if position.status != "PENDING_LIMIT":
        return position

    seconds_remaining = position.event_end_ts - int(now.timestamp())
    if seconds_remaining <= config.paper_limit_entry_min_seconds_remaining:
        return None

    current_contract_price = fetch_buy_price(position.token_id)
    if current_contract_price > position.limit_entry_price:
        print(
            f"[{now.strftime('%H:%M:%S')}] LIMIT PENDENTE {position.direction} "
            f"limit={position.limit_entry_price:.3f} atual={current_contract_price:.3f} "
            f"restante={max(seconds_remaining, 0)}s"
        )
        return position

    current_price = fetch_binance_price(config.symbol)
    return replace(
        position,
        status="OPEN",
        entry_ts=int(now.timestamp()),
        entry_btc_price=current_price,
        order_status="FILLED_LIMIT",
        order_response=f"fill_touch_price={current_contract_price:.3f}",
    )


def evaluate_entry(config: LiveConfig, now: datetime | None = None) -> PaperPosition | None:
    if now is None:
        now = utc_now()

    event_start_ts = config_event_start(config, now)
    elapsed = int(now.timestamp()) - event_start_ts
    if not is_entry_offset_allowed(elapsed, config):
        allowed = ",".join(str(item) for item in config.entry_offsets)
        print(f"[{now.strftime('%H:%M:%S')}] Aguardando janela de entrada offsets={allowed}; elapsed={elapsed}s")
        return None

    if should_skip_entry_hour(now, config):
        hour_brt = now.astimezone(BRT).hour
        excluded = ",".join(str(item) for item in config.excluded_entry_hours_brt)
        print(f"[{now.strftime('%H:%M:%S')}] HOLD horario BRT excluido hour={hour_brt} excluded={excluded}")
        return None

    if config.strategy in ("first-minute-continuation", "target-rejection", "momentum-confirmed") and elapsed < 60:
        print(f"[{now.strftime('%H:%M:%S')}] Aguardando candle 1m fechado; elapsed={elapsed}s")
        return None

    market = fetch_live_market(event_start_ts, config)
    if market is None:
        print(f"[{now.isoformat()}] Mercado atual nao encontrado.")
        return None

    df = fetch_recent_binance_1m(config.symbol, config.lookback_minutes + config.momentum_minutes + 10)
    current_price = fetch_binance_price(config.symbol)
    target_price = target_price_for_event(df, event_start_ts)

    if config.max_recent_move_pct > 0:
        recent_move = recent_abs_move_pct(df, current_price, config.recent_move_filter_seconds)
        if recent_move > config.max_recent_move_pct:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD recent-move: "
                f"move={recent_move:.4%} max={config.max_recent_move_pct:.4%} "
                f"window={config.recent_move_filter_seconds}s btc={current_price:.2f} target={target_price:.2f}"
            )
            return None

    forced_direction = None
    if config.strategy == "first-minute-continuation":
        forced_direction = first_minute_anchor_direction(df, event_start_ts, config)
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD continuation: candle 1m inicial sem corpo/direcao valida "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None
    elif config.strategy == "target-rejection":
        forced_direction = target_rejection_latest_direction(df, event_start_ts, now, config)
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD rejection: sem rejeicao valida do alvo "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None
    elif config.strategy == "momentum-confirmed":
        forced_direction = momentum_confirmed_direction(df, event_start_ts, now, target_price, config)
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD momentum: candle/EMA sem confirmacao "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None
    elif config.strategy == "bollinger-rsi-reversal":
        candles_5m = fetch_recent_binance_5m(config.symbol, 100)
        forced_direction = "DOWN" if bollinger_rsi_reversal_down_signal(candles_5m, config.rsi_threshold_sell) else None
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD reversal: sem reversao Bollinger/RSI "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None
    elif config.strategy == "rsi-reversal-down":
        candles_5m = fetch_recent_binance_5m(config.symbol, 100)
        forced_direction = "DOWN" if rsi_reversal_down_signal(candles_5m, config.rsi_threshold_sell) else None
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD reversal: sem reversao RSI "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None
    elif config.strategy == "ema-1s-trend":
        lookback_seconds = max(
            config.ema_fast_seconds,
            config.ema_mid_seconds,
            config.ema_slow_seconds,
        ) + config.ema_slope_lookback_seconds + 20
        df_1s = fetch_recent_binance_1s(config.symbol, lookback_seconds)
        forced_direction = ema_1s_trend_direction(df_1s, current_price, config)
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD ema1s: EMAs sem tendencia alinhada "
                f"btc={current_price:.2f} target={target_price:.2f}"
            )
            return None

    seconds_remaining = max(market.event_end_ts - int(now.timestamp()), 1)
    prob_up, z_score, sigma_remaining = estimate_live_probability(
        df=df,
        current_price=current_price,
        target_price=target_price,
        seconds_remaining=seconds_remaining,
        config=config,
    )
    if config.strategy == "late-window":
        forced_direction = late_window_direction(
            current_price=current_price,
            target_price=target_price,
            seconds_remaining=seconds_remaining,
            z_score=z_score,
            config=config,
        )
        if forced_direction is None:
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD late: remaining={seconds_remaining}s "
                f"z={z_score:.2f} prob_up={prob_up:.3f} btc={current_price:.2f} target={target_price:.2f}"
            )
            return None

    if config.strategy == "mispricing-contrarian":
        up_price = fetch_buy_price(market.up_token_id)
        down_price = fetch_buy_price(market.down_token_id)
        decision = decide_mispricing_contrarian(
            prob_up=prob_up,
            ask_up=up_price,
            ask_down=down_price,
            config=config,
        )
        print(
            f"[{now.strftime('%H:%M:%S')}] {decision.direction} contrarian "
            f"prob_up={prob_up:.3f} up={up_price:.3f} down={down_price:.3f} "
            f"z={z_score:.2f} sigma={sigma_remaining:.2f}"
        )
        if decision.direction == "HOLD":
            return None

        token_id = market.up_token_id if decision.direction == "UP" else market.down_token_id
        return PaperPosition(
            market_slug=market.slug,
            event_start_ts=market.event_start_ts,
            event_end_ts=market.event_end_ts,
            direction=decision.direction,
            token_id=token_id,
            entry_ts=int(now.timestamp()),
            entry_btc_price=current_price,
            target_price=target_price,
            contract_price=decision.contract_price,
            model_probability=decision.probability,
            edge=decision.edge,
            stake_usdc=config.stake_usdc,
        )

    if config.strategy in {"bollinger-rsi-reversal", "rsi-reversal-down"}:
        down_price = fetch_buy_price(market.down_token_id)
        if not (config.min_contract_price <= down_price <= config.max_contract_price):
            print(
                f"[{now.strftime('%H:%M:%S')}] HOLD reversal: preco DOWN fora da faixa "
                f"down={down_price:.3f} faixa={config.min_contract_price:.2f}-{config.max_contract_price:.2f}"
            )
            return None
        prob_down = 1.0 - prob_up
        edge = round(prob_down - down_price, 10)
        print(
            f"[{now.strftime('%H:%M:%S')}] DOWN reversal "
            f"prob_down={prob_down:.3f} down={down_price:.3f} z={z_score:.2f} sigma={sigma_remaining:.2f}"
        )
        return PaperPosition(
            market_slug=market.slug,
            event_start_ts=market.event_start_ts,
            event_end_ts=market.event_end_ts,
            direction="DOWN",
            token_id=market.down_token_id,
            entry_ts=int(now.timestamp()),
            entry_btc_price=current_price,
            target_price=target_price,
            contract_price=down_price,
            model_probability=prob_down,
            edge=edge,
            stake_usdc=config.stake_usdc,
        )

    if abs(z_score) < config.min_abs_z:
        print(
            f"[{now.strftime('%H:%M:%S')}] HOLD z={z_score:.2f} "
            f"prob_up={prob_up:.3f} btc={current_price:.2f} target={target_price:.2f}"
        )
        return None

    up_price = fetch_buy_price(market.up_token_id)
    down_price = fetch_buy_price(market.down_token_id)
    decision = decide_trade(
        prob_up=prob_up,
        ask_up=up_price,
        ask_down=down_price,
        edge_min=config.edge_min,
        max_contract_price=config.max_contract_price,
        min_contract_price=config.min_contract_price,
    )

    print(
        f"[{now.strftime('%H:%M:%S')}] {decision.direction} "
        f"prob_up={prob_up:.3f} up={up_price:.3f} down={down_price:.3f} "
        f"z={z_score:.2f} sigma={sigma_remaining:.2f}"
    )

    if decision.direction == "HOLD":
        return None
    if forced_direction is not None and decision.direction != forced_direction:
        print(
            f"[{now.strftime('%H:%M:%S')}] HOLD {config.strategy}: decisao={decision.direction} "
            f"contra direcao_forcada={forced_direction}"
        )
        return None

    token_id = market.up_token_id if decision.direction == "UP" else market.down_token_id
    return PaperPosition(
        market_slug=market.slug,
        event_start_ts=market.event_start_ts,
        event_end_ts=market.event_end_ts,
        direction=decision.direction,
        token_id=token_id,
        entry_ts=int(now.timestamp()),
        entry_btc_price=current_price,
        target_price=target_price,
        contract_price=decision.contract_price,
        model_probability=decision.probability,
        edge=decision.edge,
        stake_usdc=config.stake_usdc,
    )


def settle_position(position: PaperPosition, final_btc_price: float, closed_ts: int | None = None) -> PaperPosition:
    pnl = binary_trade_pnl(
        direction=position.direction,
        target_price=position.target_price,
        final_price=final_btc_price,
        contract_price=position.contract_price,
        stake_usdc=position.stake_usdc,
    )
    if position.direction == "UP":
        win = final_btc_price > position.target_price
    else:
        win = final_btc_price < position.target_price

    return replace(
        position,
        status="CLOSED",
        final_btc_price=final_btc_price,
        win=win,
        pnl_usdc=pnl,
        closed_ts=closed_ts or int(time.time()),
    )


def append_trade_csv(path: str, position: PaperPosition) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True) if output.parent != Path(".") else None
    row = asdict(position)
    row["entry_time_utc"] = datetime.fromtimestamp(position.entry_ts, timezone.utc).isoformat()
    row["closed_time_utc"] = (
        datetime.fromtimestamp(position.closed_ts, timezone.utc).isoformat() if position.closed_ts else ""
    )
    fieldnames = [
        "market_slug",
        "event_start_ts",
        "event_end_ts",
        "direction",
        "token_id",
        "entry_ts",
        "entry_time_utc",
        "entry_btc_price",
        "target_price",
        "contract_price",
        "model_probability",
        "edge",
        "stake_usdc",
        "status",
        "final_btc_price",
        "win",
        "pnl_usdc",
        "closed_ts",
        "closed_time_utc",
        "execution_mode",
        "shares",
        "order_id",
        "order_status",
        "order_response",
        "signal_contract_price",
        "limit_entry_price",
    ]
    write_header = not output.exists() or output.stat().st_size == 0
    with output.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in fieldnames})


def realized_pnl_from_csv(path: str) -> float:
    output = Path(path)
    if not output.exists():
        return 0.0

    total = 0.0
    with output.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("execution_mode") != "REAL":
                continue
            try:
                total += float(row.get("pnl_usdc") or 0.0)
            except ValueError:
                continue
    return total


def max_real_loss_reached(config: LiveConfig) -> bool:
    if not config.real_mode or config.max_real_loss_usdc <= 0:
        return False
    return realized_pnl_from_csv(config.trades_csv) <= -abs(config.max_real_loss_usdc)


def run_paper_loop(config: LiveConfig, cycles: int = 0, once: bool = False) -> None:
    open_position: PaperPosition | None = None
    completed = 0
    cycle = 0
    real_trades_sent = 0
    client = build_clob_client(config) if config.real_mode else None
    real_start_event_ts = config_event_start(config, utc_now()) if config.real_mode else None

    print(f"=== POLYMARKET {config.event_slug_asset.upper()} UP/DOWN {config.event_slug_duration.upper()} LIVE ===")
    print("Modo: REAL com ordens limit FOK/FAK." if config.real_mode else "Modo: simulacao somente; nenhuma ordem real sera enviada.")
    print(f"CSV: {config.trades_csv}")
    print(
        f"Filtros: strategy={config.strategy}, offsets={config.entry_offsets}, edge_min={config.edge_min}, "
        f"preco={config.min_contract_price}-{config.max_contract_price}, duration={config.event_duration_minutes}m"
    )
    if paper_limit_entry_enabled(config):
        print(
            f"PAPER LIMIT: sinal no offset normal, entrada apenas se tocar "
            f"{config.paper_limit_entry_price:.3f}; cancela com "
            f"{config.paper_limit_entry_min_seconds_remaining}s restantes."
        )
    if config.real_mode:
        print(
            f"REAL: stake={config.stake_usdc:.2f} USDC, order_type={config.real_order_type}, "
            f"max_real_trades={config.max_real_trades}, max_open_positions={config.max_open_positions}, "
            f"max_real_loss={config.max_real_loss_usdc:.2f}, price_slippage={config.real_price_slippage:.3f}"
        )
        if config.real_shared_lock_file:
            print(
                f"REAL LOCK: file={config.real_shared_lock_file}, "
                f"scope={config.real_shared_lock_scope}"
            )
        print(f"REAL START GUARD: pulando mercado inicial event_start_ts={real_start_event_ts}")

    while True:
        cycle += 1
        now = utc_now()

        try:
            if (
                open_position
                and open_position.status == "OPEN"
                and int(now.timestamp()) >= open_position.event_end_ts + config.settle_delay_seconds
            ):
                final_price = fetch_binance_price(config.symbol)
                closed = settle_position(open_position, final_price, int(now.timestamp()))
                append_trade_csv(config.trades_csv, closed)
                completed += 1
                print(
                    f"[{now.strftime('%H:%M:%S')}] FECHOU {closed.direction} "
                    f"win={closed.win} pnl={closed.pnl_usdc:.2f} "
                    f"final={closed.final_btc_price:.2f} target={closed.target_price:.2f}"
                )
                if closed.execution_mode == "REAL":
                    release_real_shared_position(closed, config)
                open_position = None
                if max_real_loss_reached(config):
                    print(
                        f"[{now.strftime('%H:%M:%S')}] STOP REAL: perda realizada atingiu "
                        f"{config.max_real_loss_usdc:.2f} USDC."
                    )
                    break

            if open_position and open_position.status == "PENDING_LIMIT":
                filled = try_fill_paper_limit_entry(open_position, config, now)
                if filled is None:
                    print(
                        f"[{now.strftime('%H:%M:%S')}] LIMIT CANCELADO {open_position.direction} "
                        f"limit={open_position.limit_entry_price:.3f}"
                    )
                    open_position = None
                elif filled.status == "OPEN":
                    open_position = filled
                    print(
                        f"[{now.strftime('%H:%M:%S')}] LIMIT EXECUTADO {filled.direction} "
                        f"contrato={filled.contract_price:.3f} prob={filled.model_probability:.3f} "
                        f"edge={filled.edge:.3f}"
                    )
                time.sleep(config.poll_seconds)
                continue

            if open_position is None:
                if config.real_mode and real_start_event_ts == config_event_start(config, now):
                    print(
                        f"[{now.strftime('%H:%M:%S')}] REAL START GUARD: "
                        f"aguardando proximo mercado para evitar duplicar posicao apos restart."
                    )
                    time.sleep(config.poll_seconds)
                    continue

                if max_real_loss_reached(config):
                    print(
                        f"[{now.strftime('%H:%M:%S')}] STOP REAL: perda realizada atingiu "
                        f"{config.max_real_loss_usdc:.2f} USDC."
                    )
                    break

                candidate = evaluate_entry(config, now)
                if candidate is not None:
                    if config.real_mode:
                        open_count = 1 if open_position is not None else 0
                        if open_count >= config.max_open_positions:
                            print(f"[{now.strftime('%H:%M:%S')}] SINAL IGNORADO: max_open_positions atingido.")
                            candidate = None
                        if real_trades_sent >= config.max_real_trades:
                            print(f"[{now.strftime('%H:%M:%S')}] SINAL IGNORADO: max_real_trades atingido.")
                            candidate = None
                        elif candidate is not None:
                            market = fetch_live_market(candidate.event_start_ts, config)
                            if market is None or not market.accepting_orders:
                                print(f"[{now.strftime('%H:%M:%S')}] SINAL IGNORADO: mercado nao aceita ordens.")
                                candidate = None
                            else:
                                reserved, lock_key, existing = reserve_real_shared_position(
                                    candidate,
                                    config,
                                    now_ts=int(now.timestamp()),
                                )
                                if not reserved:
                                    print(
                                        f"[{now.strftime('%H:%M:%S')}] SINAL IGNORADO: "
                                        f"lock real ja ocupado key={lock_key} "
                                        f"strategy={existing.get('strategy')} "
                                        f"direction={existing.get('direction')}"
                                    )
                                    candidate = None
                                else:
                                    try:
                                        candidate = place_real_buy_order(client, candidate, market, config)
                                    except Exception:
                                        release_real_shared_position(candidate, config)
                                        raise
                                    real_trades_sent += 1

                    if candidate is None:
                        time.sleep(config.poll_seconds)
                        continue

                    if paper_limit_entry_enabled(config):
                        candidate = prepare_paper_limit_entry(candidate, config, now)

                    open_position = candidate
                    label = "REAL" if candidate.execution_mode == "REAL" else "PAPER"
                    if candidate.status == "PENDING_LIMIT":
                        print(
                            f"[{now.strftime('%H:%M:%S')}] SINAL {label} LIMIT {candidate.direction} "
                            f"limit={candidate.limit_entry_price:.3f} sinal={candidate.signal_contract_price:.3f} "
                            f"prob={candidate.model_probability:.3f} edge_limit={candidate.edge:.3f}"
                        )
                    else:
                        print(
                            f"[{now.strftime('%H:%M:%S')}] ABRIU {label} {candidate.direction} "
                            f"contrato={candidate.contract_price:.3f} prob={candidate.model_probability:.3f} "
                            f"edge={candidate.edge:.3f} stake={candidate.stake_usdc:.2f} shares={candidate.shares:.4f}"
                        )
            else:
                remaining = max(open_position.event_end_ts - int(now.timestamp()), 0)
                print(
                    f"[{now.strftime('%H:%M:%S')}] POSICAO ABERTA {open_position.direction} "
                    f"restante={remaining}s contrato={open_position.contract_price:.3f}"
                )
        except Exception as exc:
            print(f"[{now.strftime('%H:%M:%S')}] ERRO: {exc}")

        if once:
            break
        if cycles and cycle >= cycles:
            break

        time.sleep(config.poll_seconds)

    print(f"Encerrado. Trades fechados nesta execucao: {completed}")


def parse_entry_offsets(raw: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("entry_offsets cannot be empty")
    return values


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper trader live para BTC Up/Down 5m na Polymarket.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--event-slug-asset",
        default="btc",
        help="Prefixo do ativo no slug da Polymarket. Ex: btc ou eth.",
    )
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument(
        "--strategy",
        default="edge",
        choices=[
            "edge",
            "first-minute-continuation",
            "target-rejection",
            "momentum-confirmed",
            "late-window",
            "mispricing-contrarian",
            "bollinger-rsi-reversal",
            "rsi-reversal-down",
            "ema-1s-trend",
        ],
    )
    parser.add_argument("--entry-offsets", default="1", help="Minutos um-based: 1,2,3,4,5.")
    parser.add_argument("--edge-min", type=float, default=0.06)
    parser.add_argument("--min-contract-price", type=float, default=0.50)
    parser.add_argument("--max-contract-price", type=float, default=0.85)
    parser.add_argument("--min-abs-z", type=float, default=1.0)
    parser.add_argument("--min-anchor-body-pct", type=float, default=0.0003)
    parser.add_argument("--anchor-volume-multiplier", type=float, default=0.0)
    parser.add_argument("--target-touch-tolerance-pct", type=float, default=0.0002)
    parser.add_argument("--min-rejection-wick-ratio", type=float, default=0.35)
    parser.add_argument("--late-entry-min-remaining", type=int, default=45)
    parser.add_argument("--late-entry-max-remaining", type=int, default=75)
    parser.add_argument("--contrarian-favorite-min-price", type=float, default=0.70)
    parser.add_argument("--rsi-threshold-sell", type=float, default=70.0)
    parser.add_argument("--lookback-minutes", type=int, default=30)
    parser.add_argument("--momentum-minutes", type=int, default=2)
    parser.add_argument("--momentum-weight", type=float, default=0.10)
    parser.add_argument(
        "--recent-move-filter-seconds",
        type=int,
        default=0,
        help="Janela do filtro de movimento recente. 0 desliga.",
    )
    parser.add_argument(
        "--max-recent-move-pct",
        type=float,
        default=0.0,
        help="Nao opera se o movimento absoluto recente passar deste percentual decimal. Ex: 0.003 = 0.3%.",
    )
    parser.add_argument("--ema-fast-seconds", type=int, default=60)
    parser.add_argument("--ema-mid-seconds", type=int, default=300)
    parser.add_argument("--ema-slow-seconds", type=int, default=600)
    parser.add_argument("--ema-slope-lookback-seconds", type=int, default=15)
    parser.add_argument("--min-ema-gap-usd", type=float, default=2.0)
    parser.add_argument("--min-ema-slope-usd", type=float, default=0.2)
    parser.add_argument("--max-price-ema-fast-distance-usd", type=float, default=80.0)
    parser.add_argument("--stake", type=float, default=10.0)
    parser.add_argument(
        "--paper-limit-entry-price",
        type=float,
        default=0.0,
        help="Paper only: se > 0, sinal aprovado vira buy limit nesse preco em vez de entrada imediata.",
    )
    parser.add_argument(
        "--paper-limit-entry-min-seconds-remaining",
        type=int,
        default=20,
        help="Paper only: cancela limit pendente quando restarem estes segundos ou menos no mercado.",
    )
    parser.add_argument("--settle-delay-seconds", type=int, default=10)
    parser.add_argument("--trades-csv", default="paper_polymarket_5m_trades.csv")
    parser.add_argument(
        "--event-duration-minutes",
        type=int,
        default=5,
        help="Duracao do mercado em minutos. Ex: 5 para btc-updown-5m, 15 para btc-updown-15m.",
    )
    parser.add_argument(
        "--event-slug-duration",
        default="5m",
        help="Parte da slug da Polymarket. Ex: 5m ou 15m.",
    )
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true", help="Roda uma iteracao e sai.")
    parser.add_argument("--real", action="store_true", help="Envia ordens reais para a Polymarket.")
    parser.add_argument("--env-file", default="", help="Arquivo .env especifico desta conta real.")
    parser.add_argument(
        "--i-understand-real-money",
        action="store_true",
        help="Confirmacao obrigatoria para modo real.",
    )
    parser.add_argument("--real-order-type", default="FOK", choices=["FOK", "FAK"])
    parser.add_argument(
        "--real-price-slippage",
        type=float,
        default=0.0,
        help="Tolerancia maxima acima do preco do sinal para ordem real. Ex: 0.01 = 1 cent.",
    )
    parser.add_argument("--real-signature-type", type=int, default=1)
    parser.add_argument("--max-real-trades", type=int, default=1)
    parser.add_argument("--max-open-positions", type=int, default=1)
    parser.add_argument(
        "--max-real-loss-usdc",
        type=float,
        default=0.0,
        help="Para o bot real quando o PnL realizado no CSV ficar <= -valor. 0 desliga.",
    )
    parser.add_argument(
        "--real-shared-lock-file",
        default="",
        help="Arquivo JSON compartilhado para impedir duas estrategias reais no mesmo mercado/outcome.",
    )
    parser.add_argument(
        "--real-shared-lock-scope",
        default="market",
        choices=["market", "outcome"],
        help="market bloqueia qualquer segundo trade no mesmo mercado; outcome bloqueia apenas o mesmo lado.",
    )
    parser.add_argument(
        "--allowed-directions",
        default="UP,DOWN",
        help="Direcoes permitidas para a estrategia, ex: DOWN ou UP,DOWN",
    )
    parser.add_argument(
        "--exclude-entry-hours-brt",
        default="",
        help="Horas BRT sem novas entradas, separadas por virgula. Ex: 10 ou 8,10,18,23.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = LiveConfig(
        symbol=args.symbol,
        event_slug_asset=args.event_slug_asset,
        poll_seconds=args.poll_seconds,
        strategy=args.strategy,
        entry_offsets=parse_entry_offsets(args.entry_offsets),
        edge_min=args.edge_min,
        min_contract_price=args.min_contract_price,
        max_contract_price=args.max_contract_price,
        min_abs_z=args.min_abs_z,
        min_anchor_body_pct=args.min_anchor_body_pct,
        anchor_volume_multiplier=args.anchor_volume_multiplier,
        target_touch_tolerance_pct=args.target_touch_tolerance_pct,
        min_rejection_wick_ratio=args.min_rejection_wick_ratio,
        late_entry_min_remaining=args.late_entry_min_remaining,
        late_entry_max_remaining=args.late_entry_max_remaining,
        contrarian_favorite_min_price=args.contrarian_favorite_min_price,
        rsi_threshold_sell=args.rsi_threshold_sell,
        lookback_minutes=args.lookback_minutes,
        momentum_minutes=args.momentum_minutes,
        momentum_weight=args.momentum_weight,
        recent_move_filter_seconds=args.recent_move_filter_seconds,
        max_recent_move_pct=args.max_recent_move_pct,
        ema_fast_seconds=args.ema_fast_seconds,
        ema_mid_seconds=args.ema_mid_seconds,
        ema_slow_seconds=args.ema_slow_seconds,
        ema_slope_lookback_seconds=args.ema_slope_lookback_seconds,
        min_ema_gap_usd=args.min_ema_gap_usd,
        min_ema_slope_usd=args.min_ema_slope_usd,
        max_price_ema_fast_distance_usd=args.max_price_ema_fast_distance_usd,
        stake_usdc=args.stake,
        paper_limit_entry_price=args.paper_limit_entry_price,
        paper_limit_entry_min_seconds_remaining=args.paper_limit_entry_min_seconds_remaining,
        settle_delay_seconds=args.settle_delay_seconds,
        trades_csv=args.trades_csv,
        event_duration_minutes=args.event_duration_minutes,
        event_slug_duration=args.event_slug_duration,
        real_mode=args.real,
        env_file=args.env_file,
        real_confirmed=args.i_understand_real_money,
        real_order_type=args.real_order_type,
        real_price_slippage=args.real_price_slippage,
        real_signature_type=args.real_signature_type,
        max_real_trades=args.max_real_trades,
        max_open_positions=args.max_open_positions,
        max_real_loss_usdc=args.max_real_loss_usdc,
        real_shared_lock_file=args.real_shared_lock_file,
        real_shared_lock_scope=args.real_shared_lock_scope,
        allowed_directions=parse_allowed_directions(args.allowed_directions),
        excluded_entry_hours_brt=parse_excluded_entry_hours(args.exclude_entry_hours_brt),
    )
    if config.real_mode and not config.real_confirmed:
        raise SystemExit("Modo real bloqueado: adicione --i-understand-real-money para confirmar.")
    run_paper_loop(config, cycles=args.cycles, once=args.once)


if __name__ == "__main__":
    main()
