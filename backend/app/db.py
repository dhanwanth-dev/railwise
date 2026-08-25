"""SQLite persistence for audit log + batch runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "railwise.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class AuditRow(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(String(64), index=True, nullable=False)
    payment_id = Column(String(64), index=True, nullable=False)
    policy_name = Column(String(64), index=True, nullable=False)
    batch_id = Column(String(64), index=True, nullable=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utc_now, nullable=False)


class BatchRow(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=_utc_now, nullable=False)
    railwise_metrics_json = Column(Text, nullable=False)
    baseline_metrics_json = Column(Text, nullable=False)


_engine = None
_SessionLocal = None


def init_db() -> None:
    global _engine, _SessionLocal
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(_engine)


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


def append_audit(session: Session, record: dict, batch_id: str | None = None) -> None:
    row = AuditRow(
        decision_id=record["decision_id"],
        payment_id=record["payment_id"],
        policy_name=record["policy_name"],
        batch_id=batch_id,
        payload_json=json.dumps(record),
    )
    session.add(row)


def save_batch(session: Session, batch_id: str, railwise: dict, baseline: dict) -> None:
    session.add(
        BatchRow(
            batch_id=batch_id,
            railwise_metrics_json=json.dumps(railwise),
            baseline_metrics_json=json.dumps(baseline),
        )
    )
