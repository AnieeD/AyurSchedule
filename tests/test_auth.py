from contextlib import closing
import time

import pytest

from ayurschedule import auth, database
from ayurschedule.access import ClinicAccess, STAFF_ACTIONS
from ayurschedule.excel_io import import_hospital_workbook
from ayurschedule.optimizer import AyurSchedulerEngine

PASSWORD = "A strong hospital passphrase!"


@pytest.fixture
def accounts(tmp_path):
    db_path = str(tmp_path / "auth_clinic.db")
    database.init_db(db_path)
    setup = auth.initialize_auth(db_path)
    code = setup.read_text(encoding="utf-8").splitlines()[2]
    staff_id = auth.register_user("Hospital Member", "staff@example.com", PASSWORD, "hospital", invite_code=code, db_path=db_path)
    token = auth.login("staff@example.com", PASSWORD, db_path)
    return db_path, staff_id, token, code


def patient_account(accounts, email="patient@example.com", patient_id="PT-101"):
    db_path, _, staff, _ = accounts
    invite = auth.create_invite(staff, email, "patient", patient_id, db_path)
    auth.register_user("Patient Account", email, PASSWORD, "patient", gender="M", invite_code=invite, db_path=db_path)
    return auth.login(email, PASSWORD, db_path)


def test_password_hashes_sessions_and_logout(accounts):
    db_path, staff_id, token, _ = accounts
    assert auth.session_user(token, db_path)["role"] == "hospital"
    with closing(database.get_connection(db_path)) as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (staff_id,)).fetchone()[0]
        stored_session = conn.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    assert stored_session != token
    auth.logout(token, db_path)
    assert auth.session_user(token, db_path) is None
    with pytest.raises(auth.AccessDenied):
        ClinicAccess(token, db_path).call("fetch_clinic_state")


def test_recovery_code_resets_password_once_and_revokes_sessions(accounts):
    db_path, _, session, _ = accounts
    first = auth.create_recovery_code(session, db_path)
    code = auth.create_recovery_code(session, db_path)
    with pytest.raises(ValueError, match="Invalid recovery"):
        auth.reset_password("staff@example.com", first, "A different strong password!", db_path)
    with pytest.raises(ValueError, match="Invalid recovery"):
        auth.reset_password("another@example.com", code, "A different strong password!", db_path)
    with pytest.raises(ValueError, match="12 to 128"):
        auth.reset_password("staff@example.com", code, "short", db_path)
    auth.reset_password("staff@example.com", code, "A different strong password!", db_path)
    assert auth.session_user(session, db_path) is None
    with pytest.raises(ValueError, match="Invalid email or password"):
        auth.login("staff@example.com", PASSWORD, db_path)
    assert auth.session_user(auth.login("staff@example.com", "A different strong password!", db_path), db_path)["role"] == "hospital"
    with pytest.raises(ValueError, match="Invalid recovery"):
        auth.reset_password("staff@example.com", code, PASSWORD, db_path)


def test_member_invites_are_required_email_bound_single_use_and_role_bound(accounts):
    db_path, _, staff, used_setup = accounts
    for code in ("", "wrong", used_setup):
        with pytest.raises(ValueError):
            auth.register_user("Intruder", "intruder@example.com", PASSWORD, "hospital", invite_code=code, db_path=db_path)
    invite = auth.create_invite(staff, "new.member@example.com", db_path=db_path)
    with pytest.raises(ValueError, match="another email"):
        auth.register_user("Wrong Email", "wrong@example.com", PASSWORD, "hospital", invite_code=invite, db_path=db_path)
    with pytest.raises(ValueError):
        auth.register_user("Wrong Role", "new.member@example.com", PASSWORD, "patient", gender="M", invite_code=invite, db_path=db_path)
    auth.register_user("New Member", "NEW.MEMBER@EXAMPLE.COM", PASSWORD, "hospital", invite_code=invite, db_path=db_path)
    assert auth.session_user(auth.login("new.member@example.com", PASSWORD, db_path), db_path)["role"] == "hospital"
    assert auth.initialize_auth(db_path) is None
    with pytest.raises(ValueError):
        auth.register_user("Again", "again@example.com", PASSWORD, "hospital", invite_code=invite, db_path=db_path)


