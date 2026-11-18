import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
from functools import partial
from ayurschedule import auth, auth_ui
from ayurschedule.ui_theme import apply_theme
from ayurschedule.access import ClinicAccess
from ayurschedule.database import (
    init_db, fetch_clinic_state, add_booking, add_therapist,
    remove_patient_bookings, remove_therapist,
    add_room, remove_room, update_room_equipment, add_therapy, remove_therapy,
)
from ayurschedule.optimizer import AyurSchedulerEngine, TIME_SLOTS
from ayurschedule.excel_io import (
    build_hospital_template, export_clinic_workbook, import_hospital_workbook,
    parse_hospital_workbook, workbook_preview_rows, WorkbookValidationError,
)

st.set_page_config(
    page_title="AyurSchedule | Panchakarma Therapy Scheduler",
    page_icon="🌿",
    layout="wide"
)
apply_theme()

# Initialize SQLite database on startup
init_db()
hospital_member_code = os.environ.get("AYUR_HOSPITAL_INVITE_CODE") or os.environ.get("AYUR_HOSPITAL_SETUP_CODE")
if not hospital_member_code:
    try:
        hospital_member_code = st.secrets.get("AYUR_HOSPITAL_INVITE_CODE") or st.secrets.get("AYUR_HOSPITAL_SETUP_CODE")
    except StreamlitSecretNotFoundError:
        hospital_member_code = None
auth.initialize_auth(member_code=hospital_member_code)
current_user = auth_ui.require_login()
if current_user["role"] == "patient":
    auth_ui.patient_portal(current_user)
    st.stop()
auth_ui.account_sidebar(current_user)
clinic = ClinicAccess(st.session_state["auth_token"])

# Every clinic read/write checks the current database session and hospital role.
fetch_clinic_state = partial(clinic.call, "fetch_clinic_state")
add_booking = partial(clinic.call, "add_booking")
remove_patient_bookings = partial(clinic.call, "remove_patient_bookings")
add_therapist = partial(clinic.call, "add_therapist")
remove_therapist = partial(clinic.call, "remove_therapist")
add_room = partial(clinic.call, "add_room")
remove_room = partial(clinic.call, "remove_room")
update_room_equipment = partial(clinic.call, "update_room_equipment")
add_therapy = partial(clinic.call, "add_therapy")
remove_therapy = partial(clinic.call, "remove_therapy")
import_hospital_workbook = partial(clinic.call, "import_hospital_workbook")
export_clinic_workbook = partial(clinic.call, "export_clinic_workbook")

st.markdown("""
<div class="ayur-hero">
  <div class="ayur-eyebrow">Intelligent clinic operations</div>
  <h1>AyurSchedule</h1>
  <p>Plan Panchakarma care, coordinate your team, and see the whole clinic day at a glance.</p>
</div>
""", unsafe_allow_html=True)

# Reserve this position even when there is no notice, so tabs never move on reruns.
notice_area = st.empty()
if "clinic_notice" in st.session_state:
    notice_area.success(st.session_state.pop("clinic_notice"))


def refresh_clinic(message):
    st.session_state.pop("latest_result", None)
    st.session_state["clinic_notice"] = message
    st.rerun()


@st.cache_data(show_spinner=False)
def hospital_template():
    return build_hospital_template()


@st.cache_data(show_spinner=False)
def preview_hospital_workbook(content, enforce_gender_match):
    return parse_hospital_workbook(content, enforce_gender_match=enforce_gender_match)


