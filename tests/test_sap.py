import json

import pytest

from mdm_config import APPROVAL_POLICIES
from services.access_service import Actor
from services.approval_service import decide_request
from services.request_service import RequestData, get_request, submit_request
from services.sap_service import export_sap_handoff


def test_sap_handoff_gates_payload_and_audit(requestor):
    result = submit_request(RequestData(
        record_type="Vendor", name="Handoff Supplies", country="Singapore",
        business_entity="SPD", justification="Need supplier",
        details={"registration_number": "REG123", "contact_email": "demo@example.com",
                 "address": "1 Example Road"},
    ), requestor)
    assert result.success
    request_id = result.request_id
    with pytest.raises(ValueError, match="fully approved"):
        export_sap_handoff(request_id, requestor)
    for role in APPROVAL_POLICIES["Vendor"]:
        decide_request(request_id, role, "Approved", "Checked",
                       Actor(f"reviewer_{role}", frozenset({role})))
    with pytest.raises(PermissionError):
        export_sap_handoff(request_id, Actor("stranger", frozenset({"requestor"})))
    with pytest.raises(PermissionError):
        export_sap_handoff(request_id, Actor("auditor", frozenset({"auditor"}), countries=()))
    payload = json.loads(export_sap_handoff(request_id, requestor))
    assert payload["mode"] == "simulation"
    assert payload["record"]["name"] == "Handoff Supplies"
    assert payload["record"]["details"]["registration_number"] == "REG123"
    assert "bank_account" not in json.dumps(payload)
    assert "credit_terms" not in json.dumps(payload)
    assert payload["handoff_reference"] == json.loads(export_sap_handoff(request_id, requestor))["handoff_reference"]
    request = get_request(request_id, requestor)
    assert request["status"] == "Approved"
    assert request["audit"][-1]["Action"] == "SAP_HANDOFF_PREPARED"
    assert payload["local_master_record_id"] == request["master_record_id"]
