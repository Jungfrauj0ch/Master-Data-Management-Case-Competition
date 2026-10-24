"""Persisted in-app notifications. No sensitive payloads are sent by email."""

from datetime import timedelta, timezone

from sqlalchemy import exists, select

from mdm_config import APPROVAL_ROLES, REMINDER_INTERVAL_HOURS
from database.connection import SessionLocal, write_session
from models.mdm import ApprovalStep, MdmRequest, Notification, NotificationRead, utc_now
from services.access_service import Actor, in_scope
from services.workflow_service import audit, notify_owner


def as_utc(value):
    # SQLite stores datetimes without timezone information; all stored times are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def send_due_reminders(now=None) -> int:
    now = as_utc(now) if now else utc_now()
    cutoff = now - timedelta(hours=REMINDER_INTERVAL_HOURS)
    count = 0
    with write_session() as session:
        steps = session.scalars(select(ApprovalStep).join(MdmRequest).where(
            ApprovalStep.status == "Pending", MdmRequest.status == "Pending Approval",
            ApprovalStep.due_at <= now,
        )).all()
        for step in steps:
            if step.last_reminded_at and as_utc(step.last_reminded_at) > cutoff:
                continue
            request = session.get(MdmRequest, step.request_id)
            session.add(Notification(
                request_id=request.id, role=step.role,
                message=f"Approval overdue: request #{request.id} needs your team's decision.",
            ))
            notify_owner(session, request, f"Request #{request.id}: {APPROVAL_ROLES[step.role]} approval is overdue; a reminder was issued.")
            audit(session, request.id, "APPROVAL_REMINDER", "system", APPROVAL_ROLES[step.role])
            step.last_reminded_at = now
            count += 1
    return count


def _visible(session, notification: Notification, actor: Actor) -> bool:
    request = session.get(MdmRequest, notification.request_id)
    if request is None or not in_scope(actor, request):
        return False
    if notification.recipient is not None:
        return notification.recipient == actor.id
    if notification.role not in actor.roles or request.requestor == actor.id:
        return False
    # Team tasks disappear when that check closes, including rejection cancellation.
    if request.status != "Pending Approval":
        return False
    return session.scalar(select(exists().where(
        ApprovalStep.request_id == request.id, ApprovalStep.role == notification.role,
        ApprovalStep.status.in_(("Pending", "Needs Clarification")),
    )))


def list_notifications(actor: Actor) -> list[dict]:
    with SessionLocal() as session:
        notifications = session.scalars(select(Notification).where(
            (Notification.recipient == actor.id) | Notification.role.in_(actor.roles),
        ).order_by(Notification.id.desc())).all()
        read_ids = set(session.scalars(select(NotificationRead.notification_id).where(
            NotificationRead.actor == actor.id,
        )).all())
        return [{"ID": item.id, "Request": item.request_id, "Message": item.message,
                 "Created (UTC)": item.created_at, "Read": item.id in read_ids}
                for item in notifications if _visible(session, item, actor)]


def mark_notifications_read(actor: Actor):
    with write_session() as session:
        read_ids = set(session.scalars(select(NotificationRead.notification_id).where(
            NotificationRead.actor == actor.id,
        )).all())
        notifications = session.scalars(select(Notification).where(
            (Notification.recipient == actor.id) | Notification.role.in_(actor.roles),
        )).all()
        for notification in notifications:
            if notification.id not in read_ids and _visible(session, notification, actor):
                session.add(NotificationRead(notification_id=notification.id, actor=actor.id))


if __name__ == "__main__":
    from database.connection import initialize_database

    initialize_database()
    print(f"Issued {send_due_reminders()} approval reminders.")
