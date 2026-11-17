"""Local accounts, opaque server sessions, and single-use membership invitations."""

from contextlib import closing
import hashlib
import re
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from . import database

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
DUMMY_PASSWORD = PASSWORDS.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
INVITE_SECONDS = 72 * 60 * 60


class AccessDenied(PermissionError):
    pass


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _email(value):
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid email address.")
    return value


def initialize_auth(db_path=database.DB_PATH, setup_code=None):
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('hospital','patient')), active INTEGER NOT NULL DEFAULT 1,
                patient_id TEXT UNIQUE, linked_patient_name TEXT, patient_gender TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL, last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL, email TEXT, patient_id TEXT, created_by INTEGER,
                expires_at REAL NOT NULL, used_by INTEGER, bootstrap INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS auth_login_attempts (
                email_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL,
                window_start REAL NOT NULL, locked_until REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS auth_recovery_codes (
                user_id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE role = 'hospital' LIMIT 1").fetchone():
            return None
        if setup_code:
            if not isinstance(setup_code, str) or len(setup_code) < 32:
                raise ValueError("Hospital setup code must have at least 32 characters.")
            token_hash = _digest(setup_code)
            current = conn.execute("SELECT token_hash FROM auth_invites WHERE bootstrap = 1 AND used_by IS NULL").fetchone()
            if current and current["token_hash"] == token_hash:
                return None
            conn.execute("DELETE FROM auth_invites WHERE bootstrap = 1 AND used_by IS NULL")
            conn.execute("INSERT INTO auth_invites (token_hash, role, expires_at, bootstrap) VALUES (?, 'hospital', ?, 1)",
                         (token_hash, 253402300799.0))
            return None
        # The setup secret stays on the server, never on the public signup page.
        actual_db = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
        path = actual_db.parent / ".streamlit" / "hospital_setup_invite.txt"
        existing = conn.execute("SELECT 1 FROM auth_invites WHERE bootstrap = 1 AND used_by IS NULL AND expires_at > ?", (time.time(),)).fetchone()
        if existing and path.exists():
            return path
        code = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM auth_invites WHERE bootstrap = 1 AND used_by IS NULL")
        conn.execute("INSERT INTO auth_invites (token_hash, role, expires_at, bootstrap) VALUES (?, 'hospital', ?, 1)",
                     (_digest(code), time.time() + 7 * 86400))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("AyurSchedule first hospital member setup invite\n\n" + code + "\n\nUse this once on Sign Up > Hospital Member. Do not share it publicly.\n", encoding="utf-8")
        return path


def session_user(token, db_path=database.DB_PATH):
    if not token or not isinstance(token, str):
        return None
    now = time.time()
    with closing(database.get_connection(db_path)) as conn, conn:
        row = conn.execute("""SELECT u.id, u.full_name, u.email, u.role, u.active,
            u.patient_id, u.linked_patient_name, u.patient_gender, s.expires_at, s.last_seen
            FROM auth_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?""", (_digest(token),)).fetchone()
        if row is None or not row["active"] or row["expires_at"] <= now or row["last_seen"] <= now - IDLE_SECONDS:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_digest(token),))
            return None
        if row["last_seen"] < now - 60:
            conn.execute("UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?", (now, _digest(token)))
        return {key: row[key] for key in ("id", "full_name", "email", "role", "patient_id", "linked_patient_name", "patient_gender")}


def require_user(token, role=None, db_path=database.DB_PATH):
    user = session_user(token, db_path)
    if user is None or (role is not None and user["role"] != role):
        raise AccessDenied("Please log in with an authorized account.")
    return user


def _invitation(conn, code, role, email):
    row = conn.execute("SELECT * FROM auth_invites WHERE token_hash = ? AND used_by IS NULL AND expires_at > ?", (_digest(code.strip()), time.time())).fetchone()
    if row is None or row["role"] != role or (row["email"] and row["email"] != email):
        raise ValueError("This invite code is invalid, expired, already used, or belongs to another email address.")
    return row


