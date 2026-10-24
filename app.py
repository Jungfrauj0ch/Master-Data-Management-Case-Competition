"""MDM application entry point. Start with: streamlit run app.py."""

import os

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from database.connection import initialize_database
from database.seed import seed_demo_records
from services.auth_service import current_actor
from ui.request_views import approval_queue, dashboard, new_request, render_feedback

st.set_page_config(page_title="Lives Group MDM", page_icon="📊", layout="wide")
st.title("Lives Group MDM Intelligence Platform")
actor = current_actor()

# Clear widgets that could retain another identity's input on a demo role switch.
if st.session_state.get("actor_id") != actor.id:
    for key in list(st.session_state):
        if key.startswith(("intake_", "reveal_", "decision_", "clarification_", "audit_export_", "flash",
                           "approval_request", "dashboard_request")):
            del st.session_state[key]
    st.session_state["actor_id"] = actor.id


@st.cache_resource
def prepare_database():
    """Initialize tables and migrations, including request clarification conversations."""
    initialize_database()
    if os.environ.get("MDM_AUTH_MODE", "demo") == "demo":
        seed_demo_records()


try:
    prepare_database()
    st.sidebar.caption(f"Signed in as {actor.id}")
    page = st.sidebar.radio("Navigation", ["Dashboard", "New MDM Request", "Approval Workflow",
                                           "Regional Sales Analytics"])
    render_feedback()
    if page == "Dashboard":
        dashboard(actor)
    elif page == "New MDM Request":
        new_request(actor)
    elif page == "Approval Workflow":
        approval_queue(actor)
    else:
        st.header("Regional Sales Intelligence")
        st.info("Sales analytics is not configured for this demo.")
except SQLAlchemyError:
    st.error("The database operation could not be completed. Refresh and try again.")
