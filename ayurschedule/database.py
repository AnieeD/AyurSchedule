import sqlite3
import os
import re
from contextlib import closing
from typing import List, Dict, Any

DB_PATH = os.environ.get("AYUR_DB_PATH", "ayurschedule_clinic.db")

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = DB_PATH, seed_default: bool = True):
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS therapies (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        duration_slots INTEGER NOT NULL,
        prep_slots INTEGER NOT NULL,
        optimal_dosha TEXT NOT NULL,
        required_equipment TEXT NOT NULL,
        follow_up_code TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS therapists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        gender TEXT NOT NULL,
        skills TEXT NOT NULL,
        shift_start_slot INTEGER NOT NULL,
        shift_end_slot INTEGER NOT NULL,
        max_daily_slots INTEGER NOT NULL,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        supported_equipment TEXT NOT NULL,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_date TEXT NOT NULL,
        patient_id TEXT NOT NULL,
        patient_name TEXT NOT NULL,
        patient_gender TEXT NOT NULL,
        therapy_code TEXT NOT NULL,
        priority INTEGER DEFAULT 1,
        preferred_dosha TEXT NOT NULL,
        depends_on_booking_id INTEGER,
        FOREIGN KEY (therapy_code) REFERENCES therapies(code)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS generated_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_date TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        booking_id INTEGER NOT NULL,
        patient_name TEXT NOT NULL,
        patient_gender TEXT NOT NULL,
        therapy_code TEXT NOT NULL,
        therapist_id INTEGER NOT NULL,
        therapist_name TEXT NOT NULL,
        room_id INTEGER NOT NULL,
        room_name TEXT NOT NULL,
        start_slot INTEGER NOT NULL,
        end_slot INTEGER NOT NULL,
        time_window TEXT NOT NULL,
        status_flags TEXT NOT NULL,
        total_penalty REAL NOT NULL
    )
    """)
    conn.commit()

    # Seed only once; removing the last resource must not restore demo records.
    cur.execute("CREATE TABLE IF NOT EXISTS clinic_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    cur.execute("CREATE TABLE IF NOT EXISTS equipment (name TEXT PRIMARY KEY)")
    if seed_default and not cur.execute("SELECT 1 FROM clinic_metadata WHERE key = 'initialized'").fetchone():
        existing_data = any(cur.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                            for table in ("therapies", "therapists", "rooms", "bookings", "generated_schedules"))
        if not existing_data:
            seed_clinic_data(conn)
        cur.execute("INSERT INTO clinic_metadata (key, value) VALUES ('initialized', '1')")
    equipment_names = {row[0] for row in cur.execute("SELECT required_equipment FROM therapies")}
    for row in cur.execute("SELECT supported_equipment FROM rooms"):
        equipment_names.update(item.strip() for item in row[0].split(",") if item.strip())
    cur.executemany("INSERT OR IGNORE INTO equipment (name) VALUES (?)", [(name,) for name in equipment_names])
    conn.commit()
    conn.close()

def seed_clinic_data(conn: sqlite3.Connection):
    cur = conn.cursor()
    # 30-min slots: 0 = 08:00-08:30 ... 19 = 17:30-18:00 (20 slots total)
    therapies = [
        ("ABHYANGA", "Abhyanga (Full Body Oil Massage)", "Purvakarma", 2, 1, "Vata", "Massage_Table", "SWEDANA"),
        ("SWEDANA", "Bashpa Swedana (Herbal Steam)", "Purvakarma", 1, 1, "Vata", "Steam_Chamber", None),
        ("SHIRODHARA", "Shirodhara (Forehead Oil Flow)", "Pradhanakarma", 2, 1, "Pitta", "Dhara_Stand", None),
        ("UDWARTANA", "Udwartana (Dry Herbal Powder Massage)", "Pradhanakarma", 2, 1, "Kapha", "Massage_Table", "SWEDANA"),
        ("PIZHICHIL", "Pizhichil (Warm Medicated Oil Bath)", "Pradhanakarma", 3, 1, "Vata", "Dhara_Stand", None),
        ("BASTI", "Matra / Niruha Basti (Enema Therapy)", "Pradhanakarma", 2, 1, "Vata", "Basti_Unit", None),
        ("NASYA", "Nasya (Nasal Administration)", "Pradhanakarma", 1, 1, "Kapha", "Massage_Table", None),
        ("TAKRADHARA", "Takradhara (Medicated Buttermilk Flow)", "Pradhanakarma", 2, 1, "Pitta", "Dhara_Stand", None)
    ]
    cur.executemany("INSERT OR IGNORE INTO therapies VALUES (?,?,?,?,?,?,?,?)", therapies)

    therapists = [
        ("Ramesh Nair", "M", "ABHYANGA,SWEDANA,SHIRODHARA,BASTI,PIZHICHIL", 0, 18, 12, 1),
        ("Suresh Menon", "M", "ABHYANGA,SWEDANA,UDWARTANA,PIZHICHIL,NASYA", 0, 18, 12, 1),
        ("Vivek Pillai", "M", "ABHYANGA,SWEDANA,BASTI,SHIRODHARA,TAKRADHARA", 2, 20, 12, 1),
        ("Anita Sharma", "F", "ABHYANGA,SWEDANA,SHIRODHARA,UDWARTANA,NASYA", 0, 18, 12, 1),
        ("Kavita Krishnan", "F", "ABHYANGA,SWEDANA,BASTI,PIZHICHIL,SHIRODHARA,TAKRADHARA", 0, 18, 12, 1),
        ("Meenakshi Iyer", "F", "ABHYANGA,SWEDANA,UDWARTANA,BASTI,TAKRADHARA", 2, 20, 12, 1)
    ]
    cur.executemany(
        "INSERT INTO therapists (name, gender, skills, shift_start_slot, shift_end_slot, max_daily_slots, active) VALUES (?,?,?,?,?,?,?)",
        therapists
    )

    rooms = [
        ("Dhara Suite 101", "Dhara_Stand,Massage_Table", 1),
        ("Dhara Suite 102", "Dhara_Stand,Massage_Table", 1),
        ("Swedana Chamber 201", "Steam_Chamber,Massage_Table", 1),
        ("Swedana Chamber 202", "Steam_Chamber,Massage_Table", 1),
        ("Panchakarma Basti Room 301", "Basti_Unit,Massage_Table,Steam_Chamber", 1),
    ]
    cur.executemany(
        "INSERT INTO rooms (name, supported_equipment, active) VALUES (?,?,?)",
        rooms
    )

    # Seed realistic daily patient bookings for '2026-11-18'
    bookings = [
        # Sequential Purvakarma chains (Abhyanga -> Swedana)
        (1, "2026-11-18", "PT-101", "Rajesh Verma", "M", "ABHYANGA", 2, "Vata", None),
        (2, "2026-11-18", "PT-101", "Rajesh Verma", "M", "SWEDANA", 2, "Vata", 1),
        (3, "2026-11-18", "PT-102", "Meera Deshmukh", "F", "SHIRODHARA", 1, "Pitta", None),
        (4, "2026-11-18", "PT-103", "Amitabh Patel", "M", "UDWARTANA", 1, "Kapha", None),
        (5, "2026-11-18", "PT-103", "Amitabh Patel", "M", "SWEDANA", 1, "Kapha", 4),
        (6, "2026-11-18", "PT-104", "Priya Nair", "F", "ABHYANGA", 2, "Vata", None),
        (7, "2026-11-18", "PT-104", "Priya Nair", "F", "SWEDANA", 2, "Vata", 6),
        (8, "2026-11-18", "PT-105", "Vikramaditya Rao", "M", "PIZHICHIL", 3, "Vata", None),
        (9, "2026-11-18", "PT-106", "Sonal Kulkarni", "F", "UDWARTANA", 1, "Kapha", None),
        (10, "2026-11-18", "PT-107", "Arjun Singhania", "M", "BASTI", 2, "Vata", None),
        (11, "2026-11-18", "PT-108", "Divya Menon", "F", "TAKRADHARA", 2, "Pitta", None),
        (12, "2026-11-18", "PT-109", "Karan Malhotra", "M", "NASYA", 1, "Kapha", None),
        (13, "2026-11-18", "PT-110", "Neha Gupta", "F", "BASTI", 3, "Vata", None),
        (14, "2026-11-18", "PT-111", "Sanjay Joshi", "M", "SHIRODHARA", 1, "Pitta", None),
        (15, "2026-11-18", "PT-112", "Anjali Verma", "F", "PIZHICHIL", 2, "Vata", None),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO bookings (id, schedule_date, patient_id, patient_name, patient_gender, therapy_code, priority, preferred_dosha, depends_on_booking_id) VALUES (?,?,?,?,?,?,?,?,?)",
        bookings
    )
    conn.commit()

def fetch_clinic_state(schedule_date: str = "2026-11-18", db_path: str = DB_PATH) -> Dict[str, Any]:
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT * FROM therapies")
    therapies = {row["code"]: dict(row) for row in cur.fetchall()}

    cur.execute("SELECT * FROM therapists WHERE active = 1")
    therapists = []
    for row in cur.fetchall():
        d = dict(row)
        d["skills_set"] = set(x.strip() for x in d["skills"].split(",") if x.strip())
        therapists.append(d)

    cur.execute("SELECT * FROM rooms WHERE active = 1")
    rooms = []
    for row in cur.fetchall():
        d = dict(row)
        d["equipment_set"] = set(x.strip() for x in d["supported_equipment"].split(",") if x.strip())
        rooms.append(d)

    cur.execute("SELECT * FROM bookings WHERE schedule_date = ? ORDER BY id ASC", (schedule_date,))
    bookings = [dict(row) for row in cur.fetchall()]

    equipment = [row[0] for row in cur.execute("SELECT name FROM equipment ORDER BY name")]
    conn.close()
    return {
        "equipment": equipment,
        "therapies": therapies,
        "therapists": therapists,
        "rooms": rooms,
        "bookings": bookings,
        "schedule_date": schedule_date
    }


def add_therapist(
    name: str, gender: str, skills: List[str], shift_start_slot: int,
    shift_end_slot: int, max_daily_slots: int, db_path: str = DB_PATH
) -> int:
    """Register a doctor/therapist as an active scheduling resource."""
    name = name.strip()
    skills = sorted(set(skills))
    if not name:
        raise ValueError("Enter the doctor / therapist's full name.")
    if gender not in {"M", "F"}:
        raise ValueError("Select a valid gender.")
    if not skills:
        raise ValueError("Select at least one qualified therapy.")
    if not 0 <= shift_start_slot < shift_end_slot <= 20:
        raise ValueError("Shift end must be after shift start, between 08:00 and 18:00.")
    if not 1 <= max_daily_slots <= shift_end_slot - shift_start_slot:
        raise ValueError("Maximum daily workload must fit within the selected shift.")

    with closing(get_connection(db_path)) as conn, conn:
        known_codes = {row["code"] for row in conn.execute("SELECT code FROM therapies")}
        if not set(skills) <= known_codes:
            raise ValueError("Select only registered therapies.")
        cur = conn.execute(
            """INSERT INTO therapists
               (name, gender, skills, shift_start_slot, shift_end_slot, max_daily_slots, active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (name, gender, ",".join(skills), shift_start_slot, shift_end_slot, max_daily_slots)
        )
        return cur.lastrowid


