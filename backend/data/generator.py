"""Synthetic Razorpay-shaped failure event generator (~80% soft declines)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SOFT = [
    "insufficient_funds",
    "nsf",
    "bank_technical_error",
    "issuer_unavailable",
    "gateway_timeout",
    "network_timeout",
    "processing_error",
    "temporary_failure",
    "debit_failed",
]
HARD = [
    "stolen_card",
    "lost_card",
    "expired_card",
    "card_blocked",
    "account_closed",
    "fraudulent",
    "do_not_retry",
    "invalid_account",
]
AMBIGUOUS = ["do_not_honor", "generic_decline", "declined", "issuer_declined"]
REGULATORY = ["rbi_approval_required", "authentication_required"]


def _payment_id(rng: random.Random, i: int) -> str:
    return f"pay_{rng.randint(10**12, 10**13 - 1)}_{i}"


def generate_event(rng: random.Random, i: int, base_time: datetime | None = None) -> dict[str, Any]:
    base_time = base_time or datetime(2026, 8, 1, 8, 0, 0)
    rail_roll = rng.random()
    if rail_roll < 0.48:
        method = "upi"
    elif rail_roll < 0.92:
        method = "card"
    else:
        method = "netbanking"

    kind_roll = rng.random()
    if kind_roll < 0.72:
        decline = rng.choice(SOFT)
        bucket = "soft"
    elif kind_roll < 0.82:
        decline = rng.choice(AMBIGUOUS)
        bucket = "ambiguous"
    elif kind_roll < 0.95:
        decline = rng.choice(HARD)
        bucket = "hard"
    else:
        decline = rng.choice(REGULATORY)
        bucket = "regulatory"

    attempt = rng.choices([1, 2, 3, 4, 5], weights=[45, 25, 15, 10, 5])[0]
    hours_since = 0.0
    if attempt > 1:
        # Mix: some within UPI cooldown (bad for baseline), some outside
        if method == "upi" and rng.random() < 0.35:
            hours_since = rng.uniform(0.05, 0.3)  # < 20 min
        else:
            hours_since = rng.uniform(0.5, 72.0)

    amount = rng.choice([19900, 49900, 99900, 149900, 299900, 999900, 1_999_00, 20_000_00])
    prior_soft = rng.randint(0, 3) if bucket == "ambiguous" else rng.randint(0, 2)
    prior_hard = rng.randint(0, 2) if bucket == "ambiguous" else 0

    ts = base_time + timedelta(hours=i * 0.1, minutes=rng.randint(0, 50))
    customer = f"cust_{rng.randint(1000, 9999)}"

    payload: dict[str, Any] = {
        "id": _payment_id(rng, i),
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": "failed",
        "method": method,
        "customer_id": customer,
        "created_at": int(ts.timestamp()),
        "attempt_number": attempt,
        "hours_since_last_attempt": hours_since,
        "prior_soft_recoveries": prior_soft,
        "prior_hard_declines": prior_hard,
        "has_alt_upi_mandate": rng.random() < 0.35,
        "has_alt_card": rng.random() < 0.25,
        "mandate_revoked": decline in ("mandate_revoked",) or (rng.random() < 0.02),
        "payday_day_of_month": rng.choice([1, 2, 5, 7, 15, 25, 28]),
        "error": {
            "code": decline,
            "description": f"Synthetic {bucket} decline",
            "source": "issuer" if method == "card" else "bank",
            "step": "payment_authorization",
            "reason": decline,
        },
        "notes": {
            "subscription_id": f"sub_{rng.randint(10000, 99999)}",
            "attempt_number": attempt,
        },
    }
    if method == "upi":
        payload["mandate_id"] = f"mandate_{rng.randint(10**8, 10**9)}"
        payload["token_id"] = payload["mandate_id"]
    if method == "card":
        payload["card"] = {"id": f"card_{rng.randint(10**8, 10**9)}", "network": rng.choice(["Visa", "MasterCard", "RuPay"])}
    return payload


def generate_batch(n: int = 500, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [generate_event(rng, i) for i in range(n)]


def write_batch(path: Path, n: int = 500, seed: int = 42) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = generate_batch(n, seed)
    path.write_text(json.dumps(data, indent=2))
    return path


if __name__ == "__main__":
    out = Path(__file__).parent / "synthetic_batch.json"
    write_batch(out, 500, 42)
    print(f"Wrote {out} ({500} events)")
