"""
Classification layer — real ISO 8583 decline code taxonomy.

Real-world grounding:
  Card networks (Visa, Mastercard, RuPay) return ISO 8583 field-39 response codes.
  Indian payment gateways (including Razorpay) translate these to human-readable
  aliases, but the source-of-truth is the numeric code.

  UPI failures use NPCI's own error taxonomy (distinct from ISO 8583) categorized as:
    TD (Technical Decline) — bank-side: issuer unavailable, network error
    BD (Business Decline) — customer-side: wrong PIN, insufficient funds

Classification logic:
  1. Hard codes → deterministic rules (zero model involvement)
  2. Soft codes → deterministic rules (no model)
  3. Regulatory codes → no retry possible
  4. Ambiguous codes (primarily ISO "05" do_not_honor) → logistic model
     Because: "do_not_honor" from HDFC at 2 AM is fraud-engine rejection (hard-ish)
     but "do_not_honor" from SBI during a load event is a temporary technical block.
     The issuer_bank feature in the model captures this difference.

Why no LLM: Audit requirement. A logistic model has auditable weights per feature.
An LLM's reasoning is opaque — cannot defend "model said it was soft" in a compliance
investigation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from engine.schemas import ClassificationResult, DeclineKind, IssuerBank, PaymentFailureEvent, Rail

# ── ISO 8583 Hard Decline Codes (never retry) ──────────────────────────────
# Source: Visa/Mastercard core spec + RuPay operating guidelines
HARD_ISO_CODES: frozenset[str] = frozenset({
    "04",   # Pick up card (no fraud) — card physically flagged
    "07",   # Pick up card, special condition (fraud) — block immediately
    "14",   # Invalid account number — card number doesn't exist
    "41",   # Lost card — reported as lost
    "43",   # Stolen card — reported as stolen
    "54",   # Expired card — past expiry date
    "62",   # Restricted card — merchant category blocked for this card
    "63",   # Security violation — CVV-level fraud signal
    "93",   # Transaction cannot complete (violation of law/AML)
    "R0",   # Recurring charge stopped — customer explicitly cancelled (Visa)
    "R1",   # Recurring charge stopped — customer explicit cancellation (Mastercard)
})

# Semantic aliases for hard codes (Razorpay-style + NPCI aliases)
HARD_CODES: frozenset[str] = frozenset({
    "stolen_card", "lost_card", "pickup_card", "pickup_card_fraud",
    "expired_card", "invalid_card_number", "card_blocked", "invalid_card",
    "account_closed", "fraudulent", "do_not_retry", "permanent_failure",
    "invalid_account", "restricted_card", "security_violation",
    # UPI-specific hard codes
    "vpa_not_found",            # UPI VPA closed/changed — hard stop
    "upi_id_not_registered",    # VPA doesn't exist in NPCI system
    "debit_failed_mandate_invalid",  # Mandate structurally invalid
    # Customer-cancelled recurring (R0/R1 semantic equivalents)
    "recurring_stopped_by_customer",
    "cardholder_cancelled_recurring",
    "stop_payment",
})

# ── ISO 8583 Soft Decline Codes (retry with correct window) ────────────────
SOFT_ISO_CODES: frozenset[str] = frozenset({
    "51",   # Insufficient funds — most common, retry after salary credit
    "61",   # Exceeds withdrawal amount limit — try after 24h (limit resets)
    "65",   # Exceeds withdrawal frequency — try after 24h
    "91",   # Issuer or switch inoperative — bank-side technical issue
    "96",   # System error — catch-all technical
    "06",   # Error — transient processing error
    "78",   # Blocked, first use — needs customer to activate card (semi-hard)
})

# Semantic soft code aliases
SOFT_CODES: frozenset[str] = frozenset({
    "insufficient_funds", "nsf",
    "bank_technical_error", "bank_server_error",
    "issuer_unavailable", "issuer_inoperative",
    "gateway_timeout", "network_timeout", "transaction_timeout",
    "processing_error", "temporary_failure", "system_error",
    "debit_failed",                     # Generic soft UPI failure
    "transaction_not_allowed_at_moment", # Transient block
    "daily_limit_exceeded",             # Customer's daily UPI limit (soft — resets in 24h)
    "exceeds_withdrawal_limit",         # Soft — resets daily
    "amount_limit_exceeded",
    "upi_payment_limit_exceeded",
})

# ── Token Lifecycle Codes (RBI CoFT, mandatory since Oct 2022) ───────────────
# When a card is renewed/replaced, the old token becomes invalid.
# Customer must re-tokenize at the merchant. Cannot retry with same token.
TOKEN_LIFECYCLE_CODES: frozenset[str] = frozenset({
    "token_expired",
    "token_not_found",
    "token_revoked",
    "invalid_token",
    "token_invalid",
    "coft_token_expired",
    "token_not_provisioned",
})

# ── Customer-Cancelled Recurring (R0/R1 semantic) ───────────────────────────
CUSTOMER_CANCELLED_CODES: frozenset[str] = frozenset({
    "R0", "R1",
    "recurring_stopped_by_customer",
    "cardholder_cancelled_recurring",
    "mandate_cancelled_by_customer",
    "stop_recurring",
})

# ── Velocity / Rate-Limit Codes ──────────────────────────────────────────────
VELOCITY_CODES: frozenset[str] = frozenset({
    "daily_limit_exceeded",
    "upi_daily_limit_exceeded",
    "exceeds_withdrawal_frequency",
    "velocity_limit_exceeded",
    "too_many_transactions",
    "65",  # ISO 8583: exceeds withdrawal frequency
    "61",  # ISO 8583: exceeds withdrawal amount limit (daily cap)
})

# ── Regulatory Codes ────────────────────────────────────────────────────────
REGULATORY_CODES: frozenset[str] = frozenset({
    "rbi_approval_required",
    "approval_required",
    "authentication_required",
    "1A",   # ISO 8583: Additional customer authentication required (SCA)
    "additional_authentication_required",
    "afa_required",
    "afa_pending",
})

# ── Ambiguous Codes (model handles these) ────────────────────────────────────
# do_not_honor is the most common card decline globally. It's issuer-specific:
#   HDFC "do_not_honor" at 2 AM → likely fraud engine trigger (conservative → hard-ish)
#   SBI "do_not_honor" during peak load → likely TD (technical decline, retry soon)
AMBIGUOUS_CODES: frozenset[str] = frozenset({
    "do_not_honor",
    "generic_decline",
    "declined",
    "unknown",
    "payment_failed",
    "issuer_declined",
    "05",  # ISO 8583: Do Not Honor — the most common and most ambiguous code
    "12",  # ISO 8583: Invalid transaction (can be soft or hard depending on context)
    "57",  # ISO 8583: Transaction not permitted (could be merchant category block = hard, or temporary = soft)
})


MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "models" / "ambiguous_clf.json"

# Feature names — MUST match training in train_model.py
FEATURE_NAMES = [
    "is_upi",
    "is_card",
    "attempt_number",
    "prior_soft_recoveries",
    "prior_hard_declines",
    "hours_since_last_attempt",
    "amount_scaled",
    "has_alt_upi",
    "has_alt_card",
    "consecutive_failures",
    # Issuer bank risk features (one-hot for high-TD issuers)
    "issuer_is_sbi",
    "issuer_is_bandhan",
    "issuer_is_jio",
    "issuer_is_hdfc",
    "bias",
]

_model_bundle: Optional[dict] = None


def _features(event: PaymentFailureEvent) -> dict[str, float]:
    return {
        "is_upi": 1.0 if event.rail == Rail.UPI else 0.0,
        "is_card": 1.0 if event.rail == Rail.CARD else 0.0,
        "attempt_number": float(event.attempt_number),
        "prior_soft_recoveries": float(event.prior_soft_recoveries),
        "prior_hard_declines": float(event.prior_hard_declines),
        "hours_since_last_attempt": float(event.hours_since_last_attempt),
        "amount_scaled": float(event.amount_paise) / 100_000.0,
        "has_alt_upi": 1.0 if event.has_alt_upi_mandate else 0.0,
        "has_alt_card": 1.0 if event.has_alt_card else 0.0,
        "consecutive_failures": float(event.consecutive_failures),
        "issuer_is_sbi": 1.0 if event.issuer_bank == IssuerBank.SBI else 0.0,
        "issuer_is_bandhan": 1.0 if event.issuer_bank == IssuerBank.BANDHAN else 0.0,
        "issuer_is_jio": 1.0 if event.issuer_bank == IssuerBank.JIO else 0.0,
        "issuer_is_hdfc": 1.0 if event.issuer_bank == IssuerBank.HDFC else 0.0,
        "bias": 1.0,
    }


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _load_model() -> Optional[dict]:
    global _model_bundle
    if _model_bundle is not None:
        return _model_bundle
    if MODEL_PATH.exists():
        _model_bundle = json.loads(MODEL_PATH.read_text())
        return _model_bundle
    return None


def reload_model() -> None:
    """Force reload of model weights from disk (call after retraining)."""
    global _model_bundle
    _model_bundle = None
    _load_model()


def train_ambiguous_model(samples: list[dict]) -> dict:
    """
    Train logistic weights with SGD on labeled ambiguous samples.

    Each sample: {features: dict[str, float], soft: 0|1, recoverability: float}
    Training runs 60 epochs with learning rate decay for stability.
    """
    weights = {name: 0.0 for name in FEATURE_NAMES}
    rec_weights = {name: 0.0 for name in FEATURE_NAMES}
    lr = 0.10

    l2 = 0.001  # L2 regularization — prevents weight explosion in regressor
    for epoch in range(60):
        epoch_lr = lr * (0.97 ** epoch)
        for s in samples:
            feats = s["features"]
            # Soft classifier
            z = sum(weights[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
            p = _sigmoid(z)
            y = float(s["soft"])
            err = p - y
            for k in FEATURE_NAMES:
                weights[k] -= epoch_lr * (err * feats.get(k, 0.0) + l2 * weights[k])
            # Recoverability linear regressor (with L2 to prevent divergence)
            pred = sum(rec_weights[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
            rerr = pred - float(s["recoverability"])
            for k in FEATURE_NAMES:
                rec_weights[k] -= epoch_lr * (0.5 * rerr * feats.get(k, 0.0) + l2 * rec_weights[k])

    bundle = {
        "type": "logistic_sgd",
        "version": "2.0",
        "feature_names": FEATURE_NAMES,
        "clf_weights": weights,
        "reg_weights": rec_weights,
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(bundle, indent=2))
    global _model_bundle
    _model_bundle = bundle
    return bundle


def _heuristic_ambiguous(event: PaymentFailureEvent) -> ClassificationResult:
    """Fallback when model weights aren't available."""
    if event.prior_hard_declines >= 2:
        return ClassificationResult(
            decline_kind=DeclineKind.HARD,
            recoverability=0.05,
            confidence=0.55,
            source="rules",
            reason_codes=["ambiguous_code", "prior_hard_history"],
        )
    if event.prior_soft_recoveries >= 1:
        return ClassificationResult(
            decline_kind=DeclineKind.SOFT,
            recoverability=min(0.75, 0.45 + 0.10 * event.prior_soft_recoveries),
            confidence=0.60,
            source="rules",
            reason_codes=["ambiguous_code", "prior_soft_recovery"],
        )
    # High-TD issuers (SBI, Bandhan, Jio) → lean soft for "do_not_honor" (likely TD not fraud)
    if event.issuer_bank in (IssuerBank.SBI, IssuerBank.BANDHAN, IssuerBank.JIO):
        return ClassificationResult(
            decline_kind=DeclineKind.SOFT,
            recoverability=0.45,
            confidence=0.50,
            source="rules",
            reason_codes=["ambiguous_code", "high_td_issuer_likely_technical"],
        )
    return ClassificationResult(
        decline_kind=DeclineKind.AMBIGUOUS,
        recoverability=0.30,
        confidence=0.40,
        source="rules",
        reason_codes=["ambiguous_code", "no_history_conservative"],
    )