def remove_therapist(therapist_id: int, db_path: str = DB_PATH) -> bool:
    """Remove a departing practitioner from the active roster; retain past records."""
    with closing(get_connection(db_path)) as conn, conn:
        cur = conn.execute(
            "UPDATE therapists SET active = 0 WHERE id = ? AND active = 1",
            (therapist_id,)
        )
        return cur.rowcount > 0


def add_booking(
    schedule_date: str, patient_id: str, patient_name: str, patient_gender: str,
    therapy_code: str, preferred_dosha: str, include_swedana: bool = False,
    db_path: str = DB_PATH
) -> int:
    """Save a booking and its optional follow-up in one transaction."""
    patient_id, patient_name = patient_id.strip(), patient_name.strip()
    if not patient_id or not patient_name:
        raise ValueError("Enter both a patient ID and full name.")
    if patient_gender not in {"M", "F"} or preferred_dosha not in {"Vata", "Pitta", "Kapha"}:
        raise ValueError("Select a valid gender and target dosha.")

    with closing(get_connection(db_path)) as conn, conn:
        required_codes = {therapy_code, "SWEDANA"} if include_swedana else {therapy_code}
        known_codes = {row["code"] for row in conn.execute("SELECT code FROM therapies")}
        if not required_codes <= known_codes:
            raise ValueError("Select only registered therapies.")
        insert_sql = """INSERT INTO bookings
            (schedule_date, patient_id, patient_name, patient_gender, therapy_code,
             priority, preferred_dosha, depends_on_booking_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        cur = conn.execute(insert_sql, (
            schedule_date, patient_id, patient_name, patient_gender, therapy_code,
            2, preferred_dosha, None
        ))
        parent_id = cur.lastrowid
        if include_swedana:
            conn.execute(insert_sql, (
                schedule_date, patient_id, patient_name, patient_gender, "SWEDANA",
                2, preferred_dosha, parent_id
            ))
        return parent_id


def remove_patient_bookings(
    patient_id: str, schedule_date: str, db_path: str = DB_PATH
) -> int:
    """Delete all of a patient's bookings and saved schedule rows for one date."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(
            """DELETE FROM generated_schedules WHERE booking_id IN (
                SELECT id FROM bookings WHERE patient_id = ? AND schedule_date = ?
            )""",
            (patient_id, schedule_date)
        )
        cur = conn.execute(
            "DELETE FROM bookings WHERE patient_id = ? AND schedule_date = ?",
            (patient_id, schedule_date)
        )
        return cur.rowcount


