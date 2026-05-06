import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_dashboard import (
    DASHBOARD_PREFS_PATH,
    SOURCES,
    SUMMARY_COLUMNS,
    append_monitored_local_status_rows,
    dataframe_visible_columns,
    default_visible_labels,
    filter_sources_by_labels,
    get_remote_processes,
    is_failed_source,
    load_dashboard_prefs,
    merge_visible_strategy_labels,
    match_source_by_command,
    METRIC_COLUMNS_PER_ROW,
    normalize_visible_columns,
    RECENT_TRADE_COLUMNS,
    refresh_run_every,
    save_dashboard_prefs,
    SUMMARY_TABLE_HEIGHT,
    split_sources_by_status,
    StrategySource,
    load_matrix_summary,
    read_trades_from_rows,
    summarize,
)


def test_summary_table_height_is_tall_enough_for_dashboard():
    assert SUMMARY_TABLE_HEIGHT >= 700


def test_metric_row_uses_responsive_column_count():
    assert METRIC_COLUMNS_PER_ROW <= 2


def test_summary_columns_are_declared_for_sidebar_control():
    assert SUMMARY_COLUMNS[:3] == ["Estrategia", "Tipo", "Trades"]
    assert "CSV" in SUMMARY_COLUMNS


def test_visible_column_helpers_keep_valid_selection_and_fallback():
    available = ["A", "B", "C"]

    assert normalize_visible_columns(["B", "X"], available) == ["B"]
    assert normalize_visible_columns([], available, default_columns=["A", "C"]) == ["A", "C"]

    df = pd.DataFrame([{"A": 1, "B": 2}])
    assert dataframe_visible_columns(df, ["C"], ["A", "B"]) == ["A", "B"]
    assert dataframe_visible_columns(df, ["B", "A"], ["A"]) == ["B", "A"]


def test_refresh_run_every_normalizes_supported_values():
    assert refresh_run_every("desligada") is None
    assert refresh_run_every("10s") == "10s"
    assert refresh_run_every("30s") == "30s"
    assert refresh_run_every("invalido") == "10s"


def test_filter_sources_by_labels_keeps_declared_order():
    filtered = filter_sources_by_labels(["[FAILED] EMA 1s valor", "Edge 3% valor"])

    assert [source.label for source in filtered] == ["Edge 3% valor", "[FAILED] EMA 1s valor"]


def test_default_visible_labels_hides_failed_sources_by_default():
    labels = default_visible_labels()

    assert "Edge 3%" in labels
    assert "Edge 3% max 0.80" in labels
    assert "Primeiro minuto valor" in labels
    assert "MT5 US500 base" in labels
    assert "MT5 US500 candidato" in labels
    assert "[FAILED] Edge 4%" not in labels
    assert "[FAILED] Primeiro minuto" not in labels
    assert "[FAILED] Late window" not in labels


def test_default_visible_labels_can_include_failed_sources():
    labels = default_visible_labels(show_failed=True)

    assert "Edge 3%" in labels
    assert "Edge 3% sem 10h" in labels
    assert "[FAILED] Edge 4%" in labels
    assert "[FAILED] Late window" in labels


def test_merge_visible_strategy_labels_adds_new_saved_labels_without_dropping_current():
    available = [
        "Edge 3%",
        "Edge 3% max 0.80",
        "Edge 3% sem 10h",
        "Primeiro minuto valor",
    ]

    merged = merge_visible_strategy_labels(
        ["Edge 3%", "Primeiro minuto valor"],
        ["Edge 3%", "Edge 3% max 0.80", "Edge 3% sem 10h"],
        available,
    )

    assert merged == [
        "Edge 3%",
        "Primeiro minuto valor",
        "Edge 3% max 0.80",
        "Edge 3% sem 10h",
    ]


