"""
Issuer Health Monitor — cross-customer adaptive backoff.

The Problem This Solves (real-world):
  SBI processes 4.95 billion UPI transactions per month with a 0.90% technical
  decline (TD) rate. During load events — IPL final, Big Billion Day, 1st of the
  month salary day — that TD rate spikes to 5-10% for short windows.

  At those moments, every PSP (Razorpay, PayU, Cashfree) is simultaneously
  retrying their respective SBI customers. This "thundering herd" hits SBI's
  overloaded servers all at once, making the TD rate worse for everyone.

  Razorpay already does gateway-level rerouting (switching between payment
  processors when latency spikes). This module is the ISSUER-level equivalent:
  detect when a specific bank is struggling across multiple customers in the
  current batch, and apply adaptive backoff for all customers of that issuer
  — wait for the bank to recover rather than hammering it further.

How it works:
  1. During batch processing, after each decision, we record whether the failure
     was a TECHNICAL decline (bank-side issue) or BUSINESS decline (customer-side).
  2. A sliding window (last 30 events per issuer) tracks the technical decline rate.
  3. If the rate exceeds the threshold (5x the issuer's known baseline), we flag
     the issuer as CRITICAL.
  4. The constraint gate checks issuer health BEFORE deciding action — if CRITICAL,
     it forces DELAYED_RETRY with a 2-hour minimum backoff.

This is NOT in Razorpay's public documentation. It requires cross-customer
signal aggregation that only a payment gateway (not individual merchants) can do.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from engine.schemas import ISSUER_BASELINE_TD_RATES, IssuerBank, IssuerHealthLevel

# Sliding window size per issuer (last N events)
WINDOW_SIZE = 30
# Minimum events before we trust the health signal (avoid false positives on cold start)
MIN_SAMPLE = 5

# Multiplier over known baseline TD rate → health level
# E.g. HDFC baseline = 0.02%. If we see 0.10% (5x), flag as DEGRADED.
DEGRADED_MULTIPLIER = 2.0
CRITICAL_MULTIPLIER = 5.0

# Minimum absolute rate thresholds (even if issuer has very low baseline)
DEGRADED_FLOOR = 0.10   # 10% failure rate → degraded regardless of baseline
CRITICAL_FLOOR = 0.25   # 25% failure rate → critical regardless of baseline

# Adaptive backoff durations
DEGRADED_BACKOFF_MINUTES = 45.0   # wait 45 min then retry
CRITICAL_BACKOFF_MINUTES = 120.0  # wait 2 hours for issuer to recover

# Technical decline codes — bank-side failures (not customer-side)
TECHNICAL_DECLINE_CODES: frozenset[str] = frozenset({
    "issuer_unavailable",
    "bank_technical_error",
    "bank_server_error",
    "gateway_timeout",
    "network_timeout",
    "processing_error",
    "system_error",
    "temporary_failure",
    "transaction_timeout",
    "91",   # ISO 8583: Issuer or switch inoperative
    "96",   # ISO 8583: System error
    "06",   # ISO 8583: Error (catch-all technical)
    "92",   # ISO 8583: Unable to route transaction
})


def is_technical_decline(decline_code: str) -> bool:
    return decline_code.lower() in TECHNICAL_DECLINE_CODES


@dataclass
class _IssuerWindow:
    """Sliding window for one issuer bank."""
    events: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))

    def record(self, is_technical: bool) -> None:
        self.events.append(is_technical)

    def technical_decline_rate(self) -> float:
        if len(self.events) < MIN_SAMPLE:
            return 0.0
        return sum(self.events) / len(self.events)

    def health_level(self, issuer: str) -> IssuerHealthLevel:
        rate = self.technical_decline_rate()
        if rate == 0.0:
            return IssuerHealthLevel.HEALTHY
        baseline = ISSUER_BASELINE_TD_RATES.get(issuer, 0.005)
        if rate >= CRITICAL_FLOOR or rate >= baseline * CRITICAL_MULTIPLIER:
            return IssuerHealthLevel.CRITICAL
        if rate >= DEGRADED_FLOOR or rate >= baseline * DEGRADED_MULTIPLIER:
            return IssuerHealthLevel.DEGRADED
        return IssuerHealthLevel.HEALTHY

    def sample_size(self) -> int:
        return len(self.events)


class IssuerHealthMonitor:
    """
    Tracks per-issuer failure rates within a batch using sliding windows.

    Thread-unsafe by design — batch processing is single-threaded.
    Reset at the start of each batch run to get fresh cross-batch signal.
    """

    def __init__(self) -> None:
        self._windows: defaultdict[str, _IssuerWindow] = defaultdict(_IssuerWindow)

    def record(self, issuer_bank: IssuerBank, decline_code: str) -> None:
        """
        Record a failure event for this issuer.
        Automatically classifies as technical vs business decline.
        """
        is_technical = is_technical_decline(decline_code)
        self._windows[issuer_bank.value].record(is_technical)

    def get_health(self, issuer_bank: IssuerBank) -> IssuerHealthLevel:
        """Current health level for this issuer based on recent batch signal."""
        key = issuer_bank.value
        return self._windows[key].health_level(key)

    def get_td_rate(self, issuer_bank: IssuerBank) -> float:
        """Raw technical decline rate (0.0 if insufficient sample)."""
        return self._windows[issuer_bank.value].technical_decline_rate()

    def get_backoff_minutes(self, issuer_bank: IssuerBank) -> float:
        """Recommended backoff duration for the current health level."""
        level = self.get_health(issuer_bank)
        if level == IssuerHealthLevel.CRITICAL:
            return CRITICAL_BACKOFF_MINUTES
        if level == IssuerHealthLevel.DEGRADED:
            return DEGRADED_BACKOFF_MINUTES
        return 0.0

    def summary(self) -> dict[str, dict]:
        """Full summary for API/UI exposure — shows all tracked issuers."""
        result = {}
        for bank_key, window in self._windows.items():
            rate = window.technical_decline_rate()
            health = window.health_level(bank_key)
            baseline = ISSUER_BASELINE_TD_RATES.get(bank_key, 0.005)
            result[bank_key] = {
                "td_rate": round(rate, 4),
                "health": health.value,
                "baseline_td_rate": baseline,
                "multiplier_over_baseline": round(rate / baseline, 1) if baseline > 0 else 0,
                "sample_size": window.sample_size(),
                "backoff_minutes": self.get_backoff_minutes(IssuerBank(bank_key)),
            }
        return result

    def reset(self) -> None:
        self._windows.clear()


# Module-level singleton — shared across a batch run, reset between batches
_monitor = IssuerHealthMonitor()


def get_monitor() -> IssuerHealthMonitor:
    """Get the current batch's issuer health monitor."""
    return _monitor


def reset_monitor() -> IssuerHealthMonitor:
    """Reset the monitor for a new batch. Returns the fresh instance."""
    global _monitor
    _monitor = IssuerHealthMonitor()
    return _monitor
