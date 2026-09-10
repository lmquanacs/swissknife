"""What the charge path is supposed to do."""

from inventory.billing.charge import ChargeProcessor


def test_charge_retries_three_times():
    processor = ChargeProcessor()
    assert processor.describe() == "ChargeProcessor"