def _model_ambiguous(event: PaymentFailureEvent) -> ClassificationResult:
    bundle = _load_model()
    if bundle is None:
        return _heuristic_ambiguous(event)

    feats = _features(event)
    clf_w = bundle["clf_weights"]

    z = sum(clf_w.get(k, 0.0) * feats.get(k, 0.0) for k in FEATURE_NAMES)
    proba_soft = _sigmoid(z)

    # Derive recoverability from classifier probability (interpretable, no regressor divergence).
    # proba_soft=1.0 → max recov=0.82; proba_soft=0.5 → recov=0.40
    base_recov = proba_soft * 0.82
    # Consecutive failures are a strong additional penalty
    cf_penalty = feats.get("consecutive_failures", 0.0) * 0.07
    # Attempt number penalty (diminishing returns)
    attempt_penalty = max(0.0, feats.get("attempt_number", 1.0) - 1) * 0.08
    recov = max(0.04, min(0.90, base_recov - cf_penalty - attempt_penalty))

    # Feature importance = |weight * value| normalized (for audit)
    importance = {k: abs(clf_w.get(k, 0.0) * feats.get(k, 0.0)) for k in FEATURE_NAMES if k != "bias"}
    total = sum(importance.values()) or 1.0
    importance = {k: round(v / total, 4) for k, v in importance.items()}

    if proba_soft >= 0.58:
        kind = DeclineKind.SOFT
    elif proba_soft <= 0.35:
        kind = DeclineKind.HARD
    else:
        kind = DeclineKind.AMBIGUOUS

    return ClassificationResult(
        decline_kind=kind,
        recoverability=recov if kind != DeclineKind.HARD else min(recov, 0.12),
        confidence=abs(proba_soft - 0.5) * 2.0,
        source="model",
        reason_codes=["ambiguous_code", "logistic_sgd_v2", f"issuer={event.issuer_bank.value}"],
        feature_importance=importance,
    )


