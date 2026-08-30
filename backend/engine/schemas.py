"""
Canonical schemas. Rail and IssuerBank are first-class fields — not afterthoughts.

Real-world grounding:
  - Rail matters because UPI and card have different NPCI/RBI compliance rules
  - IssuerBank matters because SBI has 0.90% technical decline rate vs HDFC's 0.02%
    (NPCI BD/TD monthly report, FY25). Same decline code from different banks = very
    different retry strategy.
  - MandateVitalityLevel models the reality that mandates die slowly, not all at once
  - IssuerHealthLevel enables cross-customer adaptive backoff (thundering herd defense)
"""

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


class IssuerBank(str, Enum):
    """
    Indian issuer banks tracked for health monitoring.
    Technical Decline (TD) rates from NPCI monthly report FY25:
      SBI: 0.90% | ICICI: 0.13% | Axis: 0.03% | HDFC: 0.02%
      Bandhan: 2.48% | Jio Payments: 7.23%
    """
    HDFC = "hdfc"
    SBI = "sbi"
    ICICI = "icici"
    AXIS = "axis"
    KOTAK = "kotak"
    BANDHAN = "bandhan"
    JIO = "jio"
    YES = "yes"
    INDUSIND = "indusind"
    RBL = "rbl"
    OTHER = "other"


# Baseline technical decline rates from NPCI data (for adaptive backoff calibration)
ISSUER_BASELINE_TD_RATES: dict[str, float] = {
    "hdfc": 0.0002,
    "sbi": 0.0090,
    "icici": 0.0013,
    "axis": 0.0003,
    "kotak": 0.0005,
    "bandhan": 0.0248,
    "jio": 0.0723,
    "yes": 0.0020,
    "indusind": 0.0015,
    "rbl": 0.0030,
    "other": 0.0050,
}


class IssuerHealthLevel(str, Enum):
    """
    Cross-customer issuer health assessment.
    CRITICAL means the issuer is experiencing a systemic outage — retrying all
    affected customers simultaneously creates a thundering herd making it worse.
    Adaptive backoff is the correct response.
    """
    HEALTHY = "healthy"     # TD rate within 2x baseline
    DEGRADED = "degraded"   # TD rate 2-5x baseline
    CRITICAL = "critical"   # TD rate >5x baseline — systemic outage suspected


class MandateVitalityLevel(str, Enum):
    """
    Mandate health assessment based on failure history.
    Mandates don't die instantly — they degrade: first NSF, then bank flags,
    then revocation. LIKELY_DEAD means dunning is better than another retry.
    """
    HEALTHY = "healthy"         # Normal failure, retry worthwhile
    AT_RISK = "at_risk"         # Mandate may be deteriorating
    LIKELY_DEAD = "likely_dead" # Mandate probably won't recover; dunning > retry


class DeclineKind(str, Enum):
    SOFT = "soft"
    HARD = "hard"
    AMBIGUOUS = "ambiguous"
    REGULATORY = "regulatory"


class Action(str, Enum):
    """Bounded action set — executor cannot invent actions outside this enum."""
    RETRY_NOW = "retry_now"
    DELAYED_RETRY = "delayed_retry"
    RAIL_SWITCH = "rail_switch"
    DUNNING = "dunning"
    STOP = "stop"


class ConstraintCode(str, Enum):
    # Compliance — absolute hard stops
    KILL_SWITCH = "kill_switch"
    MANDATE_REVOKED = "mandate_revoked"
    REGULATORY_BLOCK = "regulatory_block"
    HARD_DECLINE = "hard_decline"
    CUSTOMER_CANCELLED_RECURRING = "customer_cancelled_recurring"  # R0/R1 ISO 8583
    # NPCI/scheme ceilings
    ATTEMPT_BUDGET_EXHAUSTED = "attempt_budget_exhausted"
    UPI_COOLDOWN = "upi_cooldown"
    CARD_OVER_RETRY = "card_over_retry"
    # RBI e-mandate compliance (Framework 2026)
    PRE_DEBIT_NOTIFICATION_FAILED = "pre_debit_notification_failed"
    AMOUNT_NEEDS_CUSTOMER_ACTION = "amount_needs_customer_action"
    # Card-on-File Tokenization lifecycle (RBI CoFT, mandatory since Oct 2022)
    TOKEN_LIFECYCLE_ACTION = "token_lifecycle_action"
    # Velocity / limit constraints
    VELOCITY_LIMIT = "velocity_limit"
    NETBANKING_NO_RETRY = "netbanking_no_retry"
    # Intelligent defensive AI
    ISSUER_SYSTEMIC_BACKOFF = "issuer_systemic_backoff"
    MANDATE_VITALITY_CRITICAL = "mandate_vitality_critical"
    # Pipeline housekeeping
    IDEMPOTENT_REPLAY = "idempotent_replay"


class PaymentFailureEvent(BaseModel):
    """
    Normalized failure event after ingest. Downstream never sees raw rail payloads.

    New fields explained:
      issuer_bank         — which bank holds the customer's account (SBI, HDFC, etc.)
      decline_iso_code    — raw ISO 8583 code (e.g. "05", "51", "R0") for card network
      token_id            — RBI CoFT token ID (replaces raw card number since Oct 2022)
      pre_debit_notification_sent — RBI e-mandate: 24h PDN required before debit
      consecutive_failures — how many times in a row this mandate has failed
      last_successful_debit_days_ago — when did this mandate last succeed (mandate vitality)
    """
    payment_id: str
    customer_id: str
    merchant_id: str = "merch_demo"
    rail: Rail
    decline_code: str
    amount_paise: int
    currency: str = "INR"
    timestamp: datetime
    attempt_number: int = Field(ge=1, description="1 = original debit attempt")
    issuer_bank: IssuerBank = IssuerBank.OTHER
    decline_iso_code: Optional[str] = None
    mandate_id: Optional[str] = None
    card_id: Optional[str] = None
    token_id: Optional[str] = None
    pre_debit_notification_sent: bool = True
    prior_soft_recoveries: int = 0
    prior_hard_declines: int = 0
    hours_since_last_attempt: float = 0.0
    consecutive_failures: int = 0
    last_successful_debit_days_ago: Optional[int] = None
    has_alt_upi_mandate: bool = False
    has_alt_card: bool = False
    mandate_revoked: bool = False
    payday_day_of_month: Optional[int] = None
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
    classification: ClassificationResult = Field(default_factory=lambda: ClassificationResult(
        decline_kind=DeclineKind.SOFT, recoverability=0.5, confidence=0.5, source="default"
    ))
    constraint_hits: list[ConstraintHit] = Field(default_factory=list)
    reason_chain: list[str] = Field(default_factory=list)
    idempotency_key: str = ""
    policy_name: str = "railwise"
    executed: bool = False
    execution_result: Optional[str] = None
    recovered_amount_paise: int = 0
    compliance_violation: bool = False
    issuer_health_level: Optional[str] = None
    mandate_vitality_level: Optional[str] = None
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
    # New: compliance & AI metrics
    pdn_compliance_blocks: int = 0
    token_dunnings: int = 0
    issuer_adaptive_backoffs: int = 0
    mandate_vitality_dunnings: int = 0
    customer_cancelled_stops: int = 0