def test_is_failed_source_uses_kind_and_label():
    active = StrategySource("edge_3", "Edge 3%", "a.csv", "a.log")
    failed_by_kind = StrategySource("edge_4", "Edge 4%", "b.csv", "b.log", "FAILED TEST")
    failed_by_label = StrategySource("edge_6", "[FAILED] Edge 6%", "c.csv", "c.log", "PAPER")

    assert is_failed_source(active) == False
    assert is_failed_source(failed_by_kind) == True
    assert is_failed_source(failed_by_label) == True


def test_split_sources_by_status_keeps_declared_order():
    sources = [
        StrategySource("edge_4", "[FAILED] Edge 4%", "a.csv", "a.log", "FAILED TEST"),
        StrategySource("edge_3", "Edge 3%", "b.csv", "b.log"),
        StrategySource("edge_6", "[FAILED] Edge 6%", "c.csv", "c.log", "FAILED TEST"),
        StrategySource("ema", "[FAILED] EMA 1s valor", "d.csv", "d.log", "FAILED TEST"),
        StrategySource("first_minute_value", "Primeiro minuto valor", "e.csv", "e.log"),
    ]

    active, failed = split_sources_by_status(sources)

    assert [source.label for source in active] == ["Edge 3%", "Primeiro minuto valor"]
    assert [source.label for source in failed] == ["[FAILED] Edge 4%", "[FAILED] Edge 6%", "[FAILED] EMA 1s valor"]


def test_dashboard_prefs_round_trip(tmp_path):
    prefs_path = tmp_path / DASHBOARD_PREFS_PATH.name

    save_dashboard_prefs(["Edge 3%", "EMA 1s valor"], True, path=prefs_path)
    prefs = load_dashboard_prefs(path=prefs_path)

    assert prefs == {
        "visible_labels": ["Edge 3%", "EMA 1s valor"],
        "show_failed": True,
    }


def test_dashboard_prefs_can_persist_visible_columns(tmp_path):
    prefs_path = tmp_path / DASHBOARD_PREFS_PATH.name

    save_dashboard_prefs(
        ["Edge 3%"],
        False,
        summary_columns=["Estrategia", "PnL"],
        recent_columns=["entry_local", "pnl_usdc"],
        path=prefs_path,
    )
    prefs = load_dashboard_prefs(path=prefs_path)

    assert prefs["summary_columns"] == ["Estrategia", "PnL"]
    assert prefs["recent_columns"] == ["entry_local", "pnl_usdc"]


def test_dashboard_prefs_invalid_json_returns_empty(tmp_path):
    prefs_path = tmp_path / DASHBOARD_PREFS_PATH.name
    prefs_path.write_text("{ invalido", encoding="utf-8")

    prefs = load_dashboard_prefs(path=prefs_path)

    assert prefs == {}


def test_read_trades_from_rows_keeps_malformed_extra_columns():
    source = StrategySource("edge_6", "Edge 6%", "trades.csv", "bot.log")
    header = ["direction", "contract_price", "win", "pnl_usdc"]
    rows = [["UP", "0.65", "true", "5.38", "accepted", "extra"]]

    df = read_trades_from_rows(rows, header, source)

    assert len(df) == 1
    assert df.loc[0, "strategy"] == "Edge 6%"
    assert df.loc[0, "win_bool"] == True
    assert df.loc[0, "pnl_usdc"] == 5.38
    assert df.loc[0, "_extra"] == "accepted|extra"


def test_futures_vwap_macd_source_is_registered():
    source = next(item for item in SOURCES if item.key == "futures_vwap_macd")

    assert source.label == "[FAILED] Futures VWAP+MACD"
    assert source.csv_name == "paper_futures_vwap_macd_trades.csv"
    assert source.kind == "FAILED TEST"


def test_mes_ema_scalp_source_is_registered():
    source = next(item for item in SOURCES if item.key == "mes_ema_scalp")

    assert source.label == "[FAILED] MES EMA scalp"
    assert source.csv_name == "paper_mes_ema_scalp_trades.csv"
    assert source.log_name == "paper_mes_ema_scalp_live.log"
    assert source.kind == "FAILED TEST"


