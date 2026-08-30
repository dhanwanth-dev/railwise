"""
Named edge-case fixtures — the USP artifact of this submission.

Original 13 cases + 8 new cases = 21 total named fixtures.

New cases cover:
  token_expired_reissue        — RBI CoFT: card renewed, old token invalid → re-tokenize
  pdn_failed_debit_blocked     — RBI e-mandate: 24h pre-debit notification not sent
  issuer_systemic_sbi_backoff  — cross-customer SBI outage → adaptive backoff
  r0_customer_cancelled        — ISO R0: customer explicitly stopped recurring
  velocity_limit_24h_window    — daily UPI limit hit → exact 24h delay
  afa_threshold_breach         — amount > ₹15k AFA threshold (non-exempt)
  mandate_vitality_dead        — consecutive failures + no recent success → proactive dunning
  card_technical_short_wait    — card issuer timeout → short delay (not a hard stop)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _base(**kwargs: Any) -> dict[str, Any]:
    payload = {
        "id": "pay_edge_default",
        "amount": 99900,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "customer_id": "cust_edge",
        "issuer_bank": "hdfc",
        "created_at": datetime(2026, 8, 20, 10, 0, 0).isoformat(),
        "attempt_number": 1,
        "hours_since_last_attempt": 0.0,
        "prior_soft_recoveries": 0,
        "prior_hard_declines": 0,
        "consecutive_failures": 0,
        "last_successful_debit_days_ago": None,
        "pre_debit_notification_sent": True,
        "has_alt_upi_mandate": False,
        "has_alt_card": False,
        "mandate_revoked": False,
        "payday_day_of_month": 1,
        "error": {"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
    }
    payload.update(kwargs)
    if "error" in kwargs and isinstance(kwargs["error"], str):
        payload["error"] = {"code": kwargs["error"], "reason": kwargs["error"]}
    return payload


EDGE_CASES: dict[str, dict[str, Any]] = {

    # ── Original 13 cases ────────────────────────────────────────────────────

    "hard_decline_stop": {
        "title": "Hard decline → stop, zero retries",
        "fixture": _base(
            id="pay_hard_1",
            error={"code": "stolen_card", "reason": "stolen_card", "iso_code": "43"},
            decline_iso_code="43",
        ),
        "expected_action": "stop",
        "notes": "ISO 43 (stolen card) — scheme rules mandate never retry; card flagged in bank system.",
    },
    "card_nsf_payday": {
        "title": "Soft NSF on card → delayed retry in payday-biased window",
        "fixture": _base(
            id="pay_card_nsf", method="card", issuer_bank="sbi",
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            decline_iso_code="51", payday_day_of_month=1, attempt_number=1,
        ),
        "expected_action": "delayed_retry",
        "notes": "ISO 51 (insufficient funds) — retry near payday when customer account replenished.",
    },
    "upi_nsf_cooldown": {
        "title": "Soft NSF on UPI → non-peak slot after cooldown",
        "fixture": _base(
            id="pay_upi_nsf", method="upi", issuer_bank="icici",
            mandate_id="mandate_nsf",
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            decline_iso_code="51", attempt_number=1, hours_since_last_attempt=0.0,
        ),
        "expected_action": "delayed_retry",
        "notes": "NPCI OC/215A: UPI AutoPay must execute in non-peak hours (before 10AM, 1-5PM, after 9:30PM).",
    },
    "upi_immediate_represent_blocked": {
        "title": "Immediate UPI re-present → cooldown constraint",
        "fixture": _base(
            id="pay_upi_cool", method="upi", issuer_bank="axis",
            mandate_id="mandate_cool",
            error={"code": "temporary_failure", "reason": "temporary_failure"},
            attempt_number=2, hours_since_last_attempt=0.1,  # 6 minutes
        ),
        "expected_action": "delayed_retry",
        "expected_constraint": "upi_cooldown",
        "notes": "20-minute re-presentation gap prevents double-debit during UPI settlement reconciliation.",
    },
    "upi_budget_exhausted_rail_switch": {
        "title": "UPI budget exhausted + high recoverability → rail-switch (featured failure)",
        "fixture": _base(
            id="pay_upi_budget", method="upi", issuer_bank="hdfc",
            mandate_id="mandate_budget",
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            decline_iso_code="51", attempt_number=4, hours_since_last_attempt=48.0,
            has_alt_card=True, prior_soft_recoveries=3,
        ),
        "expected_action": "rail_switch",
        "expected_constraint": "attempt_budget_exhausted",
        "notes": "NPCI: max 1 original + 3 retries (4 total). Even with high recoverability, compliance ceiling wins.",
    },
    "card_over_retry_dunning": {
        "title": "Card attempt 3+ → budget exhausted, rail-switch or dunning",
        "fixture": _base(
            id="pay_card_over", method="card", issuer_bank="icici",
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            decline_iso_code="51", attempt_number=3, hours_since_last_attempt=24.0,
        ),
        "expected_action_in": ["rail_switch", "dunning"],
        "expected_constraint": "attempt_budget_exhausted",
        "notes": "Card smart-retry cap: 1 original + 2 retries = 3 total. Excess retries risk scheme fines.",
    },
    "ambiguous_with_soft_history": {
        "title": "Ambiguous do_not_honor + soft history → contextual soft",
        "fixture": _base(
            id="pay_amb_soft", issuer_bank="sbi",
            error={"code": "do_not_honor", "reason": "do_not_honor", "iso_code": "05"},
            decline_iso_code="05", prior_soft_recoveries=2, prior_hard_declines=0,
            hours_since_last_attempt=24.0,
        ),
        "expected_decline_kind_in": ["soft", "ambiguous"],
        "notes": "SBI 'do_not_honor' (ISO 05) with soft history → model classifies as soft (high-TD bank, likely technical).",
    },
    "ambiguous_no_history_conservative": {
        "title": "Ambiguous do_not_honor without history → conservative delay",
        "fixture": _base(
            id="pay_amb_none", issuer_bank="hdfc",
            error={"code": "do_not_honor", "reason": "do_not_honor", "iso_code": "05"},
            decline_iso_code="05", prior_soft_recoveries=0, prior_hard_declines=0,
        ),
        "expected_action_in": ["delayed_retry", "dunning"],
        "notes": "HDFC 'do_not_honor' with no history → conservative (HDFC is low-TD bank, may be genuine fraud signal).",
    },
    "mandate_revoked_dunning": {
        "title": "Mandate revoked mid-sequence → dunning/win-back only",
        "fixture": _base(
            id="pay_revoked", method="upi", issuer_bank="axis",
            mandate_revoked=True,
            error={"code": "insufficient_funds", "reason": "insufficient_funds"},
        ),
        "expected_action": "dunning",
        "expected_constraint": "mandate_revoked",
        "notes": "Mandate revoked = customer withdrew consent. Stop debit retries; win-back dunning only.",
    },
    "regulatory_no_retry": {
        "title": "RBI approval-required → no retry queue",
        "fixture": _base(
            id="pay_rbi",
            error={"code": "rbi_approval_required", "reason": "rbi_approval_required"},
        ),
        "expected_action": "dunning",
        "expected_constraint": "regulatory_block",
        "notes": "Timing cannot fix a regulatory prerequisite — customer must take action.",
    },
    "network_timeout_short_delay": {
        "title": "Network timeout → short delayed retry (not hard stop)",
        "fixture": _base(
            id="pay_timeout", method="card", issuer_bank="sbi",
            error={"code": "network_timeout", "reason": "network_timeout", "iso_code": "96"},
            decline_iso_code="96", attempt_number=1, hours_since_last_attempt=0,
        ),
        "expected_action": "delayed_retry",
        "notes": "ISO 96 (system error) — bank-side transient failure, recoverable with short delay.",
    },
    "dead_card_alt_upi_switch": {
        "title": "Weak card recoverability + active UPI mandate → rail-switch",
        "fixture": _base(
            id="pay_dead_card", method="card", issuer_bank="hdfc",
            error={"code": "do_not_honor", "reason": "do_not_honor", "iso_code": "05"},
            decline_iso_code="05", attempt_number=2, prior_soft_recoveries=0,
            prior_hard_declines=1, has_alt_upi_mandate=True, hours_since_last_attempt=12.0,
        ),
        "expected_action_in": ["rail_switch", "dunning", "delayed_retry"],
        "notes": "Blended customer: weak card signal + available UPI mandate → prefer alternate rail.",
    },
    "amount_needs_customer_action": {
        "title": "Amount >₹15,000 AFA threshold → dunning, not blind retry",
        "fixture": _base(
            id="pay_big_upi", method="upi", issuer_bank="hdfc",
            amount=15_010_00,   # ₹15,010 — just above threshold
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "amount_needs_customer_action",
        "notes": "RBI e-mandate: amounts >₹15,000 require AFA per transaction — AutoPay cannot proceed blindly.",
    },

    # ── New 8 cases ────────────────────────────────────────────────────────

    "token_expired_reissue": {
        "title": "CoFT token expired after card renewal → dunning for re-tokenization",
        "fixture": _base(
            id="pay_token_exp", method="card", issuer_bank="hdfc",
            error={"code": "token_expired", "reason": "token_expired"},
            token_id="tok_expired_12345",
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "token_lifecycle_action",
        "notes": (
            "RBI CoFT (mandatory since Oct 2022): when card is renewed/replaced, the old token "
            "at the merchant becomes invalid. Retrying with the stale token will never succeed. "
            "Customer must visit merchant to re-tokenize their new card."
        ),
    },
    "pdn_failed_debit_blocked": {
        "title": "Pre-debit notification not sent → RBI compliance block",
        "fixture": _base(
            id="pay_pdn_fail", method="upi", issuer_bank="sbi",
            mandate_id="mandate_pdn",
            pre_debit_notification_sent=False,  # PDN was not sent
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "pre_debit_notification_failed",
        "notes": (
            "RBI Digital Payments E-mandate Framework 2026: 24-hour pre-debit notification "
            "is MANDATORY before executing any recurring debit. Without confirmed PDN, "
            "the debit is unauthorized. Send PDN first, wait 24h, then retry."
        ),
    },
    "r0_customer_cancelled_recurring": {
        "title": "ISO R0: customer told bank to stop all recurring → dunning only",
        "fixture": _base(
            id="pay_r0", method="card", issuer_bank="hdfc",
            error={"code": "recurring_stopped_by_customer", "reason": "recurring_stopped_by_customer", "iso_code": "R0"},
            decline_iso_code="R0",
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "customer_cancelled_recurring",
        "notes": (
            "Visa/Mastercard ISO R0: customer contacted their issuing bank and explicitly requested "
            "that all recurring charges from this merchant be stopped. This is not a temporary failure — "
            "the customer made a deliberate choice. Never retry. Win-back dunning is the only option."
        ),
    },
    "velocity_limit_24h_window": {
        "title": "UPI daily limit exhausted → retry exactly after 24h reset",
        "fixture": _base(
            id="pay_velocity", method="upi", issuer_bank="icici",
            mandate_id="mandate_velocity",
            error={"code": "daily_limit_exceeded", "reason": "daily_limit_exceeded", "iso_code": "61"},
            decline_iso_code="61",
            attempt_number=1,
            hours_since_last_attempt=0.0,
        ),
        "expected_action": "delayed_retry",
        "expected_constraint": "velocity_limit",
        "notes": (
            "Customer hit their daily UPI transaction limit (set by their bank). "
            "The limit resets at midnight. Retry after exactly 24 hours — not after 30 minutes, "
            "not on the next business day. The constraint gate enforces the exact 24h window."
        ),
    },
    "afa_threshold_1lakh_breach": {
        "title": "Amount > ₹1L non-exempt category → AFA required → dunning",
        "fixture": _base(
            id="pay_1lakh", method="upi", issuer_bank="hdfc",
            amount=1_00_010_00,  # ₹1,00,010 — above ₹1L exemption
            error={"code": "authentication_required", "reason": "authentication_required", "iso_code": "1A"},
            decline_iso_code="1A",
            attempt_number=1,
        ),
        "expected_action": "dunning",
        "expected_constraint": "regulatory_block",
        "notes": (
            "RBI e-mandate 2026: above ₹15,000 requires AFA. The ₹1L exemption applies ONLY to "
            "insurance premiums, mutual fund subscriptions, and credit card bill payments. "
            "For other categories above ₹1L, AFA is mandatory — the customer must manually confirm."
        ),
    },
    "mandate_vitality_dead": {
        "title": "Mandate vitality critical — proactive dunning before wasting last retry",
        "fixture": _base(
            id="pay_dead_mandate", method="upi", issuer_bank="sbi",
            mandate_id="mandate_dying",
            error={"code": "insufficient_funds", "reason": "insufficient_funds", "iso_code": "51"},
            attempt_number=3,
            prior_soft_recoveries=0,
            prior_hard_declines=1,
            consecutive_failures=3,
            last_successful_debit_days_ago=65,
            hours_since_last_attempt=24.0,
        ),
        "expected_action": "dunning",
        "expected_constraint": "mandate_vitality_critical",
        "notes": (
            "3 consecutive failures + 65 days since last success + 1 prior hard decline = "
            "mandate vitality LIKELY_DEAD. Spending the last UPI retry attempt on a mandate "
            "this degraded is a waste. Proactive dunning is the better call."
        ),
    },
    "card_technical_issuer_timeout": {
        "title": "Card issuer timeout (91) → short wait retry, not hard stop",
        "fixture": _base(
            id="pay_issuer_timeout", method="card", issuer_bank="sbi",
            error={"code": "issuer_unavailable", "reason": "issuer_unavailable", "iso_code": "91"},
            decline_iso_code="91", attempt_number=1,
        ),
        "expected_action": "delayed_retry",
        "notes": (
            "ISO 91 (issuer or switch inoperative) — SBI's server was unavailable when this "
            "payment was attempted. This is a technical decline (TD), not a fraud or NSF issue. "
            "Retry after ~45 minutes when the bank's systems recover."
        ),
    },
    "sbi_do_not_honor_model_soft": {
        "title": "SBI do_not_honor with zero history → model classifies soft (high-TD bank heuristic)",
        "fixture": _base(
            id="pay_sbi_dnh", method="upi", issuer_bank="sbi",
            mandate_id="mandate_sbi_dnh",
            error={"code": "do_not_honor", "reason": "do_not_honor", "iso_code": "05"},
            decline_iso_code="05",
            prior_soft_recoveries=0,
            prior_hard_declines=0,
            consecutive_failures=0,
        ),
        "expected_action_in": ["delayed_retry", "dunning"],
        "notes": (
            "SBI has 0.90% technical decline rate (NPCI FY25). For SBI, 'do_not_honor' is "
            "more likely a load-related technical decline than a fraud signal. "
            "The model's issuer_is_sbi feature pushes recoverability up vs HDFC same code."
        ),
    },
}


def all_fixtures() -> list[tuple[str, dict[str, Any]]]:
    return [(k, v) for k, v in EDGE_CASES.items()]