def import_uploaded_workbook(enforce_gender_match):
    uploaded = st.session_state.get("hospital_workbook_upload")
    if uploaded is None:
        return
    try:
        imported = import_hospital_workbook(uploaded.getvalue(), enforce_gender_match=enforce_gender_match)
    except auth.AccessDenied:
        auth_ui.logout()
    except WorkbookValidationError as exc:
        st.session_state["hospital_import_errors"] = exc.errors
    except sqlite3.Error as exc:
        st.session_state["hospital_import_errors"] = [f"The import could not be saved. Existing clinic data was retained. {exc}"]
    else:
        st.session_state.pop("hospital_import_errors", None)
        st.session_state.pop("latest_result", None)
        st.session_state["schedule_date"] = imported.dates[0]
        st.session_state["clinic_notice"] = (
            f"Hospital workbook imported: {len(imported.doctors)} doctors, {len(imported.rooms)} rooms, "
            f"{len(imported.bookings)} bookings. Open Live Schedule & Gantt Chart to generate the schedule for {imported.dates[0]}."
        )


def open_excel_import():
    clinic.require_staff()
    st.session_state["clinic_tabs"] = "📥 Excel Import / Export"


with st.sidebar:
    st.header("⚙️ Clinic & GA Configuration")
    st.session_state.setdefault("schedule_date", "2026-11-18")
    schedule_date = st.text_input("Schedule Date (YYYY-MM-DD)", key="schedule_date")
    st.button("📥 Hospital Excel Upload", on_click=open_excel_import, use_container_width=True)
    enforce_gender = st.checkbox("Enforce Patient–Therapist Gender Match", value=True)
    st.divider()
    st.subheader("Evolutionary Hyperparameters")
    optimization_mode = st.radio("Parameter Selection", ["Automatic", "Manual"], horizontal=True, key="optimization_mode")
    if optimization_mode == "Automatic":
        selected = st.session_state.get("latest_result", {}).get("auto_tuning", {}).get("parameters", {
            "pop_size": 60, "generations": 80, "crossover_rate": 0.75, "mutation_rate": 0.10, "seed": 42
        })
        for key, value in selected.items():
            st.session_state[f"auto_{key}"] = value
        pop_size = st.slider("Population Size (N)", 20, 200, key="auto_pop_size", step=10, disabled=True)
        generations = st.slider("Max Generations (G)", 20, 300, key="auto_generations", step=10, disabled=True)
        crossover_rate = st.slider("Crossover Probability (Pc)", 0.50, 0.99, key="auto_crossover_rate", step=0.05, disabled=True)
        mutation_rate = st.slider("Mutation Probability (Pm)", 0.01, 0.50, key="auto_mutation_rate", step=0.02, disabled=True)
        use_memetic = st.checkbox("Enable Memetic Local Search Hybridization", value=True, disabled=True)
        random_seed = st.number_input("Random Seed (Reproducibility)", key="auto_seed", step=1, disabled=True)
        st.caption("Settings are selected by comparing schedule results. Switch to Manual to edit them.")
    else:
        chosen = st.session_state.get("latest_result", {}).get("auto_tuning", {}).get("parameters", {})
        for key, default in {"pop_size": 80, "generations": 120, "crossover_rate": 0.85,
                             "mutation_rate": 0.18, "seed": 42}.items():
            st.session_state.setdefault(f"manual_{key}", chosen.get(key, default))
        pop_size = st.slider("Population Size (N)", 20, 200, step=10, key="manual_pop_size")
        generations = st.slider("Max Generations (G)", 20, 300, step=10, key="manual_generations")
        crossover_rate = st.slider("Crossover Probability (Pc)", 0.50, 0.99, step=0.05, key="manual_crossover_rate")
        mutation_rate = st.slider("Mutation Probability (Pm)", 0.01, 0.50, step=0.02, key="manual_mutation_rate")
        use_memetic = st.checkbox("Enable Memetic Local Search Hybridization", value=True, key="manual_memetic")
        random_seed = st.number_input("Random Seed (Reproducibility)", step=1, key="manual_seed")

