"""
Mandate Vitality Scorer — proactive pre-failure defense.

The Problem This Solves (real-world):
  Mandates don't die instantly. They degrade in stages:
    Stage 1 — First failure (NSF): Customer ran out of funds before salary day.
              Recoverability is HIGH. Retry after payday. This is normal.
    Stage 2 — Second/third failure: NSF again on next attempt. Customer may have
              changed banks (opened new account), or the merchant changed amount.
              Recoverability is MEDIUM. Model as AT_RISK.
    Stage 3 — Multiple consecutive failures + no recent success: Bank may have
              flagged the recurring debit, or customer has quietly stopped using
              this account, or mandate is about to be revoked.
              Recoverability is LOW. DUNNING is better than wasting another retry.

  Razorpay's Intelligent Retry Engine fires AFTER a failure. This module fires
  DURING failure processing to ask: "Given this mandate's history, is retrying
  actually the best move, or should we proactively reach the customer?"

  This is "defensive AI" — acting on trajectory, not just the single event.

Features used:
  consecutive_failures: Strongest signal. 3+ in a row → mandate likely dying.
  last_successful_debit_days_ago: How long since this mandate worked.
  prior_hard_declines: Hard declines mixed with soft = fundamental account issue.
  attempt_number: High attempt count + no recovery = mandate trouble.
  prior_soft_recoveries: Positive signal — mandate has recovered before.

Why this is AI and not rules:
  Rules would say "if consecutive_failures >= 3: dunning". That's too rigid.
  The scorer combines multiple weak signals into a calibrated vitality score,
  treating recovery history as a counterweight (a mandate with 5 recoveries in
  the past is more resilient than one with zero, even if both just hit failure #3).
"""

from __future__ import annotations

from engine.schemas import MandateVitalityLevel, PaymentFailureEvent


def score_mandate_vitality(event: PaymentFailureEvent) -> tuple[MandateVitalityLevel, float, list[str]]:
    """
    Score mandate health based on failure history signals.

    Returns:
      (vitality_level, raw_score_0_to_10, reason_codes)
      raw_score: 0 = perfectly healthy, 10 = certainly dead
    """
    score: float = 0.0
    reasons: list[str] = []

    # --- Negative signals (mandate getting worse) ---

    cf = event.consecutive_failures
    if cf >= 4:
        score += 5.0
        reasons.append(f"consecutive_failures={cf}:critical")
    elif cf >= 3:
        score += 3.5
        reasons.append(f"consecutive_failures={cf}:high")
    elif cf >= 2:
        score += 2.0
        reasons.append(f"consecutive_failures={cf}:moderate")
    elif cf >= 1:
        score += 0.8
        reasons.append(f"consecutive_failures={cf}:low")

    if event.prior_hard_declines >= 2:
        score += 3.0
        reasons.append("prior_hard_declines>=2:account_issue_suspected")
    elif event.prior_hard_declines >= 1:
        score += 1.2
        reasons.append("prior_hard_declines=1:watchlist")

    if event.last_successful_debit_days_ago is not None:
        days = event.last_successful_debit_days_ago
        if days > 90:
            score += 3.0
            reasons.append(f"no_success_in_{days}d:mandate_likely_abandoned")
        elif days > 60:
            score += 2.0
            reasons.append(f"no_success_in_{days}d:mandate_at_risk")
        elif days > 30:
            score += 1.0
            reasons.append(f"no_success_in_{days}d:watch")

    if event.attempt_number >= 4 and event.prior_soft_recoveries == 0:
        score += 1.5
        reasons.append("high_attempt_no_recovery:diminishing_returns")
    elif event.attempt_number >= 3 and event.prior_soft_recoveries == 0:
        score += 0.8
        reasons.append("attempt_3_no_recovery:watch")

    # --- Positive signals (mandate has recovered before) ---

    if event.prior_soft_recoveries >= 3:
        score -= 2.0
        reasons.append("prior_recoveries>=3:resilient_mandate")
    elif event.prior_soft_recoveries >= 2:
        score -= 1.2
        reasons.append("prior_recoveries>=2:good_history")
    elif event.prior_soft_recoveries >= 1:
        score -= 0.5
        reasons.append("prior_recoveries>=1:some_history")

    score = max(0.0, min(10.0, score))

    if score >= 5.0:
        level = MandateVitalityLevel.LIKELY_DEAD
    elif score >= 2.5:
        level = MandateVitalityLevel.AT_RISK
    else:
        level = MandateVitalityLevel.HEALTHY

    return level, round(score, 2), reasons


def get_recoverability_multiplier(vitality: MandateVitalityLevel) -> float:
    """
    Adjust the classification recoverability score based on mandate vitality.
    AT_RISK mandates should be treated as 70% as recoverable as a healthy mandate.
    LIKELY_DEAD mandates: 20% recoverability multiplier.
    """
    return {
        MandateVitalityLevel.HEALTHY: 1.0,
        MandateVitalityLevel.AT_RISK: 0.70,
        MandateVitalityLevel.LIKELY_DEAD: 0.20,
    }[vitality]
