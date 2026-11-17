from contextlib import closing
from datetime import datetime, time
from io import BytesIO
import sqlite3

from openpyxl import load_workbook
import pytest

from ayurschedule.database import fetch_clinic_state, get_connection, init_db
from ayurschedule.excel_io import (
    COLUMNS, WorkbookValidationError, build_hospital_template, export_clinic_workbook,
    import_hospital_workbook, parse_hospital_workbook,
)
from ayurschedule.optimizer import AyurSchedulerEngine


def test_blank_template_has_headings_guidance_and_no_sample_patient_data():
    workbook = load_workbook(BytesIO(build_hospital_template()))
    assert workbook.sheetnames == ["Instructions", *COLUMNS]
    for name, columns in COLUMNS.items():
        assert [cell.value for cell in workbook[name][1]] == columns
        assert all(cell.comment for cell in workbook[name][1])
        assert all(cell.value is None for row in workbook[name].iter_rows(min_row=2) for cell in row)
    workbook.close()
    with pytest.raises(WorkbookValidationError, match="at least one data row"):
        parse_hospital_workbook(build_hospital_template())


def test_hospital_import_replaces_demo_data_and_generates_its_schedule(tmp_path, hospital_workbook_factory):
    db_path = str(tmp_path / "hospital.db")
    init_db(db_path)
    imported = import_hospital_workbook(hospital_workbook_factory(), db_path)
    assert imported.dates == ["2027-02-03"]
    assert imported.counts == dict(Equipment=3, Therapies=2, Doctors=1, Rooms=2, Bookings=2)
    assert not fetch_clinic_state("2026-11-18", db_path)["bookings"]
    init_db(db_path)
    state = fetch_clinic_state("2027-02-03", db_path)
    assert set(state["therapies"]) == {"MASSAGE", "STEAM"}
    assert state["therapists"][0]["name"] == "Hospital Doctor"
    assert "Unused_Equipment" in state["equipment"]
    assert state["bookings"][0]["patient_id"] == "0007"
    assert state["bookings"][1]["depends_on_booking_id"] == state["bookings"][0]["id"]
    assert state["bookings"][1]["priority"] == 2
    result = AyurSchedulerEngine(state).run_memetic_ga(pop_size=20, generations=30)
    schedule = result["schedule_df"].set_index("Therapy_Code")
    assert result["evaluation"]["hard_violations"] == 0
    assert set(schedule["Patient_Name"]) == {"Hospital Patient"}
    assert schedule.loc["STEAM", "Start_Slot"] == schedule.loc["MASSAGE", "End_Slot"]
    # Re-import is a replacement, so it never duplicates bookings/resources.
    import_hospital_workbook(hospital_workbook_factory(), db_path)
    assert len(fetch_clinic_state("2027-02-03", db_path)["bookings"]) == 2


def test_follow_ups_can_appear_before_their_parent(tmp_path, hospital_workbook_factory):
    def reverse(workbook):
        sheet = workbook["Bookings"]
        first, second = list(sheet.values)[1:3]
        for column, (parent, child) in enumerate(zip(first, second), start=1):
            sheet.cell(2, column).value = child
            sheet.cell(3, column).value = parent
    content = hospital_workbook_factory(reverse)
    parsed = parse_hospital_workbook(content)
    assert [row["external_id"] for row in parsed.bookings] == ["B100", "B101"]


