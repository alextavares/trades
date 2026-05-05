import pandas as pd
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_mt5_rates import infer_broker_utc_offset_hours, normalize_rates_frame, parse_timeframe


def test_parse_timeframe_accepts_supported_aliases():
    assert parse_timeframe("m1") == "M1"
    assert parse_timeframe(" H1 ") == "H1"
    assert parse_timeframe("d1") == "D1"


def test_parse_timeframe_rejects_unsupported_values():
    try:
        parse_timeframe("M2")
    except ValueError as exc:
        assert "Unsupported timeframe" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported timeframe")


def test_normalize_rates_frame_formats_mt5_output():
    raw = pd.DataFrame(
        [
            {
                "time": 1777942860,
                "open": 7205.06,
                "high": 7208.78,
                "low": 7204.74,
                "close": 7207.00,
                "tick_volume": 170,
                "spread": 43,
                "real_volume": 0,
            }
        ]
    )

    normalized = normalize_rates_frame(raw)

    assert list(normalized.columns) == [
        "time_utc",
        "time_brt",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    assert normalized.loc[0, "time_utc"] == "2026-05-05T01:01:00+00:00"
    assert normalized.loc[0, "time_brt"] == "2026-05-04T22:01:00-03:00"
    assert normalized.loc[0, "close"] == 7207.00


def test_normalize_rates_frame_applies_broker_offset_before_formatting():
    raw = pd.DataFrame(
        [
            {
                "time": 1778024700,  # raw broker bar open 2026-05-05T23:45:00 if treated as UTC
                "open": 7269.38,
                "high": 7270.10,
                "low": 7268.90,
                "close": 7269.98,
                "tick_volume": 120,
                "spread": 43,
                "real_volume": 0,
            }
        ]
    )

    normalized = normalize_rates_frame(raw, broker_utc_offset_hours=2)

    assert normalized.loc[0, "time_utc"] == "2026-05-05T21:45:00+00:00"
    assert normalized.loc[0, "time_brt"] == "2026-05-05T18:45:00-03:00"


def test_infer_broker_utc_offset_hours_uses_latest_bar_open():
    raw = pd.DataFrame(
        [
            {"time": 1778023800},
            {"time": 1778024700},
        ]
    )

    offset = infer_broker_utc_offset_hours(
        raw,
        timeframe="M15",
        now_utc=pd.Timestamp("2026-05-05T21:48:59+00:00"),
    )

    assert offset == 2
