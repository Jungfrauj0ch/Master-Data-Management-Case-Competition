from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from mdm_config import APPROVAL_SLA_HOURS
from database.connection import write_session
from models.mdm import ApprovalStep, utc_now
from services.access_service import Actor
from services.approval_service import decide_request
from services.clarification_service import answer_clarification, ask_clarification
from services.notification_service import as_utc, list_notifications, send_due_reminders
from services.request_service import RequestData, export_audit, get_request, list_requests, submit_request


def create_request(requestor):
    result = submit_request(RequestData(
        "Item", "Precision surgical clamp", "Singapore", "SPD", "New product line",
        {"item_code": "IT001", "category": "Medical equipment"},
    ), requestor)
    assert result.success, result.message
    return result.request_id


def steward(identity="steward", **kwargs):
    return Actor(identity, frozenset({"data_steward"}), **kwargs)


def test_question_reply_and_followup_preserve_audit_and_notifications(requestor):
    request_id = create_request(requestor)
    question_id = ask_clarification(request_id, "data_steward", "Which specification applies?", steward())
    request = get_request(request_id, requestor)
    assert request["status"] == "Pending Approval"
    assert next(step for step in request["steps"] if step["Role"] == "data_steward")["Status"] == "Needs Clarification"
    assert "requestor reply" in list_requests(requestor)[0]["Waiting on"]
    assert any("asked a question" in item["Message"] for item in list_notifications(requestor))
    with pytest.raises(ValueError, match="reply"):
        decide_request(request_id, "data_steward", "Approved", "OK", steward())

    # Independent checks can continue during the clarification.
    decide_request(request_id, "business_owner", "Approved", "Business check complete",
                   Actor("owner", frozenset({"business_owner"})))
    answer_clarification(question_id, "Specification revision 2.", requestor)
    request = get_request(request_id, requestor)
    question = request["clarifications"][0]
    assert question["question"] == "Which specification applies?"
    assert question["answer"] == "Specification revision 2."
    assert question["asked_by"] == "steward" and question["answered_by"] == requestor.id
    assert question["asked_at"] and question["answered_at"]
    step = next(step for step in request["steps"] if step["Role"] == "data_steward")
    assert step["Status"] == "Pending"
    assert as_utc(step["Due (UTC)"]) > utc_now() + timedelta(hours=APPROVAL_SLA_HOURS - 1)
    assert any("replied to your question" in item["Message"] for item in list_notifications(steward()))
    history = export_audit(request_id, requestor)
    assert "CLARIFICATION_REQUESTED" in history and "CLARIFICATION_ANSWERED" in history
    assert "Specification revision 2." in history

    followup_id = ask_clarification(request_id, "data_steward", "Is revision 2 current?", steward())
    answer_clarification(followup_id, "Yes, confirmed.", requestor)
    decide_request(request_id, "data_steward", "Approved", "Specification confirmed", steward())
    decide_request(request_id, "regional_manager", "Approved", "Regional check complete",
                   Actor("regional", frozenset({"regional_manager"})))
    assert get_request(request_id, requestor)["status"] == "Approved"


def test_only_assigned_approvers_can_ask_and_only_owner_can_reply(requestor):
    request_id = create_request(requestor)
    for actor in (requestor, Actor("auditor", frozenset({"auditor"})),
                  steward(countries=("Malaysia",)),
                  Actor(requestor.id, frozenset({"requestor", "data_steward"}))):
        with pytest.raises(PermissionError):
            ask_clarification(request_id, "data_steward", "Confirm specification", actor)
    with pytest.raises(ValueError):
        ask_clarification(request_id, "finance", "Confirm specification", Actor("finance", frozenset({"finance"})))
    question_id = ask_clarification(request_id, "data_steward", "Confirm specification", steward())
    for actor in (steward(), Actor("another_requestor", frozenset({"requestor"})),
                  Actor(requestor.id, frozenset({"requestor"}), countries=("Malaysia",))):
        with pytest.raises(PermissionError):
            answer_clarification(question_id, "Confirmed", actor)
    with pytest.raises(PermissionError):
        answer_clarification(999999, "Confirmed", requestor)


@pytest.mark.parametrize("text", [" ", "x" * 2001])
def test_messages_are_required_and_bounded(requestor, text):
    request_id = create_request(requestor)
    with pytest.raises(ValueError):
        ask_clarification(request_id, "data_steward", text, steward())
    question_id = ask_clarification(request_id, "data_steward", "Confirm specification", steward())
    with pytest.raises(ValueError):
        answer_clarification(question_id, text, requestor)


def test_rejection_cancels_waiting_check_and_closes_conversation(requestor):
    request_id = create_request(requestor)
    question_id = ask_clarification(request_id, "data_steward", "Confirm specification", steward())
    decide_request(request_id, "business_owner", "Rejected", "Product not required",
                   Actor("owner", frozenset({"business_owner"})))
    request = get_request(request_id, requestor)
    assert request["status"] == "Rejected"
    assert next(step for step in request["steps"] if step["Role"] == "data_steward")["Status"] == "Cancelled"
    with pytest.raises(ValueError, match="closed"):
        answer_clarification(question_id, "Confirmed", requestor)
    assert get_request(request_id, requestor)["clarifications"][0]["answer"] is None
    assert not list_notifications(steward())


def test_questions_and_replies_are_serialized(requestor):
    request_id = create_request(requestor)

    def ask(_):
        try:
            return ask_clarification(request_id, "data_steward", "Confirm specification", steward())
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        questions = list(pool.map(ask, range(2)))
    assert sum(question is not None for question in questions) == 1
    question_id = next(question for question in questions if question is not None)

    def reply(_):
        try:
            answer_clarification(question_id, "Confirmed", requestor)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reply, range(2))) == 1
    assert len(get_request(request_id, requestor)["clarifications"]) == 1


def test_approver_reminders_pause_until_requestor_replies(requestor):
    request_id = create_request(requestor)
    with write_session() as session:
        step = session.scalar(select(ApprovalStep).where(
            ApprovalStep.request_id == request_id, ApprovalStep.role == "data_steward",
        ))
        step.due_at = utc_now() - timedelta(hours=1)
    question_id = ask_clarification(request_id, "data_steward", "Confirm specification", steward())
    assert send_due_reminders() == 0
    answer_clarification(question_id, "Confirmed", requestor)
    assert send_due_reminders() == 0
