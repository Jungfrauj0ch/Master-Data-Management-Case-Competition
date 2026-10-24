"""Approval decisions and sensitive reads are authorized and audited transactions."""

from sqlalchemy import select

from mdm_config import APPROVAL_ROLES, MAX_JUSTIFICATION_LENGTH
from database.connection import write_session
from models.mdm import ApprovalStep, MasterRecord, MdmRequest, SensitiveData, utc_now
from services.access_service import Actor, can_decide, require_view
from services.duplicate_service import find_matches
from services.security_service import decrypt_sensitive
from services.workflow_service import audit, notify_owner


def decide_request(request_id: int, role: str, decision: str, reason: str, actor: Actor) -> str:
    if decision not in {"Approved", "Rejected"}:
        raise ValueError("Choose Approve or Reject.")
    reason = reason.strip()
    if not reason or len(reason) > MAX_JUSTIFICATION_LENGTH:
        raise ValueError(f"A decision reason of 1–{MAX_JUSTIFICATION_LENGTH} characters is required.")
    with write_session() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        if not can_decide(actor, request, role):
            raise PermissionError("You cannot decide this request. Self-approval is prohibited.")
        steps = session.scalars(select(ApprovalStep).where(
            ApprovalStep.request_id == request_id,
        ).order_by(ApprovalStep.id)).all()
        step = next((step for step in steps if step.role == role), None)
        if step is not None and step.status == "Needs Clarification":
            raise ValueError("Wait for the requestor's reply before deciding this check.")
        if step is None or step.status != "Pending":
            raise ValueError("This approval is no longer pending. Refresh the queue.")
        if any(other.decided_by == actor.id for other in steps):
            raise PermissionError("Each approval check must be decided by a different person.")
        step.status = decision
        step.decided_by = actor.id
        step.decided_at = utc_now()
        step.reason = reason
        audit(session, request_id, f"APPROVAL_{decision.upper()}", actor.id,
              f"{APPROVAL_ROLES[role]}: {reason}")
        if decision == "Rejected":
            request.status = "Rejected"
            for other in steps:
                if other.status in {"Pending", "Needs Clarification"}:
                    other.status = "Cancelled"
            notify_owner(session, request, f"Request #{request_id} was rejected. See the decision history for the reason.")
            audit(session, request_id, "REQUEST_REJECTED", actor.id)
        elif all(other.status == "Approved" for other in steps):
            # Recheck at finalization in case master data was added by another integration.
            if any(match.source == "Master" for match in find_matches(
                session, request.record_type, request.name, request.country,
            )):
                raise ValueError("An existing master record now matches this request. Ask a data steward to review it.")
            master = MasterRecord(
                record_type=request.record_type, name=request.name,
                normalized_name=request.normalized_name, country=request.country,
                business_entity=request.business_entity,
            )
            session.add(master)
            session.flush()
            request.master_record_id = master.id
            request.status = "Approved"
            audit(session, request_id, "MASTER_RECORD_CREATED", actor.id, f"Local master record #{master.id} created.")
            notify_owner(session, request, f"Request #{request_id} approved. Local master record #{master.id} created.")
        else:
            notify_owner(session, request,
                         f"{APPROVAL_ROLES[role]} approved request #{request_id}. Other checks remain pending.")
        return request.status


def reveal_sensitive(request_id: int, actor: Actor, reason: str) -> dict[str, str]:
    if not reason.strip() or len(reason.strip()) > 500:
        raise ValueError("Enter an access reason of 1–500 characters.")
    with write_session() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        if not actor.roles.intersection({"finance", "compliance"}):
            raise PermissionError("Only Finance and Compliance can view sensitive fields.")
        data = session.get(SensitiveData, request_id)
        if data is None:
            raise ValueError("No sensitive fields were supplied.")
        payload = decrypt_sensitive(data.ciphertext)
        audit(session, request_id, "SENSITIVE_DATA_VIEWED", actor.id, reason.strip())
    # Return plaintext only after the access audit commits successfully.
    return payload
