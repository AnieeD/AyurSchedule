"""Role-checked clinic operations and patient-scoped appointment queries."""

from contextlib import closing
import hashlib
import json

from . import auth, database, excel_io
from .optimizer import TIME_SLOTS

STAFF_ACTIONS = {
    name: getattr(database, name) for name in (
        "fetch_clinic_state", "add_booking", "remove_patient_bookings", "add_therapist",
        "remove_therapist", "add_room", "remove_room", "update_room_equipment", "add_therapy", "remove_therapy"
    )
}
STAFF_ACTIONS.update(import_hospital_workbook=excel_io.import_hospital_workbook,
                     export_clinic_workbook=excel_io.export_clinic_workbook)


def clinic_fingerprint(state):
    encoded = json.dumps(state, sort_keys=True, default=lambda item: sorted(item), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ClinicAccess:
    def __init__(self, token, db_path=database.DB_PATH):
        self.token, self.db_path = token, db_path

    def require_staff(self):
        return auth.require_user(self.token, "hospital", self.db_path)

    def call(self, operation, *args, **kwargs):
        self.require_staff()
        if operation not in STAFF_ACTIONS:
            raise auth.AccessDenied("This clinic operation is not available.")
        return STAFF_ACTIONS[operation](*args, **kwargs, db_path=self.db_path)

    def patient_registry(self):
        self.require_staff()
        with closing(database.get_connection(self.db_path)) as conn:
            return [dict(row) for row in conn.execute("SELECT DISTINCT patient_id, patient_name, patient_gender FROM bookings ORDER BY patient_name")]

    def save_patient_schedule(self, result, state):
        self.require_staff()
        if result["evaluation"]["hard_violations"]:
            return False
        schedule_date = state["schedule_date"]
        fingerprint = clinic_fingerprint(state)
        with closing(database.get_connection(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if clinic_fingerprint(database.fetch_clinic_state(schedule_date, self.db_path)) != fingerprint:
                return False
            conn.execute("DELETE FROM generated_schedules WHERE schedule_date = ?", (schedule_date,))
            for row in result["schedule_df"].to_dict("records"):
                conn.execute("""INSERT INTO generated_schedules
                    (schedule_date, algorithm, booking_id, patient_name, patient_gender, therapy_code,
                     therapist_id, therapist_name, room_id, room_name, start_slot, end_slot,
                     time_window, status_flags, total_penalty) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (schedule_date, result["algorithm"], row["Booking_ID"], row["Patient_Name"], row["Gender"], row["Therapy_Code"],
                     row["Therapist_ID"], row["Therapist"], row["Room_ID"], row["Room"], row["Start_Slot"], row["End_Slot"],
                     row["Time_Window"], row["Status"], result["evaluation"]["total_penalty"]))
            conn.execute("INSERT OR REPLACE INTO clinic_metadata (key, value) VALUES (?, ?)", (f"schedule_fingerprint:{schedule_date}", fingerprint))
        return True

    def patient_appointments(self):
        user = auth.require_user(self.token, "patient", self.db_path)
        if not user["patient_id"]:
            return []
        with closing(database.get_connection(self.db_path)) as conn:
            bookings = [dict(row) for row in conn.execute("""SELECT b.id, b.schedule_date, t.name AS therapy_name,
                t.duration_slots FROM bookings b JOIN therapies t ON b.therapy_code = t.code
                WHERE b.patient_id = ? AND b.patient_name = ? COLLATE NOCASE AND b.patient_gender = ?
                ORDER BY b.schedule_date, b.id""", (user["patient_id"], user["linked_patient_name"], user["patient_gender"]))]
            valid_dates = set()
            for schedule_date in {row["schedule_date"] for row in bookings}:
                saved = conn.execute("SELECT value FROM clinic_metadata WHERE key = ?", (f"schedule_fingerprint:{schedule_date}",)).fetchone()
                if saved and saved["value"] == clinic_fingerprint(database.fetch_clinic_state(schedule_date, self.db_path)):
                    valid_dates.add(schedule_date)
            result = []
            for booking in bookings:
                saved = conn.execute("SELECT * FROM generated_schedules WHERE booking_id = ? AND schedule_date = ?", (booking["id"], booking["schedule_date"])).fetchone() if booking["schedule_date"] in valid_dates else None
                result.append({
                    "Date": booking["schedule_date"], "Therapy": booking["therapy_name"],
                    "Duration (minutes)": booking["duration_slots"] * 30,
                    "Start": TIME_SLOTS[saved["start_slot"]] if saved else "Pending",
                    "Finish": TIME_SLOTS[saved["end_slot"]] if saved else "Pending",
                    "Doctor / Therapist": saved["therapist_name"] if saved else "Pending",
                    "Room": saved["room_name"] if saved else "Pending",
                    "Status": "Scheduled" if saved else "Awaiting hospital schedule",
                })
            return result