def test_mes_orb_30m_source_is_registered():
    source = next(item for item in SOURCES if item.key == "mes_orb_30m")

    assert source.label == "[FAILED] MES ORB 30m"
    assert source.csv_name == "paper_mes_orb_30m_trades.csv"
    assert source.log_name == "paper_mes_orb_30m_live.log"
    assert source.kind == "FAILED TEST"


def test_edge_variants_marked_failed_when_deprecated():
    edge4 = next(item for item in SOURCES if item.key == "edge_4")
    edge6 = next(item for item in SOURCES if item.key == "edge_6")
    edge10 = next(item for item in SOURCES if item.key == "edge_10")
    late_window = next(item for item in SOURCES if item.key == "late_window")
    target_rejection = next(item for item in SOURCES if item.key == "target_rejection")

    assert edge4.label == "[FAILED] Edge 4%"
    assert edge4.kind == "FAILED TEST"
    assert edge6.label == "[FAILED] Edge 6%"
    assert edge6.kind == "FAILED TEST"
    assert edge10.label == "[FAILED] Edge 10%"
    assert edge10.kind == "FAILED TEST"
    assert late_window.label == "[FAILED] Late window"
    assert late_window.kind == "FAILED TEST"
    assert target_rejection.label == "[FAILED] Rejeicao do alvo"
    assert target_rejection.kind == "FAILED TEST"


def test_edge3_candidate_variants_are_registered_as_active_papers():
    expected = {
        "edge3_max80": (
            "Edge 3% max 0.80",
            "paper_edge3_max80_polymarket_5m_trades.csv",
            "paper_edge3_max80_polymarket_5m_live.log",
        ),
        "edge3_no10h": (
            "Edge 3% sem 10h",
            "paper_edge3_no10h_polymarket_5m_trades.csv",
            "paper_edge3_no10h_polymarket_5m_live.log",
        ),
        "edge3_max80_no10h": (
            "Edge 3% max 0.80 sem 10h",
            "paper_edge3_max80_no10h_polymarket_5m_trades.csv",
            "paper_edge3_max80_no10h_polymarket_5m_live.log",
        ),
        "edge3_down_only": (
            "Edge 3% DOWN only",
            "paper_edge3_down_only_polymarket_5m_trades.csv",
            "paper_edge3_down_only_polymarket_5m_live.log",
        ),
        "edge3_limit55": (
            "Edge 3% limit 0.55",
            "paper_edge3_limit55_polymarket_5m_trades.csv",
            "paper_edge3_limit55_polymarket_5m_live.log",
        ),
        "edge3_limit60": (
            "Edge 3% limit 0.60",
            "paper_edge3_limit60_polymarket_5m_trades.csv",
            "paper_edge3_limit60_polymarket_5m_live.log",
        ),
        "edge3_eth": (
            "ETH Edge 3%",
            "paper_edge3_eth_polymarket_5m_trades.csv",
            "paper_edge3_eth_polymarket_5m_live.log",
        ),
        "poly_odds_momentum_60s": (
            "Poly odds momentum 60s",
            "paper_poly_odds_momentum_60s_trades.csv",
            "paper_poly_odds_momentum_60s_live.log",
        ),
        "poly_odds_momentum_90s": (
            "Poly odds momentum 90s",
            "paper_poly_odds_momentum_90s_trades.csv",
            "paper_poly_odds_momentum_90s_live.log",
        ),
    }

    for key, (label, csv_name, log_name) in expected.items():
        source = next(item for item in SOURCES if item.key == key)
        assert source.label == label
        assert source.csv_name == csv_name
        assert source.log_name == log_name
        assert source.kind == "PAPER"


