"""Hospital workbook templates, validation, backup, and atomic full-clinic imports."""

from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
import math
import re
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import database
from .optimizer import LUNCH_SLOTS

DB_PATH = database.DB_PATH


COLUMNS = {
    "Equipment": ["equipment_name"],
    "Therapies": ["therapy_code", "therapy_name", "category", "duration_minutes", "cleanup_minutes", "optimal_dosha", "required_equipment", "follow_up_code"],
    "Doctors": ["doctor_name", "gender", "qualified_therapies", "shift_start", "shift_end", "max_daily_minutes", "active"],
    "Rooms": ["room_name", "available_equipment", "active"],
    "Bookings": ["booking_id", "schedule_date", "patient_id", "patient_name", "patient_gender", "therapy_code", "priority", "preferred_dosha", "depends_on_booking_id"],
}
OPTIONAL = {
    "Equipment": set(), "Therapies": {"follow_up_code"}, "Doctors": {"active"},
    "Rooms": {"active"}, "Bookings": {"priority", "preferred_dosha", "depends_on_booking_id"},
}
GUIDANCE = {
    "equipment_name": ("Unique equipment name. Use the same name in Therapies and Rooms.", "Massage_Table"),
    "therapy_code": ("Unique therapy code; letters, numbers and underscores. Reuse it in Doctors and Bookings.", "ABHYANGA"),
    "therapy_name": ("Full treatment name.", "Full Body Oil Massage"),
    "category": ("Treatment category.", "Purvakarma"),
    "duration_minutes": ("Treatment duration in multiples of 30 minutes, from 30 to 300.", "60"),
    "cleanup_minutes": ("Cleanup time in multiples of 30 minutes, including 0. Treatment plus cleanup cannot exceed 600.", "30"),
    "optimal_dosha": ("One of Vata, Pitta, Kapha.", "Vata"),
    "required_equipment": ("One equipment name from the Equipment sheet.", "Massage_Table"),
    "follow_up_code": ("Optional therapy code from Therapies; describes a follow-up. Create its booking separately to schedule it.", "SWEDANA"),
    "doctor_name": ("Doctor / therapist full name. Each row represents one practitioner.", "Dr. Ananya Rao"),
    "gender": ("M or F; used by the patient-therapist gender match setting.", "F"),
    "qualified_therapies": ("Comma-separated therapy codes from Therapies.", "ABHYANGA,SWEDANA"),
    "shift_start": ("24-hour HH:MM, on a 30-minute boundary, between 08:00 and 17:30.", "08:00"),
    "shift_end": ("24-hour HH:MM, on a 30-minute boundary, after shift start and no later than 18:00.", "18:00"),
    "max_daily_minutes": ("Maximum treatment workload in multiples of 30 minutes; must fit within the shift.", "360"),
    "active": ("Optional: 1 for available, 0 for unavailable. Blank defaults to 1.", "1"),
    "room_name": ("Unique name for each available treatment room / chamber.", "Treatment Room 101"),
    "available_equipment": ("Comma-separated equipment names from Equipment. These items are available in this room.", "Massage_Table,Steam_Chamber"),
    "booking_id": ("Unique booking reference within this workbook. Use it to link follow-up bookings.", "B001"),
    "schedule_date": ("YYYY-MM-DD or an Excel date. Multiple dates are supported.", "2026-11-18"),
    "patient_id": ("Hospital patient ID; format as Text to preserve leading zeros. Reuse for the same patient.", "PT-001"),
    "patient_name": ("Patient full name; keep the name and gender consistent for the same patient ID.", "Meera Rao"),
    "patient_gender": ("M or F.", "F"),
    "priority": ("Optional whole number from 1 to 5; blank defaults to 2.", "2"),
    "preferred_dosha": ("Optional Vata, Pitta, Kapha; blank uses the therapy's optimal dosha.", "Vata"),
    "depends_on_booking_id": ("Optional parent booking_id, e.g. B001 for a Swedana booking after B001. Must be the same patient and date.", "B001"),
}


class WorkbookValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors[:50]
        if len(errors) > 50:
            self.errors.append(f"{len(errors) - 50} more issue(s). Correct these rows and upload again.")
        super().__init__("\n".join(self.errors))


