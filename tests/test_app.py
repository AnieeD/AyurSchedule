from contextlib import closing
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ayurschedule import auth, database

LIVE_TAB = "📅 Live Schedule & Gantt Chart"
MANAGE_TAB = "➕ Manage Patients & Doctors / Therapists"
REGISTRY_TAB = "🏥 Clinic Resource & Patient Registry"
EXCEL_TAB = "📥 Excel Import / Export"


@pytest.fixture
def clinic_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "app_clinic.db")
    database.init_db(db_path)
    original_get_connection = database.get_connection
    # All form actions and reruns use an isolated database, never the clinic file.
    monkeypatch.setattr(database, "get_connection", lambda *args, **kwargs: original_get_connection(db_path))
    setup = auth.initialize_auth()
    code = setup.read_text(encoding="utf-8").splitlines()[2]
    auth.register_user("Test Hospital Member", "member@example.com", "Test hospital passphrase!", "hospital", invite_code=code)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30)
    app.session_state["auth_token"] = auth.login("member@example.com", "Test hospital passphrase!")
    return app


def control(app, element_type, label):
    return next(element for element in getattr(app, element_type) if element.label == label)


def run(app):
    tabs = app.get("tab_container")
    if tabs:
        # AppTest cannot interact with stateful tabs yet. Send their browser
        # widget state explicitly so submissions preserve the selected tab.
        widget_state = app._tree.get_widget_states()
        for tab in tabs:
            if tab.key in app.session_state:
                widget_state.widgets.add(id=tab.proto.tab_container.id, string_value=app.session_state[tab.key])
        app._run(widget_state)
    else:
        app.run()
    assert not app.exception


def select_tab(app, label):
    app.session_state["clinic_tabs"] = label
    run(app)


def navigation_position(app):
    tabs = [(index, child.proto.id) for index, child in app.main.children.items()
            if child.type == "tab_container"]
    assert len(tabs) == 1
    return tabs[0]


def test_practitioner_forms_refresh_roster_and_schedule(clinic_app):
    app = clinic_app
    run(app)
    navigation = navigation_position(app)
    select_tab(app, MANAGE_TAB)
    control(app, "text_input", "Doctor / Therapist Full Name").input("Dr. Test Practitioner")
    control(app, "multiselect", "Qualified Therapies").set_value(["ABHYANGA", "SWEDANA"])
    control(app, "button", "Add Doctor / Therapist").click()
    run(app)

    practitioner = next(t for t in database.fetch_clinic_state()["therapists"] if t["name"] == "Dr. Test Practitioner")
    assert any(t["id"] == practitioner["id"] for t in app.session_state["schedule_context"][0]["therapists"])
    assert "added" in app.success[0].value
    assert app.session_state["clinic_tabs"] == MANAGE_TAB
    assert app.get("tab_container")[0].proto.tab_container.default_tab_index == 3
    assert navigation_position(app) == navigation
    assert "latest_result" not in app.session_state

    control(app, "selectbox", "Doctor / Therapist to remove").select(practitioner["id"])
    control(app, "button", "Remove Doctor / Therapist").click()
    run(app)
    assert all(t["id"] != practitioner["id"] for t in database.fetch_clinic_state()["therapists"])
    assert all(t["id"] != practitioner["id"] for t in app.session_state["schedule_context"][0]["therapists"])
    assert app.session_state["clinic_tabs"] == MANAGE_TAB
    assert "latest_result" not in app.session_state
    select_tab(app, LIVE_TAB)
    assert practitioner["id"] not in set(app.session_state["latest_result"]["schedule_df"]["Therapist_ID"])
    assert not app.success
    assert navigation_position(app) == navigation
    control(app, "button", "🚀 Generate Optimized Clinic Schedule").click()
    run(app)
    assert navigation_position(app) == navigation
    assert len(app.tabs) == 6
    assert sum(e.label == "🚀 Generate Optimized Clinic Schedule" for e in app.button) == 1