def test_binance_ema_scalp_source_is_registered():
    source = next(item for item in SOURCES if item.key == "binance_ema_scalp")

    assert source.label == "Binance EMA scalp"
    assert source.csv_name == "paper_binance_ema_scalp_live_trades.csv"
    assert source.log_name == "paper_binance_ema_scalp_live.log"


def test_binance_spot_momentum_source_is_registered_for_trade_results():
    source = next(item for item in SOURCES if item.key == "binance_spot_momentum")

    assert source.label == "Binance Spot Momentum"
    assert source.csv_name == "paper_binance_spot_momentum_scanner_trades.csv"
    assert source.log_name == "paper_binance_spot_momentum_scanner_live.log"
    assert source.kind == "PAPER"


def test_edge_15m_strict_source_is_registered():
    source = next(item for item in SOURCES if item.key == "edge_15m_strict")

    assert source.label == "[FAILED] Edge 15m strict"
    assert source.csv_name == "paper_polymarket_15m_strict_trades.csv"
    assert source.log_name == "paper_polymarket_15m_strict_live.log"
    assert source.kind == "FAILED TEST"


def test_cheap_price_cap_sources_are_registered():
    edge5m = next(item for item in SOURCES if item.key == "edge3_40_65")
    edge15m = next(item for item in SOURCES if item.key == "edge_15m_40_65")

    assert edge5m.label == "[FAILED] Edge 3% 40-65"
    assert edge5m.csv_name == "paper_edge3_40_65_polymarket_5m_trades.csv"
    assert edge5m.kind == "FAILED TEST"
    assert edge15m.label == "[FAILED] Edge 15m 40-65"
    assert edge15m.csv_name == "paper_polymarket_15m_edge_40_65_trades.csv"
    assert edge15m.kind == "FAILED TEST"


def test_value_price_cap_sources_are_registered():
    edge = next(item for item in SOURCES if item.key == "edge3_value")
    first = next(item for item in SOURCES if item.key == "first_minute_value")
    ema = next(item for item in SOURCES if item.key == "ema_1s_value")

    assert edge.label == "Edge 3% valor"
    assert edge.csv_name == "paper_edge3_value_polymarket_5m_trades.csv"
    assert first.label == "Primeiro minuto valor"
    assert first.csv_name == "paper_first_minute_value_trades.csv"
    assert ema.label == "[FAILED] EMA 1s valor"
    assert ema.csv_name == "paper_ema_1s_value_trades.csv"
    assert ema.kind == "FAILED TEST"


def test_paused_paper_sources_are_marked_failed():
    keys = {
        "edge3_40_65",
        "edge_15m",
        "edge_15m_40_65",
        "edge_15m_strict",
        "first_minute",
        "ema_1s_value",
        "mes_ema_scalp",
        "mes_orb_30m",
    }

    for source in SOURCES:
        if source.key in keys:
            assert source.kind == "FAILED TEST"
            assert source.label.startswith("[FAILED]")


def test_removed_paper_sources_are_not_registered():
    removed_keys = {
        "momentum_confirmed",
        "momentum_confirmed_value",
        "bollinger_rsi_reversal",
    }
    removed_labels = {
        "Momentum confirmado",
        "Momentum confirmado valor",
        "Reversao DOWN",
    }

    assert removed_keys.isdisjoint({source.key for source in SOURCES})
    assert removed_labels.isdisjoint({source.label for source in SOURCES})


def test_copy_wallet_observer_source_is_registered():
    source = next(item for item in SOURCES if item.key == "copy_wallet_observer")

    assert source.label == "[FAILED] Copy carteira observer"
    assert source.csv_name == "paper_copy_wallet_trades.csv"
    assert source.kind == "FAILED TEST"


def test_late_lottery_source_is_registered():
    source = next(item for item in SOURCES if item.key == "late_lottery")

    assert source.label == "Late lottery 1-10c"
    assert source.csv_name == "paper_late_lottery_trades.csv"