@dataclass
class ClinicWorkbook:
    equipment: list
    therapies: list
    doctors: list
    rooms: list
    bookings: list

    @property
    def dates(self):
        return sorted({row["schedule_date"] for row in self.bookings})

    @property
    def counts(self):
        return {"Equipment": len(self.equipment), "Therapies": len(self.therapies),
                "Doctors": len(self.doctors), "Rooms": len(self.rooms), "Bookings": len(self.bookings)}


def workbook_preview_rows(data):
    """Show reviewed values using the same headings and units as the upload."""
    def clock(slot):
        return f"{8 + slot // 2:02d}:{slot % 2 * 30:02d}"
    rows = {
        "Equipment": [[name] for name in data.equipment],
        "Therapies": [[r["code"], r["name"], r["category"], r["duration_slots"] * 30,
                       r["prep_slots"] * 30, r["optimal_dosha"], r["required_equipment"], r["follow_up_code"]] for r in data.therapies],
        "Doctors": [[r["name"], r["gender"], r["skills"], clock(r["shift_start_slot"]),
                     clock(r["shift_end_slot"]), r["max_daily_slots"] * 30, r["active"]] for r in data.doctors],
        "Rooms": [[r["name"], r["supported_equipment"], r["active"]] for r in data.rooms],
        "Bookings": [[r["external_id"], r["schedule_date"], r["patient_id"], r["patient_name"], r["patient_gender"],
                      r["therapy_code"], r["priority"], r["preferred_dosha"], r["parent_external_id"]] for r in data.bookings],
    }
    return {name: [dict(zip(COLUMNS[name], row)) for row in records] for name, records in rows.items()}


def _write_workbook(rows_by_sheet):
    workbook = Workbook()
    instructions = workbook.active
    instructions.title = "Instructions"
    for row in [
        ["AyurSchedule Hospital Data Workbook"],
        ["Fill the five data sheets below. Keep the sheet names and column headings."],
        ["Equipment, Therapies, Doctors, Rooms and Bookings each need at least one data row. Blank rows are ignored."],
        ["Enter values only, without formulas. Examples appear here and in header comments; the blank template contains no patient data."],
        ["Clinic hours: 08:00-18:00. Lunch/sanitization: 13:00-14:00. Time and duration values use 30-minute boundaries."],
        ["Create one Bookings row for every treatment. A follow-up needs its own row linked through depends_on_booking_id."],
        ["Upload to Excel Import / Export, review the validation and preview, then click Import Workbook and Replace Clinic Data."],
        ["Import replaces current equipment, therapies, doctors, rooms, bookings and generated schedules. Download the current clinic backup first if needed."],
        ["After import, the first booking date is selected. Open Live Schedule & Gantt Chart to generate the schedule."],
        [], ["Sheet", "Column", "Required", "How to fill", "Example"],
    ]:
        instructions.append(row)
    for sheet_name, columns in COLUMNS.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(columns)
        for index, column in enumerate(columns, start=1):
            guidance, example = GUIDANCE[column]
            required = column not in OPTIONAL[sheet_name]
            sheet.cell(1, index).comment = Comment(f"{'Required' if required else 'Optional'}. {guidance}\nExample: {example}", "AyurSchedule")
            sheet.column_dimensions[get_column_letter(index)].width = min(40, max(22, len(column) + 4))
            instructions.append([sheet_name, column, "Yes" if required else "No", guidance, example])
            # Text formatting preserves patient IDs and codes with leading zeros.
            for row_number in range(2, 22):
                sheet.cell(row_number, index).number_format = "@"
            choices = {"gender": "M,F", "patient_gender": "M,F", "optimal_dosha": "Vata,Pitta,Kapha", "preferred_dosha": "Vata,Pitta,Kapha", "active": "1,0"}.get(column)
            if choices:
                validation = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=not required)
                validation.errorTitle = "Invalid value"
                validation.error = f"Choose one of: {choices}"
                validation.showErrorMessage = True
                sheet.add_data_validation(validation)
                validation.add(f"{get_column_letter(index)}2:{get_column_letter(index)}10001")
        for row_number, values in enumerate(rows_by_sheet.get(sheet_name, []), start=2):
            for column_number, value in enumerate(values, start=1):
                cell = sheet.cell(row_number, column_number, value)
                if isinstance(value, str):
                    cell.data_type = "s"  # Export literal text, including names starting with '='.
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(1, len(rows_by_sheet.get(sheet_name, [])) + 1)}"
    for sheet in workbook:
        header_row = 11 if sheet.title == "Instructions" else 1
        for cell in sheet[header_row]:
            cell.fill = PatternFill("solid", fgColor="1B5E20")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(wrap_text=True)
        sheet.row_dimensions[header_row].height = 32
    for column, width in {"A": 22, "B": 28, "C": 12, "D": 95, "E": 38}.items():
        instructions.column_dimensions[column].width = width
    for row_number in range(1, 10):
        instructions.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=5)
        instructions.row_dimensions[row_number].height = 32
    instructions["A1"].font = Font(size=18, bold=True, color="1B5E20")
    for row in instructions:
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    instructions.freeze_panes = "A12"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_hospital_template():
    return _write_workbook({})


