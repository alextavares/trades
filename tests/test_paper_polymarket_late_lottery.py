from paper_polymarket_late_lottery import (
    LotteryConfig,
    LotteryPosition,
    choose_lottery_entry,
    settle_lottery_position,
)


def test_choose_lottery_entry_buys_cheap_down_when_btc_above_target_but_close():
    config = LotteryConfig(
        min_cheap_price=0.01,
        max_cheap_price=0.03,
        favorite_min_price=0.97,
        max_abs_distance_usd=50.0,
        max_abs_z=0.40,
    )

    entry = choose_lottery_entry(
        current_price=10020.0,
        target_price=10000.0,
        sigma_remaining=80.0,
        up_price=0.99,
        down_price=0.01,
        seconds_remaining=45,
        config=config,
    )

    assert entry is not None
    assert entry.direction == "DOWN"
    assert entry.contract_price == 0.01
    assert entry.favorite_price == 0.99


def test_choose_lottery_entry_uses_market_prices_for_cheap_side():
    config = LotteryConfig(
        min_cheap_price=0.01,
        max_cheap_price=0.10,
        favorite_min_price=0.90,
        max_abs_distance_usd=80.0,
        max_abs_z=5.0,
    )

    entry = choose_lottery_entry(
        current_price=10020.0,
        target_price=10000.0,
        sigma_remaining=20.0,
        up_price=0.04,
        down_price=0.97,
        seconds_remaining=38,
        config=config,
    )

    assert entry is not None
    assert entry.direction == "UP"
    assert entry.contract_price == 0.04
    assert entry.favorite_price == 0.97


def test_choose_lottery_entry_rejects_when_too_far_from_target():
    config = LotteryConfig(max_abs_distance_usd=50.0, max_abs_z=0.40)

    assert (
        choose_lottery_entry(
            current_price=10100.0,
            target_price=10000.0,
            sigma_remaining=80.0,
            up_price=0.99,
            down_price=0.01,
            seconds_remaining=45,
            config=config,
        )
        is None
    )


def test_choose_lottery_entry_rejects_when_cheap_side_is_not_cheap_enough():
    config = LotteryConfig(max_cheap_price=0.03)

    assert (
        choose_lottery_entry(
            current_price=10020.0,
            target_price=10000.0,
            sigma_remaining=80.0,
            up_price=0.96,
            down_price=0.04,
            seconds_remaining=45,
            config=config,
        )
        is None
    )


def test_choose_lottery_entry_respects_allowed_directions():
    config = LotteryConfig(allowed_directions=("DOWN",))

    assert (
        choose_lottery_entry(
            current_price=10020.0,
            target_price=10000.0,
            sigma_remaining=20.0,
            up_price=0.04,
            down_price=0.97,
            seconds_remaining=38,
            config=config,
        )
        is None
    )


def test_settle_lottery_position_pays_binary_pnl():
    position = LotteryPosition(
        market_slug="btc-updown-5m-1",
        event_start_ts=1,
        event_end_ts=301,
        direction="DOWN",
        entry_ts=250,
        entry_btc_price=10020.0,
        target_price=10000.0,
        contract_price=0.01,
        favorite_price=0.99,
        stake_usdc=1.0,
        seconds_remaining=51,
        distance_usd=20.0,
        z_score=0.25,
        sigma_remaining=80.0,
    )

    won = settle_lottery_position(position, final_btc_price=9990.0, closed_ts=310)
    lost = settle_lottery_position(position, final_btc_price=10010.0, closed_ts=310)

    assert won.win is True
    assert won.pnl_usdc == 99.0
    assert lost.win is False
    assert lost.pnl_usdc == -1.0
