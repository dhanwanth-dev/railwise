"""Ingest & normalize raw card/UPI failure payloads into PaymentFailureEvent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from engine.schemas import PaymentFailureEvent, Rail


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        # Razorpay-shaped ISO or unix string
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.now(timezone.utc).replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_raw(payload: dict[str, Any]) -> PaymentFailureEvent:
    """
    Accepts Razorpay-shaped synthetic payloads for card or UPI AutoPay failures.

    Why this layer exists: card error.error_code and UPI mandate error payloads
    differ in shape. Without normalization, rail cannot be a first-class decision axis.
    """
    method = (payload.get("method") or payload.get("rail") or "card").lower()
    if method in ("upi", "upi_autopay", "emandate_upi"):
        rail = Rail.UPI
    elif method in ("netbanking", "nb"):
        rail = Rail.NETBANKING
    else:
        rail = Rail.CARD

    error = payload.get("error") or {}
    decline_code = (
        payload.get("decline_code")
        or error.get("code")
        or error.get("reason")
        or payload.get("status_reason")
        or "unknown"
    )
    decline_code = str(decline_code).lower().strip()

    notes = payload.get("notes") or {}
    return PaymentFailureEvent(
        payment_id=str(payload.get("id") or payload.get("payment_id")),
        customer_id=str(payload.get("customer_id") or notes.get("customer_id") or "cust_unknown"),
        merchant_id=str(payload.get("merchant_id") or "merch_demo"),
        rail=rail,
        decline_code=decline_code,
        amount_paise=int(payload.get("amount") or payload.get("amount_paise") or 0),
        currency=str(payload.get("currency") or "INR"),
        timestamp=_parse_ts(payload.get("created_at") or payload.get("timestamp")),
        attempt_number=int(payload.get("attempt_number") or notes.get("attempt_number") or 1),
        mandate_id=payload.get("token_id") or payload.get("mandate_id") or notes.get("mandate_id"),
        card_id=(payload.get("card") or {}).get("id") if isinstance(payload.get("card"), dict) else payload.get("card_id"),
        prior_soft_recoveries=int(payload.get("prior_soft_recoveries") or notes.get("prior_soft_recoveries") or 0),
        prior_hard_declines=int(payload.get("prior_hard_declines") or notes.get("prior_hard_declines") or 0),
        hours_since_last_attempt=float(
            payload.get("hours_since_last_attempt") or notes.get("hours_since_last_attempt") or 0.0
        ),
        has_alt_upi_mandate=bool(payload.get("has_alt_upi_mandate") or notes.get("has_alt_upi_mandate") or False),
        has_alt_card=bool(payload.get("has_alt_card") or notes.get("has_alt_card") or False),
        mandate_revoked=bool(payload.get("mandate_revoked") or notes.get("mandate_revoked") or False),
        payday_day_of_month=payload.get("payday_day_of_month") or notes.get("payday_day_of_month"),
        raw=payload,
    )