def test_deployed_first_member_uses_private_setup_secret(tmp_path):
    db_path = str(tmp_path / "deployed.db")
    setup_code = "A-private-random-deployment-setup-code-12345"
    database.init_db(db_path)
    auth.initialize_auth(db_path, setup_code=setup_code)
    auth.initialize_auth(db_path, setup_code=setup_code)
    assert not (tmp_path / ".streamlit" / "hospital_setup_invite.txt").exists()
    auth.register_user("First Member", "first@example.com", PASSWORD, "hospital", invite_code=setup_code, db_path=db_path)
    assert auth.session_user(auth.login("first@example.com", PASSWORD, db_path), db_path)["role"] == "hospital"
    with pytest.raises(ValueError):
        auth.register_user("Second Member", "second@example.com", PASSWORD, "hospital", invite_code=setup_code, db_path=db_path)


def test_fixed_hospital_code_works_for_first_and_later_members(tmp_path):
    db_path = str(tmp_path / "fixed_code.db")
    fixed_code = "A-private-fixed-hospital-code-123456789"
    replacement = "A-different-private-hospital-code-987654321"
    database.init_db(db_path)
    auth.initialize_auth(db_path, member_code=fixed_code)
    assert not (tmp_path / ".streamlit" / "hospital_setup_invite.txt").exists()
    auth.register_user("First Member", "first@example.com", PASSWORD, "hospital", invite_code=fixed_code, db_path=db_path)
    auth.initialize_auth(db_path, member_code=fixed_code)
    auth.register_user("Second Member", "second@example.com", PASSWORD, "hospital", invite_code=fixed_code, db_path=db_path)
    with pytest.raises(ValueError, match="invite code"):
        auth.register_user("Patient", "patient@example.com", PASSWORD, "patient", gender="F", invite_code=fixed_code, db_path=db_path)
    auth.initialize_auth(db_path, member_code=replacement)
    with pytest.raises(ValueError, match="invite code"):
        auth.register_user("Old Code", "old@example.com", PASSWORD, "hospital", invite_code=fixed_code, db_path=db_path)
    auth.register_user("Third Member", "third@example.com", PASSWORD, "hospital", invite_code=replacement, db_path=db_path)
    auth.initialize_auth(db_path)
    with pytest.raises(ValueError, match="invite code"):
        auth.register_user("Disabled Code", "disabled@example.com", PASSWORD, "hospital", invite_code=replacement, db_path=db_path)


def test_patient_can_self_register_but_cannot_claim_an_existing_id(accounts):
    db_path, _, _, _ = accounts
    auth.register_user("New Patient", "new.patient@example.com", PASSWORD, "patient", gender="F", db_path=db_path)
    user = auth.session_user(auth.login("new.patient@example.com", PASSWORD, db_path), db_path)
    assert user["patient_id"].startswith("PT-")
    assert user["patient_id"] not in {row["patient_id"] for row in database.fetch_clinic_state(db_path=db_path)["bookings"]}
    assert ClinicAccess(auth.login("new.patient@example.com", PASSWORD, db_path), db_path).patient_appointments() == []


def test_all_clinic_operations_deny_patients_and_anonymous_sessions(accounts):
    db_path, _, staff, _ = accounts
    patient = patient_account(accounts)
    before = database.fetch_clinic_state(db_path=db_path)
    for token in (None, "forged-session", patient):
        access = ClinicAccess(token, db_path)
        for operation in STAFF_ACTIONS:
            with pytest.raises(auth.AccessDenied):
                access.call(operation)
        with pytest.raises(auth.AccessDenied):
            access.patient_registry()
        with pytest.raises(auth.AccessDenied):
            access.save_patient_schedule({}, {})
    with pytest.raises(auth.AccessDenied):
        auth.create_invite(patient, "elevate@example.com", db_path=db_path)
    assert database.fetch_clinic_state(db_path=db_path) == before
    assert ClinicAccess(staff, db_path).call("fetch_clinic_state")["bookings"]


