"""Runtime knobs. Everything tunable lives here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry a failed call, and how long to wait between."""

    attempts: int = 3
    backoff_ms: int = 250
    jitter: bool = True


DEFAULT_RETRY_POLICY = RetryPolicy()
CHARGE_TIMEOUT_MS = 5_000