def test_patient_forms_remove_follow_up_and_refresh_schedule(clinic_app):
    app = clinic_app
    run(app)
    select_tab(app, MANAGE_TAB)
    control(app, "text_input", "Patient ID").input("PT-TEST")
    control(app, "text_input", "Patient Full Name").input("Test Patient")
    control(app, "checkbox", "Auto-Chain Follow-up Swedana (Herbal Steam)?").check()
    control(app, "button", "Save Booking to Clinic Database").click()
    run(app)
    assert len(database.fetch_clinic_state()["bookings"]) == 17
    assert app.session_state["clinic_tabs"] == MANAGE_TAB
    assert "latest_result" not in app.session_state
    select_tab(app, LIVE_TAB)
    assert len(app.session_state["latest_result"]["schedule_df"]) == 17

    select_tab(app, MANAGE_TAB)
    control(app, "selectbox", "Patient to remove").select("PT-TEST")
    control(app, "button", "Remove Patient").click()
    run(app)
    assert len(database.fetch_clinic_state()["bookings"]) == 15
    assert app.session_state["clinic_tabs"] == MANAGE_TAB
    select_tab(app, LIVE_TAB)
    assert len(app.session_state["latest_result"]["schedule_df"]) == 15
    assert "PT-TEST" not in set(app.session_state["latest_result"]["schedule_df"]["Patient_ID"])

    control(app, "text_input", "Schedule Date (YYYY-MM-DD)").input("2026-11-19")
    run(app)
    assert "latest_result" not in app.session_state
    assert "No bookings found" in app.warning[0].value
    assert control(app, "button", "Run Comparative Benchmark Suite").disabled


def test_empty_roster_and_empty_patient_list_are_safe(clinic_app):
    app = clinic_app
    run(app)
    for practitioner in database.fetch_clinic_state()["therapists"]:
        database.remove_therapist(practitioner["id"])
    run(app)
    assert "No active doctors / therapists" in app.warning[0].value
    assert "latest_result" not in app.session_state
    assert control(app, "button", "Run Comparative Benchmark Suite").disabled
    assert not any(e.label == "Doctor / Therapist to remove" for e in app.selectbox)

    with closing(database.get_connection()) as conn, conn:
        conn.execute("DELETE FROM bookings")
    run(app)
    assert "No bookings found" in app.warning[0].value
    assert not any(e.label == "Patient to remove" for e in app.selectbox)
    assert control(app, "button", "🚀 Generate Optimized Clinic Schedule").disabled


def test_room_and_equipment_forms_preserve_registry_tab(clinic_app):
    app = clinic_app
    app.session_state["clinic_tabs"] = REGISTRY_TAB
    run(app)
    navigation = navigation_position(app)
    control(app, "text_input", "Room / Chamber Name").input("New Test Chamber")
    control(app, "multiselect", "Equipment in New Room").set_value(["Massage_Table"])
    control(app, "text_input", "Additional Equipment for New Room (comma-separated)").input("Test_Equipment")
    control(app, "button", "Add Room / Chamber").click()
    run(app)
    room = next(r for r in database.fetch_clinic_state()["rooms"] if r["name"] == "New Test Chamber")
    assert room["equipment_set"] == {"Massage_Table", "Test_Equipment"}
    assert app.session_state["clinic_tabs"] == REGISTRY_TAB
    assert navigation_position(app) == navigation

    control(app, "selectbox", "Room / Chamber for Equipment").select(room["id"])
    run(app)
    control(app, "multiselect", "Available Equipment in Selected Room").set_value(["Test_Equipment"])
    control(app, "text_input", "Additional Equipment for Selected Room (comma-separated)").input("Steam_Chamber")
    control(app, "button", "Save Room Equipment").click()
    run(app)
    updated = next(r for r in database.fetch_clinic_state()["rooms"] if r["id"] == room["id"])
    assert updated["equipment_set"] == {"Test_Equipment", "Steam_Chamber"}

    control(app, "selectbox", "Room / Chamber to Remove").select(room["id"])
    control(app, "button", "Remove Room / Chamber").click()
    run(app)
    assert room["id"] not in {r["id"] for r in database.fetch_clinic_state()["rooms"]}
    assert app.session_state["clinic_tabs"] == REGISTRY_TAB
    select_tab(app, LIVE_TAB)
    assert room["id"] not in set(app.session_state["latest_result"]["schedule_df"]["Room_ID"])
    assert navigation_position(app) == navigation