def resource_shortages(clinic_state):
    missing_equipment, missing_practitioners = set(), set()
    for booking in clinic_state["bookings"]:
        therapy = clinic_state["therapies"][booking["therapy_code"]]
        if not any(therapy["required_equipment"] in room["equipment_set"] for room in clinic_state["rooms"]):
            missing_equipment.add(therapy["required_equipment"])
        if not any(
            booking["therapy_code"] in therapist["skills_set"]
            and (not enforce_gender or therapist["gender"] == booking["patient_gender"])
            for therapist in clinic_state["therapists"]
        ):
            missing_practitioners.add(booking["therapy_code"])
    return sorted(missing_equipment), sorted(missing_practitioners)


state = fetch_clinic_state(schedule_date)
missing_equipment, missing_practitioners = resource_shortages(state)
schedule_ready = bool(state["bookings"] and state["therapists"] and state["rooms"]) and not (missing_equipment or missing_practitioners)
# A roster, booking, date, or configuration change must invalidate the old schedule.
schedule_context = (
    state, enforce_gender, optimization_mode,
    (pop_size, generations, crossover_rate, mutation_rate, use_memetic, int(random_seed)) if optimization_mode == "Manual" else None
)
if st.session_state.get("schedule_context") != schedule_context:
    st.session_state.pop("latest_result", None)
    st.session_state["schedule_context"] = schedule_context

st.markdown('<div class="ayur-section-label">Clinic overview</div>', unsafe_allow_html=True)
overview = st.columns(4)
overview[0].metric("Patients today", len({booking["patient_id"] for booking in state["bookings"]}))
overview[1].metric("Active practitioners", len(state["therapists"]))
overview[2].metric("Treatment rooms", len(state["rooms"]))
overview[3].metric("Bookings for date", len(state["bookings"]))
st.caption(f"Schedule date: {schedule_date}")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📅 Live Schedule & Gantt Chart",
    "🏥 Clinic Resource & Patient Registry",
    "🔬 Algorithm Benchmark & Convergence",
    "➕ Manage Patients & Doctors / Therapists",
    "📥 Excel Import / Export",
    "🔐 Accounts & Invites"
], key="clinic_tabs", on_change="rerun")


