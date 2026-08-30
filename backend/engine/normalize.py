"""
Ingest & normalize raw card/UPI failure payloads into PaymentFailureEvent.

Why this layer exists: Card and UPI failures arrive in completely different shapes
from Razorpay's webhook API. Without normalization, every downstream module would
need to know if it's looking at a card or UPI payload — and "rail" would never be
a first-class decision input.

New fields extracted here:
  issuer_bank       — the bank that issued the card/holds the UPI account
                      (different banks have very different failure patterns)
  decline_iso_code  — raw ISO 8583 code if present ("05", "51", "R0", etc.)
  token_id          — RBI CoFT token ID (replaces raw card number since Oct 2022)
  pre_debit_notification_sent — RBI e-mandate: PDN must be sent 24h before debit
  consecutive_failures — how many times in a row this mandate has failed
  last_successful_debit_days_ago — mandate vitality signal
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from engine.schemas import IssuerBank, PaymentFailureEvent, Rail


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.now(timezone.utc).replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_issuer_bank(payload: dict[str, Any]) -> IssuerBank:
    """
    Extract the issuer bank from multiple possible payload locations.
    Razorpay webhooks may include it as:
      payload["issuer_bank"], payload["bank"], payload["card"]["issuer"],
      payload["notes"]["issuer_bank"]
    """
    raw = (
        payload.get("issuer_bank")
        or payload.get("bank")
        or (payload.get("card") or {}).get("issuer", "")
        or (payload.get("notes") or {}).get("issuer_bank", "")
        or "other"
    )
    bank_str = str(raw).lower().strip()
    # Normalize common aliases
    aliases: dict[str, str] = {
        "state bank of india": "sbi", "sbm": "sbi",
        "hdfc bank": "hdfc", "hdfc bank ltd": "hdfc",
        "icici bank": "icici",
        "axis bank": "axis",
        "kotak mahindra bank": "kotak", "kotak bank": "kotak",
        "bandhan bank": "bandhan",
        "jio payments bank": "jio", "jio": "jio",
        "yes bank": "yes",
        "indusind bank": "indusind",
        "rbl bank": "rbl",
    }
    bank_str = aliases.get(bank_str, bank_str)
    try:
        return IssuerBank(bank_str)
    except ValueError:
        return IssuerBank.OTHER


def normalize_raw(payload: dict[str, Any]) -> PaymentFailureEvent:
    """
    Accepts Razorpay-shaped synthetic payloads for card or UPI AutoPay failures.

    Handles both webhook-style (card.id, error.code) and our internal fields.
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

    # ISO 8583 code (uppercase, e.g. "05", "51", "R0")
    decline_iso_code = (
        payload.get("decline_iso_code")
        or error.get("iso_code")
        or error.get("network_code")
        or None
    )
    if decline_iso_code:
        decline_iso_code = str(decline_iso_code).strip().upper()

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
        issuer_bank=_parse_issuer_bank(payload),
        decline_iso_code=decline_iso_code,
        mandate_id=payload.get("token_id") or payload.get("mandate_id") or notes.get("mandate_id"),
        card_id=(payload.get("card") or {}).get("id") if isinstance(payload.get("card"), dict) else payload.get("card_id"),
        token_id=payload.get("token_id") or payload.get("coft_token_id") or notes.get("token_id"),
        pre_debit_notification_sent=bool(
            payload.get("pre_debit_notification_sent",
            notes.get("pre_debit_notification_sent", True))
        ),
        prior_soft_recoveries=int(payload.get("prior_soft_recoveries") or notes.get("prior_soft_recoveries") or 0),
        prior_hard_declines=int(payload.get("prior_hard_declines") or notes.get("prior_hard_declines") or 0),
        hours_since_last_attempt=float(
            payload.get("hours_since_last_attempt") or notes.get("hours_since_last_attempt") or 0.0
        ),
        consecutive_failures=int(payload.get("consecutive_failures") or notes.get("consecutive_failures") or 0),
        last_successful_debit_days_ago=(
            int(payload["last_successful_debit_days_ago"])
            if payload.get("last_successful_debit_days_ago") is not None
            else (int(notes["last_successful_debit_days_ago"]) if notes.get("last_successful_debit_days_ago") is not None else None)
        ),
        has_alt_upi_mandate=bool(payload.get("has_alt_upi_mandate") or notes.get("has_alt_upi_mandate") or False),
        has_alt_card=bool(payload.get("has_alt_card") or notes.get("has_alt_card") or False),
        mandate_revoked=bool(payload.get("mandate_revoked") or notes.get("mandate_revoked") or False),
        payday_day_of_month=payload.get("payday_day_of_month") or notes.get("payday_day_of_month"),
        raw=payload,
    )