def test_equipment_shortage_disables_scheduling_until_restored(clinic_app):
    app = clinic_app
    app.session_state["clinic_tabs"] = REGISTRY_TAB
    run(app)
    control(app, "selectbox", "Room / Chamber for Equipment").select(5)
    run(app)
    control(app, "multiselect", "Available Equipment in Selected Room").set_value(["Massage_Table", "Steam_Chamber"])
    control(app, "button", "Save Room Equipment").click()
    run(app)
    select_tab(app, LIVE_TAB)
    assert "Basti_Unit" in app.warning[0].value
    assert control(app, "button", "🚀 Generate Optimized Clinic Schedule").disabled
    assert control(app, "button", "Run Comparative Benchmark Suite").disabled
    assert "latest_result" not in app.session_state

    select_tab(app, REGISTRY_TAB)
    control(app, "multiselect", "Available Equipment in Selected Room").set_value(["Massage_Table", "Steam_Chamber", "Basti_Unit"])
    control(app, "button", "Save Room Equipment").click()
    run(app)
    select_tab(app, LIVE_TAB)
    assert not app.warning
    assert not control(app, "button", "🚀 Generate Optimized Clinic Schedule").disabled
    assert len(app.session_state["latest_result"]["schedule_df"]) == 15


def test_therapy_forms_preserve_linked_patient_bookings(clinic_app):
    app = clinic_app
    app.session_state["clinic_tabs"] = REGISTRY_TAB
    run(app)
    control(app, "text_input", "Therapy Code").input("TEST_THERAPY")
    control(app, "text_input", "Therapy Name").input("Test Therapy")
    control(app, "selectbox", "Required Equipment").select("Massage_Table")
    control(app, "multiselect", "Qualified Practitioners for New Therapy").set_value([1])
    control(app, "button", "Add Therapy").click()
    run(app)
    assert "TEST_THERAPY" in database.fetch_clinic_state()["therapies"]
    assert app.session_state["clinic_tabs"] == REGISTRY_TAB
    select_tab(app, MANAGE_TAB)
    control(app, "selectbox", "Therapy Type").select("TEST_THERAPY")
    control(app, "button", "Save Booking to Clinic Database").click()
    run(app)

    select_tab(app, REGISTRY_TAB)
    control(app, "selectbox", "Therapy to Remove").select("TEST_THERAPY")
    control(app, "button", "Remove Therapy").click()
    run(app)
    assert "patient bookings" in app.error[0].value
    assert any(b["therapy_code"] == "TEST_THERAPY" for b in database.fetch_clinic_state()["bookings"])

    select_tab(app, MANAGE_TAB)
    control(app, "selectbox", "Patient to remove").select("PT-115")
    control(app, "button", "Remove Patient").click()
    run(app)
    select_tab(app, REGISTRY_TAB)
    control(app, "selectbox", "Therapy to Remove").select("TEST_THERAPY")
    control(app, "button", "Remove Therapy").click()
    run(app)
    assert "TEST_THERAPY" not in database.fetch_clinic_state()["therapies"]
    assert app.session_state["clinic_tabs"] == REGISTRY_TAB


def test_empty_resources_can_be_repopulated_from_registry(clinic_app):
    app = clinic_app
    with closing(database.get_connection()) as conn, conn:
        conn.execute("DELETE FROM bookings")
    state = database.fetch_clinic_state()
    for code in state["therapies"]:
        database.remove_therapy(code)
    for room in state["rooms"]:
        database.remove_room(room["id"])
    app.session_state["clinic_tabs"] = REGISTRY_TAB
    run(app)
    assert not any(e.label == "Therapy to Remove" for e in app.selectbox)
    assert not any(e.label == "Room / Chamber to Remove" for e in app.selectbox)
    assert control(app, "button", "Save Booking to Clinic Database").disabled
    control(app, "text_input", "Room / Chamber Name").input("Reopened Chamber")
    control(app, "text_input", "Additional Equipment for New Room (comma-separated)").input("New_Equipment")
    control(app, "button", "Add Room / Chamber").click()
    run(app)
    control(app, "text_input", "Therapy Code").input("NEW")
    control(app, "text_input", "Therapy Name").input("New Therapy")
    control(app, "selectbox", "Required Equipment").select("New_Equipment")
    control(app, "button", "Add Therapy").click()
    run(app)
    assert set(database.fetch_clinic_state()["therapies"]) == {"NEW"}
    assert len(database.fetch_clinic_state()["rooms"]) == 1
    assert not control(app, "button", "Save Booking to Clinic Database").disabled


