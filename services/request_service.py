"""Validated intake, duplicate discovery, and authorized request read models."""

from dataclasses import dataclass, field
import csv
import io
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from mdm_config import (
    BUSINESS_ENTITIES, COUNTRIES, INTAKE_FIELDS, INTAKE_OPTIONS, MAX_JUSTIFICATION_LENGTH,
    MAX_NAME_LENGTH, RECORD_TYPES,
)
from database.connection import SessionLocal, write_session
from models.mdm import ApprovalStep, AuditLog, Clarification, MdmRequest, SensitiveData
from services.access_service import Actor, can_view, require_view
from services.duplicate_service import find_matches, normalize_name
from services.security_service import SecurityConfigurationError, encrypt_sensitive
from services.workflow_service import audit, start_approvals

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestData:
    record_type: str
    name: str
    country: str
    business_entity: str
    justification: str
    details: dict[str, str] = field(default_factory=dict)
    bank_account: str = field(default="", repr=False)
    credit_terms: str = field(default="", repr=False)


@dataclass(frozen=True)
class SubmissionResult:
    success: bool
    message: str
    request_id: int | None = None
    duplicates: tuple[str, ...] = ()


def validate_request(data: RequestData) -> str | None:
    if data.record_type not in RECORD_TYPES:
        return "Choose a valid record type."
    if data.country not in COUNTRIES:
        return "Choose a valid country."
    if data.business_entity not in BUSINESS_ENTITIES:
        return "Choose a valid business entity."
    if not normalize_name(data.name):
        return "Enter a valid record name."
    if len(data.name.strip()) > MAX_NAME_LENGTH:
        return "Record name is too long."
    if not data.justification.strip():
        return "A business justification is required."
    if len(data.justification.strip()) > MAX_JUSTIFICATION_LENGTH:
        return "Justification is too long."
    fields = INTAKE_FIELDS[data.record_type]
    if set(data.details) - set(fields):
        return "Unexpected intake fields. Use the fields for the selected record type."
    for key, label in fields.items():
        value = data.details.get(key, "").strip()
        if not value:
            return f"{label} is required."
        if len(value) > 500:
            return f"{label} must be 500 characters or fewer."
        options = INTAKE_OPTIONS.get(data.record_type, {}).get(key)
        if options and value not in options:
            return f"Choose a valid {label.lower()}."
        if key == "contact_email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            return "Enter a valid business contact email."
    if data.bank_account.strip() and data.record_type != "Vendor":
        return "Bank account details are only accepted for vendors."
    if data.credit_terms.strip() and data.record_type != "Customer":
        return "Credit terms are only accepted for customers."
    if max(len(data.bank_account), len(data.credit_terms)) > 1000:
        return "Sensitive fields must be 1,000 characters or fewer."
    return None


def describe_match(match) -> str:
    return (f"{match.name} ({match.entity}, {match.country}; {match.source} #{match.id}; "
            f"matched on: {', '.join(match.reasons)})")


def find_duplicates(session, data: RequestData) -> tuple[str, ...]:
    return tuple(
        describe_match(match)
        for match in find_matches(session, data.record_type, data.name, data.country, data.details)
        if match.blocking
    )


def search_duplicates(actor: Actor, record_type: str, name: str, country: str,
                      details: dict[str, str] | None = None) -> list[dict]:
    if country not in actor.countries:
        raise PermissionError("Country is outside your access scope.")
    with SessionLocal() as session:
        # Cross-entity discovery exposes only identity metadata, never request payloads.
        return [
            {"Source": match.source, "ID": match.id, "Name": match.name,
             "Country": match.country, "Entity": match.entity, "Match %": match.score,
             "Matched on": ", ".join(match.reasons),
             "Action": "Blocks submission" if match.blocking else "Review shared contact details"}
            for match in find_matches(session, record_type, name, country, details)
        ]


def submit_request(data: RequestData, actor: Actor) -> SubmissionResult:
    error = validate_request(data)
    if error:
        return SubmissionResult(False, error)
    if "requestor" not in actor.roles or data.country not in actor.countries or data.business_entity not in actor.entities:
        return SubmissionResult(False, "You do not have permission to submit this request.")
    sensitive = {key: value.strip() for key, value in {
        "bank_account": data.bank_account, "credit_terms": data.credit_terms,
    }.items() if value.strip()}
    try:
        # Encrypt before starting the transaction. Never store plaintext as a fallback.
        ciphertext = encrypt_sensitive(sensitive) if sensitive else None
        with write_session() as session:
            matches = find_matches(session, data.record_type, data.name, data.country, data.details)
            duplicates = tuple(describe_match(match) for match in matches if match.blocking)
            if duplicates:
                return SubmissionResult(False, "Possible duplicate detected. Check existing records before proceeding.",
                                        duplicates=duplicates)
            request = MdmRequest(
                record_type=data.record_type, name=data.name.strip(),
                normalized_name=normalize_name(data.name), country=data.country,
                business_entity=data.business_entity, justification=data.justification.strip(),
                requestor=actor.id,
                details_json=json.dumps({key: value.strip() for key, value in data.details.items()}),
            )
            session.add(request)
            session.flush()
            if ciphertext:
                session.add(SensitiveData(request_id=request.id, ciphertext=ciphertext))
            start_approvals(session, request)
            audit(session, request.id, "REQUEST_CREATED", actor.id,
                  "Standard intake validated; parallel approval checks opened.")
            request_id = request.id
        return SubmissionResult(True, "Request submitted successfully.", request_id=request_id,
                                duplicates=tuple(describe_match(match) for match in matches if not match.blocking))
    except SecurityConfigurationError as exc:
        return SubmissionResult(False, str(exc))
    except SQLAlchemyError:
        logger.error("Database operation failed during request submission")
        return SubmissionResult(False, "Database error. Please try again.")