def test_late_lottery_variant_sources_are_registered():
    source = next(item for item in SOURCES if item.key == "late_lottery_0_3c_60s")
    assert source.label == "[FAILED] Late lottery 0-3c 60s"
    assert source.csv_name == "paper_late_lottery_0_3c_60s_trades.csv"
    assert source.kind == "FAILED TEST"

    source = next(item for item in SOURCES if item.key == "late_lottery_5_10c_down_60s")
    assert source.label == "Late lottery 5-10c DOWN 60s"
    assert source.csv_name == "paper_late_lottery_5_10c_down_60s_trades.csv"


def test_ema_1s_trend_source_is_registered():
    source = next(item for item in SOURCES if item.key == "ema_1s_trend")

    assert source.label == "EMA 1s trend"
    assert source.csv_name == "paper_ema_1s_trend_trades.csv"


def test_failed_test_sources_are_marked_in_panel():
    mispricing = next(item for item in SOURCES if item.key == "mispricing_contrarian")
    rsi = next(item for item in SOURCES if item.key == "rsi_reversal_down")

    assert mispricing.kind == "FAILED TEST"
    assert rsi.kind == "FAILED TEST"
    assert mispricing.label.startswith("[FAILED]")
    assert rsi.label.startswith("[FAILED]")


def test_real_strategy_sources_are_registered_separately():
    edge = next(item for item in SOURCES if item.key == "real_edge3")
    first_minute = next(item for item in SOURCES if item.key == "real_first_minute")
    ema_trend = next(item for item in SOURCES if item.key == "real_ema_1s_trend")

    assert edge.label == "Real Edge 3"
    assert edge.csv_name == "real_edge3_polymarket_5m_trades.csv"
    assert edge.kind == "REAL"
    assert first_minute.label == "Real Primeiro minuto"
    assert first_minute.csv_name == "real_first_minute_polymarket_5m_trades.csv"
    assert first_minute.kind == "REAL"
    assert ema_trend.label == "Real EMA 1s trend"
    assert ema_trend.csv_name == "real_ema_1s_trend_polymarket_5m_trades.csv"
    assert ema_trend.kind == "REAL"


def test_mt5_demo_sources_are_registered_separately():
    base = next(item for item in SOURCES if item.key == "mt5_us500_base")
    candidate = next(item for item in SOURCES if item.key == "mt5_us500_candidate")

    assert base.label == "MT5 US500 base"
    assert base.csv_name == "mt5_us500_demo_trades.csv"
    assert base.log_name == "mt5_us500_demo_bot.out.log"
    assert base.kind == "MT5 DEMO"
    assert candidate.label == "MT5 US500 candidato"
    assert candidate.csv_name == "mt5_us500_demo_candidate_trades.csv"
    assert candidate.log_name == "mt5_us500_demo_candidate_bot.out.log"
    assert candidate.kind == "MT5 DEMO"


def test_match_source_by_command_uses_csv_name_and_real_fallback():
    edge = match_source_by_command("python paper_polymarket_5m_live.py --trades-csv paper_edge3_polymarket_5m_trades.csv")
    edge_max80 = match_source_by_command(
        "python paper_polymarket_5m_live.py --trades-csv paper_edge3_max80_polymarket_5m_trades.csv"
    )
    spot = match_source_by_command(
        "python paper_binance_spot_momentum_scanner.py --trades-csv paper_binance_spot_momentum_scanner_trades.csv"
    )
    mes_orb = match_source_by_command("python paper_mes_orb_live.py --trades-csv paper_mes_orb_30m_trades.csv")
    real = match_source_by_command("python paper_polymarket_5m_live.py --trades-csv real_polymarket_5m_trades.csv --real")
    mt5_base = match_source_by_command("python -u mt5_us500_demo_bot.py --execute")
    mt5_candidate = match_source_by_command(
        "python -u mt5_us500_demo_bot.py --execute --magic 505018 --comment codex-us500-demo-candidate"
    )

    assert edge is not None
    assert edge.label == "Edge 3%"
    assert edge_max80 is not None
    assert edge_max80.label == "Edge 3% max 0.80"
    assert spot is not None
    assert spot.label == "Binance Spot Momentum"
    assert mes_orb is not None
    assert mes_orb.label == "[FAILED] MES ORB 30m"
    assert real is not None
    assert real.label == "Real Edge 3"
    assert mt5_base is not None
    assert mt5_base.label == "MT5 US500 base"
    assert mt5_candidate is not None
    assert mt5_candidate.label == "MT5 US500 candidato"