def test_patient_scope_and_persisted_schedule_invalidation(accounts):
    db_path, _, staff, _ = accounts
    first = patient_account(accounts)
    second = patient_account(accounts, "second@example.com", "PT-102")
    access = ClinicAccess(staff, db_path)
    state = access.call("fetch_clinic_state")
    result = AyurSchedulerEngine(state).run_memetic_ga(pop_size=60, generations=80)
    assert access.save_patient_schedule(result, state)
    first_rows = ClinicAccess(first, db_path).patient_appointments()
    second_rows = ClinicAccess(second, db_path).patient_appointments()
    assert len(first_rows) == 2
    assert len(second_rows) == 1
    assert all(row["Status"] == "Scheduled" for row in first_rows + second_rows)
    assert {row["Therapy"] for row in first_rows} == {"Abhyanga (Full Body Oil Massage)", "Bashpa Swedana (Herbal Steam)"}
    assert second_rows[0]["Therapy"] == "Shirodhara (Forehead Oil Flow)"
    access.call("add_room", "Another Room", ["Massage_Table"])
    pending = ClinicAccess(first, db_path).patient_appointments()
    assert all(row["Start"] == "Pending" for row in pending)
    assert not access.save_patient_schedule(result, state)


def test_existing_patient_links_require_private_matching_invite(accounts):
    db_path, _, staff, _ = accounts
    auth.register_user("Patient", "new@example.com", PASSWORD, "patient", gender="M", db_path=db_path)
    token = auth.login("new@example.com", PASSWORD, db_path)
    assert ClinicAccess(token, db_path).patient_appointments() == []
    code = auth.create_invite(staff, "new@example.com", "patient", "PT-101", db_path)
    auth.link_patient(token, code, db_path)
    assert len(ClinicAccess(token, db_path).patient_appointments()) == 2
    with pytest.raises(ValueError):
        auth.link_patient(token, code, db_path)
    with pytest.raises(ValueError, match="another account"):
        auth.create_invite(staff, "different@example.com", "patient", "PT-101", db_path)


def test_rate_limit_expiry_and_deactivation(accounts, monkeypatch):
    db_path, _, staff, _ = accounts
    patient = patient_account(accounts)
    for _ in range(5):
        with pytest.raises(ValueError):
            auth.login("patient@example.com", "wrong", db_path)
    with pytest.raises(ValueError, match="Too many"):
        auth.login("patient@example.com", PASSWORD, db_path)
    now = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: now + 901)
    refreshed = auth.login("patient@example.com", PASSWORD, db_path)
    user = auth.session_user(refreshed, db_path)
    auth.set_account_active(staff, user["id"], False, db_path)
    assert auth.session_user(patient, db_path) is None
    assert auth.session_user(refreshed, db_path) is None
    monkeypatch.setattr(auth.time, "time", lambda: now + 901 + auth.IDLE_SECONDS + 1)
    assert auth.session_user(staff, db_path) is None


def test_excel_replacement_preserves_accounts_but_unlinks_patient_records(accounts, hospital_workbook_factory):
    db_path, _, staff, _ = accounts
    patient = patient_account(accounts)
    unused = auth.create_invite(staff, "patient@example.com", "patient", "PT-101", db_path)
    import_hospital_workbook(hospital_workbook_factory(), db_path)
    assert auth.session_user(staff, db_path)["role"] == "hospital"
    assert auth.session_user(patient, db_path)["patient_id"] is None
    assert ClinicAccess(patient, db_path).patient_appointments() == []
    with pytest.raises(ValueError):
        auth.link_patient(patient, unused, db_path)
