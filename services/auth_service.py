"""Streamlit OIDC integration plus an explicitly labelled synthetic-data demo."""

import json
import os

from mdm_config import APPROVAL_ROLES, BUSINESS_ENTITIES, COUNTRIES
from services.access_service import Actor


DEMO_USERS = {
    "Requestor": Actor("demo_requestor", frozenset({"requestor"}), demo=True),
    **{label: Actor(f"demo_{role}", frozenset({role}), demo=True)
       for role, label in APPROVAL_ROLES.items()},
    "Auditor": Actor("demo_auditor", frozenset({"auditor"}), demo=True),
}


def configured_actor(subject: str, issuer: str) -> Actor:
    """Authorize an OIDC subject bound to its issuer, never a user-entered email."""
    try:
        users = json.loads(os.environ.get("MDM_USERS_JSON", "{}"))
        if not isinstance(users, dict):
            raise ValueError("MDM_USERS_JSON must be an object keyed by issuer|subject.")
        entry = users.get(f"{issuer}|{subject}")
        if not isinstance(entry, dict):
            raise ValueError("Your account has not been granted access.")
        roles = frozenset(entry["roles"])
        countries = tuple(entry["countries"])
        entities = tuple(entry["entities"])
        if not roles or not roles.issubset({"requestor", "auditor", *APPROVAL_ROLES}):
            raise ValueError("Account role configuration is invalid.")
        if not countries or not set(countries).issubset(COUNTRIES):
            raise ValueError("Account country configuration is invalid.")
        if not entities or not set(entities).issubset(BUSINESS_ENTITIES):
            raise ValueError("Account entity configuration is invalid.")
        identity = f"{issuer}|{subject}"
        if len(identity) > 200:
            raise ValueError("Account identity exceeds the supported length.")
        return Actor(identity, roles, countries, entities)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MDM_USERS_JSON must define roles, countries, and entities for each OIDC identity.") from exc


def current_actor() -> Actor:
    import streamlit as st

    mode = os.environ.get("MDM_AUTH_MODE", "demo")
    if mode == "demo":
        st.warning("Demo mode: identities can be switched. Use synthetic data only.")
        profile = st.sidebar.selectbox("Demo identity", tuple(DEMO_USERS), key="demo_identity")
        return DEMO_USERS[profile]
    if mode != "oidc":
        st.error("MDM_AUTH_MODE must be demo or oidc.")
        st.stop()
    if not st.user.get("is_logged_in", False):
        st.info("Sign in with your organization account to continue.")
        if st.button("Sign in"):
            try:
                st.login()
            except Exception:
                st.error("Sign-in is not configured. Install Authlib and configure .streamlit/secrets.toml.")
        st.stop()
    try:
        subject, issuer = st.user.get("sub"), st.user.get("iss")
        if not subject or not issuer:
            raise ValueError("The identity provider did not return an issuer and subject.")
        actor = configured_actor(subject, issuer)
    except ValueError as exc:
        st.error(str(exc))
        if st.button("Sign out"):
            st.logout()
        st.stop()
    if st.sidebar.button("Sign out"):
        st.logout()
    return actor