def test_append_monitored_local_status_rows_marks_missing_local_bots_down():
    frame = pd.DataFrame(
        [
            {"PID": 123, "Estrategia": "Binance Spot Momentum", "Modo": "PAPER", "Comando": "python ...", "Status": "RUNNING"},
            {"PID": 456, "Estrategia": "Real Edge 3", "Modo": "REAL REMOTO", "Comando": "python ...", "Status": "RUNNING"},
        ]
    )

    result = append_monitored_local_status_rows(frame)

    status_by_label = {
        row["Estrategia"]: row["Status"]
        for row in result.to_dict(orient="records")
    }

    assert status_by_label["Binance Spot Momentum"] == "RUNNING"
    assert status_by_label["Binance EMA scalp"] == "DOWN"
    assert status_by_label["MT5 US500 base"] == "DOWN"
    assert status_by_label["MT5 US500 candidato"] == "DOWN"


def test_get_remote_processes_returns_paper_and_real_rows(monkeypatch):
    output = "\n".join(
        [
            "74188 venv/bin/python -u paper_polymarket_5m_live.py --trades-csv paper_edge3_value_polymarket_5m_trades.csv",
            "74189 venv/bin/python -u paper_polymarket_5m_live.py --trades-csv paper_edge3_no10h_polymarket_5m_trades.csv",
            "75859 venv/bin/python -u paper_mes_ema_scalp_live.py --trades-csv paper_mes_ema_scalp_trades.csv",
            "75860 venv/bin/python -u paper_mes_orb_live.py --trades-csv paper_mes_orb_30m_trades.csv",
            "75860 venv/bin/python -u paper_binance_spot_momentum_scanner.py --trades-csv paper_binance_spot_momentum_scanner_trades.csv",
            "67749 venv/bin/python -u paper_polymarket_5m_live.py --real --trades-csv real_edge3_polymarket_5m_trades.csv",
        ]
    )
    monkeypatch.setattr("streamlit_dashboard.run_remote_command", lambda command, timeout=8: output)

    rows = get_remote_processes()

    assert rows[0]["PID"] == "74188"
    assert rows[0]["Estrategia"] == "Edge 3% valor"
    assert rows[0]["Modo"] == "PAPER REMOTO"
    assert rows[1]["PID"] == "74189"
    assert rows[1]["Estrategia"] == "Edge 3% sem 10h"
    assert rows[1]["Modo"] == "PAPER REMOTO"
    assert rows[2]["PID"] == "75859"
    assert rows[2]["Estrategia"] == "[FAILED] MES EMA scalp"
    assert rows[2]["Modo"] == "PAPER REMOTO"
    assert rows[3]["PID"] == "75860"
    assert rows[3]["Estrategia"] == "[FAILED] MES ORB 30m"
    assert rows[3]["Modo"] == "PAPER REMOTO"
    assert rows[4]["Estrategia"] == "Binance Spot Momentum"
    assert rows[4]["Modo"] == "PAPER REMOTO"
    assert rows[5]["PID"] == "67749"
    assert rows[5]["Estrategia"] == "Real Edge 3"
    assert rows[5]["Modo"] == "REAL REMOTO"


