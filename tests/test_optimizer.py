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


def test_auto_tuning_prefers_feasible_low_penalty_result(monkeypatch):
    engine = object.__new__(AyurSchedulerEngine)
    penalties = [(1, 100.0), (0, 200.0), (0, 150.0)]

    def candidate_result(**parameters):
        hard, penalty = penalties.pop(0)
        return {"evaluation": {"hard_violations": hard, "total_penalty": penalty},
                "runtime_ms": 1.0, "seed": parameters["seed"]}

    monkeypatch.setattr(engine, "run_memetic_ga", candidate_result)
    candidates = [{"seed": seed} for seed in (42, 73, 101)]
    result = engine.auto_tune_memetic_ga(candidates=candidates)
    assert result["seed"] == 101
    assert result["auto_tuning"]["parameters"] == {"seed": 101}
    assert len(result["auto_tuning"]["trials"]) == 3