@pytest.mark.parametrize("sheet, cell, value, message", [
    ("Therapies", "D1", "wrong_heading", "missing column"),
    ("Therapies", "E2", 15, "cleanup_minutes"),
    ("Therapies", "D2", "=30*2", "formulas"),
    ("Equipment", "A3", "Massage_Table", "unique"),
    ("Rooms", "B2", "Missing_Equipment", "missing from Equipment"),
    ("Doctors", "D2", "18:30", "08:00 and 18:00"),
    ("Doctors", "F2", 999, "max_daily_minutes"),
    ("Doctors", "C2", "STEAM", "no active qualified doctor"),
    ("Doctors", "B2", "M", "gender match"),
    ("Bookings", "F2", "MISSING", "missing from Therapies"),
    ("Bookings", "A3", "B100", "duplicated"),
    ("Bookings", "G2", 1.5, "priority"),
    ("Bookings", "I2", "B101", "cycle"),
    ("Bookings", "I3", "MISSING", "does not exist"),
    ("Bookings", "C3", "OTHER-PATIENT", "same patient_id"),
    ("Bookings", "B3", "2027-02-04", "same patient_id"),
    ("Bookings", "D2", None, "patient_name is required"),
    ("Bookings", "B2", "2027-02-30", "valid YYYY-MM-DD"),
])
def test_invalid_workbook_reports_rows_and_keeps_existing_clinic(
    tmp_path, hospital_workbook_factory, sheet, cell, value, message
):
    db_path = str(tmp_path / "unchanged.db")
    init_db(db_path)
    before = fetch_clinic_state(db_path=db_path)
    def edit(workbook):
        workbook[sheet][cell] = value
    with pytest.raises(WorkbookValidationError, match=message) as error:
        import_hospital_workbook(hospital_workbook_factory(edit), db_path)
    assert any("row" in issue or "cycle" in issue for issue in error.value.errors)
    assert fetch_clinic_state(db_path=db_path) == before


def test_missing_sheet_and_non_excel_upload_are_explained(hospital_workbook_factory):
    with pytest.raises(WorkbookValidationError, match="Missing sheet: Rooms"):
        parse_hospital_workbook(hospital_workbook_factory(lambda workbook: workbook.remove(workbook["Rooms"])))
    with pytest.raises(WorkbookValidationError, match="Cannot read this Excel file"):
        parse_hospital_workbook(b"this is not an Excel file")


def test_native_excel_dates_times_and_gender_setting(hospital_workbook_factory):
    def edit(workbook):
        workbook["Doctors"]["D2"] = time(8)
        workbook["Doctors"]["E2"] = time(18)
        workbook["Doctors"]["B2"] = "M"
        workbook["Bookings"]["B2"] = datetime(2027, 2, 3)
        workbook["Bookings"]["B3"] = datetime(2027, 2, 3)
    data = parse_hospital_workbook(hospital_workbook_factory(edit), enforce_gender_match=False)
    assert data.dates == ["2027-02-03"]
    assert data.doctors[0]["shift_end_slot"] == 20


def test_backup_round_trip_keeps_all_dates_and_chains(tmp_path, hospital_workbook_factory):
    original, restored = str(tmp_path / "original.db"), str(tmp_path / "restored.db")
    import_hospital_workbook(hospital_workbook_factory(), original)
    with closing(get_connection(original)) as conn, conn:
        conn.execute("UPDATE bookings SET patient_name = ?", ("=Literal Patient Name",))
        conn.execute("INSERT INTO bookings (schedule_date, patient_id, patient_name, patient_gender, therapy_code, priority, preferred_dosha) VALUES ('2027-02-04','0008','Second Patient','F','MASSAGE',1,'Vata')")
    backup = export_clinic_workbook(original)
    workbook = load_workbook(BytesIO(backup))
    assert workbook["Bookings"]["D2"].data_type == "s"
    workbook.close()
    imported = import_hospital_workbook(backup, restored)
    assert imported.dates == ["2027-02-03", "2027-02-04"]
    state = fetch_clinic_state("2027-02-03", restored)
    assert state["bookings"][0]["patient_name"] == "=Literal Patient Name"
    assert state["bookings"][1]["depends_on_booking_id"] == state["bookings"][0]["id"]
    assert len(fetch_clinic_state("2027-02-04", restored)["bookings"]) == 1


def test_database_failure_rolls_back_entire_import(tmp_path, hospital_workbook_factory):
    db_path = str(tmp_path / "rollback.db")
    init_db(db_path)
    before = fetch_clinic_state(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute("CREATE TRIGGER block_import BEFORE INSERT ON therapists WHEN NEW.name = 'Hospital Doctor' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        import_hospital_workbook(hospital_workbook_factory(), db_path)
    assert fetch_clinic_state(db_path=db_path) == before
