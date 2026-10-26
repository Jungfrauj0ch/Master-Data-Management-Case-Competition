from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import csv
import io
import json

import pytest
from sqlalchemy import func, select

from mdm_config import APPROVAL_POLICIES
from database.connection import SessionLocal, initialize_database, write_session
from database.seed import seed_demo_records
from models.mdm import ApprovalStep, AuditLog, MasterRecord, MdmRequest, SensitiveData, utc_now
from services.access_service import Actor
from services.approval_service import decide_request, reveal_sensitive
from services.auth_service import configured_actor
from services.notification_service import list_notifications, mark_notifications_read, send_due_reminders
from services.request_service import RequestData, export_audit, get_request, list_requests, search_duplicates, submit_request
from services.security_service import SecurityConfigurationError


def data(**changes):
    fields = dict(record_type="Vendor", name="Orchid Medical Supplies", country="Singapore",
                  business_entity="SPD", justification="New equipment supply partner",
                  details={"registration_number": "SG-123456", "contact_email": "contact@example.com",
                           "address": "1 Example Road"})
    fields.update(changes)
    return RequestData(**fields)


def approver(role, **kwargs):
    return Actor(f"approver_{role}", frozenset({role}), **kwargs)


def create(requestor, **changes):
    result = submit_request(data(**changes), requestor)
    assert result.success, result.message
    return result.request_id


@pytest.mark.parametrize("changes", [
    {"name": " !!! "}, {"country": "Nowhere"}, {"record_type": "Other"},
    {"business_entity": "Other"}, {"justification": " "}, {"details": {}},
    {"details": {"registration_number": "123", "contact_email": "bad", "address": "test"}},
    {"details": {"registration_number": "123", "contact_email": "a@b.co", "address": "test", "bank_account": "oops"}},
    {"credit_terms": "Net 30"},
])
def test_invalid_intake_creates_nothing(requestor, changes):
    assert not submit_request(data(**changes), requestor).success
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(MdmRequest)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize("record_type, details", [
    ("Customer", {"registration_number": "R123", "contact_email": "a@b.co", "address": "Example"}),
    ("Vendor", {"registration_number": "R123", "contact_email": "a@b.co", "address": "Example"}),
    ("Item", {"item_code": "IT001", "category": "Medical equipment"}),
    ("Cost Center", {"cost_center_code": "CC001", "department": "Operations"}),
])
def test_record_specific_forms(requestor, record_type, details):
    request_id = create(requestor, record_type=record_type, details=details)
    request = get_request(request_id, requestor)
    assert len(request["steps"]) == len(APPROVAL_POLICIES[record_type])
    assert request["details"] == details


@pytest.mark.parametrize("category", ["", "Unlisted product type"])
def test_item_type_must_be_a_supported_dropdown_option(requestor, category):
    result = submit_request(data(record_type="Item", details={"item_code": "IT001", "category": category}), requestor)
    assert not result.success
    assert "item type" in result.message.lower()


def test_duplicate_master_acronym_and_pending_requests(requestor):
    seed_demo_records()
    matches = search_duplicates(requestor, "Customer", "NUH", "Singapore")
    assert matches[0]["Name"] == "National University Hospital"
    assert not submit_request(data(record_type="Customer", name="National-University Hospital"), requestor).success
    request_id = create(requestor)
    duplicate = submit_request(data(name="ORCHID MEDICAL SUPPLIES!", business_entity="QTG"), requestor)
    assert not duplicate.success and duplicate.duplicates
    assert get_request(request_id, requestor)["status"] == "Pending Approval"
    assert create(requestor, name="医疗器材公司",
                  details={"registration_number": "SG-654321", "contact_email": "new@example.com",
                           "address": "2 Example Road"})