def _patient_identity(conn, patient_id):
    rows = conn.execute("SELECT DISTINCT patient_name, patient_gender FROM bookings WHERE patient_id = ?", (patient_id,)).fetchall()
    identities = {(row["patient_name"].strip().casefold(), row["patient_gender"]) for row in rows}
    if len(identities) != 1:
        raise ValueError("The hospital must verify this patient's records before linking an account.")
    return rows[0]["patient_name"], rows[0]["patient_gender"]


def register_user(full_name, email, password, role, gender=None, invite_code="", db_path=database.DB_PATH):
    full_name, email = full_name.strip(), _email(email)
    if not full_name or len(full_name) > 150:
        raise ValueError("Enter your full name (up to 150 characters).")
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password with 12 to 128 characters.")
    if role not in {"hospital", "patient"}:
        raise ValueError("Select Patient or Hospital Member.")
    if role == "hospital" and not invite_code.strip():
        raise ValueError("A hospital member invite code is required.")
    if role == "patient" and gender not in {"M", "F"}:
        raise ValueError("Select your gender.")
    password_hash = PASSWORDS.hash(password)
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("An account with this email already exists. Please log in.")
        invitation = _invitation(conn, invite_code, role, email) if invite_code.strip() else None
        if invitation and invitation["bootstrap"] and conn.execute("SELECT 1 FROM users WHERE role = 'hospital'").fetchone():
            raise ValueError("Setup is complete. Request a new invite from a hospital member.")
        patient_id, linked_name = None, None
        if role == "patient":
            patient_id = invitation["patient_id"] if invitation else "PT-" + secrets.token_hex(6).upper()
            linked_name, gender = _patient_identity(conn, patient_id) if invitation else (full_name, gender)
            if conn.execute("SELECT 1 FROM users WHERE patient_id = ?", (patient_id,)).fetchone():
                raise ValueError("This patient record is already linked to an account. Contact the hospital.")
        cur = conn.execute("""INSERT INTO users
            (full_name, email, password_hash, role, patient_id, linked_patient_name, patient_gender, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (full_name, email, password_hash, role, patient_id, linked_name, gender, time.time()))
        if invitation:
            conn.execute("UPDATE auth_invites SET used_by = ? WHERE id = ?", (cur.lastrowid, invitation["id"]))
        return cur.lastrowid


def login(email, password, db_path=database.DB_PATH):
    email = email.strip().casefold()
    now, key = time.time(), _digest(email)
    with closing(database.get_connection(db_path)) as conn:
        attempt = conn.execute("SELECT * FROM auth_login_attempts WHERE email_hash = ?", (key,)).fetchone()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if attempt and attempt["locked_until"] > now:
        raise ValueError("Too many login attempts. Try again after 15 minutes.")
    valid = False
    if len(password) <= 128:
        try:
            valid = PASSWORDS.verify(row["password_hash"] if row else DUMMY_PASSWORD, password)
        except (VerificationError, InvalidHashError):
            pass
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not valid or row is None or not row["active"]:
            attempt = conn.execute("SELECT * FROM auth_login_attempts WHERE email_hash = ?", (key,)).fetchone()
            failures = attempt["failures"] + 1 if attempt and attempt["window_start"] > now - 900 else 1
            start = attempt["window_start"] if attempt and attempt["window_start"] > now - 900 else now
            conn.execute("INSERT OR REPLACE INTO auth_login_attempts VALUES (?, ?, ?, ?)", (key, failures, start, now + 900 if failures >= 5 else 0))
        else:
            fresh = conn.execute("SELECT active FROM users WHERE id = ?", (row["id"],)).fetchone()
            attempt = conn.execute("SELECT locked_until FROM auth_login_attempts WHERE email_hash = ?", (key,)).fetchone()
            if fresh is None or not fresh["active"] or (attempt and attempt["locked_until"] > now):
                raise ValueError("Unable to log in. Please try again later.")
            token = secrets.token_urlsafe(32)
            conn.execute("DELETE FROM auth_login_attempts WHERE email_hash = ?", (key,))
            conn.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, ?)", (_digest(token), row["id"], now + SESSION_SECONDS, now))
            return token
    raise ValueError("Invalid email or password.")


def logout(token, db_path=database.DB_PATH):
    if token:
        with closing(database.get_connection(db_path)) as conn, conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_digest(token),))


def create_recovery_code(token, db_path=database.DB_PATH):
    """Replace the signed-in user's recovery code and reveal it once."""
    user = require_user(token, db_path=db_path)
    code = secrets.token_urlsafe(32)
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO auth_recovery_codes (user_id, token_hash, created_at) VALUES (?, ?, ?)",
                     (user["id"], _digest(code), time.time()))
    return code