def _text(value, label, optional=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return ""
        raise ValueError(f"{label} is required.")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label} must be text or an ID number.")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _integer(value, label, minimum, maximum, default=None, multiple=1):
    if value is None or value == "":
        value = default
    try:
        number = float(value)
        valid = not isinstance(value, bool) and math.isfinite(number) and number.is_integer()
    except (ValueError, TypeError):
        valid = False
    if not valid or not minimum <= number <= maximum or number % multiple:
        raise ValueError(f"{label} must be a whole number from {minimum} to {maximum}" + (f", in multiples of {multiple}." if multiple != 1 else "."))
    return int(number)


def _active(value):
    text = str(value).strip().lower() if value is not None else ""
    if text in {"", "1", "1.0", "true", "yes"}:
        return 1
    if text in {"0", "0.0", "false", "no"}:
        return 0
    raise ValueError("active must be 1 (available) or 0 (unavailable).")


def _dosha(value, default=None):
    value = _text(value, "dosha", optional=default is not None).title() or default
    if value not in {"Vata", "Pitta", "Kapha"}:
        raise ValueError("dosha must be Vata, Pitta, or Kapha.")
    return value


def _gender(value):
    value = _text(value, "gender").upper()
    if value not in {"M", "F"}:
        raise ValueError("gender must be M or F.")
    return value


def _slot(value, label):
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, str):
        try:
            value = datetime.strptime(value.strip(), "%H:%M").time()
        except ValueError:
            pass
    if not isinstance(value, time) or value.second or value.microsecond or value.minute not in {0, 30}:
        raise ValueError(f"{label} must be HH:MM on a 30-minute boundary.")
    slot = (value.hour - 8) * 2 + value.minute // 30
    if not 0 <= slot <= 20:
        raise ValueError(f"{label} must be between 08:00 and 18:00.")
    return slot


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        text = str(value).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError("schedule_date must be a valid YYYY-MM-DD date or an Excel date.") from None


def _list(value):
    return list(dict.fromkeys(item.strip() for item in _text(value, "list", optional=True).split(",") if item.strip()))


def _ordered_bookings(bookings, errors):
    by_id = {row["external_id"]: row for row in bookings}
    children = {key: [] for key in by_id}
    degrees = {key: 0 for key in by_id}
    for row in bookings:
        parent_id = row["parent_external_id"]
        if parent_id:
            parent = by_id.get(parent_id)
            prefix = f"Bookings, row {row['_row']}: "
            if parent is None:
                errors.append(prefix + f"depends_on_booking_id '{parent_id}' does not exist in Bookings.")
                continue
            if (parent["patient_id"], parent["schedule_date"]) != (row["patient_id"], row["schedule_date"]):
                errors.append(prefix + "follow-ups must have the same patient_id and schedule_date as their parent.")
            children[parent_id].append(row["external_id"])
            degrees[row["external_id"]] += 1
    pending = deque(key for key, degree in degrees.items() if degree == 0)
    result = []
    while pending:
        key = pending.popleft()
        result.append(by_id[key])
        for child in children[key]:
            degrees[child] -= 1
            if degrees[child] == 0:
                pending.append(child)
    if len(result) != len(bookings):
        errors.append("Bookings: follow-up links contain a cycle or a booking depends on itself.")
    return result


