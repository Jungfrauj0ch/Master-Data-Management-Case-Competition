"""Streamlit views; business rules and authorization remain in the services."""

from datetime import datetime, timezone
from functools import partial

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from mdm_config import APPROVAL_ROLES, INTAKE_FIELDS, INTAKE_OPTIONS, RECORD_TYPES
from services.access_service import Actor
from services.approval_service import decide_request, reveal_sensitive
from services.clarification_service import answer_clarification, ask_clarification
from services.duplicate_service import normalize_name
from services.notification_service import as_utc, list_notifications, mark_notifications_read, send_due_reminders
from services.request_service import (
    RequestData, export_audit, get_request, list_requests, search_duplicates, submit_request,
)
from services.security_service import SecurityConfigurationError, cipher

LIVE_REFRESH_SECONDS = 5

_PENDING_ROW_STYLE = "background-color: #fee2e2; color: #991b1b;"
_STATUS_ROW_STYLES = {
    "Approved": "background-color: #dcfce7; color: #166534;",
    "Pending Approval": _PENDING_ROW_STYLE,
    "Pending": _PENDING_ROW_STYLE,
    "Needs Clarification": _PENDING_ROW_STYLE,
}


def style_status_rows(rows: list[dict]):
    def row_style(row):
        return [_STATUS_ROW_STYLES.get(row.get("Status"), "")] * len(row)

    return pd.DataFrame(rows).style.apply(row_style, axis=1)


def render_feedback():
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    if "flash_error" in st.session_state:
        st.error(st.session_state.pop("flash_error"))


def submit_intake(record_type: str, actor: Actor):
    state = st.session_state
    data = RequestData(
        record_type=record_type,
        name=state[f"intake_name_{record_type}"],
        country=state[f"intake_country_{record_type}"],
        business_entity=state[f"intake_entity_{record_type}"],
        justification=state[f"intake_justification_{record_type}"],
        details={key: state[f"intake_{record_type}_{key}"] or "" for key in INTAKE_FIELDS[record_type]},
        bank_account=state.get("intake_bank_account", "") if record_type == "Vendor" else "",
        credit_terms=state.get("intake_credit_terms", "") if record_type == "Customer" else "",
    )
    try:
        result = submit_request(data, actor)
        state["intake_result"] = result
        if result.success:
            # Start a fresh intake instead of warning about the request just created.
            state[f"intake_name_{record_type}"] = ""
            state[f"intake_justification_{record_type}"] = ""
            for key in INTAKE_FIELDS[record_type]:
                state[f"intake_{record_type}_{key}"] = (
                    None if key in INTAKE_OPTIONS.get(record_type, {}) else ""
                )
    finally:
        # Preserve ordinary input after validation errors while always clearing secrets.
        for key in ("intake_bank_account", "intake_credit_terms"):
            if key in state:
                state[key] = ""


def record_decision(request_id: int, actor: Actor):
    """Run before the next render so the queue and request details stay consistent."""
    try:
        status = decide_request(request_id, st.session_state[f"decision_role_{request_id}"],
                                st.session_state[f"decision_value_{request_id}"],
                                st.session_state[f"decision_reason_{request_id}"], actor)
        st.session_state["flash"] = f"Decision recorded. Request status: {status}."
    except (ValueError, PermissionError) as exc:
        st.session_state["flash_error"] = str(exc)
    except SQLAlchemyError:
        st.session_state["flash_error"] = "The decision could not be saved. Refresh and try again."


def send_question(request_id: int, actor: Actor):
    try:
        ask_clarification(request_id, st.session_state[f"clarification_role_{request_id}"],
                          st.session_state[f"clarification_question_{request_id}"], actor)
        st.session_state["flash"] = "Question sent to the requestor. This check is waiting for their reply."
    except (ValueError, PermissionError) as exc:
        st.session_state["flash_error"] = str(exc)
    except SQLAlchemyError:
        st.session_state["flash_error"] = "The question could not be saved. Please try again."