def render_schedule_content(run_opt, state, schedule_ready, missing_equipment, missing_practitioners):
    if not state["bookings"]:
        st.warning(f"No bookings found for {schedule_date}. Add a patient booking in the management tab.")
    elif not state["therapists"]:
        st.warning("No active doctors / therapists. Add a practitioner in the management tab to generate a schedule.")
    elif not state["rooms"]:
        st.warning("No active treatment rooms are available.")
    elif missing_equipment:
        st.warning(f"No active room has the equipment required by current bookings: {', '.join(missing_equipment)}. Add the equipment to a room or rebook the affected patients.")
    elif missing_practitioners:
        st.warning(f"No qualified doctor / therapist matches the current booking requirements for: {', '.join(missing_practitioners)}. Assign qualified practitioners or rebook the affected patients.")
    elif run_opt or "latest_result" not in st.session_state:
        with st.spinner("Generating optimized clinic schedule..."):
            engine = AyurSchedulerEngine(state, enforce_gender_match=enforce_gender)
            if optimization_mode == "Automatic":
                res = engine.auto_tune_memetic_ga()
            else:
                res = engine.run_memetic_ga(
                    pop_size=pop_size,
                    generations=generations,
                    crossover_rate=crossover_rate,
                    mutation_rate=mutation_rate,
                    use_memetic_ls=use_memetic,
                    seed=int(random_seed)
                )
            res["patient_schedule_saved"] = clinic.save_patient_schedule(res, state)
            st.session_state["latest_result"] = res
            if optimization_mode == "Automatic":
                st.rerun(scope="app")

    if schedule_ready and "latest_result" in st.session_state:
        res = st.session_state["latest_result"]
        ev = res["evaluation"]
        df = res["schedule_df"]

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Penalty Score", f"{ev['total_penalty']:.2f}")
        m2.metric("Hard Violations", ev["hard_violations"], delta="Feasible" if ev["hard_violations"] == 0 else "Conflict", delta_color="normal" if ev["hard_violations"] == 0 else "inverse")
        m3.metric("Soft Violations (Dosha/Chain)", ev["soft_violations"])
        m4.metric("Workload StdDev (Slots)", f"{ev['workload_std']:.2f}")
        m5.metric("Execution Time", f"{res['runtime_ms']} ms")

        if tuning := res.get("auto_tuning"):
            chosen = tuning["parameters"]
            st.caption(
                f"Automatic search compared {len(tuning['trials'])} parameter sets and selected "
                f"N={chosen['pop_size']}, G={chosen['generations']}, Pc={chosen['crossover_rate']:.2f}, "
                f"Pm={chosen['mutation_rate']:.2f}, seed={chosen['seed']}."
            )
            with st.expander("View automatic search results"):
                st.dataframe(pd.DataFrame(tuning["trials"]), use_container_width=True, hide_index=True)

        st.subheader("Interactive Room & Therapist Timeline (Gantt View)")
        # Build timeline coordinates for Plotly
        gantt_rows = []
        for _, row in df.iterrows():
            start_hr = 8 + (row["Start_Slot"] * 0.5)
            end_hr = 8 + (row["End_Slot"] * 0.5)
            start_dt = f"{schedule_date} {int(start_hr):02d}:{int((start_hr % 1)*60):02d}:00"
            end_dt = f"{schedule_date} {int(end_hr):02d}:{int((end_hr % 1)*60):02d}:00"
            gantt_rows.append({
                "Room": row["Room"],
                "Therapist": row["Therapist"],
                "Patient_Name": row["Patient_Name"],
                "Task": f"{row['Patient_Name']} ({row['Therapy_Code']})",
                "Start": start_dt,
                "Finish": end_dt,
                "Target_Dosha": row["Target_Dosha"],
                "Status": row["Status"]
            })
        gantt_df = pd.DataFrame(gantt_rows)
        fig_gantt = px.timeline(
            gantt_df,
            x_start="Start",
            x_end="Finish",
            y="Room",
            color="Target_Dosha",
            text="Patient_Name",
            hover_name="Task",
            hover_data={"Therapist": True, "Status": True, "Patient_Name": False},
            color_discrete_map={"Kapha": "#2e7d32", "Pitta": "#e65100", "Vata": "#1565c0"},
            title=f"Panchakarma Room Allocation Schedule ({schedule_date})"
        )
        fig_gantt.update_traces(
            textposition="inside", insidetextanchor="middle", textangle=0,
            constraintext="inside", cliponaxis=True,
            insidetextfont={"color": "white"}
        )
        fig_gantt.update_layout(
            uniformtext_minsize=10, uniformtext_mode="hide",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#dce8dd"}, title_font={"color": "#f5f4e9", "size": 17},
            margin={"l": 15, "r": 20, "t": 58, "b": 22},
            legend_title_text="Dosha",
        )
        fig_gantt.update_xaxes(showgrid=True, gridcolor="rgba(187, 209, 190, 0.12)", zeroline=False)
        fig_gantt.update_yaxes(autorange="reversed")
        st.plotly_chart(fig_gantt, use_container_width=True)

        st.subheader("Master Clinical Roster Table")
        st.caption("Only conflict-free schedules are shared with patient accounts. Clinic changes require a fresh schedule before patients see updated timings.")
        st.dataframe(df, use_container_width=True)

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        def authorized_schedule_csv():
            clinic.require_staff()
            return csv_bytes
        st.download_button(
            "📥 Download Schedule as CSV",
            data=authorized_schedule_csv,
            file_name=f"AyurSchedule_{schedule_date}.csv",
            mime="text/csv"
        )


