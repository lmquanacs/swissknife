"""The one place an outbound HTTP call is allowed to be retried."""

import time

from ..config.settings import DEFAULT_RETRY_POLICY, RetryPolicy


def fetch_with_retry(send, policy: RetryPolicy = DEFAULT_RETRY_POLICY):
    """Call `send`, retrying up to `policy.attempts` times on failure."""
    last_error = None
    for attempt in range(policy.attempts):
        try:
            return send()
        except TransientError as error:
            last_error = error
            time.sleep(policy.backoff_ms * (attempt + 1) / 1000)
    raise last_error


class TransientError(Exception):
    """Raised for a failure worth retrying."""
