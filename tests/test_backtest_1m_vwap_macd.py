import pandas as pd

from backtest_1m_vwap_macd import detect_vwap_macd_signal, next_5m_block_end


def test_detect_vwap_macd_signal_for_up():
    prev_row = pd.Series({"close": 99.0, "vwap": 100.0, "macd_line": -0.2, "macd_signal": -0.1})
    curr_row = pd.Series({"close": 101.0, "vwap": 100.0, "macd_line": 0.1, "macd_signal": 0.0})
    assert detect_vwap_macd_signal(prev_row, curr_row) == "UP"


def test_detect_vwap_macd_signal_for_down():
    prev_row = pd.Series({"close": 101.0, "vwap": 100.0, "macd_line": 0.2, "macd_signal": 0.1})
    curr_row = pd.Series({"close": 99.0, "vwap": 100.0, "macd_line": -0.1, "macd_signal": 0.0})
    assert detect_vwap_macd_signal(prev_row, curr_row) == "DOWN"


def test_detect_vwap_macd_signal_rejects_without_cross():
    prev_row = pd.Series({"close": 101.0, "vwap": 100.0, "macd_line": 0.2, "macd_signal": 0.1})
    curr_row = pd.Series({"close": 102.0, "vwap": 100.0, "macd_line": 0.3, "macd_signal": 0.2})
    assert detect_vwap_macd_signal(prev_row, curr_row) is None


def test_next_5m_block_end_rounds_to_current_block():
    ts = pd.Timestamp("2026-04-30T14:02:00Z")
    assert next_5m_block_end(ts) == pd.Timestamp("2026-04-30T14:04:00Z")

    ts = pd.Timestamp("2026-04-30T14:05:00Z")
    assert next_5m_block_end(ts) == pd.Timestamp("2026-04-30T14:09:00Z")