def _equipment_list(equipment: List[str], allow_empty: bool = False) -> List[str]:
    values = sorted({item.strip() for item in equipment if item.strip()})
    if (not values and not allow_empty) or any("," in item for item in values):
        raise ValueError("Select at least one equipment item; enter additional items separated by commas.")
    return values


def add_room(name: str, equipment: List[str], db_path: str = DB_PATH) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Enter the treatment room / chamber name.")
    equipment = _equipment_list(equipment)
    with closing(get_connection(db_path)) as conn, conn:
        if conn.execute("SELECT 1 FROM rooms WHERE name = ? COLLATE NOCASE AND active = 1", (name,)).fetchone():
            raise ValueError("An active room / chamber with this name already exists.")
        cur = conn.execute(
            "INSERT INTO rooms (name, supported_equipment, active) VALUES (?, ?, 1)",
            (name, ",".join(equipment))
        )
        conn.executemany("INSERT OR IGNORE INTO equipment (name) VALUES (?)", [(item,) for item in equipment])
        return cur.lastrowid


def update_room_equipment(room_id: int, equipment: List[str], db_path: str = DB_PATH):
    equipment = _equipment_list(equipment, allow_empty=True)
    with closing(get_connection(db_path)) as conn, conn:
        cur = conn.execute("UPDATE rooms SET supported_equipment = ? WHERE id = ? AND active = 1",
                           (",".join(equipment), room_id))
        if not cur.rowcount:
            raise ValueError("This room / chamber is no longer active.")
        conn.executemany("INSERT OR IGNORE INTO equipment (name) VALUES (?)", [(item,) for item in equipment])