def send_reply(clarification_id: int, actor: Actor):
    try:
        answer_clarification(clarification_id, st.session_state[f"clarification_reply_{clarification_id}"], actor)
        st.session_state["flash"] = "Reply sent. The approver has been notified."
    except (ValueError, PermissionError) as exc:
        st.session_state["flash_error"] = str(exc)
    except SQLAlchemyError:
        st.session_state["flash_error"] = "The reply could not be saved. Please try again."


def select_request_row(table_key: str, selection_key: str, rendered_ids: tuple[int, ...]):
    """Map a click against the rows the user saw, before fetching refreshed data."""
    rows = st.session_state.get(table_key, {}).get("selection", {}).get("rows", [])
    if rows and isinstance(rows[0], int) and 0 <= rows[0] < len(rendered_ids):
        st.session_state[selection_key] = rendered_ids[rows[0]]
    else:
        st.session_state.pop(selection_key, None)


def request_table(requests: list[dict], selection_key: str) -> int | None:
    request_ids = tuple(request["ID"] for request in requests)
    selected_id = st.session_state.get(selection_key)
    if selected_id not in request_ids:
        st.session_state.pop(selection_key, None)
        selected_id = None
    table_key = f"{selection_key}_rows"
    # Selection follows the request ID when refresh inserts or removes rows.
    st.session_state[table_key] = {
        "selection": {"rows": [request_ids.index(selected_id)] if selected_id is not None else [],
                      "columns": [], "cells": []},
    }
    st.dataframe(style_status_rows(requests), hide_index=True, width="stretch", key=table_key,
                 selection_mode="single-row",
                 on_select=partial(select_request_row, table_key, selection_key, request_ids))
    return selected_id


def clarification_history(request: dict, actor: Actor):
    if not request["clarifications"]:
        return
    st.write("**Questions and replies**")
    st.caption("Keep bank details and credit terms in their protected fields, not in questions or replies.")
    for item in request["clarifications"]:
        with st.container(border=True):
            st.caption(f"{APPROVAL_ROLES[item['role']]} · Asked by {item['asked_by']} · "
                       f"{item['asked_at'].strftime('%Y-%m-%d %H:%M')} UTC")
            st.text(item["question"])
            if item["answered_at"] is not None:
                st.caption(f"Replied by {item['answered_by']} · "
                           f"{item['answered_at'].strftime('%Y-%m-%d %H:%M')} UTC")
                st.text(item["answer"])
            elif request["status"] != "Pending Approval":
                st.caption("Request closed without a reply to this question.")
            elif request["requestor"] == actor.id:
                with st.form(f"clarification_reply_form_{item['id']}", clear_on_submit=True):
                    st.text_area("Your reply *", max_chars=2000, key=f"clarification_reply_{item['id']}")
                    st.form_submit_button("Send reply", on_click=send_reply, args=(item["id"], actor))
            else:
                st.caption("Waiting for the requestor's reply.")


def duplicate_preview(actor: Actor, record_type: str, name: str, country: str):
    if not normalize_name(name):
        return
    matches = search_duplicates(actor, record_type, name, country)
    if matches:
        st.warning("Possible existing records found. Review these matches before creating a request.")
        st.dataframe(matches, hide_index=True, width="stretch")
    else:
        st.caption("No close matches found. Duplicates will be checked again when you submit.")


