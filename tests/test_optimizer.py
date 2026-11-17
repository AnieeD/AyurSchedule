import os
from ayurschedule.database import init_db, fetch_clinic_state
from ayurschedule.optimizer import AyurSchedulerEngine

def test_zero_hard_violations_on_clinic_seed(tmp_path):
    db_file = str(tmp_path / "test_clinic.db")
    init_db(db_path=db_file, seed_default=True)
    state = fetch_clinic_state("2026-11-18", db_path=db_file)
    engine = AyurSchedulerEngine(state, enforce_gender_match=True)
    res = engine.run_memetic_ga(pop_size=60, generations=80, use_memetic_ls=True, seed=42)
    assert res["evaluation"]["hard_violations"] == 0, "Memetic GA must resolve all hard constraints"
    assert len(res["schedule_df"]) == len(state["bookings"])

def test_purvakarma_chain_continuity(tmp_path):
    db_file = str(tmp_path / "test_chain.db")
    init_db(db_path=db_file, seed_default=True)
    state = fetch_clinic_state("2026-11-18", db_path=db_file)
    engine = AyurSchedulerEngine(state, enforce_gender_match=True)
    res = engine.run_memetic_ga(pop_size=50, generations=60, use_memetic_ls=True, seed=42)
    df = res["schedule_df"].set_index("Booking_ID")
    # Booking 2 (Swedana) depends on Booking 1 (Abhyanga)
    assert df.loc[2, "Start_Slot"] == df.loc[1, "End_Slot"]
