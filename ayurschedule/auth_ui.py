"""Login/signup screens, the patient portal, and hospital account controls."""

import pandas as pd
import streamlit as st

from . import auth
from .access import ClinicAccess


def logout():
    auth.logout(st.session_state.get("auth_token"))
    st.session_state.clear()


def _start_session(email, password, issue_recovery_code=False):
    token = auth.login(email, password)
    recovery_code = auth.create_recovery_code(token) if issue_recovery_code else None
    st.session_state.clear()
    st.session_state["auth_token"] = token
    if recovery_code:
        st.session_state["recovery_code_once"] = recovery_code
    st.rerun()


def require_login():
    token = st.session_state.get("auth_token")
    user = auth.session_user(token)
    if user is not None:
        return user
    if token:
        st.session_state.clear()
        st.session_state["login_notice"] = "Your session expired. Please log in again."
    st.title("🌿 Welcome to AyurSchedule")
    st.caption("Hospital scheduling and your personal therapy appointments")
    if "login_notice" in st.session_state:
        st.info(st.session_state.pop("login_notice"))
    login_tab, signup_tab, recovery_tab = st.tabs(["Login", "Sign Up", "Forgot Password"], key="auth_page", on_change="rerun")
    with login_tab:
        st.subheader("Log in to your account")
        with st.form("login_form", clear_on_submit=True):
            email = st.text_input("Login Email", max_chars=254)
            password = st.text_input("Login Password", type="password", max_chars=128)
            if st.form_submit_button("Log In", type="primary"):
                try:
                    _start_session(email, password)
                except ValueError as exc:
                    st.error(str(exc))
    with signup_tab:
        st.subheader("Create an account")
        account_type = st.radio("Account Type", ["Patient", "Hospital Member"], horizontal=True)
        if account_type == "Hospital Member":
            st.caption("Enter the clinic's fixed hospital code or an individual invite from an existing hospital member.")
        else:
            st.caption("Patients can view only their own appointments. A hospital patient invite links an existing medical record.")
        with st.form("signup_form", clear_on_submit=True):
            name = st.text_input("Full Name", max_chars=150)
            signup_email = st.text_input("Signup Email", max_chars=254)
            signup_password = st.text_input("Signup Password", type="password", max_chars=128, help="Use 12 or more characters.")
            confirmation = st.text_input("Confirm Password", type="password", max_chars=128)
            gender = st.selectbox("Patient Gender", ["M", "F"]) if account_type == "Patient" else None
            invite = st.text_input("Hospital Invite Code" if account_type == "Hospital Member" else "Patient Invite Code (optional)", type="password")
            if st.form_submit_button("Create Account", type="primary"):
                try:
                    if signup_password != confirmation:
                        raise ValueError("The passwords do not match.")
                    auth.register_user(name, signup_email, signup_password,
                                       "hospital" if account_type == "Hospital Member" else "patient",
                                       gender=gender, invite_code=invite)
                    _start_session(signup_email, signup_password, issue_recovery_code=True)
                except ValueError as exc:
                    st.error(str(exc))
    with recovery_tab:
        st.subheader("Reset your password")
        st.caption("Enter the recovery code saved when your account was created or from Account Recovery while signed in.")
        with st.form("password_recovery_form", clear_on_submit=True):
            email = st.text_input("Account Email", max_chars=254)
            code = st.text_input("Recovery Code", type="password")
            new_password = st.text_input("New Password", type="password", max_chars=128)
            confirmation = st.text_input("Confirm New Password", type="password", max_chars=128)
            if st.form_submit_button("Reset Password"):
                try:
                    if new_password != confirmation:
                        raise ValueError("The passwords do not match.")
                    auth.reset_password(email, code, new_password)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Password reset. Log in with your new password.")
    st.stop()