def new_request(actor: Actor):
    st.header("Create Master Data Request")
    if "requestor" not in actor.roles:
        st.info("Your account can review requests. Requestor permission is needed to submit.")
        return
    record_type = st.selectbox("Record Type", RECORD_TYPES, key="intake_type")
    st.caption("All fields marked * are required. Duplicate checking is automatic.")
    country = st.selectbox("Country *", actor.countries, key=f"intake_country_{record_type}")
    name = st.text_input("Record Name *", max_chars=200, key=f"intake_name_{record_type}",
                         help="Matching records appear automatically after you finish editing the name.")
    duplicate_preview(actor, record_type, name, country)
    sensitive_enabled = True
    try:
        cipher()
    except SecurityConfigurationError as exc:
        sensitive_enabled = False
        if record_type in {"Customer", "Vendor"}:
            st.info(f"Optional protected fields are disabled. {exc}")
    st.caption("Put bank details and credit terms only in their protected fields.")
    with st.form(f"intake_form_{record_type}"):
        st.selectbox("Business Entity *", actor.entities, key=f"intake_entity_{record_type}")
        for key, label in INTAKE_FIELDS[record_type].items():
            options = INTAKE_OPTIONS.get(record_type, {}).get(key)
            if options:
                st.selectbox(f"{label} *", options, index=None,
                             placeholder=f"Choose {label.lower()}", key=f"intake_{record_type}_{key}")
            else:
                st.text_input(f"{label} *", max_chars=500, key=f"intake_{record_type}_{key}")
        st.text_area("Business Justification *", max_chars=2000,
                     key=f"intake_justification_{record_type}")
        if record_type == "Vendor":
            st.text_input("Bank account details (optional, encrypted)", type="password",
                          disabled=not sensitive_enabled, max_chars=1000, key="intake_bank_account")
        elif record_type == "Customer":
            st.text_input("Credit terms (optional, encrypted)", type="password",
                          disabled=not sensitive_enabled, max_chars=1000, key="intake_credit_terms")
        st.form_submit_button("Submit Request", on_click=submit_intake, args=(record_type, actor))
    result = st.session_state.pop("intake_result", None)
    if result:
        if result.success:
            st.success(f"Request #{result.request_id} submitted. Track progress on the Dashboard.")
        else:
            st.error(result.message)
            for match in result.duplicates:
                st.warning(match)


def inbox(actor: Actor):
    send_due_reminders()
    notifications = list_notifications(actor)
    unread = [item for item in notifications if not item["Read"]]
    with st.expander(f"Notifications ({len(unread)} unread)", expanded=bool(unread)):
        st.caption(f"Updates automatically every {LIVE_REFRESH_SECONDS} seconds.")
        if notifications:
            st.dataframe(notifications[:50], hide_index=True, width="stretch")
            if unread:
                st.button("Mark notifications read", on_click=mark_notifications_read, args=(actor,))
        else:
            st.info("No notifications.")


def request_detail(request_id: int, actor: Actor, allow_decision=False):
    request = get_request(request_id, actor)
    st.subheader(f"Request #{request_id}: {request['name']}")
    st.write(f"**Status:** {request['status']} · **Requestor:** {request['requestor']}")
    st.write(f"{request['record_type']} · {request['country']} · {request['entity']}")
    st.write("**Business justification**")
    st.text(request["justification"])
    for key, value in request["details"].items():
        st.text(f"{INTAKE_FIELDS[request['record_type']].get(key, key)}: {value}")
    if request["master_record_id"]:
        st.success(f"Local master record #{request['master_record_id']} created.")
    if request["status"] == "Needs Update":
        st.warning("This legacy submission lacks the standardized intake fields. Submit a new request using the intake form.")
    if request["steps"]:
        st.write("**Parallel approval checks**")
        st.dataframe(style_status_rows(request["steps"]), hide_index=True, width="stretch")
        now = datetime.now(timezone.utc)
        overdue = [step for step in request["steps"] if step["Status"] == "Pending"
                   and as_utc(step["Due (UTC)"]) < now]
        if overdue:
            st.warning("Overdue: " + ", ".join(APPROVAL_ROLES[step["Role"]] for step in overdue))
    if request["sensitive_present"]:
        st.caption("Protected fields: •••••••• — excluded from request tables and audit exports.")
        if actor.roles.intersection({"finance", "compliance"}):
            with st.form(f"reveal_{request_id}", clear_on_submit=True):
                reason = st.text_input("Reason for viewing protected fields", max_chars=500,
                                       key=f"reveal_reason_{request_id}")
                reveal = st.form_submit_button("View protected fields")
            if reveal:
                try:
                    payload = reveal_sensitive(request_id, actor, reason)
                    st.json(payload)
                    st.caption("Access recorded in the audit history. Values clear on the next page action.")
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
    clarification_history(request, actor)
    if allow_decision:
        roles = [step["Role"] for step in request["steps"]
                 if step["Status"] == "Pending" and step["Role"] in actor.roles]
        if request["requestor"] == actor.id:
            st.info("You cannot approve your own request.")
        elif request["status"] == "Pending Approval" and roles:
            with st.form(f"decision_{request_id}", clear_on_submit=True):
                st.selectbox("Approval check", roles, format_func=APPROVAL_ROLES.get,
                             key=f"decision_role_{request_id}")
                st.radio("Decision", ("Approved", "Rejected"), horizontal=True,
                         key=f"decision_value_{request_id}")
                st.text_area("Decision reason * (exclude sensitive details)", max_chars=2000,
                             key=f"decision_reason_{request_id}")
                st.form_submit_button("Record decision", on_click=record_decision, args=(request_id, actor))
            with st.form(f"clarification_question_form_{request_id}", clear_on_submit=True):
                st.selectbox("Question for approval check", roles, format_func=APPROVAL_ROLES.get,
                             key=f"clarification_role_{request_id}")
                st.text_area("Clarifying question *", max_chars=2000,
                             key=f"clarification_question_{request_id}")
                st.form_submit_button("Send question", on_click=send_question, args=(request_id, actor))
        elif any(step["Status"] == "Needs Clarification" and step["Role"] in actor.roles
                 for step in request["steps"]):
            st.info("Your approval check is waiting for the requestor's reply.")
    with st.expander("Audit history", expanded=True):
        # Read again to include a sensitive-access event recorded during this render.
        st.dataframe(get_request(request_id, actor)["audit"], hide_index=True, width="stretch")
        if st.button("Prepare audit export", key=f"audit_export_{request_id}"):
            contents = export_audit(request_id, actor)
            st.download_button("Download audit CSV", contents, f"request-{request_id}-audit.csv",
                               "text/csv", on_click="ignore")


