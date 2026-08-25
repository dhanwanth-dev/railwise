"""Canonical schemas. Rail is a first-class field — not an afterthought."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Rail(str, Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"


class DeclineKind(str, Enum):
    SOFT = "soft"
    HARD = "hard"
    AMBIGUOUS = "ambiguous"
    REGULATORY = "regulatory"  # e.g. RBI approval required — never retry


class Action(str, Enum):
    """Bounded action set — executor cannot invent actions outside this enum."""

    RETRY_NOW = "retry_now"
    DELAYED_RETRY = "delayed_retry"
    RAIL_SWITCH = "rail_switch"
    DUNNING = "dunning"
    STOP = "stop"


class ConstraintCode(str, Enum):
    HARD_DECLINE = "hard_decline"
    MANDATE_REVOKED = "mandate_revoked"
    REGULATORY_BLOCK = "regulatory_block"
    ATTEMPT_BUDGET_EXHAUSTED = "attempt_budget_exhausted"
    UPI_COOLDOWN = "upi_cooldown"
    CARD_OVER_RETRY = "card_over_retry"
    KILL_SWITCH = "kill_switch"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    AMOUNT_NEEDS_CUSTOMER_ACTION = "amount_needs_customer_action"
    NETBANKING_NO_RETRY = "netbanking_no_retry"


class PaymentFailureEvent(BaseModel):
    """Normalized failure event after ingest. Downstream never sees raw rail payloads."""

    payment_id: str
    customer_id: str
    merchant_id: str = "merch_demo"
    rail: Rail
    decline_code: str
    amount_paise: int
    currency: str = "INR"
    timestamp: datetime
    attempt_number: int = Field(ge=1, description="1 = original debit attempt")
    mandate_id: Optional[str] = None
    card_id: Optional[str] = None
    # Prior soft recoveries for this instrument — feeds ambiguous-code classifier
    prior_soft_recoveries: int = 0
    prior_hard_declines: int = 0
    hours_since_last_attempt: float = 0.0
    has_alt_upi_mandate: bool = False
    has_alt_card: bool = False
    mandate_revoked: bool = False
    payday_day_of_month: Optional[int] = None  # synthetic customer payday hint
    raw: dict[str, Any] = Field(default_factory=dict)


class ClassificationResult(BaseModel):
    decline_kind: DeclineKind
    recoverability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str  # "rules" | "model" | "rules+model"
    reason_codes: list[str] = Field(default_factory=list)
    feature_importance: dict[str, float] = Field(default_factory=dict)


class ConstraintHit(BaseModel):
    code: ConstraintCode
    message: str
    overrides_recoverability: bool = True
    forced_action: Optional[Action] = None
    min_delay_minutes: Optional[float] = None


class Decision(BaseModel):
    decision_id: str
    payment_id: str
    action: Action
    scheduled_at: Optional[datetime] = None
    delay_minutes: Optional[float] = None
    target_rail: Optional[Rail] = None
    classification: ClassificationResult
    constraint_hits: list[ConstraintHit] = Field(default_factory=list)
    reason_chain: list[str] = Field(default_factory=list)
    idempotency_key: str
    policy_name: str = "railwise"
    executed: bool = False
    execution_result: Optional[str] = None
    recovered_amount_paise: int = 0
    compliance_violation: bool = False
    created_at: datetime = Field(default_factory=_utc_now)


class BatchMetrics(BaseModel):
    policy_name: str
    total_failures: int
    soft_failures: int
    hard_failures: int
    recovered_count: int
    recovered_paise: int
    soft_recovery_rate: float
    hard_decline_wasted_retries: int
    upi_cooldown_violations: int
    decisions_with_audit: int
    audit_coverage_pct: float
    action_counts: dict[str, int]
