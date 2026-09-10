"""Charging a customer. Contains the planted bug."""

from ..config.settings import DEFAULT_RETRY_POLICY
from ..http.client import TransientError, fetch_with_retry
from .base import BaseProcessor, audited


class ChargeProcessor(BaseProcessor):
    """Charges a card, with one retry loop too many."""

    @audited
    def charge(self, card, amount):
        def send():
            # BUG: this retries on its own, inside fetch_with_retry's retry
            # loop, so 3 attempts become 9 charges.
            for _ in range(DEFAULT_RETRY_POLICY.attempts):
                try:
                    return gateway_charge(card, amount)
                except TransientError:
                    continue
            raise TransientError("gateway did not settle")

        return fetch_with_retry(send)


def gateway_charge(card, amount):
    raise TransientError("stub gateway")
