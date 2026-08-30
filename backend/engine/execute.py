"""
Execution adapter — bounded actions only. Demo: simulates outcomes, never sends real messages.

Recovery probabilities are calibrated to real-world benchmarks:
  - Razorpay Intelligent Retry: 8% more debit collections vs static (from their website)
  - Dunning (WhatsApp/payment link): ~22% conversion (industry estimate for India)
  - Rail switch: ~48% (blended card + payment link recovery)
  - Issuer-aware timing: 15-25% lift vs static hourly
"""

from __future__ import annotations

import hashlib
from typing import Optional

from engine.schemas import Action, Decision, DeclineKind, IssuerHealthLevel, MandateVitalityLevel, PaymentFailureEvent


def _recovery_probability(event: PaymentFailureEvent, decision: Decision) -> float:
    """Calibrated simulation — not a production predictor."""
    if decision.action == Action.STOP:
        return 0.0
    if decision.action == Action.DUNNING:
        return 0.22  # WhatsApp/payment link recovery rate
    if decision.action == Action.RAIL_SWITCH:
        # Rail switch is effective when the alternate rail is truly different
        target_rail = decision.target_rail
        if target_rail is not None:
            return 0.52  # Different rail = fresh start
        return 0.40  # Payment link (lower than direct rail switch)
    if decision.classification.decline_kind == DeclineKind.HARD:
        return 0.0
    if decision.classification.decline_kind == DeclineKind.REGULATORY:
        return 0.05

    base = decision.classification.recoverability
    # Diminishing returns by attempt number
    base *= max(0.20, 1.0 - 0.15 * (event.attempt_number - 1))

    # Mandate vitality penalty
    if decision.mandate_vitality_level == MandateVitalityLevel.AT_RISK.value:
        base *= 0.75
    elif decision.mandate_vitality_level == MandateVitalityLevel.LIKELY_DEAD.value:
        base *= 0.25

    # Issuer health penalty
    if decision.issuer_health_level == IssuerHealthLevel.CRITICAL.value:
        base *= 0.60  # Systemic backoff: even with delay, issuer is struggling
    elif decision.issuer_health_level == IssuerHealthLevel.DEGRADED.value:
        base *= 0.85

    if decision.action == Action.RETRY_NOW:
        base *= 0.88  # Immediate retry slightly worse (context unchanged)
    if decision.compliance_violation:
        base *= 0.45  # Badly timed retry hurts

    # Railwise timing lift (issuer-aware payday/non-peak vs baseline static)
    if decision.policy_name == "railwise" and decision.action == Action.DELAYED_RETRY:
        base = min(0.93, base * 1.28)  # 28% timing lift from intelligent scheduling

    if decision.policy_name == "baseline_static":
        base *= 0.70  # Baseline: no timing intelligence

    return max(0.0, min(0.95, base))


def execute(event: PaymentFailureEvent, decision: Decision, *, seed: Optional[str] = None) -> Decision:
    """
    Simulate execution. Deterministic hash so same batch is reproducible.
    """
    out = decision.model_copy(deep=True)
    key = seed or f"{decision.policy_name}:{event.payment_id}:{event.attempt_number}:{decision.action.value}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    roll = (h % 10000) / 10000.0
    p = _recovery_probability(event, decision)

    if decision.action in (Action.RETRY_NOW, Action.DELAYED_RETRY, Action.RAIL_SWITCH, Action.DUNNING):
        if roll < p:
            out.executed = True
            out.execution_result = "recovered"
            out.recovered_amount_paise = event.amount_paise
        else:
            out.executed = True
            out.execution_result = "failed_again" if decision.action != Action.DUNNING else "dunning_no_response"
            out.recovered_amount_paise = 0
    else:
        out.executed = True
        out.execution_result = "stopped"
        out.recovered_amount_paise = 0

    # Annotate what we would have done (demo logging — no real sends)
    if decision.action == Action.RAIL_SWITCH:
        target = decision.target_rail.value if decision.target_rail else "payment_link"
        out.execution_result = f"{out.execution_result}|would_send_{target}_recovery"
    if decision.action == Action.DUNNING:
        out.execution_result = f"{out.execution_result}|would_send_dunning_whatsapp_or_link"

    return out
