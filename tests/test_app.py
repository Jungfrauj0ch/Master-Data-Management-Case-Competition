"""Exercise real Streamlit widgets against the isolated fixture database."""

from pathlib import Path
import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from services.access_service import Actor
from services.request_service import get_request, list_requests


@pytest.fixture(autouse=True)
def clear_app_resources():
    # Each test has a new database and needs its own startup/seed pass.
    st.cache_resource.clear()


def widget(elements, label):
    return next(element for element in elements if element.label == label)


def click_request(app, table_key, request_id):
    # AppTest exposes dataframe contents but has no row-click helper. Send the
    # same selection event as the browser to exercise the real callback.
    table = next(frame for frame in app.dataframe if frame.key == table_key)
    row = list(table.value["ID"]).index(request_id)
    states = app._tree.get_widget_states()
    event = states.widgets.add()
    event.id = table.proto.id
    event.string_value = json.dumps({"selection": {"rows": [row], "columns": [], "cells": []}})
    return app._run(widget_state=states)


def test_ui_submission_approval_and_tracking():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    assert not app.exception
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    assert not app.exception
    widget(app.selectbox, "Record Type").set_value("Item").run()
    widget(app.text_input, "Record Name *").set_value("Precision surgical clamp")
    widget(app.text_input, "Item code *").set_value("ITEM-001")
    assert not any("Unit of measure" in field.label for field in app.text_input)
    item_type = widget(app.selectbox, "Item type *")
    assert item_type.options == ["In-vitro diagnostics (IVD)", "Medical equipment", "Laboratory solutions"]
    item_type.set_value("Medical equipment")
    widget(app.text_area, "Business Justification *").set_value("New inventory line")
    widget(app.button, "Submit Request").click().run()
    assert not app.exception
    assert any("submitted" in message.value for message in app.success)
    requestor = Actor("demo_requestor", frozenset({"requestor"}))
    request_id = list_requests(requestor)[0]["ID"]
    assert get_request(request_id, requestor)["details"] == {
        "item_code": "ITEM-001", "category": "Medical equipment",
    }
    assert widget(app.selectbox, "Item type *").value is None

    for identity in ("Business owner", "Data steward", "Regional manager"):
        widget(app.selectbox, "Demo identity").set_value(identity).run()
        assert not app.exception
        widget(app.radio, "Navigation").set_value("Approval Workflow").run()
        assert not app.exception
        assert not any(item.label == "Review request" for item in app.selectbox)
        click_request(app, "approval_request_rows", request_id)
        assert not app.exception
        widget(app.text_area, "Decision reason * (exclude sensitive details)").set_value("Verified")
        widget(app.button, "Record decision").click().run()
        assert not app.exception

    widget(app.selectbox, "Demo identity").set_value("Requestor").run()
    widget(app.radio, "Navigation").set_value("Dashboard").run()
    assert not app.exception
    click_request(app, "dashboard_request_rows", request_id)
    assert get_request(request_id, requestor)["status"] == "Approved"
    assert any("Local master record" in message.value for message in app.success)


def test_protected_field_disabled_without_configuration():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    assert not app.exception
    assert widget(app.text_input, "Credit terms (optional, encrypted)").disabled


def test_credit_terms_enabled_when_encryption_is_configured(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv("MDM_ENCRYPTION_KEY", fernet.Fernet.generate_key().decode())
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    assert not app.exception
    field = widget(app.text_input, "Credit terms (optional, encrypted)")
    assert not field.disabled
    field.set_value("Net 30")
    assert field.value == "Net 30"


def test_validation_preserves_ordinary_form_input():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    widget(app.text_input, "Record Name *").set_value("Example healthcare customer")
    widget(app.button, "Submit Request").click().run()
    assert not app.exception
    assert app.error
    assert widget(app.text_input, "Record Name *").value == "Example healthcare customer"


def test_authenticated_mode_does_not_expose_demo_roles(monkeypatch):
    monkeypatch.setenv("MDM_AUTH_MODE", "oidc")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    assert not app.exception
    assert not app.selectbox
    assert widget(app.button, "Sign in")


def test_duplicates_appear_automatically_and_follow_intake_fields():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    assert any(heading.value == "Create Master Data Request" for heading in app.header)
    assert not any(expander.label == "Search existing records before you submit"
                   for expander in app.expander)
    assert not any(button.label == "Check for duplicates" for button in app.button)
    assert not any(item.label == "Name or abbreviation to search" for item in app.text_input)

    widget(app.text_input, "Record Name *").set_value("NUH").run()
    assert not app.exception
    assert any("Possible existing records" in item.value for item in app.warning)
    assert app.dataframe[0].value.iloc[0]["Name"] == "National University Hospital"

    widget(app.selectbox, "Country *").set_value("Malaysia").run()
    assert not app.exception
    assert not app.dataframe
    assert not any("Possible existing records" in item.value for item in app.warning)

    widget(app.selectbox, "Country *").set_value("Singapore").run()
    assert app.dataframe[0].value.iloc[0]["Name"] == "National University Hospital"
    widget(app.text_input, "Record Name *").set_value("Orchid Healthcare Services").run()
    assert not app.exception
    assert not app.dataframe
    widget(app.selectbox, "Record Type").set_value("Vendor").run()
    widget(app.text_input, "Record Name *").set_value("NUH").run()
    assert not app.exception
    assert not app.dataframe


def test_submit_rechecks_duplicates_added_after_preview():
    from database.connection import write_session
    from models.mdm import MasterRecord

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.radio, "Navigation").set_value("New MDM Request").run()
    widget(app.selectbox, "Record Type").set_value("Item").run()
    widget(app.text_input, "Record Name *").set_value("Precision surgical clamp").run()
    assert not app.dataframe
    with write_session() as session:
        session.add(MasterRecord(record_type="Item", name="Precision surgical clamp",
                                 normalized_name="precision surgical clamp",
                                 country="Singapore", business_entity="SPD"))
    widget(app.text_input, "Item code *").set_value("ITEM-001")
    widget(app.selectbox, "Item type *").set_value("Medical equipment")
    widget(app.text_area, "Business Justification *").set_value("New inventory line")
    widget(app.button, "Submit Request").click().run()
    assert not app.exception
    assert any("duplicate" in item.value.lower() for item in app.error)
    assert list_requests(Actor("demo_requestor", frozenset({"requestor"}))) == []


