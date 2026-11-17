import argparse
import os
import pandas as pd
import matplotlib.pyplot as plt
from ayurschedule.database import init_db, fetch_clinic_state
from ayurschedule.optimizer import AyurSchedulerEngine

def main():
    parser = argparse.ArgumentParser(description="AyurSchedule — CLI & Benchmark Suite (CSE 578)")
    parser.add_argument("--date", type=str, default="2026-11-18", help="Target clinic date (YYYY-MM-DD)")
    parser.add_argument("--pop", type=int, default=80, help="GA Population size")
    parser.add_argument("--gen", type=int, default=120, help="GA Generations")
    parser.add_argument("--benchmark", action="store_true", help="Run full CSE 578 comparative benchmark")
    parser.add_argument("--out", type=str, default="optimized_schedule.csv", help="Output CSV path")
    args = parser.parse_args()

    init_db()
    state = fetch_clinic_state(args.date)
    engine = AyurSchedulerEngine(state, enforce_gender_match=True)

    if args.benchmark:
        print("=" * 75)
        print(f"AYURSCHEDULE CSE 578 BENCHMARK SUITE | Date: {args.date} | Sessions: {len(state['bookings'])}")
        print("=" * 75)
        r_greedy = engine.run_greedy_fcfs()
        r_sa = engine.run_simulated_annealing(seed=42)
        r_ga = engine.run_memetic_ga(pop_size=args.pop, generations=args.gen, use_memetic_ls=False, seed=42)
        r_memetic = engine.run_memetic_ga(pop_size=args.pop, generations=args.gen, use_memetic_ls=True, seed=42)

        summary = []
        for r in [r_greedy, r_sa, r_ga, r_memetic]:
            ev = r["evaluation"]
            summary.append({
                "Algorithm": r["algorithm"],
                "Total_Penalty": ev["total_penalty"],
                "Hard_Violations": ev["hard_violations"],
                "Soft_Violations": ev["soft_violations"],
                "Workload_StdDev": ev["workload_std"],
                "Runtime_ms": r["runtime_ms"]
            })
        bench_df = pd.DataFrame(summary)
        print(bench_df.to_string(index=False))
        bench_df.to_csv("benchmark_comparison.csv", index=False)

        # Save high-res convergence plot
        plt.figure(figsize=(8, 4.5))
        plt.plot([h["generation"] for h in r_ga["history"]], [h["best_penalty"] for h in r_ga["history"]], label="Standard GA", linestyle="--", linewidth=2)
        plt.plot([h["generation"] for h in r_memetic["history"]], [h["best_penalty"] for h in r_memetic["history"]], label="Memetic GA (Proposed)", linewidth=2.5, color="#1b5e20")
        plt.title("AyurSchedule Convergence: Standard GA vs. Hybrid Memetic GA")
        plt.xlabel("Generation")
        plt.ylabel("Total Penalty Cost")
        plt.legend()
        plt.grid(True, alpha=0.4)
        plt.tight_layout()
        plt.savefig("convergence_benchmark.png", dpi=200)
        print("\nSaved benchmark table to 'benchmark_comparison.csv' and plot to 'convergence_benchmark.png'.")
        res = r_memetic
    else:
        res = engine.run_memetic_ga(pop_size=args.pop, generations=args.gen, use_memetic_ls=True, seed=42)

    df = res["schedule_df"]
    df.to_csv(args.out, index=False)
    print(f"\n--- Optimized Schedule ({res['algorithm']}) ---")
    print(df[["Time_Window", "Patient_Name", "Gender", "Therapy_Code", "Target_Dosha", "Scheduled_Dosha", "Therapist", "Room", "Status"]].to_string(index=False))
    print(f"\nSaved schedule to '{args.out}'.")

if __name__ == "__main__":
    main()