def classify(event: PaymentFailureEvent) -> ClassificationResult:
    """
    Classify a payment failure event into decline kind + recoverability score.

    Priority:
    1. ISO 8583 numeric codes (most authoritative)
    2. Semantic aliases (Razorpay/NPCI labels)
    3. Logistic model for ambiguous codes
    """
    code = event.decline_code.lower().strip()
    iso = (event.decline_iso_code or "").strip().upper()

    # Check ISO code first (most authoritative)
    if iso in HARD_ISO_CODES or iso in {"R0", "R1"}:
        return ClassificationResult(
            decline_kind=DeclineKind.HARD,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["hard_iso_code", f"iso={iso}"],
        )

    # Regulatory check (must come before hard — AFA is not a hard decline)
    if code in REGULATORY_CODES or iso in {"1A"}:
        return ClassificationResult(
            decline_kind=DeclineKind.REGULATORY,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["regulatory_code"],
        )

    # Token lifecycle — not a hard decline but requires customer action
    if code in TOKEN_LIFECYCLE_CODES:
        return ClassificationResult(
            decline_kind=DeclineKind.REGULATORY,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["token_lifecycle_code", "coft_rbi_mandate"],
        )

    # Customer explicitly stopped recurring
    if code in CUSTOMER_CANCELLED_CODES:
        return ClassificationResult(
            decline_kind=DeclineKind.HARD,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["customer_cancelled_recurring", "r0_r1_equivalent"],
        )

    # Hard decline (semantic)
    if code in HARD_CODES or event.mandate_revoked:
        return ClassificationResult(
            decline_kind=DeclineKind.HARD,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["hard_code_map"],
        )

    # Soft ISO codes
    if iso in SOFT_ISO_CODES:
        base = 0.72 if iso == "51" else 0.58
        recov = max(0.15, base - 0.12 * (event.attempt_number - 1))
        return ClassificationResult(
            decline_kind=DeclineKind.SOFT,
            recoverability=recov,
            confidence=0.95,
            source="rules",
            reason_codes=["soft_iso_code", f"iso={iso}"],
        )

    # Soft semantic codes
    if code in SOFT_CODES:
        if "insufficient" in code or code == "nsf":
            base = 0.72
        elif "timeout" in code or "technical" in code or "processing" in code or "system" in code:
            base = 0.62
        elif "limit" in code or "velocity" in code:
            base = 0.58
        else:
            base = 0.55
        recov = max(0.15, base - 0.12 * (event.attempt_number - 1))
        return ClassificationResult(
            decline_kind=DeclineKind.SOFT,
            recoverability=recov,
            confidence=0.92,
            source="rules",
            reason_codes=["soft_code_map"],
        )

    # Ambiguous — use model
    return _model_ambiguous(event)