def test_parallel_approvals_create_exactly_one_master_and_audit(requestor):
    request_id = create(requestor)
    roles = APPROVAL_POLICIES["Vendor"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda role: decide_request(request_id, role, "Approved", "Checks passed", approver(role)), roles))
    assert results.count("Approved") == 1
    request = get_request(request_id, requestor)
    assert request["status"] == "Approved"
    assert request["master_record_id"]
    assert all(step["Status"] == "Approved" for step in request["steps"])
    assert len([log for log in request["audit"] if log["Action"] == "APPROVAL_APPROVED"]) == 5
    assert all(step["Decided by"] and step["Decided (UTC)"] and step["Reason"] for step in request["steps"])
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(MasterRecord)) == 1
    with pytest.raises(PermissionError):
        decide_request(request_id, roles[0], "Approved", "Again", approver(roles[0]))
    assert not submit_request(data(), requestor).success


def test_rejection_cancels_other_checks_and_allows_corrected_submission(requestor):
    request_id = create(requestor)
    assert decide_request(request_id, "finance", "Rejected", "Registration evidence is incomplete", approver("finance")) == "Rejected"
    request = get_request(request_id, requestor)
    assert sum(step["Status"] == "Cancelled" for step in request["steps"]) == 4
    assert list_notifications(approver("data_steward")) == []
    assert create(requestor) != request_id


def test_authorization_self_approval_scope_and_reason(requestor):
    request_id = create(requestor)
    for actor in (Actor("stranger", frozenset({"requestor"})),
                  Actor("elsewhere", frozenset({"finance"}), countries=("Malaysia",))):
        assert list_requests(actor) == []
        with pytest.raises(PermissionError):
            get_request(request_id, actor)
        with pytest.raises(PermissionError):
            export_audit(request_id, actor)
    with pytest.raises(PermissionError):
        decide_request(request_id, "finance", "Approved", "OK", replace(requestor, roles=frozenset({"finance"})))
    with pytest.raises(PermissionError):
        decide_request(request_id, "finance", "Approved", "OK", approver("data_steward"))
    with pytest.raises(ValueError):
        decide_request(request_id, "finance", "Approved", " ", approver("finance"))
    assert not submit_request(data(), approver("finance")).success


def test_distinct_people_required_for_multiple_roles(requestor):
    request_id = create(requestor)
    actor = Actor("multi_role", frozenset({"finance", "compliance"}))
    decide_request(request_id, "finance", "Approved", "OK", actor)
    with pytest.raises(PermissionError):
        decide_request(request_id, "compliance", "Approved", "OK", actor)


def test_concurrent_duplicate_submission_and_repeated_decision(requestor):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit_request(data(), requestor), range(2)))
    assert sum(result.success for result in results) == 1
    request_id = next(result.request_id for result in results if result.success)

    def decide(_):
        try:
            decide_request(request_id, "finance", "Approved", "OK", approver("finance"))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(decide, range(2))) == 1


def test_reminders_are_throttled_and_reads_are_per_person(requestor):
    request_id = create(requestor)
    now = utc_now()
    with write_session() as session:
        for step in session.scalars(select(ApprovalStep)):
            step.due_at = now - timedelta(hours=1)
    assert send_due_reminders(now) == 5
    assert send_due_reminders(now) == 0
    assert send_due_reminders(now + timedelta(hours=24)) == 5
    finance = approver("finance")
    assert len(list_notifications(finance)) == 3
    mark_notifications_read(finance)
    mark_notifications_read(finance)
    assert all(item["Read"] for item in list_notifications(finance))
    colleague = Actor("another_finance_user", frozenset({"finance"}))
    assert not any(item["Read"] for item in list_notifications(colleague))
    assert all(item["Request"] == request_id for item in list_notifications(requestor))
    assert list_notifications(Actor("outsider", frozenset({"requestor"}))) == []


def test_sensitive_data_fails_closed_without_key(requestor):
    result = submit_request(data(bank_account="SYNTHETIC-123456"), requestor)
    assert not result.success
    assert "MDM_ENCRYPTION_KEY" in result.message
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(MdmRequest)) == 0