def remove_room(room_id: int, db_path: str = DB_PATH) -> bool:
    """Exclude a room from scheduling while preserving historical references."""
    with closing(get_connection(db_path)) as conn, conn:
        return conn.execute("UPDATE rooms SET active = 0 WHERE id = ? AND active = 1", (room_id,)).rowcount > 0


def add_therapy(
    code: str, name: str, category: str, duration_slots: int, prep_slots: int,
    optimal_dosha: str, required_equipment: str, follow_up_code: str = None,
    therapist_ids: List[int] = None, db_path: str = DB_PATH
) -> str:
    code, name, category = code.strip().upper(), name.strip(), category.strip()
    required_equipment = required_equipment.strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", code):
        raise ValueError("Enter a therapy code using letters, numbers, and underscores, starting with a letter.")
    if not name or not category:
        raise ValueError("Enter the therapy name and category.")
    if not 1 <= duration_slots <= 20 or not 0 <= prep_slots <= 20 or duration_slots + prep_slots > 20:
        raise ValueError("Therapy and cleanup durations must fit within the 20-slot clinic day.")
    if optimal_dosha not in {"Vata", "Pitta", "Kapha"}:
        raise ValueError("Select a valid optimal dosha.")
    _equipment_list([required_equipment])
    with closing(get_connection(db_path)) as conn, conn:
        if conn.execute("SELECT 1 FROM therapies WHERE code = ?", (code,)).fetchone():
            raise ValueError("This therapy code is already registered.")
        if follow_up_code and not conn.execute("SELECT 1 FROM therapies WHERE code = ?", (follow_up_code,)).fetchone():
            raise ValueError("Select a registered follow-up therapy.")
        practitioners = {}
        for therapist_id in set(therapist_ids or []):
            row = conn.execute("SELECT skills FROM therapists WHERE id = ? AND active = 1", (therapist_id,)).fetchone()
            if row is None:
                raise ValueError("Select only active doctors / therapists.")
            practitioners[therapist_id] = sorted({s.strip() for s in row["skills"].split(",") if s.strip()} | {code})
        conn.execute("INSERT INTO therapies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (code, name, category, duration_slots, prep_slots, optimal_dosha, required_equipment, follow_up_code))
        conn.execute("INSERT OR IGNORE INTO equipment (name) VALUES (?)", (required_equipment,))
        for therapist_id, skills in practitioners.items():
            conn.execute("UPDATE therapists SET skills = ? WHERE id = ?", (",".join(skills), therapist_id))
    return code


def remove_therapy(code: str, db_path: str = DB_PATH) -> bool:
    """Remove an unused therapy without silently deleting patient bookings."""
    with closing(get_connection(db_path)) as conn, conn:
        if conn.execute("SELECT 1 FROM bookings WHERE therapy_code = ? LIMIT 1", (code,)).fetchone():
            raise ValueError("This therapy has patient bookings. Remove or rebook those bookings before removing the therapy.")
        if not conn.execute("SELECT 1 FROM therapies WHERE code = ?", (code,)).fetchone():
            return False
        conn.execute("UPDATE therapies SET follow_up_code = NULL WHERE follow_up_code = ?", (code,))
        for row in conn.execute("SELECT id, skills FROM therapists").fetchall():
            skills = [s.strip() for s in row["skills"].split(",") if s.strip() and s.strip() != code]
            conn.execute("UPDATE therapists SET skills = ? WHERE id = ?", (",".join(skills), row["id"]))
        conn.execute("DELETE FROM therapies WHERE code = ?", (code,))
        return True