def reset_password(email, recovery_code, new_password, db_path=database.DB_PATH):
    """Consume a recovery code, change the password, and revoke old sessions."""
    email = _email(email)
    if not 12 <= len(new_password) <= 128:
        raise ValueError("Use a password with 12 to 128 characters.")
    if not recovery_code or not isinstance(recovery_code, str):
        raise ValueError("Invalid recovery code or email.")
    password_hash = PASSWORDS.hash(new_password)
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT id FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
        stored = conn.execute("SELECT token_hash FROM auth_recovery_codes WHERE user_id = ?", (user["id"],)).fetchone() if user else None
        if stored is None or not secrets.compare_digest(stored["token_hash"], _digest(recovery_code.strip())):
            raise ValueError("Invalid recovery code or email.")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user["id"]))
        conn.execute("DELETE FROM auth_recovery_codes WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM auth_login_attempts WHERE email_hash = ?", (_digest(email),))


def create_invite(token, email, role="hospital", patient_id=None, db_path=database.DB_PATH):
    user, email = require_user(token, "hospital", db_path), _email(email)
    if role not in {"hospital", "patient"}:
        raise ValueError("Select a valid invitation type.")
    with closing(database.get_connection(db_path)) as conn, conn:
        if role == "patient":
            if not patient_id:
                raise ValueError("Select a patient record.")
            _patient_identity(conn, patient_id)
            owner = conn.execute("SELECT email FROM users WHERE patient_id = ?", (patient_id,)).fetchone()
            if owner and owner["email"] != email:
                raise ValueError("This patient record already belongs to another account.")
        code = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO auth_invites (token_hash, role, email, patient_id, created_by, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (_digest(code), role, email, patient_id, user["id"], time.time() + INVITE_SECONDS))
        return code


def link_patient(token, code, db_path=database.DB_PATH):
    user = require_user(token, "patient", db_path)
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        invitation = _invitation(conn, code, "patient", user["email"])
        patient_id = invitation["patient_id"]
        name, gender = _patient_identity(conn, patient_id)
        owner = conn.execute("SELECT id FROM users WHERE patient_id = ?", (patient_id,)).fetchone()
        if owner and owner["id"] != user["id"]:
            raise ValueError("This patient record is already linked to another account.")
        conn.execute("UPDATE users SET patient_id = ?, linked_patient_name = ?, patient_gender = ? WHERE id = ?", (patient_id, name, gender, user["id"]))
        conn.execute("UPDATE auth_invites SET used_by = ? WHERE id = ?", (user["id"], invitation["id"]))


def list_accounts(token, db_path=database.DB_PATH):
    require_user(token, "hospital", db_path)
    with closing(database.get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute("SELECT id, full_name, email, role, active, patient_id, patient_gender FROM users ORDER BY id")]


def set_account_active(token, user_id, active, db_path=database.DB_PATH):
    actor = require_user(token, "hospital", db_path)
    if user_id == actor["id"] and not active:
        raise ValueError("You cannot deactivate your own account.")
    with closing(database.get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        actor_active = conn.execute("SELECT 1 FROM users WHERE id = ? AND active = 1 AND role = 'hospital'", (actor["id"],)).fetchone()
        if not actor_active:
            raise AccessDenied("Please log in with an authorized account.")
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(bool(active)), user_id))
        if not active:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
