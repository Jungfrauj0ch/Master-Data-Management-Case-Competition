"""Request-specific questions and replies, committed with workflow and audit changes."""

from datetime import timedelta

from sqlalchemy import select

from mdm_config import APPROVAL_ROLES, APPROVAL_SLA_HOURS, MAX_JUSTIFICATION_LENGTH
from database.connection import write_session
from models.mdm import ApprovalStep, Clarification, MdmRequest, Notification, utc_now
from services.access_service import Actor, can_decide, require_view
from services.workflow_service import audit, notify_owner


def _validate_message(value: str, label: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError(f"{label} must contain 1–{MAX_JUSTIFICATION_LENGTH} characters.")
    return value


def ask_clarification(request_id: int, role: str, question: str, actor: Actor) -> int:
    question = _validate_message(question, "Question")
    with write_session() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        if not can_decide(actor, request, role):
            raise PermissionError("Only an assigned approver can ask the requestor for clarification.")
        step = session.scalar(select(ApprovalStep).where(
            ApprovalStep.request_id == request_id, ApprovalStep.role == role,
        ))
        if step is None or step.status != "Pending":
            raise ValueError("This check is closed or already waiting for a reply.")
        clarification = Clarification(request_id=request_id, role=role, question=question, asked_by=actor.id)
        session.add(clarification)
        session.flush()
        step.status = "Needs Clarification"
        audit(session, request_id, "CLARIFICATION_REQUESTED", actor.id,
              f"Question #{clarification.id} ({APPROVAL_ROLES[role]}): {question}")
        notify_owner(session, request,
                     f"{APPROVAL_ROLES[role]} asked a question on request #{request_id}. Open the request to reply.")
        clarification_id = clarification.id
    return clarification_id


def answer_clarification(clarification_id: int, answer: str, actor: Actor) -> None:
    answer = _validate_message(answer, "Reply")
    with write_session() as session:
        clarification = session.get(Clarification, clarification_id)
        request = session.get(MdmRequest, clarification.request_id) if clarification else None
        require_view(actor, request)
        if request.requestor != actor.id:
            raise PermissionError("Only the requestor can reply to this question.")
        if request.status != "Pending Approval":
            raise ValueError("This request is closed. Its clarification history is read-only.")
        if clarification.answered_at is not None:
            raise ValueError("This question already has a reply.")
        step = session.scalar(select(ApprovalStep).where(
            ApprovalStep.request_id == request.id, ApprovalStep.role == clarification.role,
        ))
        if step is None or step.status != "Needs Clarification":
            raise ValueError("This approval check is no longer waiting for a reply.")
        now = utc_now()
        clarification.answer = answer
        clarification.answered_by = actor.id
        clarification.answered_at = now
        step.status = "Pending"
        # The approver's response window restarts once the requested information arrives.
        step.due_at = now + timedelta(hours=APPROVAL_SLA_HOURS)
        step.last_reminded_at = None
        audit(session, request.id, "CLARIFICATION_ANSWERED", actor.id,
              f"Reply to question #{clarification.id} ({APPROVAL_ROLES[clarification.role]}): {answer}")
        session.add(Notification(
            request_id=request.id, recipient=clarification.asked_by,
            message=f"The requestor replied to your question on request #{request.id}. Your approval check can continue.",
        ))
