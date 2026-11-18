# AyurSchedule

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Streamlit App](https://img.shields.io/badge/Streamlit-Clinic%20Dashboard-FF4B4B.svg)](https://streamlit.io)

**AyurSchedule** is a Streamlit application for planning Ayurvedic Panchakarma appointments across patients, therapists, rooms, and equipment. Its genetic algorithm and optional local search compare candidate schedules against clinic constraints and show the best result found in the allotted search. The app includes role-based accounts, an interactive Gantt chart, clinic registry management, and Excel import/export.

> **Data storage:** The default SQLite database lives on the server. On Streamlit Community Cloud, local files may be lost when the app is replaced or restarted. Use a persistent database and a proper backup plan before relying on this app for real patient records. Do not put patient data or invite codes in Git.

---

## Features

1. **Sequential Therapy Chaining (*Purvakarma* $\rightarrow$ *Pradhanakarma*):** Automatically schedules dependent multi-stage treatments back-to-back without idle patient waiting (e.g., *Abhyanga* herbal oil massage immediately followed by *Bashpa Swedana* herbal steam bath).
2. **Room Sanitization & Turnover Buffers:** Enforces mandatory 30-minute cleanup/preparation buffers (`prep_slots`) on specialized treatment rooms (*Dhara Suites*, *Steam Chambers*, *Basti Units*) after every session.
3. **Chronobiological *Dosha* Optimization:** Aligns therapies with Ayurvedic circadian rhythms (*Kapha* in morning slots `08:00–10:30`, *Pitta* in midday slots `10:30–14:00`, and *Vata* in afternoon slots `14:00–18:00`).
4. **Clinical constraints:** Checks therapist and room collisions, therapy qualifications, optional patient–therapist gender matching, shifts, and clinic breaks. Review the reported hard violations before using a schedule.
5. **SQLite Persistence & Streamlit Web Portal:** Add doctors/therapists with their qualified therapies, shifts, and workload limits; remove departing practitioners from the active roster; and add or remove daily patient bookings, including chained follow-ups. Schedules refresh after registry changes. Interactive Plotly Gantt charts keep patient names inside their bars, hide labels that do not fit, and retain full details on hover. Export schedules as CSV.

---

## Repository layout

```text
AyurSchedule/
├── .streamlit/
│   └── config.toml          # Local Streamlit configuration
├── ayurschedule/
│   ├── __init__.py          # Package exports
│   ├── access.py            # Hospital permissions & patient-scoped queries
│   ├── auth.py              # Accounts, sessions & invitations
│   ├── auth_ui.py           # Login, signup & patient portal
│   ├── database.py          # SQLite schema, seed data & resource management
│   ├── excel_io.py          # Excel templates, validation, backup & import
│   ├── optimizer.py         # Scheduling algorithms & constraint evaluation
│   └── ui_theme.py          # Shared visual theme
├── templates/
│   └── AyurSchedule_Hospital_Template.xlsx
├── tests/                   # Authentication, UI, database, Excel & optimizer tests
├── app.py                   # Streamlit clinic application
├── cli.py                   # Command-line scheduling & benchmark runner
├── requirements.txt         # Python dependencies
└── README.md                # Setup & usage documentation
```

The clinic database and first-member setup invite are local runtime files excluded from Git. Generated CSV/PNG reports and Python/pytest caches are disposable; the CLI recreates its reports when needed.

---

## Run locally

### Install dependencies

```bash
git clone https://github.com/AnieeD/AyurSchedule.git
cd AyurSchedule
pip install -r requirements.txt
```

### Start the app

```bash
streamlit run app.py
```
Open `http://localhost:8501` in your browser. A fresh local database starts with sample clinic data; replace it through **Excel Import / Export** when you are ready to use your own records.

Schedule generation defaults to **Automatic** parameter selection. It compares several population sizes, generation limits, crossover and mutation probabilities, and random seeds, then uses the lowest-penalty feasible schedule found within a bounded search. The selected settings and trial scores appear with the schedule. Choose **Manual** in the sidebar to edit the sliders, memetic search setting, and seed yourself. Automatic search may take longer than one manual run and does not guarantee a global optimum.

### Accounts and role permissions

The app opens with **Login / Sign Up**. Clinic data is unavailable until login.

| Account | Access |
| --- | --- |
| Hospital Member | Full clinic management, schedules, benchmarks, Excel import/export, member invites, patient record linking, and account activation/deactivation. |
| Patient | Their own appointments, assigned treatment times, doctor and room; private hospital invite redemption to link existing records. |
| Logged out | Login and signup only. |

For a new local installation without a configured fixed code, the server writes a private first-member invite to `.streamlit/hospital_setup_invite.txt`. Open that file and select **Sign Up → Hospital Member**. The file is excluded from Git. After setup, use **Accounts & Invites** to issue email-bound, single-use member invites, valid for 72 hours. There is no public default invite code or password.

For a deployed app, configure a random code of at least 32 characters in Streamlit **Secrets** before the first hospital signup:

```toml
AYUR_HOSPITAL_INVITE_CODE = "<private-random-code-at-least-32-characters>"
```

Anyone with this exact code can sign up as a hospital member. It remains valid until you change or remove the secret, so share it only with authorized staff and keep it out of Git. The app stores only its hash. The older `AYUR_HOSPITAL_SETUP_CODE` setting also works for compatibility. Individual email-bound invites from **Accounts & Invites** continue to work. Accounts and invites stored in one SQLite database do not automatically appear in another deployment.

Patients can sign up without an invite and receive a new patient ID. To access an existing clinic record, a hospital member must create a **Patient Record Invite** for that patient ID and the patient's account email. The patient can redeem it at signup or through **Link My Existing Patient Record**. Entering another patient's ID or changing interface state cannot grant access to their records. Account lists and workbook backups never include password hashes or login tokens.

### Password recovery

After signup, save the recovery code shown under **Account Recovery** in the sidebar. Existing users can generate a code there while signed in. If you forget your password, open **Forgot Password** on the login screen and enter your account email, recovery code, and a new password. Each recovery code works once; generating a new one invalidates the previous code. A successful reset signs out all existing sessions. Recovery codes are stored only as hashes, so the clinic cannot display a lost code. If you lose both your password and recovery code, an administrator with local database access must recover the account.

Passwords use Argon2id hashes. Sessions use random tokens stored as hashes, expire after eight hours or 30 minutes idle, and are revoked on logout or account deactivation. Five failed login attempts lock that email's login for 15 minutes. Sessions are tied to the current Streamlit browser connection; a browser reload requires logging in again. All staff operations, fragment reruns, and private download generation recheck the database session and role.

Only schedules with zero hard violations are made available to patients. Booking/resource changes mark previously shared timings as pending until the hospital generates a fresh valid schedule. Excel replacement preserves user accounts but unlinks patient medical records and revokes unused patient record invites, since imported patient IDs may belong to different people. Issue new patient invites after replacing the dataset. Authentication records are never part of the hospital workbook.

Use **Manage Patients & Doctors / Therapists** to add or remove patients and practitioners. **Remove Patient** deletes all of the selected patient's bookings and follow-ups for the date selected in the sidebar. **Remove Doctor / Therapist** removes that practitioner from the active roster and future scheduling while retaining historical records.

Use **Clinic Resource & Patient Registry** to add or remove therapies, treatment rooms, and chambers. Expand **Add / Remove Equipment in a Room** to select available equipment, deselect removed equipment, or enter new equipment names. New therapies support duration, cleanup time, dosha, required equipment, follow-up therapy, and qualified practitioner assignments. Remove or rebook a therapy's existing patient bookings before removing it. Removed rooms remain excluded after restarts. Scheduling pauses with an explanation if bookings need unavailable equipment or qualified practitioners. Registry actions preserve the selected tab.

Use **Excel Import / Export** (or **Hospital Excel Upload** in the sidebar) to load a hospital's entire dataset without entering records one by one:

1. Download **Excel Template** in the app, or use [the blank hospital template](templates/AyurSchedule_Hospital_Template.xlsx).
2. Read **Instructions**, then fill **Equipment**, **Therapies**, **Doctors**, **Rooms**, and **Bookings**. Every data sheet needs at least one row. Header comments explain each field. Use minutes in multiples of 30, shift times such as `08:00`, dates such as `2027-02-03`, and comma-separated therapy/equipment lists. Each treatment gets its own booking row; link a follow-up through `depends_on_booking_id`.
3. Download **Current Clinic Backup** if you want to retain the existing data. The backup includes all booking dates and can be edited and imported using the same format.
4. Upload the completed `.xlsx`, review the counts, booking dates and sheet previews, then click **Import Workbook and Replace Clinic Data**. Import replaces the existing clinic dataset and generated schedules; it does not mix hospital records with demo data. Invalid rows show sheet and row numbers, and a failed import leaves existing records intact.
5. The first uploaded booking date is selected automatically. Open **Live Schedule & Gantt Chart** to generate the hospital schedule, or select another uploaded date in the sidebar.

Uploads accept `.xlsx` files up to 10 MB and 10,000 data rows per sheet. Use literal cell values rather than formulas. Clinic hours remain `08:00–18:00`, with `13:00–14:00` reserved for lunch/sanitization. Individual booking/resource checks do not guarantee that a heavily loaded day can fit; review the generated schedule's hard violations and penalty score.

## Command-line scheduling and benchmark

Run a daily schedule and write the result to a CSV file (today is the default date; pass `--date YYYY-MM-DD` for another day):

```bash
python cli.py --out optimized_schedule.csv
```

Compare the available scheduling algorithms:

```bash
python cli.py --benchmark --pop 80 --gen 120
```

The CLI reads the local SQLite clinic database and writes reports to the current directory. See `python cli.py --help` for all options.

## Tests

```bash
python -m pytest -q
```
