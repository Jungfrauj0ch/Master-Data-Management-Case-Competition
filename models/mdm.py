
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.connection import Base


def utc_now():
    return datetime.now(timezone.utc)


class MasterRecord(Base):
    __tablename__ = "master_records"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    record_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    normalized_name: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    country: Mapped[str] = mapped_column(
        String(60), nullable=False
    )
    business_entity: Mapped[str] = mapped_column(
        String(10), nullable=False
    )


class MdmRequest(Base):
    __tablename__ = "mdm_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    record_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    normalized_name: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    country: Mapped[str] = mapped_column(
        String(60), nullable=False
    )
    business_entity: Mapped[str] = mapped_column(
        String(10), nullable=False
    )
    justification: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="Pending Approval",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    requestor: Mapped[str] = mapped_column(String(200), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    master_record_id: Mapped[int | None] = mapped_column(ForeignKey("master_records.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    request_id: Mapped[int] = mapped_column(
        ForeignKey("mdm_requests.id"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(60), nullable=False
    )
    actor: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (UniqueConstraint("request_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("mdm_requests.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="Pending")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SensitiveData(Base):
    __tablename__ = "sensitive_data"

    request_id: Mapped[int] = mapped_column(ForeignKey("mdm_requests.id"), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)


class Clarification(Base):
    __tablename__ = "clarifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("mdm_requests.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    asked_by: Mapped[str] = mapped_column(String(200))
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(String(200))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("mdm_requests.id"), index=True)
    recipient: Mapped[str | None] = mapped_column(String(200), index=True)
    role: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "actor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id"))
    actor: Mapped[str] = mapped_column(String(200))