def test_read_trades_uses_entry_ts_when_entry_time_utc_is_missing():
    source = StrategySource("late_lottery", "Late lottery 1-3c", "late.csv", "late.log")
    header = ["entry_ts", "direction", "contract_price", "win", "pnl_usdc"]
    rows = [["1777668000", "DOWN", "0.01", "True", "99"]]

    df = read_trades_from_rows(rows, header, source)

    assert len(df) == 1
    assert df.loc[0, "entry_local"] != ""


def test_read_binance_spot_momentum_trade_results():
    source = StrategySource(
        "binance_spot_momentum",
        "Binance Spot Momentum",
        "paper_binance_spot_momentum_scanner_trades.csv",
        "paper_binance_spot_momentum_scanner_live.log",
    )
    header = [
        "symbol",
        "entry_time_utc",
        "entry_price",
        "notional_usdc",
        "quantity",
        "exit_price",
        "exit_reason",
        "pnl_usdc",
        "win",
    ]
    rows = [["ETHUSDT", "2026-05-04T01:00:00+00:00", "100", "25", "0.25", "103", "TAKE_PROFIT", "0.65", "True"]]

    df = read_trades_from_rows(rows, header, source)

    assert len(df) == 1
    assert df.loc[0, "strategy"] == "Binance Spot Momentum"
    assert df.loc[0, "symbol"] == "ETHUSDT"
    assert df.loc[0, "entry_price"] == 100.0
    assert df.loc[0, "exit_price"] == 103.0
    assert df.loc[0, "pnl_usdc"] == 0.65
    assert df.loc[0, "win_bool"] == True
    assert df.loc[0, "entry_local"] != ""


def test_read_futures_trade_results_uses_pnl_usd_as_dashboard_pnl():
    source = StrategySource(
        "mes_orb_30m",
        "MES ORB 30m",
        "paper_mes_orb_30m_trades.csv",
        "paper_mes_orb_30m_live.log",
    )
    header = [
        "symbol",
        "direction",
        "entry_time_utc",
        "exit_time_utc",
        "entry_price",
        "exit_price",
        "exit_reason",
        "pnl_usd",
        "win",
    ]
    rows = [["MES=F", "LONG", "2026-05-04T14:15:00+00:00", "2026-05-04T14:30:00+00:00", "7259.5", "7267.5", "TAKE_PROFIT", "36.26", "True"]]

    df = read_trades_from_rows(rows, header, source)

    assert df.loc[0, "pnl_usdc"] == 36.26
    summary = summarize(df, sources=[source])
    assert summary.iloc[0]["PnL"] == 36.26


def test_recent_trade_columns_include_spot_result_fields():
    assert "symbol" in RECENT_TRADE_COLUMNS
    assert "entry_price" in RECENT_TRADE_COLUMNS
    assert "exit_price" in RECENT_TRADE_COLUMNS
    assert "exit_reason" in RECENT_TRADE_COLUMNS


def test_copy_wallet_observer_rows_show_only_copy_signals():
    source = StrategySource(
        "copy_wallet_observer",
        "Copy carteira observer",
        "paper_copy_wallet_trades.csv",
        "paper_copy_wallet_observer.log",
    )
    header = [
        "observed_time_utc",
        "copy_decision",
        "reason",
        "outcome",
        "trader_price",
        "current_buy_price",
        "simulated_stake_usdc",
        "lag_seconds",
    ]
    rows = [
        ["2026-05-01T20:10:00+00:00", "SKIP", "stale", "Up", "0.50", "0.51", "5", "200"],
        ["2026-05-01T20:11:00+00:00", "COPY", "copy_ok", "Down", "0.55", "0.56", "5", "20"],
    ]

    df = read_trades_from_rows(rows, header, source)

    assert len(df) == 1
    assert df.iloc[0]["direction"] == "Down"
    assert df.iloc[0]["contract_price"] == 0.56
    assert df.iloc[0]["order_status"] == "COPY copy_ok"
    assert df.iloc[0]["entry_local"] != ""