def parse_hospital_workbook(content: bytes, enforce_gender_match=True):
    errors, raw = [], {}
    if not content or len(content) > 10 * 1024 * 1024:
        raise WorkbookValidationError(["Upload a nonempty .xlsx file no larger than 10 MB."])
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise WorkbookValidationError(["This workbook is too large when expanded. Use a smaller file."])
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except WorkbookValidationError:
        raise
    except Exception as exc:
        raise WorkbookValidationError(["Cannot read this Excel file. Download the .xlsx template and upload a filled copy."]) from exc
    try:
        sheets = {name.strip().casefold(): name for name in workbook.sheetnames}
        for name, columns in COLUMNS.items():
            actual = sheets.get(name.casefold())
            if actual is None:
                errors.append(f"Missing sheet: {name}.")
                raw[name] = []
                continue
            sheet = workbook[actual]
            if sheet.max_row > 10001 or sheet.max_column > 100:
                errors.append(f"{name}: use at most 10,000 data rows and 100 columns.")
                raw[name] = []
                continue
            rows = sheet.iter_rows()
            headers = [str(cell.value).strip().lower().replace(" ", "_") if cell.value is not None else "" for cell in next(rows, [])]
            missing = set(columns) - OPTIONAL[name] - set(headers)
            if missing:
                errors.append(f"{name}, row 1: missing column(s): {', '.join(sorted(missing))}.")
            if len([header for header in headers if header]) != len({header for header in headers if header}):
                errors.append(f"{name}, row 1: duplicate column headings.")
            records = []
            for row_number, cells in enumerate(rows, start=2):
                record = {header: cell.value for header, cell in zip(headers, cells) if header in columns}
                if all(value is None or value == "" for value in record.values()):
                    continue
                if any(cell.data_type == "f" for cell in cells):
                    errors.append(f"{name}, row {row_number}: formulas are not supported; paste values instead.")
                    continue
                record["_row"] = row_number
                records.append(record)
            raw[name] = records
            if not records:
                errors.append(f"{name}: add at least one data row below the headings.")
    finally:
        workbook.close()

    data = ClinicWorkbook([], [], [], [], [])
    equipment_lookup, therapies, booking_ids, patients, room_names = {}, {}, set(), {}, set()
    for row in raw["Equipment"]:
        try:
            name = _text(row.get("equipment_name"), "equipment_name")
            if "," in name or name.casefold() in equipment_lookup:
                raise ValueError("equipment_name must be unique and cannot contain a comma.")
            equipment_lookup[name.casefold()] = name
            data.equipment.append(name)
        except ValueError as exc:
            errors.append(f"Equipment, row {row['_row']}: {exc}")

    def equipment_name(value):
        value = _text(value, "equipment name")
        if value.casefold() not in equipment_lookup:
            raise ValueError(f"equipment '{value}' is missing from Equipment.")
        return equipment_lookup[value.casefold()]

    for row in raw["Therapies"]:
        try:
            code = _text(row.get("therapy_code"), "therapy_code").upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", code) or code in therapies:
                raise ValueError("therapy_code must be unique, start with a letter, and contain only letters, numbers and underscores.")
            duration = _integer(row.get("duration_minutes"), "duration_minutes", 30, 300, multiple=30)
            cleanup = _integer(row.get("cleanup_minutes"), "cleanup_minutes", 0, 600, multiple=30)
            if duration + cleanup > 600:
                raise ValueError("duration_minutes plus cleanup_minutes cannot exceed 600.")
            therapy = dict(code=code, name=_text(row.get("therapy_name"), "therapy_name"), category=_text(row.get("category"), "category"),
                           duration_slots=duration // 30, prep_slots=cleanup // 30, optimal_dosha=_dosha(row.get("optimal_dosha")),
                           required_equipment=equipment_name(row.get("required_equipment")),
                           follow_up_code=_text(row.get("follow_up_code"), "follow_up_code", optional=True).upper() or None, _row=row["_row"])
            therapies[code] = therapy
            data.therapies.append(therapy)
        except ValueError as exc:
            errors.append(f"Therapies, row {row['_row']}: {exc}")
    for therapy in data.therapies:
        if therapy["follow_up_code"] and therapy["follow_up_code"] not in therapies:
            errors.append(f"Therapies, row {therapy['_row']}: follow_up_code '{therapy['follow_up_code']}' is not registered in Therapies.")

    for row in raw["Doctors"]:
        try:
            skills = sorted({code.upper() for code in _list(row.get("qualified_therapies"))})
            if not skills or not set(skills) <= therapies.keys():
                raise ValueError("qualified_therapies must contain registered therapy codes, separated by commas.")
            start, end = _slot(row.get("shift_start"), "shift_start"), _slot(row.get("shift_end"), "shift_end")
            if start >= end:
                raise ValueError("shift_end must be after shift_start.")
            workload = _integer(row.get("max_daily_minutes"), "max_daily_minutes", 30, (end - start) * 30, multiple=30)
            data.doctors.append(dict(name=_text(row.get("doctor_name"), "doctor_name"), gender=_gender(row.get("gender")), skills=",".join(skills),
                                     shift_start_slot=start, shift_end_slot=end, max_daily_slots=workload // 30, active=_active(row.get("active")), _row=row["_row"]))
        except ValueError as exc:
            errors.append(f"Doctors, row {row['_row']}: {exc}")
    for row in raw["Rooms"]:
        try:
            name, active = _text(row.get("room_name"), "room_name"), _active(row.get("active"))
            if active and name.casefold() in room_names:
                raise ValueError("active room_name must be unique.")
            equipment = [equipment_name(value) for value in _list(row.get("available_equipment"))]
            data.rooms.append(dict(name=name, supported_equipment=",".join(equipment), active=active, _row=row["_row"]))
            if active:
                room_names.add(name.casefold())
        except ValueError as exc:
            errors.append(f"Rooms, row {row['_row']}: {exc}")
    for row in raw["Bookings"]:
        try:
            booking_id = _text(row.get("booking_id"), "booking_id")
            if booking_id in booking_ids:
                raise ValueError(f"booking_id '{booking_id}' is duplicated.")
            code = _text(row.get("therapy_code"), "therapy_code").upper()
            if code not in therapies:
                raise ValueError(f"therapy_code '{code}' is missing from Therapies.")
            patient_id = _text(row.get("patient_id"), "patient_id")
            name, gender = _text(row.get("patient_name"), "patient_name"), _gender(row.get("patient_gender"))
            if patient_id in patients and patients[patient_id] != (name, gender):
                raise ValueError(f"patient_id '{patient_id}' has inconsistent patient_name or patient_gender.")
            booking = dict(external_id=booking_id, schedule_date=_date(row.get("schedule_date")), patient_id=patient_id, patient_name=name,
                           patient_gender=gender, therapy_code=code, priority=_integer(row.get("priority"), "priority", 1, 5, default=2),
                           preferred_dosha=_dosha(row.get("preferred_dosha"), default=therapies[code]["optimal_dosha"]),
                           parent_external_id=_text(row.get("depends_on_booking_id"), "depends_on_booking_id", optional=True) or None, _row=row["_row"])
            data.bookings.append(booking)
            booking_ids.add(booking_id)
            patients[patient_id] = (name, gender)
        except ValueError as exc:
            errors.append(f"Bookings, row {row['_row']}: {exc}")
    data.bookings = _ordered_bookings(data.bookings, errors)
    for booking in data.bookings:
        therapy = therapies[booking["therapy_code"]]
        if not any(room["active"] and therapy["required_equipment"] in room["supported_equipment"].split(",") for room in data.rooms):
            errors.append(f"Bookings, row {booking['_row']}: no active room has '{therapy['required_equipment']}'.")
        duration = therapy["duration_slots"]
        if not any(
            doctor["active"] and booking["therapy_code"] in doctor["skills"].split(",")
            and (not enforce_gender_match or doctor["gender"] == booking["patient_gender"])
            and doctor["max_daily_slots"] >= duration
            and any(not set(range(start, start + duration)) & LUNCH_SLOTS
                    for start in range(doctor["shift_start_slot"], doctor["shift_end_slot"] - duration + 1))
            for doctor in data.doctors
        ):
            errors.append(f"Bookings, row {booking['_row']}: no active qualified doctor with a suitable shift, workload and gender match is available for '{booking['therapy_code']}'.")
    if errors:
        raise WorkbookValidationError(errors)
    return data


def import_hospital_workbook(content: bytes, db_path: str = DB_PATH, enforce_gender_match=True):
    """Validate first, then replace the entire clinic in a single transaction."""
    data = parse_hospital_workbook(content, enforce_gender_match=enforce_gender_match)
    database.init_db(db_path, seed_default=False)
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("generated_schedules", "bookings", "therapists", "rooms", "therapies", "equipment"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM clinic_metadata WHERE key LIKE 'schedule_fingerprint:%'")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone():
            # Patient IDs may belong to different people in a replacement dataset.
            # Keep accounts, but require a new hospital-issued link to medical records.
            conn.execute("UPDATE users SET patient_id = NULL, linked_patient_name = NULL WHERE role = 'patient'")
            conn.execute("DELETE FROM auth_invites WHERE role = 'patient' AND used_by IS NULL")
        conn.executemany("INSERT INTO equipment (name) VALUES (?)", [(name,) for name in data.equipment])
        conn.executemany(
            "INSERT INTO therapies (code, name, category, duration_slots, prep_slots, optimal_dosha, required_equipment, follow_up_code) VALUES (:code, :name, :category, :duration_slots, :prep_slots, :optimal_dosha, :required_equipment, :follow_up_code)", data.therapies
        )
        conn.executemany(
            "INSERT INTO therapists (name, gender, skills, shift_start_slot, shift_end_slot, max_daily_slots, active) VALUES (:name, :gender, :skills, :shift_start_slot, :shift_end_slot, :max_daily_slots, :active)", data.doctors
        )
        conn.executemany("INSERT INTO rooms (name, supported_equipment, active) VALUES (:name, :supported_equipment, :active)", data.rooms)
        booking_ids = {}
        for booking in data.bookings:
            cur = conn.execute(
                "INSERT INTO bookings (schedule_date, patient_id, patient_name, patient_gender, therapy_code, priority, preferred_dosha, depends_on_booking_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (booking["schedule_date"], booking["patient_id"], booking["patient_name"], booking["patient_gender"], booking["therapy_code"], booking["priority"], booking["preferred_dosha"], booking_ids.get(booking["parent_external_id"]))
            )
            booking_ids[booking["external_id"]] = cur.lastrowid
        conn.execute("INSERT OR REPLACE INTO clinic_metadata (key, value) VALUES ('initialized', '1')")
    return data


def export_clinic_workbook(db_path: str = DB_PATH):
    """Export all dates and inactive practitioners/rooms as an editable backup."""
    with closing(database.get_connection(db_path)) as conn:
        equipment = [[row["name"]] for row in conn.execute("SELECT name FROM equipment ORDER BY name")]
        therapies = [[r["code"], r["name"], r["category"], r["duration_slots"] * 30, r["prep_slots"] * 30,
                      r["optimal_dosha"], r["required_equipment"], r["follow_up_code"]] for r in conn.execute("SELECT * FROM therapies ORDER BY code")]
        doctors = [[r["name"], r["gender"], r["skills"], f"{8 + r['shift_start_slot'] // 2:02d}:{r['shift_start_slot'] % 2 * 30:02d}",
                    f"{8 + r['shift_end_slot'] // 2:02d}:{r['shift_end_slot'] % 2 * 30:02d}", r["max_daily_slots"] * 30, r["active"]]
                   for r in conn.execute("SELECT * FROM therapists ORDER BY id")]
        rooms = [[r["name"], r["supported_equipment"], r["active"]] for r in conn.execute("SELECT * FROM rooms ORDER BY id")]
        bookings = [[f"B{r['id']}", r["schedule_date"], r["patient_id"], r["patient_name"], r["patient_gender"], r["therapy_code"],
                     r["priority"], r["preferred_dosha"], f"B{r['depends_on_booking_id']}" if r["depends_on_booking_id"] is not None else None]
                    for r in conn.execute("SELECT * FROM bookings ORDER BY id")]
    return _write_workbook(dict(Equipment=equipment, Therapies=therapies, Doctors=doctors, Rooms=rooms, Bookings=bookings))
