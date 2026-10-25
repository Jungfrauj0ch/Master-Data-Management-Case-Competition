from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from sqlalchemy import select

from database.connection import SessionLocal, initialize_database, write_session
from mdm_config import APPROVAL_POLICIES
from models.mdm import MasterRecord
from services.access_service import Actor
from services.approval_service import decide_request
from services.request_service import RequestData, get_request, search_duplicates, submit_request


def vendor(name="Orchid Medical Supplies", **changes):
    fields = dict(record_type="Vendor", name=name, country="Singapore", business_entity="SPD",
                  justification="New supplier", details={"registration_number": "REG-001",
                  "contact_email": "contact@example.com", "address": "1 Example Road"})
    fields.update(changes)
    return RequestData(**fields)


def approve(request_id, record_type="Vendor", roles=None):
    for role in roles or APPROVAL_POLICIES[record_type]:
        decide_request(request_id, role, "Approved", "Checked", Actor(role, frozenset({role})))


@pytest.mark.parametrize("master", [False, True])
@pytest.mark.parametrize("record_type,key,original,variant", [
    ("Vendor", "registration_number", "REG-001", " reg 001 "),
    ("Customer", "registration_number", "REG-001", "ＲＥＧ００１"),
    ("Item", "item_code", "IT-001", "it 001"),
    ("Cost Center", "cost_center_code", "CC-001", "cc001"),
])
def test_identifiers_block_different_names(requestor, master, record_type, key, original, variant):
    details = {key: original}
    if record_type in {"Customer", "Vendor"}:
        details.update(contact_email="contact@example.com", address="1 Example Road")
    elif record_type == "Item":
        details["category"] = "Medical equipment"
    else:
        details["department"] = "Operations"
    first = submit_request(vendor(record_type=record_type, details=details), requestor)
    assert first.success
    if master:
        approve(first.request_id, record_type)
    result = submit_request(vendor("Zenith Trading", record_type=record_type,
                                   business_entity="QTG", details={**details, key: variant}), requestor)
    assert not result.success
    assert any("matched on:" in match for match in result.duplicates)
    matches = search_duplicates(requestor, record_type, "Zenith Trading", "Singapore", {key: variant})
    assert matches[0]["Source"] == ("Master" if master else "Request")
    assert matches[0]["Action"] == "Blocks submission"
    # Cross-entity discovery reveals the reason, not the identifier value.
    assert original not in str(matches)


@pytest.mark.parametrize("shared", [
    {"contact_email": " CONTACT@EXAMPLE.COM "},
    {"address": "1, EXAMPLE Road."},
])
def test_shared_contacts_warn_without_blocking(requestor, shared):
    first = submit_request(vendor(), requestor)
    assert first.success
    details = {"registration_number": "OTHER002", "contact_email": "different@example.com",
               "address": "99 Other Lane", **shared}
    second = submit_request(vendor("Zenith Trading", details=details), requestor)
    assert second.success
    assert second.duplicates
    approve(second.request_id)


def test_missing_identifiers_and_different_scope_do_not_match(requestor):
    assert submit_request(vendor(), requestor).success
    assert not search_duplicates(requestor, "Vendor", "Zenith Trading", "Singapore",
                                 {"registration_number": "", "contact_email": "", "address": ""})
    assert not search_duplicates(requestor, "Customer", "Zenith Trading", "Singapore",
                                 {"registration_number": "REG001"})
    assert not search_duplicates(requestor, "Vendor", "Zenith Trading", "Malaysia",
                                 {"registration_number": "REG001"})


def test_concurrent_same_identifier_creates_one_request(requestor):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: submit_request(vendor(name), requestor),
                                ["Orchid Medical Supplies", "Zenith Trading"]))
    assert sum(result.success for result in results) == 1


def test_identifier_conflict_at_final_approval_rolls_back(requestor):
    first = submit_request(vendor(), requestor)
    assert first.success
    roles = APPROVAL_POLICIES["Vendor"]
    approve(first.request_id, roles=roles[:-1])
    with write_session() as session:
        session.add(MasterRecord(record_type="Vendor", name="Zenith Trading", normalized_name="zenith trading",
                                 country="Singapore", business_entity="QTG",
                                 details_json=json.dumps({"registration_number": "reg001"})))
    with pytest.raises(ValueError, match="existing master"):
        approve(first.request_id, roles=roles[-1:])
    request = get_request(first.request_id, requestor)
    assert request["status"] == "Pending Approval"
    assert request["steps"][-1]["Status"] == "Pending"


def test_existing_master_details_are_backfilled(requestor):
    first = submit_request(vendor(), requestor)
    approve(first.request_id)
    with write_session() as session:
        master = session.scalar(select(MasterRecord))
        assert json.loads(master.details_json)["registration_number"] == "REG-001"
        master.details_json = "{}"
    initialize_database()
    initialize_database()
    with SessionLocal() as session:
        assert json.loads(session.scalar(select(MasterRecord)).details_json)["registration_number"] == "REG-001"