def test_copy_wallet_observer_summary_does_not_count_unknown_results_as_losses():
    source = StrategySource(
        "copy_wallet_observer",
        "Copy carteira observer",
        "paper_copy_wallet_trades.csv",
        "paper_copy_wallet_observer.log",
    )
    header = ["entry_time_utc", "direction", "contract_price", "stake_usdc", "status", "win", "pnl_usdc"]
    rows = [["2026-05-01T20:11:00+00:00", "Down", "0.56", "5", "OPEN", "False", "0"]]
    df = read_trades_from_rows(rows, header, source)

    summary = summarize(df)
    row = summary[summary["Estrategia"].eq("[FAILED] Copy carteira observer")].iloc[0]

    assert row["Trades"] == 1
    assert row["Wins"] == 0
    assert row["Losses"] == 0
    assert row["Win rate"] == "-"


def test_copy_wallet_observer_summary_counts_only_closed_results_for_win_rate():
    source = StrategySource(
        "copy_wallet_observer",
        "Copy carteira observer",
        "paper_copy_wallet_trades.csv",
        "paper_copy_wallet_observer.log",
    )
    header = ["entry_time_utc", "direction", "contract_price", "stake_usdc", "status", "win", "pnl_usdc"]
    rows = [
        ["2026-05-01T20:11:00+00:00", "Down", "0.56", "5", "OPEN", "False", "0"],
        ["2026-05-01T20:12:00+00:00", "Up", "0.50", "5", "CLOSED", "True", "5"],
        ["2026-05-01T20:13:00+00:00", "Up", "0.50", "5", "CLOSED", "False", "-5"],
    ]
    df = read_trades_from_rows(rows, header, source)

    summary = summarize(df)
    row = summary[summary["Estrategia"].eq("[FAILED] Copy carteira observer")].iloc[0]

    assert row["Trades"] == 3
    assert row["Wins"] == 1
    assert row["Losses"] == 1
    assert row["Win rate"] == "50.00%"
    assert row["PnL"] == 0.0


def test_summarize_can_limit_visible_sources():
    source = StrategySource("edge_3", "Edge 3%", "paper_edge3_polymarket_5m_trades.csv", "paper_edge3_polymarket_5m_live.log")
    header = ["entry_time_utc", "direction", "contract_price", "win", "pnl_usdc"]
    rows = [["2026-05-01T20:12:00+00:00", "Up", "0.50", "True", "5"]]
    df = read_trades_from_rows(rows, header, source)

    summary = summarize(df, sources=[source])

    assert len(summary) == 1
    assert summary.iloc[0]["Estrategia"] == "Edge 3%"
    assert summary.iloc[0]["Trades"] == 1


def test_load_matrix_summary_sorts_and_formats_known_columns(tmp_path, monkeypatch):
    matrix_path = tmp_path / "backtest_polymarket_5m_matrix_summary.csv"
    pd.DataFrame(
        [
            {
                "strategy": "first-minute-selective",
                "edge_min": 0.06,
                "trades": 10,
                "win_rate_pct": 70.0,
                "pnl_usdc": 12.0,
                "avg_roi_pct": 8.0,
            },
            {
                "strategy": "edge-regime",
                "edge_min": 0.08,
                "trades": 20,
                "win_rate_pct": 80.0,
                "pnl_usdc": 30.0,
                "avg_roi_pct": 10.0,
            },
        ]
    ).to_csv(matrix_path, index=False)
    monkeypatch.setattr("streamlit_dashboard.MATRIX_SUMMARY_PATH", matrix_path)

    summary = load_matrix_summary()

    assert summary.iloc[0]["strategy"] == "edge-regime"
    assert summary.iloc[0]["pnl_usdc"] == 30.0
    assert list(summary.columns[:6]) == [
        "strategy",
        "edge_min",
        "trades",
        "win_rate_pct",
        "pnl_usdc",
        "avg_roi_pct",
    ]
