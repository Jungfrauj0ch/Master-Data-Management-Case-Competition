"""Transactional workflow helpers; callers own the surrounding transaction."""

from datetime import timedelta

from mdm_config import APPROVAL_POLICIES, APPROVAL_SLA_HOURS
from models.mdm import ApprovalStep, AuditLog, Notification, utc_now


def audit(session, request_id: int, action: str, actor: str, details: str = ""):
    session.add(AuditLog(request_id=request_id, action=action, actor=actor, details=details))


def notify_owner(session, request, message: str):
    session.add(Notification(request_id=request.id, recipient=request.requestor, message=message))


def start_approvals(session, request):
    due = utc_now() + timedelta(hours=APPROVAL_SLA_HOURS)
    for role in APPROVAL_POLICIES[request.record_type]:
        session.add(ApprovalStep(request_id=request.id, role=role, due_at=due))
        session.add(Notification(
            request_id=request.id,
            role=role,
            message=f"Request #{request.id} is waiting for your team's approval.",
        ))
    notify_owner(session, request, f"Request #{request.id} submitted. Independent approvals are running in parallel.")
