#!/usr/bin/env python3
"""Dashboard Streamlit para acompanhar os robos Polymarket BTC 5m."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
PLINK_PATH = Path(r"C:\Program Files\PuTTY\plink.exe")
GIT_SSH_PATH = Path(r"C:\Program Files\Git\usr\bin\ssh.exe")
DEFAULT_REMOTE_REAL_SSH_HOST = "208.85.18.176"
DEFAULT_REMOTE_REAL_SSH_KEY = Path.home() / ".ssh" / "fintechtrading_vps_ed25519"
REMOTE_REAL_KEY = "real"
REMOTE_REAL_DIR = "/root/fintechtrading_real"
REMOTE_REAL_CSV_PATH = f"{REMOTE_REAL_DIR}/real_polymarket_5m_trades.csv"
REMOTE_REAL_LOG_PATH = f"{REMOTE_REAL_DIR}/real_polymarket_5m_live.log"
REMOTE_REAL_PGREP = "pgrep -af '[p]aper_polymarket_5m_live.py.*--real'"
REMOTE_PROCESS_PGREP = (
    "pgrep -af "
    "'[p]aper_polymarket_5m_live.py|"
    "[p]aper_mes_ema_scalp_live.py|"
    "[p]aper_mes_orb_live.py|"
    "[p]aper_binance_ema_scalp_live.py|"
    "[p]aper_binance_spot_momentum_scanner.py|"
    "[p]aper_futures_vwap_macd_live.py|"
    "[p]aper_polymarket_copy_observer.py|"
    "[p]aper_polymarket_late_lottery.py'"
)
MATRIX_SUMMARY_PATH = ROOT / "backtest_polymarket_5m_matrix_summary.csv"
SUMMARY_TABLE_HEIGHT = 760
METRIC_COLUMNS_PER_ROW = 2
DASHBOARD_PREFS_PATH = ROOT / ".streamlit_dashboard_prefs.json"
RECENT_TRADE_LIMIT = 100
SUMMARY_COLUMNS = [
    "Estrategia",
    "Tipo",
    "Trades",
    "Wins",
    "Losses",
    "Win rate",
    "PnL",
    "Media/trade",
    "Ultimo trade",
    "CSV",
]
LOCAL_MONITORED_SOURCE_KEYS = (
    "binance_spot_momentum",
    "mt5_us500_base",
    "mt5_us500_candidate",
)
SOURCE_COMMAND_PATTERNS = {
    "binance_ema_scalp": ("paper_binance_ema_scalp_live.py",),
    "binance_spot_momentum": ("paper_binance_spot_momentum_scanner.py",),
    "mt5_us500_candidate": ("codex-us500-demo-candidate", "--magic 505018"),
    "mt5_us500_base": ("mt5_us500_demo_bot.py",),
    "real_edge3": ("real_polymarket_5m_trades.csv",),
}


@dataclass(frozen=True)
class StrategySource:
    key: str
    label: str
    csv_name: str
    log_name: str
    kind: str = "PAPER"


SOURCES = [
    StrategySource("edge_3", "Edge 3%", "paper_edge3_polymarket_5m_trades.csv", "paper_edge3_polymarket_5m_live.log"),
    StrategySource(
        "edge3_max80",
        "Edge 3% max 0.80",
        "paper_edge3_max80_polymarket_5m_trades.csv",
        "paper_edge3_max80_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_no10h",
        "Edge 3% sem 10h",
        "paper_edge3_no10h_polymarket_5m_trades.csv",
        "paper_edge3_no10h_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_max80_no10h",
        "Edge 3% max 0.80 sem 10h",
        "paper_edge3_max80_no10h_polymarket_5m_trades.csv",
        "paper_edge3_max80_no10h_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_down_only",
        "Edge 3% DOWN only",
        "paper_edge3_down_only_polymarket_5m_trades.csv",
        "paper_edge3_down_only_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_limit55",
        "[FAILED] Edge 3% limit 0.55",
        "paper_edge3_limit55_polymarket_5m_trades.csv",
        "paper_edge3_limit55_polymarket_5m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge3_limit60",
        "Edge 3% limit 0.60",
        "paper_edge3_limit60_polymarket_5m_trades.csv",
        "paper_edge3_limit60_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_eth",
        "ETH Edge 3%",
        "paper_edge3_eth_polymarket_5m_trades.csv",
        "paper_edge3_eth_polymarket_5m_live.log",
    ),
    StrategySource(
        "poly_odds_momentum_60s",
        "Poly odds momentum 60s",
        "paper_poly_odds_momentum_60s_trades.csv",
        "paper_poly_odds_momentum_60s_live.log",
    ),
    StrategySource(
        "poly_odds_momentum_90s",
        "Poly odds momentum 90s",
        "paper_poly_odds_momentum_90s_trades.csv",
        "paper_poly_odds_momentum_90s_live.log",
    ),
    StrategySource(
        "poly_75_breakout",
        "Poly 75 breakout",
        "paper_poly_75_breakout_trades.csv",
        "paper_poly_75_breakout_live.log",
    ),
    StrategySource(
        "edge3_value",
        "Edge 3% valor",
        "paper_edge3_value_polymarket_5m_trades.csv",
        "paper_edge3_value_polymarket_5m_live.log",
    ),
    StrategySource(
        "edge3_40_65",
        "[FAILED] Edge 3% 40-65",
        "paper_edge3_40_65_polymarket_5m_trades.csv",
        "paper_edge3_40_65_polymarket_5m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_15m",
        "[FAILED] Edge 15m",
        "paper_polymarket_15m_edge_trades.csv",
        "paper_polymarket_15m_edge_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_15m_40_65",
        "[FAILED] Edge 15m 40-65",
        "paper_polymarket_15m_edge_40_65_trades.csv",
        "paper_polymarket_15m_edge_40_65_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_15m_strict",
        "[FAILED] Edge 15m strict",
        "paper_polymarket_15m_strict_trades.csv",
        "paper_polymarket_15m_strict_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_4",
        "[FAILED] Edge 4%",
        "paper_edge4_polymarket_5m_trades.csv",
        "paper_edge4_polymarket_5m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_6",
        "[FAILED] Edge 6%",
        "paper_polymarket_5m_trades.csv",
        "paper_polymarket_5m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "edge_10",
        "[FAILED] Edge 10%",
        "paper_edge10_polymarket_5m_trades.csv",
        "paper_edge10_polymarket_5m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "first_minute",
        "[FAILED] Primeiro minuto",
        "paper_first_minute_continuation_trades.csv",
        "paper_first_minute_continuation_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "first_minute_value",
        "Primeiro minuto valor",
        "paper_first_minute_value_trades.csv",
        "paper_first_minute_value_live.log",
    ),
    StrategySource(
        "first_minute_down",
        "Primeiro minuto DOWN",
        "paper_first_minute_continuation_down_trades.csv",
        "paper_first_minute_continuation_down_live.log",
    ),
    StrategySource(
        "target_rejection",
        "[FAILED] Rejeicao do alvo",
        "paper_target_rejection_trades.csv",
        "paper_target_rejection_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "ema_1s_trend",
        "EMA 1s trend",
        "paper_ema_1s_trend_trades.csv",
        "paper_ema_1s_trend_live.log",
    ),
    StrategySource(
        "ema_1s_value",
        "[FAILED] EMA 1s valor",
        "paper_ema_1s_value_trades.csv",
        "paper_ema_1s_value_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "late_window",
        "[FAILED] Late window",
        "paper_late_window_trades.csv",
        "paper_late_window_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "mispricing_contrarian",
        "[FAILED] Mispricing contrarian",
        "paper_mispricing_contrarian_trades.csv",
        "paper_mispricing_contrarian_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "rsi_reversal_down",
        "[FAILED] Reversao RSI70",
        "paper_rsi_reversal_down_trades.csv",
        "paper_rsi_reversal_down_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "futures_vwap_macd",
        "[FAILED] Futures VWAP+MACD",
        "paper_futures_vwap_macd_trades.csv",
        "paper_futures_vwap_macd_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "mes_ema_scalp",
        "[FAILED] MES EMA scalp",
        "paper_mes_ema_scalp_trades.csv",
        "paper_mes_ema_scalp_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "mes_orb_30m",
        "[FAILED] MES ORB 30m",
        "paper_mes_orb_30m_trades.csv",
        "paper_mes_orb_30m_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "binance_ema_scalp",
        "[FAILED] Binance EMA scalp",
        "paper_binance_ema_scalp_live_trades.csv",
        "paper_binance_ema_scalp_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "binance_spot_momentum",
        "Binance Spot Momentum",
        "paper_binance_spot_momentum_scanner_trades.csv",
        "paper_binance_spot_momentum_scanner_live.log",
    ),
    StrategySource(
        "copy_wallet_observer",
        "[FAILED] Copy carteira observer",
        "paper_copy_wallet_trades.csv",
        "paper_copy_wallet_observer.log",
        "FAILED TEST",
    ),
    StrategySource(
        "late_lottery",
        "Late lottery 1-10c",
        "paper_late_lottery_trades.csv",
        "paper_late_lottery_live.log",
    ),
    StrategySource(
        "late_lottery_0_3c_60s",
        "[FAILED] Late lottery 0-3c 60s",
        "paper_late_lottery_0_3c_60s_trades.csv",
        "paper_late_lottery_0_3c_60s_live.log",
        "FAILED TEST",
    ),
    StrategySource(
        "late_lottery_5_10c_down_60s",
        "Late lottery 5-10c DOWN 60s",
        "paper_late_lottery_5_10c_down_60s_trades.csv",
        "paper_late_lottery_5_10c_down_60s_live.log",
    ),
    StrategySource(
        "mt5_us500_base",
        "MT5 US500 base",
        "mt5_us500_demo_trades.csv",
        "mt5_us500_demo_bot.out.log",
        "MT5 DEMO",
    ),
    StrategySource(
        "mt5_us500_candidate",
        "MT5 US500 candidato",
        "mt5_us500_demo_candidate_trades.csv",
        "mt5_us500_demo_candidate_bot.out.log",
        "MT5 DEMO",
    ),
    StrategySource("real_edge3", "Real Edge 3", "real_edge3_polymarket_5m_trades.csv", "real_edge3_polymarket_5m_live.log", "REAL"),
    StrategySource(
        "real_first_minute",
        "Real Primeiro minuto",
        "real_first_minute_polymarket_5m_trades.csv",
        "real_first_minute_polymarket_5m_live.log",
        "REAL",
    ),
    StrategySource(
        "real_ema_1s_trend",
        "Real EMA 1s trend",
        "real_ema_1s_trend_polymarket_5m_trades.csv",
        "real_ema_1s_trend_polymarket_5m_live.log",
        "REAL",
    ),
    StrategySource(
        "real_poly_odds_momentum_60s",
        "Real Poly odds 60s",
        "real_poly_odds_momentum_60s_trades.csv",
        "real_poly_odds_momentum_60s_main_live.log",
        "REAL",
    ),
]


def filter_sources_by_labels(labels: list[str]) -> list[StrategySource]:
    allowed = set(labels)
    return [source for source in SOURCES if source.label in allowed]


def is_failed_source(source: StrategySource) -> bool:
    return source.kind == "FAILED TEST" or source.label.startswith("[FAILED]")


def default_visible_labels(show_failed: bool = False) -> list[str]:
    return [
        source.label
        for source in SOURCES
        if show_failed or not is_failed_source(source)
    ]


def merge_visible_strategy_labels(
    current_labels: list[object] | tuple[object, ...] | None,
    saved_labels: list[object] | tuple[object, ...] | None,
    available_labels: list[str],
) -> list[str]:
    normalized_current = [str(label) for label in (current_labels or []) if str(label) in available_labels]
    normalized_saved = [str(label) for label in (saved_labels or []) if str(label) in available_labels]
    merged_labels = list(dict.fromkeys(normalized_current + normalized_saved))
    return merged_labels or available_labels


def load_dashboard_prefs(path: Path = DASHBOARD_PREFS_PATH) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_dashboard_prefs(
    visible_labels: list[str],
    show_failed: bool,
    summary_columns: list[str] | None = None,
    recent_columns: list[str] | None = None,
    path: Path = DASHBOARD_PREFS_PATH,
) -> None:
    prefs = {
        "visible_labels": visible_labels,
        "show_failed": show_failed,
    }
    if summary_columns is not None:
        prefs["summary_columns"] = summary_columns
    if recent_columns is not None:
        prefs["recent_columns"] = recent_columns
    try:
        path.write_text(json.dumps(prefs, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        return


def normalize_visible_columns(
    selected: object,
    available_columns: list[str],
    default_columns: list[str] | None = None,
) -> list[str]:
    default_columns = default_columns or available_columns
    if not isinstance(selected, list):
        selected = default_columns
    normalized = [str(column) for column in selected if str(column) in available_columns]
    if normalized:
        return normalized
    return [column for column in default_columns if column in available_columns] or available_columns


def dataframe_visible_columns(
    df: pd.DataFrame,
    selected_columns: list[str],
    fallback_columns: list[str],
) -> list[str]:
    available = [column for column in selected_columns if column in df.columns]
    if available:
        return available
    return [column for column in fallback_columns if column in df.columns]


def filter_trades_by_sources(df: pd.DataFrame, sources: list[StrategySource]) -> pd.DataFrame:
    if df.empty or "strategy_key" not in df.columns:
        return df
    keys = {source.key for source in sources}
    if not keys:
        return df.iloc[0:0].copy()
    return df[df["strategy_key"].isin(keys)].copy()


def split_sources_by_status(sources: list[StrategySource]) -> tuple[list[StrategySource], list[StrategySource]]:
    active = [source for source in sources if not is_failed_source(source)]
    failed = [source for source in sources if is_failed_source(source)]
    return active, failed


def match_source_by_command(command_line: str) -> StrategySource | None:
    for source in SOURCES:
        if source.csv_name in command_line:
            return source
    for key, patterns in SOURCE_COMMAND_PATTERNS.items():
        if all(pattern in command_line for pattern in patterns):
            return next((source for source in SOURCES if source.key == key), None)
    return None


def append_monitored_local_status_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        frame = pd.DataFrame(columns=["PID", "Estrategia", "Modo", "Comando", "Status"])
    elif "Status" not in frame.columns:
        frame = frame.copy()
        frame["Status"] = "RUNNING"

    local_labels = set(
        frame.loc[~frame["Modo"].astype(str).str.contains("REMOTO", na=False), "Estrategia"].astype(str)
    )
    down_rows = []
    for key in LOCAL_MONITORED_SOURCE_KEYS:
        source = next((item for item in SOURCES if item.key == key), None)
        if source is None or source.label in local_labels:
            continue
        down_rows.append(
            {
                "PID": "-",
                "Estrategia": source.label,
                "Modo": source.kind,
                "Comando": "",
                "Status": "DOWN",
            }
        )
    if down_rows:
        frame = pd.concat([frame, pd.DataFrame(down_rows)], ignore_index=True)
    return frame

REFRESH_OPTIONS = ["desligada", "10s", "30s", "60s"]
TRADE_COLUMNS = [
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
]
MATRIX_DISPLAY_COLUMNS = [
    "strategy",
    "edge_min",
    "trades",
    "win_rate_pct",
    "pnl_usdc",
    "avg_roi_pct",
    "max_drawdown_usdc",
    "min_abs_z",
    "volume_multiplier",
    "max_contract_price",
    "entry_offsets",
    "entry_slippage",
    "min_anchor_body_pct",
    "max_retrace_pct",
    "min_target_distance_pct",
]
RECENT_TRADE_COLUMNS = [
    "entry_local",
    "strategy",
    "source_kind",
    "symbol",
    "direction",
    "entry_price",
    "exit_price",
    "exit_reason",
    "contract_price",
    "model_probability",
    "edge",
    "win",
    "pnl_usdc",
    "order_status",
]


def query_param_value(name: str, default: str) -> str:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value)


def refresh_run_every(refresh: str) -> str | None:
    if refresh == "desligada":
        return None
    return refresh if refresh in REFRESH_OPTIONS else "10s"


def local_time_label(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%d/%m %H:%M:%S")


def remote_real_config() -> dict[str, str] | None:
    config = {
        "host": os.getenv("REMOTE_REAL_SSH_HOST", "").strip(),
        "user": os.getenv("REMOTE_REAL_SSH_USER", "root").strip(),
        "password": os.getenv("REMOTE_REAL_SSH_PASSWORD", "").strip(),
        "hostkey": os.getenv("REMOTE_REAL_SSH_HOSTKEY", "").strip(),
    }
    if all(config.values()):
        return config
    return None


def remote_real_openssh_config() -> dict[str, str] | None:
    key_path = Path(os.getenv("REMOTE_REAL_SSH_KEY", str(DEFAULT_REMOTE_REAL_SSH_KEY))).expanduser()
    config = {
        "host": os.getenv("REMOTE_REAL_SSH_HOST", DEFAULT_REMOTE_REAL_SSH_HOST).strip(),
        "user": os.getenv("REMOTE_REAL_SSH_USER", "root").strip(),
        "key": str(key_path),
    }
    if config["host"] and config["user"] and key_path.exists():
        return config
    return None


def openssh_executable() -> str | None:
    if GIT_SSH_PATH.exists():
        return str(GIT_SSH_PATH)
    return shutil.which("ssh")


def run_remote_command(command: str, timeout: int = 12) -> str | None:
    config = remote_real_config()
    if config and PLINK_PATH.exists():
        try:
            result = subprocess.run(
                [
                    str(PLINK_PATH),
                    "-ssh",
                    "-batch",
                    "-hostkey",
                    config["hostkey"],
                    "-pw",
                    config["password"],
                    f'{config["user"]}@{config["host"]}',
                    command,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            return result.stdout

    ssh_config = remote_real_openssh_config()
    ssh_path = openssh_executable()
    if not ssh_config or not ssh_path:
        return None
    try:
        result = subprocess.run(
            [
                ssh_path,
                "-i",
                ssh_config["key"],
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                f'{ssh_config["user"]}@{ssh_config["host"]}',
                command,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def remote_csv_candidates(source: StrategySource) -> list[str]:
    candidates = [f"{REMOTE_REAL_DIR}/{source.csv_name}"]
    if source.key == "real_edge3":
        candidates.append(REMOTE_REAL_CSV_PATH)
    return candidates


def remote_log_candidates(source: StrategySource) -> list[str]:
    candidates = [f"{REMOTE_REAL_DIR}/{source.log_name}"]
    if source.key == "real_edge3":
        candidates.append(REMOTE_REAL_LOG_PATH)
    if source.key == "real_poly_odds_momentum_60s":
        candidates.append(f"{REMOTE_REAL_DIR}/real_poly_odds_momentum_60s_live.log")
    return candidates


def read_trades_from_rows(rows: list[list[str]], header: list[str], source: StrategySource) -> pd.DataFrame:
    try:
        parsed_rows = []
        for raw_row in rows:
            if not raw_row or not any(raw_row):
                continue
            can_expand_legacy_trade = (
                len(raw_row) > len(header)
                and len(raw_row) <= len(TRADE_COLUMNS)
                and header == TRADE_COLUMNS[: len(header)]
            )
            columns = TRADE_COLUMNS if can_expand_legacy_trade else header
            row = {name: raw_row[index] if index < len(raw_row) else "" for index, name in enumerate(columns)}
            if len(raw_row) > len(columns):
                row["_extra"] = "|".join(raw_row[len(columns) :])
            parsed_rows.append(row)
        df = pd.DataFrame(parsed_rows)
    except (OSError, csv.Error):
        return pd.DataFrame()

    if df.empty:
        return df

    df["strategy_key"] = source.key
    df["strategy"] = source.label
    df["source_kind"] = source.kind
    if source.key == "copy_wallet_observer":
        if "copy_decision" in df.columns:
            df = df[df["copy_decision"].astype(str).str.upper().eq("COPY")].copy()
        if df.empty:
            return df
        if "outcome" in df.columns:
            df["direction"] = df["outcome"]
        if "current_buy_price" in df.columns:
            df["contract_price"] = df["current_buy_price"]
        if "simulated_stake_usdc" in df.columns:
            df["stake_usdc"] = df["simulated_stake_usdc"]
        if "copy_decision" in df.columns:
            reason = df["reason"].astype(str) if "reason" in df.columns else ""
            df["order_status"] = (df["copy_decision"].astype(str) + " " + reason).str.strip()
        if "observed_time_utc" in df.columns:
            df["entry_time_utc"] = df["observed_time_utc"]

    for column in [
        "contract_price",
        "model_probability",
        "edge",
        "stake_usdc",
        "pnl_usdc",
        "trader_price",
        "current_buy_price",
        "price_diff",
        "lag_seconds",
        "entry_price",
        "exit_price",
        "notional_usdc",
        "quantity",
        "gross_pnl_usdc",
        "fees_usdc",
        "gross_pnl_usd",
        "costs_usd",
        "pnl_usd",
        "pnl_pct_notional",
    ]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if "pnl_usdc" not in df.columns and "pnl_usd" in df.columns:
        df["pnl_usdc"] = df["pnl_usd"]
    if "win" in df.columns:
        df["win_bool"] = df["win"].astype(str).str.lower().eq("true")
    else:
        df["win_bool"] = False
    if "entry_time_utc" in df.columns:
        df["entry_dt"] = pd.to_datetime(df["entry_time_utc"], errors="coerce", utc=True)
        df["entry_local"] = df["entry_dt"].dt.tz_convert(LOCAL_TZ).dt.strftime("%d/%m %H:%M:%S")
    elif "entry_ts" in df.columns:
        df["entry_dt"] = pd.to_datetime(pd.to_numeric(df["entry_ts"], errors="coerce"), unit="s", errors="coerce", utc=True)
        df["entry_local"] = df["entry_dt"].dt.tz_convert(LOCAL_TZ).dt.strftime("%d/%m %H:%M:%S")
    return df


def read_trades(source: StrategySource) -> pd.DataFrame:
    if source.kind == "REAL":
        for remote_path in remote_csv_candidates(source):
            remote_content = run_remote_command(f"cat {remote_path}", timeout=45)
            if remote_content and remote_content.strip():
                reader = csv.reader(StringIO(remote_content))
                header = next(reader, [])
                if header:
                    return read_trades_from_rows(list(reader), header, source)
        return pd.DataFrame()

    path = ROOT / source.csv_name
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if not header:
            return pd.DataFrame()
        return read_trades_from_rows(list(reader), header, source)


def load_all_trades() -> pd.DataFrame:
    frames = [read_trades(source) for source in SOURCES]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def load_matrix_summary() -> pd.DataFrame:
    if not MATRIX_SUMMARY_PATH.exists() or MATRIX_SUMMARY_PATH.stat().st_size == 0:
        return pd.DataFrame()
    try:
        summary = pd.read_csv(MATRIX_SUMMARY_PATH)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
    if summary.empty:
        return summary

    for column in [
        "edge_min",
        "trades",
        "unique_signals",
        "win_rate_pct",
        "pnl_usdc",
        "avg_roi_pct",
        "max_drawdown_usdc",
        "brier",
        "stake_usdc",
        "min_abs_z",
        "volume_multiplier",
        "max_contract_price",
        "entry_slippage",
        "min_anchor_body_pct",
        "max_retrace_pct",
        "min_target_distance_pct",
    ]:
        if column in summary.columns:
            summary[column] = pd.to_numeric(summary[column], errors="coerce")

    sort_columns = [column for column in ["pnl_usdc", "win_rate_pct", "trades"] if column in summary.columns]
    if sort_columns:
        summary = summary.sort_values(sort_columns, ascending=[False] * len(sort_columns))

    display_columns = [column for column in MATRIX_DISPLAY_COLUMNS if column in summary.columns]
    remaining = [column for column in summary.columns if column not in display_columns]
    return summary[display_columns + remaining].reset_index(drop=True)


def summarize(df: pd.DataFrame, sources: list[StrategySource] | None = None) -> pd.DataFrame:
    if sources is None:
        sources = SOURCES
    rows = []
    for source in sources:
        item = df[df["strategy_key"] == source.key] if not df.empty and "strategy_key" in df.columns else pd.DataFrame()
        trades = len(item)
        is_observer = source.key == "copy_wallet_observer"
        if is_observer and trades and "status" in item.columns:
            closed_item = item[item["status"].astype(str).str.upper().eq("CLOSED")]
            closed_trades = len(closed_item)
            wins = int(closed_item["win_bool"].sum()) if "win_bool" in closed_item.columns else 0
            losses = closed_trades - wins
            pnl = float(closed_item["pnl_usdc"].fillna(0).sum()) if "pnl_usdc" in closed_item.columns else 0.0
            win_rate_label = f"{(wins / closed_trades * 100):.2f}%" if closed_trades else "-"
        else:
            wins = int(item["win_bool"].sum()) if trades and "win_bool" in item.columns else 0
            losses = trades - wins
            pnl = float(item["pnl_usdc"].fillna(0).sum()) if trades and "pnl_usdc" in item.columns else 0.0
            win_rate_label = f"{(wins / trades * 100):.2f}%" if trades else "-"
        avg = pnl / trades if trades else 0.0
        last_trade = ""
        if trades and "entry_dt" in item.columns:
            last_dt = item["entry_dt"].dropna().max()
            if pd.notna(last_dt):
                last_trade = last_dt.to_pydatetime().astimezone(LOCAL_TZ).strftime("%d/%m %H:%M")

        rows.append(
            {
                "Estrategia": source.label,
                "Tipo": source.kind,
                "Trades": trades,
                "Wins": wins,
                "Losses": losses,
                "Win rate": win_rate_label,
                "PnL": round(pnl, 4),
                "Media/trade": round(avg, 4),
                "Ultimo trade": last_trade or "-",
                "CSV": source.csv_name,
            }
        )
    return pd.DataFrame(rows)


def cumulative_pnl(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "entry_dt" not in df.columns:
        return pd.DataFrame()
    chart = df.dropna(subset=["entry_dt"]).copy()
    if chart.empty:
        return pd.DataFrame()
    chart = chart.sort_values("entry_dt")
    chart["pnl_usdc"] = chart["pnl_usdc"].fillna(0.0)
    chart["cumulative_pnl"] = chart.groupby("strategy")["pnl_usdc"].cumsum()
    return chart.pivot_table(
        index="entry_dt",
        columns="strategy",
        values="cumulative_pnl",
        aggfunc="last",
    ).ffill()


def get_bot_processes() -> pd.DataFrame:
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*paper_polymarket_5m_live.py*' -or "
        "$_.CommandLine -like '*paper_mes_ema_scalp_live.py*' -or "
        "$_.CommandLine -like '*paper_mes_orb_live.py*' -or "
        "$_.CommandLine -like '*paper_futures_vwap_macd_live.py*' -or "
        "$_.CommandLine -like '*paper_binance_ema_scalp_live.py*' -or "
        "$_.CommandLine -like '*paper_binance_spot_momentum_scanner.py*' -or "
        "$_.CommandLine -like '*mt5_us500_demo_bot.py*' -or "
        "$_.CommandLine -like '*paper_polymarket_copy_observer.py*' -or "
        "$_.CommandLine -like '*paper_polymarket_late_lottery.py*' } | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    for shell in ("pwsh", "powershell"):
        try:
            result = subprocess.run(
                [shell, "-NoProfile", "-Command", command],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue

        raw = json.loads(result.stdout)
        rows = raw if isinstance(raw, list) else [raw]
        normalized = []
        for row in rows:
            command_line = str(row.get("CommandLine") or "")
            if "ConvertTo-Json" in command_line:
                continue
            source = match_source_by_command(command_line)
            normalized.append(
                {
                    "PID": row.get("ProcessId"),
                    "Estrategia": source.label if source else "-",
                    "Modo": "REAL" if " --real " in f" {command_line} " else "PAPER",
                    "Comando": command_line,
                    "Status": "RUNNING",
                }
            )
        frame = pd.DataFrame(normalized)
        remote_rows = get_remote_processes()
        if remote_rows:
            frame = pd.concat([frame, pd.DataFrame(remote_rows)], ignore_index=True)
        return append_monitored_local_status_rows(frame)
    remote_rows = get_remote_processes()
    remote_frame = pd.DataFrame(remote_rows) if remote_rows else pd.DataFrame()
    return append_monitored_local_status_rows(remote_frame)


def get_remote_processes() -> list[dict[str, str]]:
    output = run_remote_command(REMOTE_PROCESS_PGREP, timeout=8)
    if not output:
        return []
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, command = line.partition(" ")
        source = match_source_by_command(command)
        is_real = " --real " in f" {command} "
        rows.append(
            {
                "PID": pid,
                "Estrategia": source.label if source else ("Real" if is_real else "-"),
                "Modo": "REAL REMOTO" if is_real else "PAPER REMOTO",
                "Comando": command.strip(),
                "Status": "RUNNING",
            }
        )
    return rows


def tail_file(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return "Arquivo ainda nao existe."
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Erro lendo log: {exc}"
    return "\n".join(content[-lines:]) if content else "Log vazio."


def tail_source_log(source: StrategySource, lines: int = 80) -> str:
    if source.kind == "REAL":
        for remote_path in remote_log_candidates(source):
            output = run_remote_command(f"tail -n {lines} {remote_path}", timeout=12)
            if output is not None:
                return output.strip() or "Log remoto vazio."
        return "Log remoto ainda nao existe."
    return tail_file(ROOT / source.log_name, lines=lines)


def render_metric_row(df: pd.DataFrame) -> None:
    total_trades = len(df)
    total_wins = int(df["win_bool"].sum()) if total_trades and "win_bool" in df.columns else 0
    total_pnl = float(df["pnl_usdc"].fillna(0).sum()) if total_trades and "pnl_usdc" in df.columns else 0.0
    win_rate = (total_wins / total_trades * 100) if total_trades else 0.0

    metrics = [
        ("Trades fechados", total_trades),
        ("Win rate geral", f"{win_rate:.2f}%"),
        ("PnL simulado/real", f"{total_pnl:.2f}"),
        ("Processos vivos", len(get_bot_processes())),
    ]
    for offset in range(0, len(metrics), METRIC_COLUMNS_PER_ROW):
        cols = st.columns(METRIC_COLUMNS_PER_ROW)
        for col, (label, value) in zip(cols, metrics[offset : offset + METRIC_COLUMNS_PER_ROW]):
            col.metric(label, value)


def render_matrix_summary() -> None:
    st.subheader("Matrix backtests")
    summary = load_matrix_summary()
    if summary.empty:
        st.info("Ainda nao ha resumo da matriz. Rode backtest_polymarket_5m_matrix.py para gerar o CSV.")
        return

    modified = datetime.fromtimestamp(MATRIX_SUMMARY_PATH.stat().st_mtime, tz=timezone.utc)
    st.caption(
        f"Fonte: {MATRIX_SUMMARY_PATH.name} | gerado em "
        f"{modified.astimezone(LOCAL_TZ).strftime('%d/%m %H:%M:%S')}"
    )
    st.dataframe(summary.head(30).round(4), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Polymarket BTC 5m", layout="wide")
    st.markdown(
        """
        <style>
        html, body, .stApp, [data-testid="stAppViewContainer"] {
            background: #0b0f19;
            color: #e5e7eb;
        }
        [data-testid="stHeader"] {
            background: rgba(11, 15, 25, 0.92);
        }
        [data-testid="stSidebar"] {
            background: #181b26;
        }
        [data-testid="stSidebar"] * {
            color: #f3f4f6;
        }
        .block-container {
            padding-top: 1.2rem;
            max-width: 100%;
            overflow-x: hidden;
        }
        div[data-testid="stMetric"] {
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 12px 14px;
            background: rgba(255, 255, 255, 0.03);
            box-shadow: none;
        }
        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #f9fafb;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        div[data-testid="stDataFrame"] {
            max-width: 100%;
            overflow-x: auto;
        }
        div[data-testid="stSidebar"] div[data-baseweb="tag"] {
            max-width: 100%;
        }
        div[data-testid="stSidebar"] div[data-baseweb="tag"] span {
            overflow: hidden;
            text-overflow: ellipsis;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Polymarket BTC Up/Down 5m")

    saved_refresh = query_param_value("refresh", "10s")
    if saved_refresh not in REFRESH_OPTIONS:
        saved_refresh = "10s"

    prefs = load_dashboard_prefs()
    saved_show_failed = bool(prefs.get("show_failed", False))
    if "show_failed_sources" not in st.session_state:
        st.session_state["show_failed_sources"] = saved_show_failed

    refresh = st.sidebar.selectbox(
        "Atualizacao automatica",
        REFRESH_OPTIONS,
        index=REFRESH_OPTIONS.index(saved_refresh),
        key="refresh_interval",
    )
    show_failed = st.sidebar.checkbox(
        "Mostrar descartadas",
        key="show_failed_sources",
    )
    available_labels = default_visible_labels(show_failed=show_failed)
    saved_visible_labels = prefs.get("visible_labels")
    if not isinstance(saved_visible_labels, list):
        saved_visible_labels = available_labels
    normalized_saved_labels = [str(label) for label in saved_visible_labels if str(label) in available_labels]
    if not normalized_saved_labels:
        normalized_saved_labels = available_labels
    if "visible_strategy_labels" not in st.session_state:
        st.session_state["visible_strategy_labels"] = normalized_saved_labels
    else:
        current = st.session_state.get("visible_strategy_labels", [])
        if not isinstance(current, list):
            current = []
        st.session_state["visible_strategy_labels"] = merge_visible_strategy_labels(
            current,
            normalized_saved_labels,
            available_labels,
        )

    visible_labels = st.sidebar.multiselect(
        "Estrategias visiveis",
        available_labels,
        key="visible_strategy_labels",
    )
    trades = load_all_trades()
    recent_available_columns = [column for column in RECENT_TRADE_COLUMNS if column in trades.columns] or RECENT_TRADE_COLUMNS
    with st.sidebar.expander("Colunas visiveis", expanded=False):
        summary_columns = st.multiselect(
            "Resumo",
            SUMMARY_COLUMNS,
            default=normalize_visible_columns(prefs.get("summary_columns"), SUMMARY_COLUMNS),
            key="summary_visible_columns",
        )
        recent_columns = st.multiselect(
            "Ultimos trades",
            recent_available_columns,
            default=normalize_visible_columns(prefs.get("recent_columns"), recent_available_columns),
            key="recent_visible_columns",
        )
    if query_param_value("refresh", "10s") != refresh:
        st.query_params["refresh"] = refresh

    @st.fragment(run_every=refresh_run_every(refresh))
    def render_dashboard_content() -> None:
        st.caption(f"Atualizado em {local_time_label()} | dashboard somente leitura")

        live_trades = load_all_trades()
        visible_sources = filter_sources_by_labels(visible_labels)
        active_sources, failed_sources = split_sources_by_status(visible_sources)
        active_trades = filter_trades_by_sources(live_trades, active_sources)
        failed_trades = filter_trades_by_sources(live_trades, failed_sources)

        save_dashboard_prefs(visible_labels, show_failed, summary_columns, recent_columns)
        render_metric_row(active_trades)

        st.subheader("Resumo por estrategia")
        summary = summarize(active_trades, sources=active_sources)
        st.dataframe(
            summary[dataframe_visible_columns(summary, summary_columns, SUMMARY_COLUMNS)],
            use_container_width=True,
            hide_index=True,
            height=SUMMARY_TABLE_HEIGHT,
        )

        chart = cumulative_pnl(active_trades)
        if not chart.empty:
            st.subheader("PnL acumulado")
            st.line_chart(chart)

        if failed_sources:
            with st.expander("Historico descartado", expanded=False):
                st.dataframe(
                    summarize(failed_trades, sources=failed_sources),
                    use_container_width=True,
                    hide_index=True,
                    height=min(360, SUMMARY_TABLE_HEIGHT),
                )

        render_matrix_summary()

        st.subheader("Processos")
        processes = get_bot_processes()
        if processes.empty:
            st.warning("Nenhum processo do robo encontrado.")
        else:
            st.dataframe(processes[["PID", "Estrategia", "Modo", "Status"]], use_container_width=True, hide_index=True)

        st.subheader("Ultimos trades")
        if active_trades.empty:
            st.info("Ainda nao ha trades fechados nos CSVs.")
        else:
            recent = active_trades.sort_values("entry_dt", ascending=False, na_position="last").head(RECENT_TRADE_LIMIT)
            available = dataframe_visible_columns(recent, recent_columns, RECENT_TRADE_COLUMNS)
            st.dataframe(recent[available], use_container_width=True, hide_index=True)

        st.subheader("Logs")
        labels = {source.label: source for source in active_sources}
        if not labels:
            st.info("Selecione pelo menos uma estrategia visivel para mostrar logs.")
        else:
            selected = st.selectbox("Escolha o log", list(labels.keys()))
            st.code(tail_source_log(labels[selected], lines=100), language="text")

    render_dashboard_content()


if __name__ == "__main__":
    main()
