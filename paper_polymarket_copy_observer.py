#!/usr/bin/env python3
"""Observador/paper copy de carteiras vencedoras na Polymarket.

Nao envia ordens. Coleta trades publicos de carteiras do leaderboard e registra
se ainda seria copiavel pelo preco atual do CLOB.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass(frozen=True)
class CopyConfig:
    category: str = "CRYPTO"
    time_period: str = "WEEK"
    order_by: str = "PNL"
    leaderboard_limit: int = 20
    min_leaderboard_pnl: float = 0.0
    min_leaderboard_volume: float = 0.0
    poll_seconds: int = 20
    refresh_wallets_seconds: int = 600
    trade_limit_per_wallet: int = 20
    max_lag_seconds: int = 120
    max_price_worse: float = 0.03
    min_contract_price: float = 0.05
    max_contract_price: float = 0.85
    simulated_stake_usdc: float = 5.0
    csv_path: str = "paper_copy_wallet_observer.csv"
    trades_csv_path: str = "paper_copy_wallet_trades.csv"
    wallets: tuple[str, ...] = ()


@dataclass(frozen=True)
class CopyObservation:
    observed_time_utc: str
    observed_ts: int
    wallet: str
    trader_name: str
    leaderboard_rank: str
    leaderboard_pnl: float
    leaderboard_volume: float
    category: str
    time_period: str
    side: str
    asset: str
    condition_id: str
    market_slug: str
    title: str
    outcome: str
    trade_ts: int
    lag_seconds: int
    trader_price: float
    current_buy_price: float | None
    price_diff: float | None
    copy_decision: str
    reason: str
    trader_size: float
    simulated_stake_usdc: float
    simulated_shares: float
    transaction_hash: str


@dataclass(frozen=True)
class CopyPaperTrade:
    trade_id: str
    market_slug: str
    direction: str
    contract_price: float
    stake_usdc: float
    shares: float
    entry_time_utc: str
    entry_ts: int
    wallet: str
    trader_name: str
    title: str
    outcome: str
    asset: str
    transaction_hash: str
    status: str = "OPEN"
    closed_time_utc: str = ""
    closed_ts: int = 0
    winning_outcome: str = ""
    win: bool = False
    pnl_usdc: float = 0.0
    order_status: str = "COPY"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def decode_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def fetch_leaderboard(config: CopyConfig) -> list[dict[str, Any]]:
    if config.wallets:
        return [
            {
                "rank": "manual",
                "proxyWallet": wallet,
                "userName": "manual",
                "vol": 0.0,
                "pnl": 0.0,
            }
            for wallet in config.wallets
        ]

    rows = request_json(
        f"{DATA_API}/v1/leaderboard",
        {
            "category": config.category,
            "timePeriod": config.time_period,
            "orderBy": config.order_by,
            "limit": config.leaderboard_limit,
        },
    )
    selected = []
    for row in rows:
        pnl = safe_float(row.get("pnl"))
        volume = safe_float(row.get("vol"))
        if pnl < config.min_leaderboard_pnl:
            continue
        if volume < config.min_leaderboard_volume:
            continue
        selected.append(row)
    return selected


def fetch_wallet_trades(wallet: str, limit: int) -> list[dict[str, Any]]:
    rows = request_json(
        f"{DATA_API}/trades",
        {
            "user": wallet,
            "limit": limit,
            "side": "BUY",
        },
    )
    if not isinstance(rows, list):
        return []
    return rows


def fetch_current_buy_price(asset: str) -> float | None:
    try:
        payload = request_json(f"{CLOB_API}/price", {"token_id": asset, "side": "BUY"})
    except requests.RequestException:
        return None
    price = safe_float(payload.get("price") if isinstance(payload, dict) else None, default=-1.0)
    return price if price >= 0 else None


def fetch_event_by_slug(slug: str) -> dict[str, Any] | None:
    rows = request_json(f"{GAMMA_API}/events", {"slug": slug})
    if isinstance(rows, list) and rows:
        return rows[0] if isinstance(rows[0], dict) else None
    return None


def winning_outcome_from_event(event: dict[str, Any]) -> str | None:
    markets = event.get("markets")
    if not isinstance(markets, list) or not markets:
        return None
    market = markets[0]
    if not isinstance(market, dict):
        return None
    if not (event.get("closed") or market.get("closed")):
        return None
    status = str(market.get("umaResolutionStatus") or "").lower()
    if status and status != "resolved":
        return None

    outcomes = [str(item) for item in decode_json_list(market.get("outcomes"))]
    prices = [safe_float(item, default=-1.0) for item in decode_json_list(market.get("outcomePrices"))]
    if not outcomes or len(outcomes) != len(prices):
        return None
    winning_index = max(range(len(prices)), key=lambda index: prices[index])
    if prices[winning_index] < 0.99:
        return None
    return outcomes[winning_index]


def trade_key(trade: dict[str, Any]) -> str:
    tx_hash = str(trade.get("transactionHash") or "").strip()
    asset = str(trade.get("asset") or "").strip()
    if tx_hash:
        return f"{tx_hash}:{asset}"
    return ":".join(
        [
            str(trade.get("proxyWallet") or ""),
            asset,
            str(trade.get("timestamp") or ""),
            str(trade.get("price") or ""),
            str(trade.get("size") or ""),
        ]
    )


def normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "wallet": str(trade.get("proxyWallet") or ""),
        "side": str(trade.get("side") or "").upper(),
        "asset": str(trade.get("asset") or ""),
        "condition_id": str(trade.get("conditionId") or ""),
        "trader_size": safe_float(trade.get("size")),
        "trader_price": safe_float(trade.get("price")),
        "trade_ts": safe_int(trade.get("timestamp")),
        "title": str(trade.get("title") or ""),
        "market_slug": str(trade.get("slug") or ""),
        "outcome": str(trade.get("outcome") or ""),
        "trader_name": str(trade.get("name") or trade.get("pseudonym") or ""),
        "transaction_hash": str(trade.get("transactionHash") or ""),
    }


def should_copy_trade(
    side: str,
    trader_price: float,
    current_buy_price: float | None,
    lag_seconds: int,
    config: CopyConfig,
) -> tuple[bool, str]:
    if side.upper() != "BUY":
        return False, "not_buy"
    if lag_seconds > config.max_lag_seconds:
        return False, "stale"
    if current_buy_price is None:
        return False, "no_current_price"
    if current_buy_price < config.min_contract_price:
        return False, "price_below_min"
    if current_buy_price > config.max_contract_price:
        return False, "price_above_max"
    if current_buy_price > trader_price + config.max_price_worse:
        return False, "price_moved"
    return True, "copy_ok"


def build_observation(
    trade: dict[str, Any],
    leader: dict[str, Any],
    current_buy_price: float | None,
    config: CopyConfig,
    now: datetime | None = None,
) -> CopyObservation:
    now = now or utc_now()
    normalized = normalize_trade(trade)
    lag_seconds = max(int(now.timestamp()) - int(normalized["trade_ts"]), 0)
    should_copy, reason = should_copy_trade(
        side=normalized["side"],
        trader_price=normalized["trader_price"],
        current_buy_price=current_buy_price,
        lag_seconds=lag_seconds,
        config=config,
    )
    price_diff = None if current_buy_price is None else round(current_buy_price - normalized["trader_price"], 6)
    simulated_shares = (
        round(config.simulated_stake_usdc / current_buy_price, 6)
        if should_copy and current_buy_price and current_buy_price > 0
        else 0.0
    )
    return CopyObservation(
        observed_time_utc=now.isoformat(),
        observed_ts=int(now.timestamp()),
        wallet=normalized["wallet"],
        trader_name=normalized["trader_name"] or str(leader.get("userName") or ""),
        leaderboard_rank=str(leader.get("rank") or ""),
        leaderboard_pnl=safe_float(leader.get("pnl")),
        leaderboard_volume=safe_float(leader.get("vol")),
        category=config.category,
        time_period=config.time_period,
        side=normalized["side"],
        asset=normalized["asset"],
        condition_id=normalized["condition_id"],
        market_slug=normalized["market_slug"],
        title=normalized["title"],
        outcome=normalized["outcome"],
        trade_ts=normalized["trade_ts"],
        lag_seconds=lag_seconds,
        trader_price=round(normalized["trader_price"], 6),
        current_buy_price=round(current_buy_price, 6) if current_buy_price is not None else None,
        price_diff=price_diff,
        copy_decision="COPY" if should_copy else "SKIP",
        reason=reason,
        trader_size=round(normalized["trader_size"], 6),
        simulated_stake_usdc=round(config.simulated_stake_usdc, 4),
        simulated_shares=simulated_shares,
        transaction_hash=normalized["transaction_hash"],
    )


def copy_trade_id(transaction_hash: str, asset: str) -> str:
    return f"{transaction_hash}:{asset}" if transaction_hash else asset


def observation_to_paper_trade(observation: CopyObservation) -> CopyPaperTrade:
    price = observation.current_buy_price or 0.0
    return CopyPaperTrade(
        trade_id=copy_trade_id(observation.transaction_hash, observation.asset),
        market_slug=observation.market_slug,
        direction=observation.outcome,
        contract_price=round(price, 6),
        stake_usdc=round(observation.simulated_stake_usdc, 4),
        shares=round(observation.simulated_shares, 6),
        entry_time_utc=observation.observed_time_utc,
        entry_ts=observation.observed_ts,
        wallet=observation.wallet,
        trader_name=observation.trader_name,
        title=observation.title,
        outcome=observation.outcome,
        asset=observation.asset,
        transaction_hash=observation.transaction_hash,
    )


def normalize_outcome(value: str) -> str:
    return value.strip().lower()


def settle_copy_trade(trade: CopyPaperTrade, winning_outcome: str, closed_time_utc: str) -> CopyPaperTrade:
    win = normalize_outcome(trade.direction) == normalize_outcome(winning_outcome)
    pnl = (trade.shares - trade.stake_usdc) if win else -trade.stake_usdc
    closed_ts = safe_int(datetime.fromisoformat(closed_time_utc.replace("Z", "+00:00")).timestamp())
    return CopyPaperTrade(
        **{
            **asdict(trade),
            "status": "CLOSED",
            "closed_time_utc": closed_time_utc,
            "closed_ts": closed_ts,
            "winning_outcome": winning_outcome,
            "win": win,
            "pnl_usdc": round(pnl, 4),
            "order_status": "COPY_CLOSED",
        }
    )


def append_paper_trade(path: str, trade: CopyPaperTrade) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trade).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(trade))


def load_paper_trades(path: str) -> list[CopyPaperTrade]:
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    trades: list[CopyPaperTrade] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            trades.append(
                CopyPaperTrade(
                    trade_id=str(row.get("trade_id") or ""),
                    market_slug=str(row.get("market_slug") or ""),
                    direction=str(row.get("direction") or ""),
                    contract_price=safe_float(row.get("contract_price")),
                    stake_usdc=safe_float(row.get("stake_usdc")),
                    shares=safe_float(row.get("shares")),
                    entry_time_utc=str(row.get("entry_time_utc") or ""),
                    entry_ts=safe_int(row.get("entry_ts")),
                    wallet=str(row.get("wallet") or ""),
                    trader_name=str(row.get("trader_name") or ""),
                    title=str(row.get("title") or ""),
                    outcome=str(row.get("outcome") or ""),
                    asset=str(row.get("asset") or ""),
                    transaction_hash=str(row.get("transaction_hash") or ""),
                    status=str(row.get("status") or "OPEN"),
                    closed_time_utc=str(row.get("closed_time_utc") or ""),
                    closed_ts=safe_int(row.get("closed_ts")),
                    winning_outcome=str(row.get("winning_outcome") or ""),
                    win=str(row.get("win") or "").lower() == "true",
                    pnl_usdc=safe_float(row.get("pnl_usdc")),
                    order_status=str(row.get("order_status") or "COPY"),
                )
            )
    return trades


def write_paper_trades(path: str, trades: list[CopyPaperTrade]) -> None:
    csv_path = Path(path)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) if trades else list(CopyPaperTrade.__dataclass_fields__.keys()))
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def sync_copy_observations_to_trades(config: CopyConfig) -> int:
    observation_path = Path(config.csv_path)
    if not observation_path.exists() or observation_path.stat().st_size == 0:
        return 0
    existing_ids = {trade.trade_id for trade in load_paper_trades(config.trades_csv_path)}
    added = 0
    with observation_path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("copy_decision") or "").upper() != "COPY":
                continue
            transaction_hash = str(row.get("transaction_hash") or "")
            asset = str(row.get("asset") or "")
            trade_id = copy_trade_id(transaction_hash, asset)
            if trade_id in existing_ids:
                continue
            observation = CopyObservation(
                observed_time_utc=str(row.get("observed_time_utc") or ""),
                observed_ts=safe_int(row.get("observed_ts")),
                wallet=str(row.get("wallet") or ""),
                trader_name=str(row.get("trader_name") or ""),
                leaderboard_rank=str(row.get("leaderboard_rank") or ""),
                leaderboard_pnl=safe_float(row.get("leaderboard_pnl")),
                leaderboard_volume=safe_float(row.get("leaderboard_volume")),
                category=str(row.get("category") or config.category),
                time_period=str(row.get("time_period") or config.time_period),
                side=str(row.get("side") or "BUY"),
                asset=asset,
                condition_id=str(row.get("condition_id") or ""),
                market_slug=str(row.get("market_slug") or ""),
                title=str(row.get("title") or ""),
                outcome=str(row.get("outcome") or ""),
                trade_ts=safe_int(row.get("trade_ts")),
                lag_seconds=safe_int(row.get("lag_seconds")),
                trader_price=safe_float(row.get("trader_price")),
                current_buy_price=safe_float(row.get("current_buy_price")),
                price_diff=safe_float(row.get("price_diff")),
                copy_decision="COPY",
                reason=str(row.get("reason") or "copy_ok"),
                trader_size=safe_float(row.get("trader_size")),
                simulated_stake_usdc=safe_float(row.get("simulated_stake_usdc"), config.simulated_stake_usdc),
                simulated_shares=safe_float(row.get("simulated_shares")),
                transaction_hash=transaction_hash,
            )
            append_paper_trade(config.trades_csv_path, observation_to_paper_trade(observation))
            existing_ids.add(trade_id)
            added += 1
    return added


def settle_open_copy_trades(config: CopyConfig) -> tuple[int, int]:
    trades = load_paper_trades(config.trades_csv_path)
    if not trades:
        return 0, 0
    changed = False
    closed_count = 0
    open_count = 0
    for index, trade in enumerate(trades):
        if trade.status != "OPEN":
            continue
        open_count += 1
        event = fetch_event_by_slug(trade.market_slug)
        if event is None:
            continue
        winning_outcome = winning_outcome_from_event(event)
        if not winning_outcome:
            continue
        closed_time = str(event.get("closedTime") or event.get("endDate") or utc_now().isoformat())
        trades[index] = settle_copy_trade(trade, winning_outcome=winning_outcome, closed_time_utc=closed_time)
        closed_count += 1
        changed = True
    if changed:
        write_paper_trades(config.trades_csv_path, trades)
    return open_count, closed_count


def append_observation(path: str, observation: CopyObservation) -> None:
    csv_path = Path(path)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(observation).keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(asdict(observation))


def load_seen_keys(path: str) -> set[str]:
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return set()
    seen: set[str] = set()
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            tx_hash = row.get("transaction_hash", "")
            asset = row.get("asset", "")
            if tx_hash:
                seen.add(f"{tx_hash}:{asset}")
    return seen


def parse_wallets(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def run_loop(config: CopyConfig, cycles: int = 0, once: bool = False) -> None:
    print("=== PAPER COPY WALLET OBSERVER POLYMARKET ===")
    print("Modo: observacao/paper; nenhuma ordem real sera enviada.")
    print(f"CSV: {config.csv_path}")
    print(f"Trades paper: {config.trades_csv_path}")
    print(
        f"Leaderboard: category={config.category}, period={config.time_period}, limit={config.leaderboard_limit}, "
        f"max_lag={config.max_lag_seconds}s, price_worse={config.max_price_worse:.3f}"
    )

    synced = sync_copy_observations_to_trades(config)
    if synced:
        print(f"[{utc_now().strftime('%H:%M:%S')}] Sinais COPY antigos sincronizados: {synced}")

    seen = load_seen_keys(config.csv_path)
    leaders: list[dict[str, Any]] = []
    last_refresh = 0.0
    iterations = 0

    while True:
        now = utc_now()
        try:
            open_count, closed_count = settle_open_copy_trades(config)
            if closed_count:
                print(f"[{now.strftime('%H:%M:%S')}] Fechou paper copy: {closed_count}/{open_count} abertas")

            if not leaders or time.time() - last_refresh >= config.refresh_wallets_seconds:
                leaders = fetch_leaderboard(config)
                last_refresh = time.time()
                print(f"[{now.strftime('%H:%M:%S')}] Carteiras monitoradas: {len(leaders)}")

            observations = 0
            copies = 0
            for leader in leaders:
                wallet = str(leader.get("proxyWallet") or "")
                if not wallet:
                    continue
                for trade in fetch_wallet_trades(wallet, config.trade_limit_per_wallet):
                    key = trade_key(trade)
                    if key in seen:
                        continue
                    seen.add(key)
                    current_price = fetch_current_buy_price(str(trade.get("asset") or ""))
                    observation = build_observation(trade, leader, current_price, config, now=utc_now())
                    append_observation(config.csv_path, observation)
                    observations += 1
                    if observation.copy_decision == "COPY":
                        copies += 1
                        append_paper_trade(config.trades_csv_path, observation_to_paper_trade(observation))
                    print(
                        f"[{utc_now().strftime('%H:%M:%S')}] {observation.copy_decision} "
                        f"{observation.trader_name} {observation.outcome} trader={observation.trader_price:.3f} "
                        f"now={observation.current_buy_price} lag={observation.lag_seconds}s reason={observation.reason}"
                    )
            if observations == 0:
                print(f"[{now.strftime('%H:%M:%S')}] Sem trades novos. Carteiras={len(leaders)}")
            else:
                print(f"[{now.strftime('%H:%M:%S')}] Novos={observations} copiaveis={copies}")
        except Exception as exc:
            print(f"[{now.strftime('%H:%M:%S')}] ERRO: {exc}")

        iterations += 1
        if once or (cycles and iterations >= cycles):
            break
        time.sleep(config.poll_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observer paper para copiar carteiras vencedoras da Polymarket.")
    parser.add_argument("--category", default="CRYPTO")
    parser.add_argument("--time-period", default="WEEK", choices=["DAY", "WEEK", "MONTH", "ALL"])
    parser.add_argument("--order-by", default="PNL", choices=["PNL", "VOL"])
    parser.add_argument("--leaderboard-limit", type=int, default=20)
    parser.add_argument("--min-leaderboard-pnl", type=float, default=0.0)
    parser.add_argument("--min-leaderboard-volume", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--refresh-wallets-seconds", type=int, default=600)
    parser.add_argument("--trade-limit-per-wallet", type=int, default=20)
    parser.add_argument("--max-lag-seconds", type=int, default=120)
    parser.add_argument("--max-price-worse", type=float, default=0.03)
    parser.add_argument("--min-contract-price", type=float, default=0.05)
    parser.add_argument("--max-contract-price", type=float, default=0.85)
    parser.add_argument("--simulated-stake", type=float, default=5.0)
    parser.add_argument("--csv", default="paper_copy_wallet_observer.csv")
    parser.add_argument("--trades-csv", default="paper_copy_wallet_trades.csv")
    parser.add_argument("--wallets", default="", help="Carteiras manuais separadas por virgula. Se vazio usa leaderboard.")
    parser.add_argument("--cycles", type=int, default=0, help="0 roda indefinidamente.")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = CopyConfig(
        category=args.category,
        time_period=args.time_period,
        order_by=args.order_by,
        leaderboard_limit=args.leaderboard_limit,
        min_leaderboard_pnl=args.min_leaderboard_pnl,
        min_leaderboard_volume=args.min_leaderboard_volume,
        poll_seconds=args.poll_seconds,
        refresh_wallets_seconds=args.refresh_wallets_seconds,
        trade_limit_per_wallet=args.trade_limit_per_wallet,
        max_lag_seconds=args.max_lag_seconds,
        max_price_worse=args.max_price_worse,
        min_contract_price=args.min_contract_price,
        max_contract_price=args.max_contract_price,
        simulated_stake_usdc=args.simulated_stake,
        csv_path=args.csv,
        trades_csv_path=args.trades_csv,
        wallets=parse_wallets(args.wallets),
    )
    run_loop(config, cycles=args.cycles, once=args.once)


if __name__ == "__main__":
    main()
