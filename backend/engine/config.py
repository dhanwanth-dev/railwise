"""Runtime toggles for ablation studies and sandbox testing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EngineConfig(BaseModel):
    """
    Toggle Railwise layers on/off for live ablation in the control panel.

    Full Railwise: all True.
    Rules-only baseline: use_ml_model=False, defensive AI off.
    No compliance: use_compliance_blocks=False (demo only — never production).
    """

    use_ml_model: bool = Field(default=True, description="Logistic model for ambiguous decline codes")
    use_compliance_blocks: bool = Field(default=True, description="NPCI/RBI hard constraint gate")
    use_issuer_health: bool = Field(default=True, description="Cross-customer issuer outage backoff")
    use_mandate_vitality: bool = Field(default=True, description="Proactive mandate death scoring")
    use_timing_ai: bool = Field(default=True, description="Issuer-aware payday/non-peak timing")

    @classmethod
    def full(cls) -> "EngineConfig":
        return cls()

    @classmethod
    def rules_only(cls) -> "EngineConfig":
        return cls(use_ml_model=False, use_issuer_health=False, use_mandate_vitality=False)

    @classmethod
    def no_compliance(cls) -> "EngineConfig":
        return cls(use_compliance_blocks=False)

    def label(self) -> str:
        parts = []
        if self.use_ml_model:
            parts.append("ML")
        if self.use_compliance_blocks:
            parts.append("compliance")
        if self.use_issuer_health:
            parts.append("issuer")
        if self.use_mandate_vitality:
            parts.append("vitality")
        if self.use_timing_ai:
            parts.append("timing")
        return "+".join(parts) if parts else "minimal"