@st.fragment
def render_live_schedule():
    try:
        clinic.require_staff()
    except auth.AccessDenied:
        auth_ui.logout()
        st.rerun(scope="app")
    # Fragment reruns also need current resources, including changes by another session.
    current_state = fetch_clinic_state(schedule_date)
    equipment_shortages, practitioner_shortages = resource_shortages(current_state)
    ready = bool(current_state["bookings"] and current_state["therapists"] and current_state["rooms"]) and not (equipment_shortages or practitioner_shortages)
    current_context = (current_state, *schedule_context[1:])
    if st.session_state.get("schedule_context") != current_context:
        st.session_state.pop("latest_result", None)
        st.session_state["schedule_context"] = current_context
    col_btn1, _ = st.columns([1, 3])
    with col_btn1:
        run_opt = st.button(
            "🚀 Generate Optimized Clinic Schedule", key="generate_schedule",
            type="primary", use_container_width=True, disabled=not ready
        )
    # Keep schedule output in one replaceable container across fragment reruns.
    with st.empty().container():
        render_schedule_content(run_opt, current_state, ready, equipment_shortages, practitioner_shortages)


with tab1:
    if tab1.open:
        render_live_schedule()

with tab2:
    st.subheader("Registered Ayurvedic Therapies & Equipment Requirements")
    st.dataframe(pd.DataFrame(list(state["therapies"].values())), use_container_width=True)

    equipment_options = sorted(
        set(state["equipment"]) | {therapy["required_equipment"] for therapy in state["therapies"].values()}
        | {item for room in state["rooms"] for item in room["equipment_set"]}
    )
    with st.expander("Add / Remove Therapies"):
        with st.form("add_therapy_form", clear_on_submit=True):
            th_c1, th_c2, th_c3 = st.columns(3)
            therapy_code = th_c1.text_input("Therapy Code", placeholder="e.g. KATI_BASTI")
            therapy_name = th_c2.text_input("Therapy Name")
            therapy_category = th_c3.text_input("Therapy Category", value="Pradhanakarma")
            th_c4, th_c5, th_c6 = st.columns(3)
            therapy_duration = th_c4.number_input("Duration (30-minute slots)", 1, 20, 2)
            therapy_cleanup = th_c5.number_input("Cleanup / Preparation (30-minute slots)", 0, 20, 1)
            therapy_dosha = th_c6.selectbox("Optimal Dosha", ["Vata", "Pitta", "Kapha"])
            required_equipment = st.selectbox("Required Equipment", equipment_options)
            new_required_equipment = st.text_input("New Required Equipment (optional)", help="Enter a new equipment name if it is not in the list above.")
            follow_up_code = st.selectbox("Follow-up Therapy (optional)", [None] + list(state["therapies"]), format_func=lambda code: code or "None")
            practitioner_names = {t["id"]: t["name"] for t in state["therapists"]}
            qualified_practitioners = st.multiselect(
                "Qualified Practitioners for New Therapy", list(practitioner_names),
                format_func=lambda practitioner_id: f"{practitioner_names[practitioner_id]} (ID: {practitioner_id})"
            )
            if st.form_submit_button("Add Therapy"):
                try:
                    saved_code = add_therapy(
                        therapy_code, therapy_name, therapy_category,
                        int(therapy_duration), int(therapy_cleanup), therapy_dosha,
                        new_required_equipment.strip() or required_equipment or "",
                        follow_up_code=follow_up_code, therapist_ids=qualified_practitioners
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    refresh_clinic(f"Therapy {saved_code} added to the clinic registry.")

        if state["therapies"]:
            st.caption("Therapies with patient bookings can be removed after those bookings are removed or rebooked.")
            with st.form("remove_therapy_form"):
                therapy_to_remove = st.selectbox(
                    "Therapy to Remove", list(state["therapies"]),
                    format_func=lambda code: f"{state['therapies'][code]['name']} ({code})"
                )
                if st.form_submit_button("Remove Therapy"):
                    try:
                        remove_therapy(therapy_to_remove)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        refresh_clinic(f"Therapy {therapy_to_remove} removed from the clinic registry.")
        else:
            st.info("No registered therapies. Add a therapy using the form above.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Active Doctors / Therapists")
        t_df = pd.DataFrame(state["therapists"]).drop(columns=["skills_set"], errors="ignore")
        st.dataframe(t_df, use_container_width=True)
    with c2:
        st.subheader("Treatment Rooms & Chambers")
        r_df = pd.DataFrame(state["rooms"]).drop(columns=["equipment_set"], errors="ignore")
        st.dataframe(r_df, use_container_width=True)

    with st.expander("Add / Remove Treatment Rooms & Chambers"):
        with st.form("add_room_form", clear_on_submit=True):
            room_name = st.text_input("Room / Chamber Name", placeholder="e.g. Dhara Suite 103")
            room_equipment = st.multiselect("Equipment in New Room", equipment_options)
            extra_room_equipment = st.text_input("Additional Equipment for New Room (comma-separated)")
            if st.form_submit_button("Add Room / Chamber"):
                try:
                    add_room(room_name, room_equipment + extra_room_equipment.split(","))
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    refresh_clinic(f"{room_name.strip()} added to the active rooms / chambers.")

        active_rooms = {room["id"]: room for room in state["rooms"]}
        if active_rooms:
            with st.form("remove_room_form"):
                room_to_remove = st.selectbox(
                    "Room / Chamber to Remove", list(active_rooms),
                    format_func=lambda room_id: f"{active_rooms[room_id]['name']} (ID: {room_id})"
                )
                if st.form_submit_button("Remove Room / Chamber"):
                    remove_room(room_to_remove)
                    refresh_clinic(f"{active_rooms[room_to_remove]['name']} removed from the active rooms / chambers.")
        else:
            st.info("No active rooms / chambers. Add one using the form above.")

    with st.expander("Add / Remove Equipment in a Room"):
        if active_rooms:
            equipment_room_id = st.selectbox(
                "Room / Chamber for Equipment", list(active_rooms),
                format_func=lambda room_id: f"{active_rooms[room_id]['name']} (ID: {room_id})"
            )
            with st.form("update_room_equipment_form"):
                updated_equipment = st.multiselect(
                    "Available Equipment in Selected Room", equipment_options,
                    default=sorted(active_rooms[equipment_room_id]["equipment_set"])
                )
                additional_equipment = st.text_input("Additional Equipment for Selected Room (comma-separated)")
                st.caption("Select equipment to add it, or deselect it to remove it from this room.")
                if st.form_submit_button("Save Room Equipment"):
                    try:
                        update_room_equipment(equipment_room_id, updated_equipment + additional_equipment.split(","))
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        refresh_clinic(f"Equipment updated for {active_rooms[equipment_room_id]['name']}.")
        else:
            st.info("Add a room / chamber before assigning equipment.")

    st.subheader(f"Patient Bookings for {schedule_date}")
    st.dataframe(pd.DataFrame(state["bookings"]), use_container_width=True)

with tab3:
    st.subheader("🔬 Multi-Algorithm Benchmarking")
    st.markdown("Compare **Greedy FCFS**, **Simulated Annealing (SA)**, **Standard GA**, and **Proposed Memetic GA** on the current clinic dataset.")
    if st.button("Run Comparative Benchmark Suite", disabled=not schedule_ready):
        engine = AyurSchedulerEngine(state, enforce_gender_match=enforce_gender)
        r_greedy = engine.run_greedy_fcfs()
        r_sa = engine.run_simulated_annealing(seed=int(random_seed))
        r_ga = engine.run_memetic_ga(pop_size=pop_size, generations=generations, use_memetic_ls=False, seed=int(random_seed))
        r_memetic = engine.run_memetic_ga(pop_size=pop_size, generations=generations, use_memetic_ls=True, seed=int(random_seed))

        bench_rows = []
        for r in [r_greedy, r_sa, r_ga, r_memetic]:
            ev = r["evaluation"]
            bench_rows.append({
                "Algorithm": r["algorithm"],
                "Total Penalty": ev["total_penalty"],
                "Hard Violations": ev["hard_violations"],
                "Soft Violations": ev["soft_violations"],
                "Workload StdDev": ev["workload_std"],
                "Runtime (ms)": r["runtime_ms"]
            })
        st.table(pd.DataFrame(bench_rows))

        fig_conv = go.Figure()
        fig_conv.add_trace(go.Scatter(
            x=[h["generation"] for h in r_ga["history"]],
            y=[h["best_penalty"] for h in r_ga["history"]],
            mode="lines", name="Standard GA", line={"color": "#58b9c5", "width": 3}
        ))
        fig_conv.add_trace(go.Scatter(
            x=[h["generation"] for h in r_memetic["history"]],
            y=[h["best_penalty"] for h in r_memetic["history"]],
            mode="lines", name="Memetic GA (Proposed)", line={"color": "#dfb45e", "width": 3}
        ))
        fig_conv.update_layout(
            title="Convergence Trajectory: Standard GA vs. Memetic GA",
            xaxis_title="Generation",
            yaxis_title="Best Total Penalty Score",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#dce8dd"}, title_font={"color": "#f5f4e9", "size": 17},
            margin={"l": 15, "r": 20, "t": 58, "b": 20},
        )
        fig_conv.update_xaxes(showgrid=True, gridcolor="rgba(187, 209, 190, 0.12)", zeroline=False)
        fig_conv.update_yaxes(showgrid=True, gridcolor="rgba(187, 209, 190, 0.12)", zeroline=False)
        st.plotly_chart(fig_conv, use_container_width=True)

with tab4:
    st.subheader("Add New Patient Therapy Booking")
    if not state["therapies"]:
        st.info("Add a therapy in Clinic Resource & Patient Registry before creating a booking.")
    with st.form("add_booking_form"):
        f_c1, f_c2, f_c3 = st.columns(3)
        p_id = f_c1.text_input("Patient ID", value="PT-115")
        p_name = f_c2.text_input("Patient Full Name", value="Rohan Deshpande")
        p_gender = f_c3.selectbox("Gender", ["M", "F"])

        f_c4, f_c5, f_c6 = st.columns(3)
        t_code = f_c4.selectbox("Therapy Type", list(state["therapies"].keys()))
        pref_dosha = f_c5.selectbox("Target Dosha Period", ["Vata", "Pitta", "Kapha"])
        include_swedana = f_c6.checkbox("Auto-Chain Follow-up Swedana (Herbal Steam)?", value=False, disabled="SWEDANA" not in state["therapies"])

        submitted = st.form_submit_button("Save Booking to Clinic Database", disabled=not state["therapies"])
        if submitted:
            try:
                add_booking(
                    schedule_date, p_id, p_name, p_gender, t_code, pref_dosha,
                    include_swedana=include_swedana
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                refresh_clinic(f"Booking saved for {p_name.strip()} on {schedule_date}. The schedule has been refreshed.")

    st.divider()
    st.subheader("Remove Patient Bookings")
    st.caption(f"Remove all therapy bookings, including follow-ups, for the selected patient on {schedule_date}.")
    patients = {booking["patient_id"]: booking["patient_name"] for booking in state["bookings"]}
    if patients:
        with st.form("remove_patient_form"):
            patient_to_remove = st.selectbox(
                "Patient to remove", list(patients),
                format_func=lambda patient_id: f"{patients[patient_id]} ({patient_id})"
            )
            if st.form_submit_button("Remove Patient"):
                count = remove_patient_bookings(patient_to_remove, schedule_date)
                refresh_clinic(f"Removed {count} booking(s) for {patients[patient_to_remove]} on {schedule_date}.")
    else:
        st.info("No patients to remove for the selected date.")

    st.divider()
    st.subheader("Add Doctor / Therapist")
    with st.form("add_therapist_form", clear_on_submit=True):
        d_c1, d_c2 = st.columns(2)
        doctor_name = d_c1.text_input("Doctor / Therapist Full Name")
        doctor_gender = d_c2.selectbox("Doctor / Therapist Gender", ["M", "F"])
        doctor_skills = st.multiselect("Qualified Therapies", list(state["therapies"]))
        d_c3, d_c4, d_c5 = st.columns(3)
        shift_start = d_c3.selectbox("Shift Start", range(20), format_func=lambda slot: TIME_SLOTS[slot])
        shift_end = d_c4.selectbox("Shift End", range(1, 21), index=19, format_func=lambda slot: TIME_SLOTS[slot])
        max_daily_slots = d_c5.number_input(
            "Maximum Daily Workload (30-minute slots)", min_value=1, max_value=20, value=12, step=1
        )
        if st.form_submit_button("Add Doctor / Therapist"):
            try:
                add_therapist(
                    doctor_name, doctor_gender, doctor_skills, shift_start,
                    shift_end, int(max_daily_slots)
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                refresh_clinic(f"{doctor_name.strip()} added to the active doctor / therapist roster.")

    st.subheader("Remove Doctor / Therapist")
    st.caption("Departing practitioners are removed from the active roster and future scheduling.")
    doctors = {therapist["id"]: therapist["name"] for therapist in state["therapists"]}
    if doctors:
        with st.form("remove_therapist_form"):
            doctor_to_remove = st.selectbox(
                "Doctor / Therapist to remove", list(doctors),
                format_func=lambda therapist_id: f"{doctors[therapist_id]} (ID: {therapist_id})"
            )
            if st.form_submit_button("Remove Doctor / Therapist"):
                remove_therapist(doctor_to_remove)
                refresh_clinic(f"{doctors[doctor_to_remove]} removed from the active doctor / therapist roster.")
    else:
        st.info("No active doctors / therapists to remove. Add a practitioner using the form above.")


with tab5:
    st.subheader("Hospital Excel Template & Data Import")
    st.markdown(
        "Download the template, fill **Equipment**, **Therapies**, **Doctors**, **Rooms**, and **Bookings**, "
        "then upload the completed `.xlsx` file. The **Instructions** sheet explains every column."
    )
    st.download_button(
        "Download Excel Template", data=hospital_template(),
        file_name="AyurSchedule_Hospital_Template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore"
    )
    if tab5.open:
        st.download_button(
            "Download Current Clinic Backup", data=export_clinic_workbook,
            file_name="AyurSchedule_Clinic_Backup.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore"
        )
    uploaded_workbook = st.file_uploader(
        "Upload Filled Hospital Workbook", type=["xlsx"], key="hospital_workbook_upload",
        max_upload_size=10,
        on_change=lambda: st.session_state.pop("hospital_import_errors", None)
    )
    if "hospital_import_errors" in st.session_state:
        for issue in st.session_state["hospital_import_errors"]:
            st.error(issue)
    if uploaded_workbook is not None:
        try:
            workbook_preview = preview_hospital_workbook(uploaded_workbook.getvalue(), enforce_gender)
        except WorkbookValidationError as exc:
            st.error("The workbook needs corrections. No clinic data has been changed.")
            st.dataframe(pd.DataFrame({"Workbook issues": exc.errors}), use_container_width=True, hide_index=True)
            st.button("Import Workbook and Replace Clinic Data", disabled=True)
        else:
            st.success("Workbook validated. Review the data before importing.")
            for column, (label, count) in zip(st.columns(5), workbook_preview.counts.items()):
                column.metric(label, count)
            st.write("Booking dates: " + ", ".join(workbook_preview.dates))
            for label, rows in workbook_preview_rows(workbook_preview).items():
                with st.expander(f"Preview {label}"):
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "Import replaces all current equipment, therapies, doctors, rooms, patient bookings and generated schedules "
                "with this workbook. Download the current clinic backup above if you want to keep a copy. "
                "The first booking date will be selected automatically."
            )
            st.button(
                "Import Workbook and Replace Clinic Data", type="primary",
                on_click=import_uploaded_workbook, args=(enforce_gender,)
            )


with tab6:
    auth_ui.hospital_accounts(clinic)