def test_sensitive_access_is_denied_without_finance_or_compliance(requestor):
    request_id = create(requestor)
    # Permission is checked before decryption or key lookup, regardless of storage.
    with write_session() as session:
        session.add(SensitiveData(request_id=request_id, ciphertext="not-a-real-token"))
    with pytest.raises(PermissionError):
        reveal_sensitive(request_id, requestor, "Review")
    with pytest.raises(PermissionError):
        reveal_sensitive(request_id, approver("business_owner"), "Review")
    assert not any(log["Action"] == "SENSITIVE_DATA_VIEWED"
                   for log in get_request(request_id, requestor)["audit"])


def test_encrypted_storage_and_audited_access(requestor, monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet", reason="Encryption dependency installation was not approved")
    key = fernet.Fernet.generate_key().decode()
    monkeypatch.setenv("MDM_ENCRYPTION_KEY", key)
    secret = "SYNTHETIC-BANK-123456"
    request_id = create(requestor, bank_account=secret)
    request = get_request(request_id, requestor)
    assert request["sensitive_present"]
    assert secret not in json.dumps(request, default=str)
    with SessionLocal() as session:
        assert secret not in session.get(SensitiveData, request_id).ciphertext
    with pytest.raises(PermissionError):
        reveal_sensitive(request_id, requestor, "Need details")
    with pytest.raises(PermissionError):
        reveal_sensitive(request_id, approver("business_owner"), "Need details")
    assert reveal_sensitive(request_id, approver("finance"), "Verify bank evidence") == {"bank_account": secret}
    history = get_request(request_id, requestor)["audit"]
    assert history[-1]["Action"] == "SENSITIVE_DATA_VIEWED"
    assert secret not in export_audit(request_id, requestor)
    monkeypatch.setenv("MDM_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    with pytest.raises(SecurityConfigurationError):
        reveal_sensitive(request_id, approver("finance"), "Verify bank evidence")


def test_audit_csv_escapes_spreadsheet_formulas(requestor):
    request_id = create(requestor)
    decide_request(request_id, "finance", "Approved", '=HYPERLINK("unsafe")', approver("finance"))
    with write_session() as session:
        session.add(AuditLog(request_id=request_id, action="NOTE", actor="tester", details="=1+1"))
    rows = list(csv.reader(io.StringIO(export_audit(request_id, requestor))))
    assert any(row[-1] == "'=1+1" for row in rows)
    assert get_request(request_id, requestor)["audit"][-1]["Action"] == "AUDIT_EXPORT_PREPARED"


def test_finalization_conflict_rolls_back_decision(requestor):
    request_id = create(requestor)
    roles = APPROVAL_POLICIES["Vendor"]
    for role in roles[:-1]:
        decide_request(request_id, role, "Approved", "OK", approver(role))
    with write_session() as session:
        session.add(MasterRecord(record_type="Vendor", name="Orchid Medical Supplies",
                                 normalized_name="orchid medical supplies", country="Singapore", business_entity="SPD"))
    with pytest.raises(ValueError, match="existing master"):
        decide_request(request_id, roles[-1], "Approved", "OK", approver(roles[-1]))
    request = get_request(request_id, requestor)
    assert request["status"] == "Pending Approval"
    assert request["steps"][-1]["Status"] == "Pending"


def test_oidc_identity_is_issuer_bound_and_defaults_to_denied(monkeypatch):
    users = {"https://id.example|123": {"roles": ["finance"], "countries": ["Singapore"], "entities": ["SPD"]}}
    monkeypatch.setenv("MDM_USERS_JSON", json.dumps(users))
    assert configured_actor("123", "https://id.example").roles == frozenset({"finance"})
    with pytest.raises(ValueError):
        configured_actor("123", "https://other.example")
    monkeypatch.setenv("MDM_USERS_JSON", "invalid")
    with pytest.raises(ValueError):
        configured_actor("123", "https://id.example")


def test_initialization_is_idempotent(requestor):
    request_id = create(requestor)
    initialize_database()
    initialize_database()
    assert get_request(request_id, requestor)["status"] == "Pending Approval"
