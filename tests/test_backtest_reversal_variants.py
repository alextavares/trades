import pandas as pd

from backtest_reversal_variants import VariantConfig, detect_reversal_down_signal


def make_row(color: int, rsi: float, high: float, upper_band: float) -> pd.Series:
    return pd.Series(
        {
            "color": color,
            "rsi": rsi,
            "high": high,
            "upper_band": upper_band,
        }
    )


def test_detect_reversal_down_signal_for_strict_variant():
    config = VariantConfig(name="strict", rsi_threshold=70.0, require_bollinger=True)
    prev_row = make_row(color=1, rsi=72.0, high=105.0, upper_band=104.0)
    curr_row = make_row(color=-1, rsi=60.0, high=103.0, upper_band=104.0)
    assert detect_reversal_down_signal(prev_row, curr_row, config) is True


def test_detect_reversal_down_signal_rejects_without_bollinger_touch_when_required():
    config = VariantConfig(name="strict", rsi_threshold=70.0, require_bollinger=True)
    prev_row = make_row(color=1, rsi=72.0, high=103.0, upper_band=104.0)
    curr_row = make_row(color=-1, rsi=60.0, high=103.0, upper_band=104.0)
    assert detect_reversal_down_signal(prev_row, curr_row, config) is False


def test_detect_reversal_down_signal_accepts_rsi_only_variant():
    config = VariantConfig(name="rsi_only", rsi_threshold=70.0, require_bollinger=False)
    prev_row = make_row(color=1, rsi=72.0, high=103.0, upper_band=104.0)
    curr_row = make_row(color=-1, rsi=60.0, high=103.0, upper_band=104.0)
    assert detect_reversal_down_signal(prev_row, curr_row, config) is True


def test_detect_reversal_down_signal_rejects_if_colors_do_not_reverse():
    config = VariantConfig(name="rsi_only", rsi_threshold=70.0, require_bollinger=False)
    prev_row = make_row(color=1, rsi=72.0, high=105.0, upper_band=104.0)
    curr_row = make_row(color=1, rsi=60.0, high=103.0, upper_band=104.0)
    assert detect_reversal_down_signal(prev_row, curr_row, config) is False
