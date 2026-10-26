"""MDM application entry point. Start with: streamlit run app.py."""

import os

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from database.connection import DATABASE_SCHEMA_VERSION, initialize_database
from database.seed import seed_demo_records
from mdm_config import APPROVAL_ROLES
from services.auth_service import current_actor
from ui.request_views import approval_queue, dashboard, messages, new_request, render_feedback

st.set_page_config(page_title="Lives Group MDM", page_icon="📊", layout="wide")
st.title("Lives Group MDM Intelligence Platform")
actor = current_actor()

# Clear widgets that could retain another identity's input on a demo role switch.
if st.session_state.get("actor_id") != actor.id:
    for key in list(st.session_state):
        if key.startswith(("intake_", "reveal_", "decision_", "clarification_", "audit_export_", "flash",
                           "approval_request", "dashboard_request", "messages_")):
            del st.session_state[key]
    st.session_state["actor_id"] = actor.id


@st.cache_resource
def prepare_database(schema_version: int):
    """Initialize tables and migrations, including request clarification conversations."""
    initialize_database()
    if os.environ.get("MDM_AUTH_MODE", "demo") == "demo":
        seed_demo_records()


try:
    prepare_database(DATABASE_SCHEMA_VERSION)
    st.sidebar.caption(f"Signed in as {actor.id}")
    pages = ["Dashboard"]
    if "requestor" in actor.roles:
        pages.append("New MDM Request")
    pages.append("Messages")
    if actor.roles.intersection(APPROVAL_ROLES):
        pages.append("Approval Workflow")
    pages.append("Regional Sales Analytics")
    target = st.session_state.pop("navigation_target", None)
    if target in pages:
        st.session_state["navigation"] = target
    if st.session_state.get("navigation") not in pages:
        st.session_state["navigation"] = "Dashboard"
    page = st.sidebar.radio("Navigation", pages, key="navigation")
    render_feedback()
    if page == "Dashboard":
        dashboard(actor)
    elif page == "New MDM Request":
        new_request(actor)
    elif page == "Approval Workflow":
        approval_queue(actor)
    elif page == "Messages":
        messages(actor)
    else:
        st.header("Regional Sales Intelligence")
        st.info("Sales analytics is not configured for this demo.")
except SQLAlchemyError:
    st.error("The database operation could not be completed. Refresh and try again.")
