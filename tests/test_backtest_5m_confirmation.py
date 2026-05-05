from backtest_5m_confirmation import candle_color, detect_confirmation_signal


def test_candle_color_detects_green_red_and_doji():
    assert candle_color(100.0, 101.0) == 1
    assert candle_color(100.0, 99.0) == -1
    assert candle_color(100.0, 100.0) == 0


def test_detect_confirmation_signal_for_up_pattern():
    assert detect_confirmation_signal(-1, 1, 1, entry_mode="third") == "UP"
    assert detect_confirmation_signal(-1, 1, 0, entry_mode="second") == "UP"


def test_detect_confirmation_signal_for_down_pattern():
    assert detect_confirmation_signal(1, -1, -1, entry_mode="third") == "DOWN"
    assert detect_confirmation_signal(1, -1, 0, entry_mode="second") == "DOWN"


def test_detect_confirmation_signal_rejects_invalid_sequences():
    assert detect_confirmation_signal(1, 1, -1, entry_mode="third") is None
    assert detect_confirmation_signal(-1, -1, 1, entry_mode="third") is None
    assert detect_confirmation_signal(-1, 0, 1, entry_mode="third") is None
    assert detect_confirmation_signal(1, 1, 0, entry_mode="second") is None
    assert detect_confirmation_signal(-1, -1, 0, entry_mode="second") is None