def test_excel_upload_preview_import_and_generation(clinic_app, hospital_workbook_factory):
    app = clinic_app
    run(app)
    control(app, "button", "📥 Hospital Excel Upload").click()
    run(app)
    assert app.session_state["clinic_tabs"] == EXCEL_TAB
    original = database.fetch_clinic_state()
    control(app, "file_uploader", "Upload Filled Hospital Workbook").set_value((
        "hospital.xlsx", hospital_workbook_factory(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ))
    run(app)
    assert not control(app, "button", "Import Workbook and Replace Clinic Data").disabled
    assert database.fetch_clinic_state() == original
    assert app.session_state["clinic_tabs"] == EXCEL_TAB

    control(app, "button", "Import Workbook and Replace Clinic Data").click()
    run(app)
    assert "Hospital workbook imported" in app.success[0].value
    assert app.session_state["schedule_date"] == "2027-02-03"
    assert app.session_state["clinic_tabs"] == EXCEL_TAB
    assert "latest_result" not in app.session_state
    assert not database.fetch_clinic_state()["bookings"]
    assert len(database.fetch_clinic_state("2027-02-03")["bookings"]) == 2
    select_tab(app, LIVE_TAB)
    result = app.session_state["latest_result"]
    assert len(result["schedule_df"]) == 2
    assert set(result["schedule_df"]["Patient_Name"]) == {"Hospital Patient"}
    assert result["evaluation"]["hard_violations"] == 0


def test_invalid_excel_upload_keeps_clinic_and_schedule(clinic_app, hospital_workbook_factory):
    app = clinic_app
    run(app)
    original = database.fetch_clinic_state()
    schedule = app.session_state["latest_result"]["schedule_df"].copy()
    select_tab(app, EXCEL_TAB)
    content = hospital_workbook_factory(lambda workbook: setattr(workbook["Bookings"]["D2"], "value", None))
    control(app, "file_uploader", "Upload Filled Hospital Workbook").set_value((
        "invalid.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ))
    run(app)
    assert control(app, "button", "Import Workbook and Replace Clinic Data").disabled
    assert any("No clinic data has been changed" in error.value for error in app.error)
    assert database.fetch_clinic_state() == original
    assert app.session_state["latest_result"]["schedule_df"].equals(schedule)


def test_login_and_logout_hide_all_clinic_information(clinic_app):
    app = clinic_app
    run(app)
    previous_token = app.session_state["auth_token"]
    control(app, "button", "Log Out").click()
    run(app)
    assert auth.session_user(previous_token) is None
    assert [tab.label for tab in app.tabs] == ["Login", "Sign Up", "Forgot Password"]
    assert not app.dataframe
    assert not app.file_uploader
    assert not any("Generate Optimized" in button.label for button in app.button)
    control(app, "text_input", "Login Email").input("member@example.com")
    control(app, "text_input", "Login Password").input("wrong password")
    control(app, "button", "Log In").click()
    run(app)
    assert "Invalid email or password" in app.error[0].value
    control(app, "text_input", "Login Email").input("member@example.com")
    control(app, "text_input", "Login Password").input("Test hospital passphrase!")
    control(app, "button", "Log In").click()
    run(app)
    assert len(app.tabs) == 6
    assert auth.session_user(app.session_state["auth_token"])["role"] == "hospital"


def test_forgot_password_screen_redeems_saved_recovery_code(clinic_app):
    app = clinic_app
    run(app)
    control(app, "button", "Generate New Recovery Code").click()
    run(app)
    code = app.session_state["recovery_code_once"]
    control(app, "button", "Log Out").click()
    run(app)
    app.session_state["auth_page"] = "Forgot Password"
    run(app)
    for label, value in {
        "Account Email": "member@example.com",
        "Recovery Code": code,
        "New Password": "A replacement hospital password!",
        "Confirm New Password": "A replacement hospital password!",
    }.items():
        control(app, "text_input", label).input(value)
    control(app, "button", "Reset Password").click()
    run(app)
    assert any("Password reset" in message.value for message in app.success)
    control(app, "text_input", "Login Email").input("member@example.com")
    control(app, "text_input", "Login Password").input("A replacement hospital password!")
    control(app, "button", "Log In").click()
    run(app)
    assert auth.session_user(app.session_state["auth_token"])["role"] == "hospital"


def test_member_signup_uses_hospital_invite(clinic_app):
    app = clinic_app
    code = auth.create_invite(app.session_state["auth_token"], "new.member@example.com")
    run(app)
    control(app, "button", "Log Out").click()
    run(app)
    app.session_state["auth_page"] = "Sign Up"
    control(app, "radio", "Account Type").set_value("Hospital Member")
    run(app)
    for label, value in {
        "Full Name": "New Member", "Signup Email": "new.member@example.com",
        "Signup Password": "New member strong passphrase!", "Confirm Password": "New member strong passphrase!",
        "Hospital Invite Code": code,
    }.items():
        control(app, "text_input", label).input(value)
    control(app, "button", "Create Account").click()
    run(app)
    assert auth.session_user(app.session_state["auth_token"])["role"] == "hospital"
    assert any(button.label == "Add Room / Chamber" for button in app.button)


def test_fixed_hospital_code_signup_in_app(clinic_app, monkeypatch):
    fixed_code = "A-private-fixed-hospital-code-123456789"
    monkeypatch.setenv("AYUR_HOSPITAL_INVITE_CODE", fixed_code)
    app = clinic_app
    run(app)
    control(app, "button", "Log Out").click()
    run(app)
    app.session_state["auth_page"] = "Sign Up"
    control(app, "radio", "Account Type").set_value("Hospital Member")
    run(app)
    for label, value in {
        "Full Name": "Fixed Code Member", "Signup Email": "fixed@example.com",
        "Signup Password": "A strong fixed-code password!", "Confirm Password": "A strong fixed-code password!",
        "Hospital Invite Code": fixed_code,
    }.items():
        control(app, "text_input", label).input(value)
    control(app, "button", "Create Account").click()
    run(app)
    assert auth.session_user(app.session_state["auth_token"])["role"] == "hospital"


def test_patient_signup_sees_only_own_appointments_despite_role_tampering(clinic_app):
    app = clinic_app
    invite = auth.create_invite(app.session_state["auth_token"], "patient@example.com", "patient", "PT-102")
    run(app)
    control(app, "button", "Log Out").click()
    run(app)
    app.session_state["auth_page"] = "Sign Up"
    for label, value in {
        "Full Name": "Patient Viewer", "Signup Email": "patient@example.com",
        "Signup Password": "Patient account passphrase!", "Confirm Password": "Patient account passphrase!",
        "Patient Invite Code (optional)": invite,
    }.items():
        control(app, "text_input", label).input(value)
    control(app, "selectbox", "Patient Gender").select("F")
    control(app, "button", "Create Account").click()
    run(app)
    assert not app.tabs
    assert not app.slider
    assert not app.file_uploader
    assert not any(button.label in {"Add Room / Chamber", "Remove Patient", "Import Workbook and Replace Clinic Data"} for button in app.button)
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["Therapy"] == "Shirodhara (Forehead Oil Flow)"
    app.session_state["role"] = "hospital"
    app.session_state["patient_id"] = "PT-101"
    app.session_state["clinic_tabs"] = REGISTRY_TAB
    run(app)
    assert not app.tabs
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["Therapy"] == "Shirodhara (Forehead Oil Flow)"


def test_revoked_import_callback_cannot_replace_data(clinic_app, hospital_workbook_factory):
    app = clinic_app
    app.session_state["clinic_tabs"] = EXCEL_TAB
    run(app)
    control(app, "file_uploader", "Upload Filled Hospital Workbook").set_value((
        "hospital.xlsx", hospital_workbook_factory(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ))
    run(app)
    before = database.fetch_clinic_state()
    auth.logout(app.session_state["auth_token"])
    control(app, "button", "Import Workbook and Replace Clinic Data").click()
    run(app)
    assert database.fetch_clinic_state() == before
    assert [tab.label for tab in app.tabs] == ["Login", "Sign Up", "Forgot Password"]
