from backtest_5m_wick_reversal import detect_wick_reversal_signal


def test_detect_wick_reversal_signal_for_bullish_lower_wick():
    signal = detect_wick_reversal_signal(
        open_price=100.0,
        high_price=101.0,
        low_price=96.0,
        close_price=101.0,
        wick_to_body_ratio=2.0,
        opposite_wick_max_body_ratio=0.75,
    )
    assert signal is not None
    assert signal.direction == "UP"


def test_detect_wick_reversal_signal_for_bearish_upper_wick():
    signal = detect_wick_reversal_signal(
        open_price=101.0,
        high_price=105.0,
        low_price=100.5,
        close_price=100.0,
        wick_to_body_ratio=2.0,
        opposite_wick_max_body_ratio=0.75,
    )
    assert signal is not None
    assert signal.direction == "DOWN"


def test_detect_wick_reversal_signal_rejects_when_opposite_wick_too_large():
    signal = detect_wick_reversal_signal(
        open_price=100.0,
        high_price=103.0,
        low_price=98.0,
        close_price=101.0,
        wick_to_body_ratio=2.0,
        opposite_wick_max_body_ratio=0.75,
    )
    assert signal is None


def test_detect_wick_reversal_signal_rejects_doji():
    signal = detect_wick_reversal_signal(
        open_price=100.0,
        high_price=102.0,
        low_price=95.0,
        close_price=100.0,
        wick_to_body_ratio=2.0,
        opposite_wick_max_body_ratio=0.75,
    )
    assert signal is None
