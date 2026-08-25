"""
Classification layer.

Clear decline codes → deterministic rules (no model vote).
Ambiguous codes → lightweight trained scoring model (pure Python, no sklearn).

Why pure Python: honest for demo volume, fully auditable feature weights,
and installs cleanly without native ML wheels.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from engine.schemas import ClassificationResult, DeclineKind, PaymentFailureEvent, Rail

HARD_CODES = {
    "stolen_card",
    "lost_card",
    "pickup_card",
    "expired_card",
    "invalid_card_number",
    "card_blocked",
    "account_closed",
    "fraudulent",
    "do_not_retry",
    "permanent_failure",
    "invalid_account",
}

SOFT_CODES = {
    "insufficient_funds",
    "nsf",
    "bank_technical_error",
    "issuer_unavailable",
    "gateway_timeout",
    "network_timeout",
    "processing_error",
    "temporary_failure",
    "debit_failed",
    "transaction_not_allowed_at_moment",
}

REGULATORY_CODES = {
    "rbi_approval_required",
    "approval_required",
    "authentication_required",
}

AMBIGUOUS_CODES = {
    "do_not_honor",
    "generic_decline",
    "declined",
    "unknown",
    "payment_failed",
    "issuer_declined",
}

MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "models" / "ambiguous_clf.json"

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


def train_ambiguous_model(samples: list[dict]) -> dict:
    """
    Train logistic weights with simple SGD on labeled ambiguous samples.

    Each sample: {features: dict, soft: 0|1, recoverability: float}
    """
    weights = {name: 0.0 for name in FEATURE_NAMES}
    rec_weights = {name: 0.0 for name in FEATURE_NAMES}
    lr = 0.08
    for _epoch in range(40):
        for s in samples:
            feats = s["features"]
            # soft classifier
            z = sum(weights[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
            p = _sigmoid(z)
            y = float(s["soft"])
            err = p - y
            for k in FEATURE_NAMES:
                weights[k] -= lr * err * feats.get(k, 0.0)
            # recoverability linear regressor
            pred = sum(rec_weights[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
            rerr = pred - float(s["recoverability"])
            for k in FEATURE_NAMES:
                rec_weights[k] -= lr * 0.5 * rerr * feats.get(k, 0.0)

    bundle = {
        "type": "logistic_sgd",
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
            recoverability=min(0.75, 0.45 + 0.1 * event.prior_soft_recoveries),
            confidence=0.6,
            source="rules",
            reason_codes=["ambiguous_code", "prior_soft_recovery"],
        )
    return ClassificationResult(
        decline_kind=DeclineKind.AMBIGUOUS,
        recoverability=0.35,
        confidence=0.4,
        source="rules",
        reason_codes=["ambiguous_code", "no_history_conservative"],
    )


def _model_ambiguous(event: PaymentFailureEvent) -> ClassificationResult:
    bundle = _load_model()
    if bundle is None:
        return _heuristic_ambiguous(event)

    feats = _features(event)
    clf_w = bundle["clf_weights"]
    reg_w = bundle["reg_weights"]
    z = sum(clf_w[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
    proba_soft = _sigmoid(z)
    recov = sum(reg_w[k] * feats.get(k, 0.0) for k in FEATURE_NAMES)
    recov = max(0.0, min(1.0, recov))

    # Feature importance = |weight * value| normalized
    importance = {k: abs(clf_w[k] * feats.get(k, 0.0)) for k in FEATURE_NAMES if k != "bias"}
    total = sum(importance.values()) or 1.0
    importance = {k: round(v / total, 4) for k, v in importance.items()}

    if proba_soft >= 0.55:
        kind = DeclineKind.SOFT
    elif proba_soft <= 0.35:
        kind = DeclineKind.HARD
    else:
        kind = DeclineKind.AMBIGUOUS

    return ClassificationResult(
        decline_kind=kind,
        recoverability=recov if kind != DeclineKind.HARD else min(recov, 0.15),
        confidence=abs(proba_soft - 0.5) * 2,
        source="model",
        reason_codes=["ambiguous_code", "logistic_sgd"],
        feature_importance=importance,
    )


def classify(event: PaymentFailureEvent) -> ClassificationResult:
    code = event.decline_code.lower()

    if code in REGULATORY_CODES:
        return ClassificationResult(
            decline_kind=DeclineKind.REGULATORY,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["regulatory_code"],
        )

    if code in HARD_CODES or event.mandate_revoked:
        return ClassificationResult(
            decline_kind=DeclineKind.HARD,
            recoverability=0.0,
            confidence=1.0,
            source="rules",
            reason_codes=["hard_code_map"],
        )

    if code in SOFT_CODES:
        base = 0.7 if "insufficient" in code or code == "nsf" else 0.55
        if "timeout" in code or "technical" in code or "processing" in code:
            base = 0.65
        recov = max(0.15, base - 0.12 * (event.attempt_number - 1))
        return ClassificationResult(
            decline_kind=DeclineKind.SOFT,
            recoverability=recov,
            confidence=0.95,
            source="rules",
            reason_codes=["soft_code_map"],
        )

    return _model_ambiguous(event)