def account_sidebar(user):
    with st.sidebar:
        st.write(f"Signed in as **{user['full_name']}**")
        st.caption("Hospital Member" if user["role"] == "hospital" else "Patient")
        st.button("Log Out", on_click=logout, use_container_width=True)
        with st.expander("Account Recovery", expanded=bool(st.session_state.get("recovery_code_once"))):
            if st.button("Generate New Recovery Code", use_container_width=True):
                st.session_state["recovery_code_once"] = auth.create_recovery_code(st.session_state["auth_token"])
            if st.session_state.get("recovery_code_once"):
                st.code(st.session_state["recovery_code_once"], language=None)
                st.caption("Save this code privately. It works once to reset your password and is replaced when you generate another code.")
        st.divider()


def patient_portal(user):
    account_sidebar(user)
    st.title("🌿 My Therapy Appointments")
    st.write(f"Welcome, **{user['full_name']}**.")
    if user["patient_id"]:
        st.caption(f"Your Patient ID: {user['patient_id']}")
    else:
        st.info("Ask the hospital for a patient invite to link your current records.")
    if st.button("Refresh My Appointments"):
        st.rerun()
    appointments = ClinicAccess(st.session_state["auth_token"]).patient_appointments()
    if appointments:
        st.dataframe(pd.DataFrame(appointments), use_container_width=True, hide_index=True)
        st.caption("Pending timings will appear after the hospital generates a conflict-free schedule.")
    else:
        st.info("No appointments are linked to your account yet. Contact the hospital to book a treatment or link your existing records.")
    with st.expander("Link My Existing Patient Record"):
        st.caption("Use the patient invite issued to your account email by the hospital.")
        with st.form("link_patient_form", clear_on_submit=True):
            code = st.text_input("Patient Record Invite Code", type="password")
            if st.form_submit_button("Link My Patient Record"):
                try:
                    auth.link_patient(st.session_state["auth_token"], code)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()


def hospital_accounts(access):
    access.require_staff()
    token = st.session_state["auth_token"]
    st.subheader("Hospital Members & Patient Accounts")
    accounts = auth.list_accounts(token)
    st.dataframe(pd.DataFrame(accounts), use_container_width=True, hide_index=True)
    st.subheader("Invite a Hospital Member")
    st.caption("Individual invites remain available for a specific email, even when the clinic also uses a fixed hospital code.")
    with st.form("invite_member_form", clear_on_submit=True):
        email = st.text_input("New Member Email")
        if st.form_submit_button("Create Hospital Member Invite"):
            try:
                st.session_state["member_invite_code"] = auth.create_invite(token, email)
            except ValueError as exc:
                st.error(str(exc))
    if st.session_state.get("member_invite_code"):
        st.code(st.session_state["member_invite_code"], language=None)
        st.caption("Share this code privately with the invited member. It works once, for that email, within 72 hours.")
    st.subheader("Link a Patient Account to Existing Bookings")
    patients = {row["patient_id"]: row for row in access.patient_registry()}
    if patients:
        with st.form("invite_patient_form", clear_on_submit=True):
            patient_id = st.selectbox("Patient Record to Link", list(patients), format_func=lambda key: f"{patients[key]['patient_name']} ({key})")
            patient_email = st.text_input("Patient Account Email")
            if st.form_submit_button("Create Patient Record Invite"):
                try:
                    st.session_state["patient_invite_code"] = auth.create_invite(token, patient_email, "patient", patient_id)
                except ValueError as exc:
                    st.error(str(exc))
        if st.session_state.get("patient_invite_code"):
            st.code(st.session_state["patient_invite_code"], language=None)
            st.caption("Give this code to the patient. They can use it during signup or in Link My Existing Patient Record. It expires in 72 hours.")
    else:
        st.info("Create patient bookings before issuing patient record invites.")
    st.subheader("Account Access")
    account_lookup = {row["id"]: row for row in accounts}
    account_id = st.selectbox("Account", list(account_lookup), format_func=lambda key: f"{account_lookup[key]['full_name']} ({account_lookup[key]['email']})")
    with st.form("account_access_form"):
        active = st.checkbox("Allow Login", value=bool(account_lookup[account_id]["active"]))
        if st.form_submit_button("Save Account Access"):
            try:
                auth.set_account_active(token, account_id, active)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()