def test_queue_schedules_refresh_and_preserves_selection_and_draft(monkeypatch):
    from streamlit.testing.v1.local_script_runner import LocalScriptRunner

    from services.approval_service import decide_request
    from services.request_service import RequestData, submit_request

    # AppTest does not run browser timers. Inspect the actual timer instructions
    # and explicitly execute their subsequent renders after external changes.
    scheduled_intervals = []
    original_run = LocalScriptRunner.run

    def capture_scheduled_refreshes(runner, *args, **kwargs):
        tree = original_run(runner, *args, **kwargs)
        scheduled_intervals.extend(message.auto_rerun.interval for message in runner.forward_msgs()
                                   if message.HasField("auto_rerun"))
        return tree

    monkeypatch.setattr(LocalScriptRunner, "run", capture_scheduled_refreshes)
    requestor = Actor("demo_requestor", frozenset({"requestor"}))

    def create_item(name, code):
        result = submit_request(RequestData(
            record_type="Item", name=name, country="Singapore", business_entity="SPD",
            justification="New inventory line", details={"item_code": code, "category": "Medical equipment"},
        ), requestor)
        assert result.success, result.message
        return result.request_id

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    assert scheduled_intervals[-1] == 5
    assert not any(button.label == "Refresh requests" for button in app.button)
    widget(app.selectbox, "Demo identity").set_value("Data steward").run()
    widget(app.radio, "Navigation").set_value("Approval Workflow").run()
    assert scheduled_intervals[-1] == 5
    assert not any(button.label == "Refresh queue" for button in app.button)
    assert any("No pending checks" in item.value for item in app.info)

    first_id = create_item("Precision surgical clamp", "ITEM-001")
    app.run()
    assert not app.exception
    click_request(app, "approval_request_rows", first_id)
    assert not app.exception
    assert app.session_state["approval_request"] == first_id
    widget(app.text_area, "Decision reason * (exclude sensitive details)").set_value("Checking the product specification").run()

    second_id = create_item("Digital laboratory thermometer", "ITEM-002")
    app.run()
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key == "approval_request_rows")
    assert list(table.value["ID"]) == [second_id, first_id]
    assert app.session_state["approval_request"] == first_id
    assert app.session_state["approval_request_rows"]["selection"]["rows"] == [1]
    assert widget(app.text_area, "Decision reason * (exclude sensitive details)").value == "Checking the product specification"

    decide_request(first_id, "data_steward", "Approved", "Verified by another reviewer",
                   Actor("another_steward", frozenset({"data_steward"})))
    app.run()
    assert not app.exception
    assert "approval_request" not in app.session_state
    # Click the displayed row while another request is inserted before rerender.
    # The callback must use the displayed snapshot, not the refreshed row index.
    create_item("Automated diagnostic analyzer", "ITEM-003")
    click_request(app, "approval_request_rows", second_id)
    assert not app.exception
    assert app.session_state["approval_request"] == second_id
    assert app.session_state["approval_request_rows"]["selection"]["rows"] == [1]
    assert widget(app.text_area, "Decision reason * (exclude sensitive details)").value == ""


def test_row_click_question_and_requestor_reply():
    from services.request_service import RequestData, submit_request

    requestor = Actor("demo_requestor", frozenset({"requestor"}))
    result = submit_request(RequestData(
        record_type="Item", name="Precision surgical clamp", country="Singapore", business_entity="SPD",
        justification="New inventory line", details={"item_code": "IT-001", "category": "Medical equipment"},
    ), requestor)
    assert result.success
    request_id = result.request_id
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    app.run()
    widget(app.selectbox, "Demo identity").set_value("Data steward").run()
    widget(app.radio, "Navigation").set_value("Approval Workflow").run()
    click_request(app, "approval_request_rows", request_id)
    assert not app.exception
    widget(app.text_area, "Clarifying question *").set_value("Which product specification should we use?")
    widget(app.button, "Send question").click().run()
    assert not app.exception
    assert get_request(request_id, requestor)["clarifications"][0]["question"] == "Which product specification should we use?"
    assert not any(item.label == "Record decision" for item in app.button)

    widget(app.selectbox, "Demo identity").set_value("Requestor").run()
    widget(app.radio, "Navigation").set_value("Dashboard").run()
    click_request(app, "dashboard_request_rows", request_id)
    assert not app.exception
    assert any(item.value == "Which product specification should we use?" for item in app.text)
    widget(app.text_area, "Your reply *").set_value("Use specification revision 2.")
    widget(app.button, "Send reply").click().run()
    assert not app.exception

    widget(app.selectbox, "Demo identity").set_value("Data steward").run()
    widget(app.radio, "Navigation").set_value("Approval Workflow").run()
    click_request(app, "approval_request_rows", request_id)
    assert not app.exception
    assert any(item.value == "Use specification revision 2." for item in app.text)
    widget(app.text_area, "Decision reason * (exclude sensitive details)").set_value("Specification confirmed")
    widget(app.button, "Record decision").click().run()
    assert not app.exception
    assert any("No pending checks" in item.value for item in app.info)
