from contextlib import closing

import pytest

from ayurschedule.database import (
    add_booking, add_therapist, fetch_clinic_state, get_connection, init_db,
    remove_patient_bookings, remove_therapist,
    add_room, remove_room, update_room_equipment, add_therapy, remove_therapy,
)
from ayurschedule.optimizer import AyurSchedulerEngine


@pytest.fixture
def clinic_db(tmp_path):
    db_path = str(tmp_path / "clinic.db")
    init_db(db_path)
    return db_path


def test_add_and_remove_practitioner_persists_after_restart(clinic_db):
    practitioner_id = add_therapist(
        "  Dr. New Practitioner  ", "F", ["ABHYANGA", "SWEDANA"],
        2, 20, 12, db_path=clinic_db
    )
    state = fetch_clinic_state(db_path=clinic_db)
    practitioner = next(t for t in state["therapists"] if t["id"] == practitioner_id)
    assert practitioner["name"] == "Dr. New Practitioner"
    assert practitioner["skills_set"] == {"ABHYANGA", "SWEDANA"}
    assert (practitioner["shift_start_slot"], practitioner["shift_end_slot"]) == (2, 20)

    assert remove_therapist(practitioner_id, db_path=clinic_db)
    assert not remove_therapist(practitioner_id, db_path=clinic_db)
    init_db(clinic_db)
    assert practitioner_id not in {t["id"] for t in fetch_clinic_state(db_path=clinic_db)["therapists"]}
    with closing(get_connection(clinic_db)) as conn:
        assert conn.execute("SELECT active FROM therapists WHERE id = ?", (practitioner_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("overrides", [
    {"name": "   "}, {"skills": []}, {"skills": ["UNKNOWN"]},
    {"shift_start_slot": 18, "shift_end_slot": 2},
    {"shift_end_slot": 21}, {"max_daily_slots": 0},
    {"shift_end_slot": 4, "max_daily_slots": 12},
])
def test_invalid_practitioner_does_not_change_roster(clinic_db, overrides):
    fields = dict(name="New Practitioner", gender="F", skills=["ABHYANGA"],
                  shift_start_slot=0, shift_end_slot=20, max_daily_slots=12)
    fields.update(overrides)
    before = fetch_clinic_state(db_path=clinic_db)["therapists"]
    with pytest.raises(ValueError):
        add_therapist(**fields, db_path=clinic_db)
    assert fetch_clinic_state(db_path=clinic_db)["therapists"] == before


def test_remove_patient_deletes_chain_and_saved_rows_only_on_selected_date(clinic_db):
    next_day_id = add_booking(
        "2026-11-19", "PT-101", "Rajesh Verma", "M", "ABHYANGA", "Vata",
        include_swedana=True, db_path=clinic_db
    )
    with closing(get_connection(clinic_db)) as conn, conn:
        # Saved output for this patient on both dates, and for another patient.
        for booking_id, date in [(1, "2026-11-18"), (2, "2026-11-18"),
                                 (3, "2026-11-18"), (next_day_id, "2026-11-19")]:
            conn.execute(
                """INSERT INTO generated_schedules
                   (schedule_date, algorithm, booking_id, patient_name, patient_gender,
                    therapy_code, therapist_id, therapist_name, room_id, room_name,
                    start_slot, end_slot, time_window, status_flags, total_penalty)
                   VALUES (?, 'test', ?, 'Patient', 'M', 'ABHYANGA', 1, 'Doctor',
                           1, 'Room', 0, 2, '08:00 - 09:00', 'OPTIMAL', 0)""",
                (date, booking_id)
            )

    assert remove_patient_bookings("PT-101", "2026-11-18", db_path=clinic_db) == 2
    assert remove_patient_bookings("PT-101", "2026-11-18", db_path=clinic_db) == 0
    today = fetch_clinic_state(db_path=clinic_db)
    assert len(today["bookings"]) == 13
    assert all(b["patient_id"] != "PT-101" for b in today["bookings"])
    assert len(fetch_clinic_state("2026-11-19", db_path=clinic_db)["bookings"]) == 2
    with closing(get_connection(clinic_db)) as conn:
        assert {row[0] for row in conn.execute("SELECT booking_id FROM generated_schedules")} == {3, next_day_id}


def test_adding_and_removing_last_patient_does_not_reseed(clinic_db):
    add_booking(
        "2026-11-20", "PT-NEW", "New Patient", "F", "ABHYANGA", "Vata",
        include_swedana=True, db_path=clinic_db
    )
    bookings = fetch_clinic_state("2026-11-20", db_path=clinic_db)["bookings"]
    assert len(bookings) == 2
    assert bookings[1]["depends_on_booking_id"] == bookings[0]["id"]
    assert remove_patient_bookings("PT-NEW", "2026-11-20", db_path=clinic_db) == 2
    init_db(clinic_db)
    assert not fetch_clinic_state("2026-11-20", db_path=clinic_db)["bookings"]


def test_invalid_follow_up_does_not_leave_a_partial_booking(clinic_db):
    with closing(get_connection(clinic_db)) as conn, conn:
        conn.execute("DELETE FROM bookings WHERE therapy_code = 'SWEDANA'")
        conn.execute("DELETE FROM therapies WHERE code = 'SWEDANA'")
    before = fetch_clinic_state(db_path=clinic_db)["bookings"]
    with pytest.raises(ValueError):
        add_booking(
            "2026-11-18", "PT-NEW", "New Patient", "F", "ABHYANGA", "Vata",
            include_swedana=True, db_path=clinic_db
        )
    assert fetch_clinic_state(db_path=clinic_db)["bookings"] == before


def test_rooms_and_equipment_persist_and_removed_rooms_stay_removed(clinic_db):
    room_id = add_room("  New Chamber  ", ["Massage_Table", "Custom_Equipment"], db_path=clinic_db)
    update_room_equipment(room_id, ["Custom_Equipment", "Steam_Chamber"], db_path=clinic_db)
    init_db(clinic_db)
    room = next(r for r in fetch_clinic_state(db_path=clinic_db)["rooms"] if r["id"] == room_id)
    assert room["name"] == "New Chamber"
    assert room["equipment_set"] == {"Custom_Equipment", "Steam_Chamber"}
    update_room_equipment(room_id, [], db_path=clinic_db)
    assert next(r for r in fetch_clinic_state(db_path=clinic_db)["rooms"] if r["id"] == room_id)["equipment_set"] == set()
    assert remove_room(room_id, db_path=clinic_db)
    assert not remove_room(room_id, db_path=clinic_db)
    init_db(clinic_db)
    assert room_id not in {r["id"] for r in fetch_clinic_state(db_path=clinic_db)["rooms"]}


def test_duplicate_and_invalid_rooms_do_not_change_registry(clinic_db):
    before = fetch_clinic_state(db_path=clinic_db)["rooms"]
    for name, equipment in [(" ", ["Massage_Table"]), ("New Room", []),
                            ("dhara suite 101", ["Massage_Table"])]:
        with pytest.raises(ValueError):
            add_room(name, equipment, db_path=clinic_db)
    with pytest.raises(ValueError):
        update_room_equipment(9999, ["Massage_Table"], db_path=clinic_db)
    assert fetch_clinic_state(db_path=clinic_db)["rooms"] == before


def test_new_therapy_practitioner_and_room_are_used_by_scheduler(clinic_db):
    room_id = add_room("New Therapy Chamber", ["New_Equipment"], db_path=clinic_db)
    code = add_therapy(
        " new_therapy ", "New Therapy", "Purvakarma", 1, 1, "Vata", "New_Equipment",
        follow_up_code="SWEDANA", therapist_ids=[1], db_path=clinic_db
    )
    assert code == "NEW_THERAPY"
    add_booking("2026-11-19", "PT-NEW", "New Patient", "M", code, "Vata", db_path=clinic_db)
    state = fetch_clinic_state("2026-11-19", db_path=clinic_db)
    result = AyurSchedulerEngine(state).run_memetic_ga(pop_size=20, generations=20)
    assert result["evaluation"]["hard_violations"] == 0
    assert set(result["schedule_df"]["Room_ID"]) == {room_id}
    assert set(result["schedule_df"]["Therapist_ID"]) == {1}
    with pytest.raises(ValueError, match="patient bookings"):
        remove_therapy(code, db_path=clinic_db)
    remove_patient_bookings("PT-NEW", "2026-11-19", db_path=clinic_db)
    add_therapy("CHAIN", "Chained Therapy", "Purvakarma", 1, 0, "Vata", "New_Equipment",
                follow_up_code=code, db_path=clinic_db)
    assert remove_therapy(code, db_path=clinic_db)
    state = fetch_clinic_state(db_path=clinic_db)
    assert code not in state["therapies"]
    assert state["therapies"]["CHAIN"]["follow_up_code"] is None
    assert all(code not in t["skills_set"] for t in state["therapists"])


@pytest.mark.parametrize("overrides", [
    {"code": "INVALID CODE"}, {"code": "ABHYANGA"}, {"name": " "},
    {"duration_slots": 0}, {"duration_slots": 20, "prep_slots": 1},
    {"required_equipment": ""}, {"follow_up_code": "MISSING"}, {"therapist_ids": [9999]},
])
def test_invalid_therapy_is_atomic(clinic_db, overrides):
    fields = dict(code="NEW", name="New Therapy", category="Purvakarma", duration_slots=2,
                  prep_slots=1, optimal_dosha="Vata", required_equipment="Massage_Table", therapist_ids=[1])
    fields.update(overrides)
    before = fetch_clinic_state(db_path=clinic_db)
    with pytest.raises(ValueError):
        add_therapy(**fields, db_path=clinic_db)
    assert fetch_clinic_state(db_path=clinic_db) == before


def test_empty_resource_registry_does_not_restore_demo_data(clinic_db):
    with closing(get_connection(clinic_db)) as conn, conn:
        conn.execute("DELETE FROM bookings")
    state = fetch_clinic_state(db_path=clinic_db)
    for code in state["therapies"]:
        remove_therapy(code, db_path=clinic_db)
    for room in state["rooms"]:
        remove_room(room["id"], db_path=clinic_db)
    init_db(clinic_db)
    current = fetch_clinic_state(db_path=clinic_db)
    assert not current["therapies"]
    assert not current["rooms"]
    assert not current["bookings"]
    assert [t["id"] for t in current["therapists"]] == [t["id"] for t in state["therapists"]]