def list_requests(actor: Actor) -> list[dict]:
    with SessionLocal() as session:
        requests = session.scalars(select(MdmRequest).where(
            MdmRequest.country.in_(actor.countries), MdmRequest.business_entity.in_(actor.entities),
            MdmRequest.status != "Deleted",
        ).order_by(MdmRequest.id.desc())).all()
        steps = session.scalars(select(ApprovalStep).join(MdmRequest).where(
            MdmRequest.country.in_(actor.countries), MdmRequest.business_entity.in_(actor.entities),
        )).all()
        by_request = {}
        for step in steps:
            by_request.setdefault(step.request_id, []).append(step)
        return [
            {"ID": request.id, "Type": request.record_type, "Name": request.name,
             "Country": request.country, "Entity": request.business_entity,
             "Requestor": request.requestor, "Status": request.status,
             "Progress": f"{sum(step.status == 'Approved' for step in by_request.get(request.id, []))}"
                         f"/{len(by_request.get(request.id, []))}",
             "Waiting on": ", ".join(
                 step.role.replace("_", " ") + (" (requestor reply)" if step.status == "Needs Clarification" else "")
                 for step in by_request.get(request.id, []) if step.status in {"Pending", "Needs Clarification"}
             ) or "—",
             "Created (UTC)": request.created_at.strftime("%Y-%m-%d %H:%M")}
            for request in requests if can_view(actor, request)
        ]


def list_reply_requests(actor: Actor) -> list[dict]:
    """Outstanding questions remain actionable even after notifications are read."""
    if "requestor" not in actor.roles:
        return []
    with SessionLocal() as session:
        rows = session.execute(select(MdmRequest, Clarification).join(
            Clarification, Clarification.request_id == MdmRequest.id,
        ).join(ApprovalStep, (ApprovalStep.request_id == MdmRequest.id)
               & (ApprovalStep.role == Clarification.role)).where(
            MdmRequest.requestor == actor.id, MdmRequest.status == "Pending Approval",
            MdmRequest.country.in_(actor.countries), MdmRequest.business_entity.in_(actor.entities),
            Clarification.answered_at.is_(None), ApprovalStep.status == "Needs Clarification",
        ).order_by(Clarification.asked_at, Clarification.id)).all()
        requests = {}
        for request, question in rows:
            item = requests.setdefault(request.id, {"ID": request.id, "Name": request.name,
                                                   "Questions": 0, "Roles": []})
            item["Questions"] += 1
            if question.role not in item["Roles"]:
                item["Roles"].append(question.role)
        return list(requests.values())


def get_request(request_id: int, actor: Actor) -> dict:
    with SessionLocal() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        steps = session.scalars(select(ApprovalStep).where(
            ApprovalStep.request_id == request_id,
        ).order_by(ApprovalStep.id)).all()
        logs = session.scalars(select(AuditLog).where(
            AuditLog.request_id == request_id,
        ).order_by(AuditLog.created_at, AuditLog.id)).all()
        clarifications = session.scalars(select(Clarification).where(
            Clarification.request_id == request_id,
        ).order_by(Clarification.id)).all()
        return {
            "id": request.id, "name": request.name, "record_type": request.record_type,
            "country": request.country, "entity": request.business_entity,
            "status": request.status, "requestor": request.requestor,
            "justification": request.justification, "details": json.loads(request.details_json),
            "master_record_id": request.master_record_id,
            "sensitive_present": session.get(SensitiveData, request_id) is not None,
            "clarifications": [{"id": item.id, "role": item.role, "question": item.question,
                                "asked_by": item.asked_by, "asked_at": item.asked_at,
                                "answer": item.answer, "answered_by": item.answered_by,
                                "answered_at": item.answered_at} for item in clarifications],
            "steps": [{"ID": step.id, "Role": step.role, "Status": step.status,
                       "Due (UTC)": step.due_at, "Decided by": step.decided_by,
                       "Decided (UTC)": step.decided_at, "Reason": step.reason} for step in steps],
            "audit": [{"Time (UTC)": log.created_at, "Action": log.action,
                       "Actor": log.actor, "Details": log.details} for log in logs],
        }


def delete_request(request_id: int, actor: Actor) -> None:
    """Remove an owned request from active views while retaining its audit trail."""
    with write_session() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        if "requestor" not in actor.roles or request.requestor != actor.id:
            raise PermissionError("Only the requestor can delete their own request.")
        if request.status == "Approved" or request.master_record_id is not None:
            raise ValueError("Approved requests with master records cannot be deleted.")
        if request.status == "Deleted":
            raise ValueError("This request has already been deleted.")
        request.status = "Deleted"
        for step in session.scalars(select(ApprovalStep).where(ApprovalStep.request_id == request_id)):
            if step.status in {"Pending", "Needs Clarification"}:
                step.status = "Cancelled"
        audit(session, request_id, "REQUEST_DELETED", actor.id,
              "Request removed from active views by its requestor; history retained.")


def export_audit(request_id: int, actor: Actor) -> str:
    with write_session() as session:
        require_view(actor, session.get(MdmRequest, request_id))
        audit(session, request_id, "AUDIT_EXPORT_PREPARED", actor.id)
        session.flush()
        logs = session.scalars(select(AuditLog).where(
            AuditLog.request_id == request_id,
        ).order_by(AuditLog.created_at, AuditLog.id)).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Request ID", "Time (UTC)", "Action", "Actor", "Details"])
        for log in logs:
            cells = [str(request_id), log.created_at.isoformat(), log.action, log.actor, log.details]
            writer.writerow(["'" + cell if cell.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                             else cell for cell in cells])
    return output.getvalue()
