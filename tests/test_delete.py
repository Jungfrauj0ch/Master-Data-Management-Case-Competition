import pytest

from mdm_config import APPROVAL_POLICIES
from services.access_service import Actor
from services.approval_service import decide_request
from services.clarification_service import ask_clarification, answer_clarification
from services.notification_service import list_notifications
from services.request_service import RequestData, delete_request, get_request, list_requests, submit_request


def create_request(requestor):
    result = submit_request(RequestData(
        "Item", "Deletion test item", "Singapore", "SPD", "New equipment",
        {"item_code": "DEL001", "category": "Medical equipment"},
    ), requestor)
    assert result.success
    return result.request_id


def reviewer(role):
    return Actor(f"reviewer_{role}", frozenset({role}))


def test_delete_cancels_work_and_retains_history(requestor):
    request_id = create_request(requestor)
    ask_clarification(request_id, "data_steward", "Which specification?", reviewer("data_steward"))
    question_id = get_request(request_id, requestor)["clarifications"][0]["id"]
    delete_request(request_id, requestor)
    assert list_requests(requestor) == []
    assert list_requests(reviewer("data_steward")) == []
    assert list_notifications(requestor) == []
    assert list_notifications(reviewer("data_steward")) == []
    request = get_request(request_id, requestor)
    assert request["status"] == "Deleted"
    assert all(step["Status"] == "Cancelled" for step in request["steps"])
    assert request["audit"][-1]["Action"] == "REQUEST_DELETED"
    with pytest.raises(PermissionError):
        decide_request(request_id, "business_owner", "Approved", "OK", reviewer("business_owner"))
    with pytest.raises(ValueError):
        answer_clarification(question_id, "Example", requestor)
    with pytest.raises(ValueError):
        delete_request(request_id, requestor)
    assert submit_request(RequestData(
        "Item", "Deletion test item", "Singapore", "SPD", "Resubmit",
        {"item_code": "DEL001", "category": "Medical equipment"},
    ), requestor).success


def test_delete_checks_owner_scope_and_master(requestor):
    request_id = create_request(requestor)
    for actor in (Actor("other", frozenset({"requestor"})), reviewer("data_steward"),
                  Actor(requestor.id, frozenset({"requestor"}), countries=())):
        with pytest.raises(PermissionError):
            delete_request(request_id, actor)
    for role in APPROVAL_POLICIES["Item"]:
        decide_request(request_id, role, "Approved", "Checked", reviewer(role))
    with pytest.raises(ValueError, match="master records"):
        delete_request(request_id, requestor)
    assert get_request(request_id, requestor)["status"] == "Approved"
