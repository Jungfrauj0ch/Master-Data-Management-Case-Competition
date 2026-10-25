"""Prepare a demo handoff file; no SAP connection or receipt is implied."""

import json

from sqlalchemy import select

from database.connection import write_session
from mdm_config import INTAKE_FIELDS
from models.mdm import ApprovalStep, MasterRecord, MdmRequest, utc_now
from services.access_service import Actor, require_view
from services.workflow_service import audit


def export_sap_handoff(request_id: int, actor: Actor) -> str:
    with write_session() as session:
        request = session.get(MdmRequest, request_id)
        require_view(actor, request)
        steps = session.scalars(select(ApprovalStep).where(
            ApprovalStep.request_id == request_id,
        )).all()
        master = session.get(MasterRecord, request.master_record_id) if request.master_record_id else None
        if request.status != "Approved" or not steps or any(step.status != "Approved" for step in steps) or master is None:
            raise ValueError("SAP handoff requires a fully approved request and a local master record.")
        details = json.loads(request.details_json)
        payload = {
            "schema_version": 1,
            "mode": "simulation",
            "status": "Prepared for handoff",
            "handoff_reference": f"MDM-{request.id}-MASTER-{master.id}",
            "prepared_at_utc": utc_now().isoformat(),
            "request_id": request.id,
            "local_master_record_id": master.id,
            "record": {
                "type": master.record_type, "name": master.name,
                "country": master.country, "business_entity": master.business_entity,
                "details": {key: details[key] for key in INTAKE_FIELDS[request.record_type] if key in details},
            },
        }
        contents = json.dumps(payload, indent=2, ensure_ascii=False)
        audit(session, request_id, "SAP_HANDOFF_PREPARED", actor.id,
              f"Simulated JSON handoff prepared for local master #{master.id}; no data sent to SAP.")
    return contents
