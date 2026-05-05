import math

from backtest_polymarket_5m_edge import (
    PricePoint,
    TradeDecision,
    binary_trade_pnl,
    decide_trade,
    estimate_up_probability,
    nearest_price_at,
    normal_cdf,
)


def test_normal_cdf_matches_standard_values():
    assert normal_cdf(0.0) == 0.5
    assert math.isclose(normal_cdf(1.96), 0.975, abs_tol=0.001)
    assert math.isclose(normal_cdf(-1.96), 0.025, abs_tol=0.001)


def test_estimate_up_probability_increases_with_distance_above_target():
    prob_above = estimate_up_probability(
        current_price=101.0,
        target_price=100.0,
        sigma_remaining=1.0,
        momentum_score=0.0,
    )
    prob_below = estimate_up_probability(
        current_price=99.0,
        target_price=100.0,
        sigma_remaining=1.0,
        momentum_score=0.0,
    )

    assert prob_above > 0.84
    assert prob_below < 0.16


def test_decide_trade_requires_positive_edge_after_contract_price():
    decision = decide_trade(
        prob_up=0.72,
        ask_up=0.65,
        ask_down=0.35,
        edge_min=0.06,
        max_contract_price=0.85,
    )

    assert decision == TradeDecision(
        direction="UP",
        probability=0.72,
        contract_price=0.65,
        edge=0.07,
    )


def test_decide_trade_rejects_expensive_contract_without_enough_edge():
    decision = decide_trade(
        prob_up=0.88,
        ask_up=0.91,
        ask_down=0.09,
        edge_min=0.06,
        max_contract_price=0.85,
    )

    assert decision.direction == "HOLD"


def test_binary_trade_pnl_uses_binary_contract_payout():
    winning = binary_trade_pnl(
        direction="UP",
        target_price=100.0,
        final_price=102.0,
        contract_price=0.65,
        stake_usdc=10.0,
    )
    losing = binary_trade_pnl(
        direction="DOWN",
        target_price=100.0,
        final_price=102.0,
        contract_price=0.35,
        stake_usdc=10.0,
    )

    assert math.isclose(winning, 5.3846153846, rel_tol=1e-9)
    assert losing == -10.0


def test_nearest_price_at_returns_none_when_point_is_too_far():
    points = [
        PricePoint(timestamp=100, price=0.40),
        PricePoint(timestamp=165, price=0.60),
    ]

    assert nearest_price_at(points, target_ts=160, max_distance_seconds=10) == 0.60
    assert nearest_price_at(points, target_ts=200, max_distance_seconds=10) is None