def _dashboard(actor: Actor):
    st.header("MDM Overview")
    st.caption(f"Requests and notifications update automatically every {LIVE_REFRESH_SECONDS} seconds.")
    render_feedback()
    requests = list_requests(actor)
    columns = st.columns(3)
    columns[0].metric("Visible requests", len(requests))
    columns[1].metric("Pending approvals", sum(item["Status"] == "Pending Approval" for item in requests))
    columns[2].metric("Approved requests", sum(item["Status"] == "Approved" for item in requests))
    inbox(actor)
    if not requests:
        st.info("No requests visible to your account.")
        return
    statuses = st.multiselect("Filter by status", sorted({item["Status"] for item in requests}))
    visible = [item for item in requests if not statuses or item["Status"] in statuses]
    if visible:
        request_id = request_table(visible, "dashboard_request")
        if request_id is not None:
            request_detail(request_id, actor)
        else:
            st.info("Click a request row to view its details and reply to questions.")
    else:
        st.session_state.pop("dashboard_request", None)
        st.info("No requests match these filters.")


@st.fragment(run_every=LIVE_REFRESH_SECONDS)
def dashboard(actor: Actor):
    try:
        _dashboard(actor)
    except SQLAlchemyError:
        st.error("Requests could not be refreshed. The app will retry automatically.")


def _approval_queue(actor: Actor):
    st.header("Approval Queue")
    st.caption("Independent checks run in parallel. Each needs a different approver; self-approval is prohibited.")
    render_feedback()
    inbox(actor)
    pending = []
    for item in list_requests(actor):
        if item["Status"] != "Pending Approval" or item["Requestor"] == actor.id:
            continue
        request = get_request(item["ID"], actor)
        if any(step["Status"] in {"Pending", "Needs Clarification"} and step["Role"] in actor.roles
               for step in request["steps"]):
            pending.append(item)
    if not pending:
        st.session_state.pop("approval_request", None)
        st.info("No pending checks assigned to your roles.")
        return
    request_id = request_table(pending, "approval_request")
    if request_id is not None:
        request_detail(request_id, actor, allow_decision=True)
    else:
        st.info("Click a request row to review it.")


@st.fragment(run_every=LIVE_REFRESH_SECONDS)
def approval_queue(actor: Actor):
    try:
        _approval_queue(actor)
    except SQLAlchemyError:
        st.error("The approval queue could not be refreshed. The app will retry automatically.")
