import random
import time
import copy
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Any

# 20 discrete 30-minute time slots: 08:00 to 18:00
TIME_SLOTS = [
    "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
    "16:00", "16:30", "17:00", "17:30", "18:00"
]

# Slot indices 8 and 9 (12:00-13:00) or 10 and 11 (13:00-14:00) can be lunch breaks
LUNCH_SLOTS = {10, 11}  # 13:00 to 14:00 reserved for clinic lunch/sanitization

def get_slot_dosha(slot_idx: int) -> str:
    """Circadian Ayurvedic Dosha Mapping:
    08:00-10:30 (slots 0..4) -> Kapha
    10:30-14:00 (slots 5..11) -> Pitta
    14:00-18:00 (slots 12..19) -> Vata
    """
    if slot_idx <= 4:
        return "Kapha"
    elif slot_idx <= 11:
        return "Pitta"
    return "Vata"

def format_time_window(start_slot: int, end_slot: int) -> str:
    s_str = TIME_SLOTS[max(0, min(start_slot, len(TIME_SLOTS)-1))]
    e_str = TIME_SLOTS[max(0, min(end_slot, len(TIME_SLOTS)-1))]
    return f"{s_str} - {e_str}"

class AyurSchedulerEngine:
    """
    Production Memetic Genetic Algorithm + Baselines (Greedy FCFS, Simulated Annealing)
    for Multi-Resource Ayurvedic Panchakarma Therapy Scheduling.
    """
    def __init__(self, state: Dict[str, Any], enforce_gender_match: bool = True):
        self.therapies = state["therapies"]
        self.therapists = state["therapists"]
        self.rooms = state["rooms"]
        self.bookings = state["bookings"]
        self.enforce_gender_match = enforce_gender_match
        self.num_slots = 20  # 08:00 to 18:00 in 30-min blocks

        # Map booking ID to index in self.bookings
        self.booking_id_to_idx = {b["id"]: idx for idx, b in enumerate(self.bookings)}

        # Precompute feasible (therapist_idx, room_idx, valid_start_slots) per booking
        self.feasible_domains = self._precompute_domains()

    def _precompute_domains(self) -> List[Dict[str, List[int]]]:
        domains = []
        for b in self.bookings:
            t_info = self.therapies[b["therapy_code"]]
            dur = t_info["duration_slots"]
            req_equip = t_info["required_equipment"]

            valid_therapists = []
            for t_idx, t in enumerate(self.therapists):
                gender_ok = (not self.enforce_gender_match) or (t["gender"] == b["patient_gender"])
                skill_ok = b["therapy_code"] in t["skills_set"]
                if gender_ok and skill_ok:
                    valid_therapists.append(t_idx)
            if not valid_therapists:
                valid_therapists = list(range(len(self.therapists)))

            valid_rooms = []
            for r_idx, r in enumerate(self.rooms):
                if req_equip in r["equipment_set"]:
                    valid_rooms.append(r_idx)
            if not valid_rooms:
                valid_rooms = list(range(len(self.rooms)))

            valid_slots = [
                s for s in range(self.num_slots - dur + 1)
                if not any((s + offset) in LUNCH_SLOTS for offset in range(dur))
            ]
            if not valid_slots:
                valid_slots = list(range(self.num_slots - dur + 1))

            domains.append({
                "therapists": valid_therapists,
                "rooms": valid_rooms,
                "slots": valid_slots,
                "duration": dur,
                "prep": t_info["prep_slots"]
            })
        return domains

    def random_chromosome(self, guided: bool = True) -> List[Tuple[int, int, int]]:
        """Creates a candidate chromosome: list of (therapist_idx, room_idx, start_slot)."""
        chrom = [None] * len(self.bookings)
        for idx, b in enumerate(self.bookings):
            dom = self.feasible_domains[idx]
            if guided:
                t_idx = random.choice(dom["therapists"])
                r_idx = random.choice(dom["rooms"])
                s_idx = random.choice(dom["slots"])
            else:
                t_idx = random.randint(0, len(self.therapists) - 1)
                r_idx = random.randint(0, len(self.rooms) - 1)
                s_idx = random.randint(0, self.num_slots - dom["duration"])
            chrom[idx] = (t_idx, r_idx, s_idx)

        # Enforce sequential chain alignment during initialization if guided
        if guided:
            self._repair_chains(chrom)
        return chrom

    def _repair_chains(self, chrom: List[Tuple[int, int, int]]) -> None:
        """Aligns Purvakarma -> Pradhanakarma sequential therapies (e.g. Abhyanga -> Swedana)."""
        for idx, b in enumerate(self.bookings):
            dep_id = b.get("depends_on_booking_id")
            if dep_id and dep_id in self.booking_id_to_idx:
                prev_idx = self.booking_id_to_idx[dep_id]
                prev_t, _, prev_s = chrom[prev_idx]
                prev_dur = self.feasible_domains[prev_idx]["duration"]
                cur_dur = self.feasible_domains[idx]["duration"]
                target_slot = prev_s + prev_dur
                if target_slot + cur_dur <= self.num_slots:
                    # Keep same therapist if skilled for follow-up therapy
                    cur_t = prev_t if prev_t in self.feasible_domains[idx]["therapists"] else chrom[idx][0]
                    chrom[idx] = (cur_t, chrom[idx][1], target_slot)

    def evaluate(self, chrom: List[Tuple[int, int, int]]) -> Dict[str, Any]:
        """
        Comprehensive Evaluation Function computing Hard & Soft Constraint Penalties
        and Detailed Violation Breakdown.
        """
        hard_violations = 0
        soft_violations = 0
        violation_notes = {i: [] for i in range(len(self.bookings))}

        therapist_busy = {}  # (t_idx, slot) -> booking_idx
        room_busy = {}       # (r_idx, slot) -> booking_idx
        patient_busy = {}    # (patient_id, slot) -> booking_idx
        therapist_slots_used = {t_idx: 0 for t_idx in range(len(self.therapists))}

        for idx, (t_idx, r_idx, s_idx) in enumerate(chrom):
            b = self.bookings[idx]
            t = self.therapists[t_idx]
            r = self.rooms[r_idx]
            t_info = self.therapies[b["therapy_code"]]
            dur = t_info["duration_slots"]
            prep = t_info["prep_slots"]
            end_slot = s_idx + dur

            # 1. Out of Clinic Hours or Shift Window
            if end_slot > self.num_slots or s_idx < t["shift_start_slot"] or end_slot > t["shift_end_slot"]:
                hard_violations += 1
                violation_notes[idx].append("ShiftBounds")

            # 2. Lunch Break Collision
            for slot in range(s_idx, end_slot):
                if slot in LUNCH_SLOTS:
                    hard_violations += 1
                    violation_notes[idx].append("LunchBreak")
                    break

            # 3. Gender Compatibility
            if self.enforce_gender_match and t["gender"] != b["patient_gender"]:
                hard_violations += 1
                violation_notes[idx].append("GenderMismatch")

            # 4. Skill Compatibility
            if b["therapy_code"] not in t["skills_set"]:
                hard_violations += 1
                violation_notes[idx].append("SkillMismatch")

            # 5. Room Equipment Compatibility
            if t_info["required_equipment"] not in r["equipment_set"]:
                hard_violations += 1
                violation_notes[idx].append("RoomEquipMismatch")

            # 6. Therapist Overlap (active treatment duration)
            for slot in range(s_idx, end_slot):
                if (t_idx, slot) in therapist_busy:
                    hard_violations += 1
                    violation_notes[idx].append("TherapistConflict")
                else:
                    therapist_busy[(t_idx, slot)] = idx

            # 7. Room Overlap (includes room sanitization/turnover prep_slots)
            for slot in range(s_idx, min(self.num_slots, end_slot + prep)):
                if (r_idx, slot) in room_busy:
                    hard_violations += 1
                    violation_notes[idx].append("RoomTurnoverConflict")
                else:
                    room_busy[(r_idx, slot)] = idx

            # 8. Patient Overlap
            for slot in range(s_idx, end_slot):
                if (b["patient_id"], slot) in patient_busy:
                    hard_violations += 1
                    violation_notes[idx].append("PatientConflict")
                else:
                    patient_busy[(b["patient_id"], slot)] = idx

            # 9. Sequential Therapy Precedence (e.g. Swedana immediately after Abhyanga)
            dep_id = b.get("depends_on_booking_id")
            if dep_id and dep_id in self.booking_id_to_idx:
                prev_idx = self.booking_id_to_idx[dep_id]
                prev_t, _, prev_s = chrom[prev_idx]
                prev_dur = self.feasible_domains[prev_idx]["duration"]
                expected_start = prev_s + prev_dur
                if s_idx != expected_start:
                    hard_violations += 1
                    violation_notes[idx].append("SequenceGap")
                # Soft preference: keep same therapist for continuous Purvakarma chain
                if t_idx != prev_t:
                    soft_violations += 1
                    violation_notes[idx].append("DiffChainTherapist")

            # 10. Soft Constraint: Dosha Circadian Alignment
            slot_dosha = get_slot_dosha(s_idx)
            if slot_dosha != b["preferred_dosha"]:
                soft_violations += 1
                violation_notes[idx].append(f"SuboptimalDosha({slot_dosha})")

            therapist_slots_used[t_idx] += dur

        # 11. Max Daily Therapist Workload Check + Workload Balance
        for t_idx, used in therapist_slots_used.items():
            if used > self.therapists[t_idx]["max_daily_slots"]:
                hard_violations += 1

        loads = list(therapist_slots_used.values())
        workload_std = float(np.std(loads)) if loads else 0.0

        total_penalty = (hard_violations * 1000.0) + (soft_violations * 15.0) + (workload_std * 20.0)
        fitness = 1.0 / (1.0 + total_penalty)

        return {
            "total_penalty": round(total_penalty, 2),
            "fitness": fitness,
            "hard_violations": hard_violations,
            "soft_violations": soft_violations,
            "workload_std": round(workload_std, 3),
            "violation_notes": violation_notes,
            "therapist_loads": therapist_slots_used
        }

    def local_search_refine(self, chrom: List[Tuple[int, int, int]], max_steps: int = 15) -> List[Tuple[int, int, int]]:
        """Memetic Hill-Climbing Operator: repairs hard conflicts and improves Dosha slot alignment."""
        best = chrom[:]
        best_eval = self.evaluate(best)
        if best_eval["total_penalty"] == 0:
            return best

        for _ in range(max_steps):
            # Target bookings that currently have violations
            violating_indices = [i for i, notes in best_eval["violation_notes"].items() if notes]
            if not violating_indices:
                break
            idx = random.choice(violating_indices)
            dom = self.feasible_domains[idx]
            candidate = best[:]
            candidate[idx] = (
                random.choice(dom["therapists"]),
                random.choice(dom["rooms"]),
                random.choice(dom["slots"])
            )
            self._repair_chains(candidate)
            cand_eval = self.evaluate(candidate)
            if cand_eval["total_penalty"] < best_eval["total_penalty"]:
                best = candidate
                best_eval = cand_eval
        return best

    def run_memetic_ga(
        self,
        pop_size: int = 80,
        generations: int = 120,
        crossover_rate: float = 0.85,
        mutation_rate: float = 0.18,
        elitism_count: int = 4,
        use_memetic_ls: bool = True,
        seed: int = 42
    ) -> Dict[str, Any]:
        random.seed(seed)
        np.random.seed(seed)
        t0 = time.time()

        population = [self.random_chromosome(guided=True) for _ in range(pop_size)]
        history = []

        for gen in range(generations):
            evals = [self.evaluate(c) for c in population]
            order = np.argsort([e["total_penalty"] for e in evals])
            population = [population[i] for i in order]
            evals = [evals[i] for i in order]

            history.append({
                "generation": gen,
                "best_penalty": evals[0]["total_penalty"],
                "avg_penalty": float(np.mean([e["total_penalty"] for e in evals])),
                "hard_violations": evals[0]["hard_violations"],
                "soft_violations": evals[0]["soft_violations"]
            })

            if evals[0]["total_penalty"] == 0:
                break

            new_pop = [copy.deepcopy(population[i]) for i in range(min(elitism_count, pop_size))]

            while len(new_pop) < pop_size:
                # Tournament Selection (k=3)
                idxs1 = random.sample(range(pop_size), min(3, pop_size))
                p1 = population[min(idxs1, key=lambda i: evals[i]["total_penalty"])]
                idxs2 = random.sample(range(pop_size), min(3, pop_size))
                p2 = population[min(idxs2, key=lambda i: evals[i]["total_penalty"])]

                # Two-Point Crossover
                if random.random() < crossover_rate and len(p1) > 2:
                    pt1, pt2 = sorted(random.sample(range(len(p1)), 2))
                    c1 = p1[:pt1] + p2[pt1:pt2] + p1[pt2:]
                    c2 = p2[:pt1] + p1[pt1:pt2] + p2[pt2:]
                else:
                    c1, c2 = p1[:], p2[:]

                # Domain-Guided Mutation
                for child in (c1, c2):
                    for i in range(len(child)):
                        if random.random() < mutation_rate:
                            dom = self.feasible_domains[i]
                            child[i] = (
                                random.choice(dom["therapists"]),
                                random.choice(dom["rooms"]),
                                random.choice(dom["slots"])
                            )
                    self._repair_chains(child)
                    if use_memetic_ls and random.random() < 0.25:
                        child = self.local_search_refine(child, max_steps=8)
                    if len(new_pop) < pop_size:
                        new_pop.append(child)

            population = new_pop

        best_chrom = population[0]
        final_eval = self.evaluate(best_chrom)
        elapsed_ms = round((time.time() - t0) * 1000.0, 2)

        return {
            "algorithm": "Memetic GA (Proposed)" if use_memetic_ls else "Standard GA",
            "chromosome": best_chrom,
            "evaluation": final_eval,
            "history": history,
            "runtime_ms": elapsed_ms,
            "schedule_df": self.chromosome_to_dataframe(best_chrom, final_eval)
        }

    def auto_tune_memetic_ga(self, candidates=None, max_runtime_seconds: float = 45.0) -> Dict[str, Any]:
        """Try several GA settings and return the best feasible schedule found."""
        if candidates is None:
            candidates = [
                {"pop_size": 60, "generations": 80, "crossover_rate": 0.75, "mutation_rate": 0.10, "seed": 42},
                {"pop_size": 80, "generations": 120, "crossover_rate": 0.85, "mutation_rate": 0.18, "seed": 73},
                {"pop_size": 100, "generations": 150, "crossover_rate": 0.95, "mutation_rate": 0.30, "seed": 101},
            ]
        if not candidates:
            raise ValueError("At least one parameter set is required.")

        started = time.time()
        trials = []
        best_result, best_parameters = None, None
        for parameters in candidates:
            if trials and time.time() - started >= max_runtime_seconds:
                break
            result = self.run_memetic_ga(**parameters, use_memetic_ls=True)
            evaluation = result["evaluation"]
            trials.append({**parameters, "total_penalty": evaluation["total_penalty"],
                           "hard_violations": evaluation["hard_violations"], "runtime_ms": result["runtime_ms"]})
            rank = (evaluation["hard_violations"], evaluation["total_penalty"])
            if best_result is None or rank < (best_result["evaluation"]["hard_violations"],
                                               best_result["evaluation"]["total_penalty"]):
                best_result, best_parameters = result, parameters.copy()
            if evaluation["total_penalty"] == 0:
                break

        best_result["runtime_ms"] = round((time.time() - started) * 1000.0, 2)
        best_result["auto_tuning"] = {"parameters": best_parameters, "trials": trials}
        return best_result

    def run_simulated_annealing(self, iterations: int = 2500, initial_temp: float = 500.0, cooling_rate: float = 0.995, seed: int = 42) -> Dict[str, Any]:
        random.seed(seed)
        np.random.seed(seed)
        t0 = time.time()

        current = self.random_chromosome(guided=True)
        current_eval = self.evaluate(current)
        best = current[:]
        best_eval = current_eval
        temp = initial_temp
        history = []

        for step in range(iterations):
            neighbor = current[:]
            idx = random.randint(0, len(neighbor) - 1)
            dom = self.feasible_domains[idx]
            neighbor[idx] = (
                random.choice(dom["therapists"]),
                random.choice(dom["rooms"]),
                random.choice(dom["slots"])
            )
            self._repair_chains(neighbor)
            neigh_eval = self.evaluate(neighbor)

            delta = neigh_eval["total_penalty"] - current_eval["total_penalty"]
            if delta < 0 or random.random() < np.exp(-delta / max(temp, 1e-6)):
                current = neighbor
                current_eval = neigh_eval
                if current_eval["total_penalty"] < best_eval["total_penalty"]:
                    best = current[:]
                    best_eval = current_eval

            temp *= cooling_rate
            if step % 25 == 0:
                history.append({
                    "generation": step // 25,
                    "best_penalty": best_eval["total_penalty"],
                    "hard_violations": best_eval["hard_violations"],
                    "soft_violations": best_eval["soft_violations"]
                })

        elapsed_ms = round((time.time() - t0) * 1000.0, 2)
        return {
            "algorithm": "Simulated Annealing (SA)",
            "chromosome": best,
            "evaluation": best_eval,
            "history": history,
            "runtime_ms": elapsed_ms,
            "schedule_df": self.chromosome_to_dataframe(best, best_eval)
        }

    def run_greedy_fcfs(self) -> Dict[str, Any]:
        """First-Come-First-Served (FCFS) constructive heuristic baseline."""
        t0 = time.time()
        chrom = [None] * len(self.bookings)
        used_therapist = set()
        used_room = set()
        used_patient = set()

        for idx, b in enumerate(self.bookings):
            dom = self.feasible_domains[idx]
            dur = dom["duration"]
            prep = dom["prep"]
            assigned = None

            # Check if chained after previous booking
            dep_id = b.get("depends_on_booking_id")
            preferred_start = None
            if dep_id and dep_id in self.booking_id_to_idx:
                prev_idx = self.booking_id_to_idx[dep_id]
                if chrom[prev_idx] is not None:
                    preferred_start = chrom[prev_idx][2] + self.feasible_domains[prev_idx]["duration"]

            slot_candidates = [preferred_start] if (preferred_start is not None and preferred_start in dom["slots"]) else dom["slots"]

            for s in slot_candidates:
                for t_idx in dom["therapists"]:
                    if any((t_idx, slot) in used_therapist for slot in range(s, s + dur)):
                        continue
                    for r_idx in dom["rooms"]:
                        if any((r_idx, slot) in used_room for slot in range(s, s + dur + prep)):
                            continue
                        if any((b["patient_id"], slot) in used_patient for slot in range(s, s + dur)):
                            continue
                        assigned = (t_idx, r_idx, s)
                        break
                    if assigned:
                        break
                if assigned:
                    break

            if not assigned:
                assigned = (dom["therapists"][0], dom["rooms"][0], dom["slots"][0])

            t_idx, r_idx, s = assigned
            for slot in range(s, s + dur):
                used_therapist.add((t_idx, slot))
                used_patient.add((b["patient_id"], slot))
            for slot in range(s, s + dur + prep):
                used_room.add((r_idx, slot))
            chrom[idx] = assigned

        ev = self.evaluate(chrom)
        elapsed_ms = round((time.time() - t0) * 1000.0, 2)
        return {
            "algorithm": "Greedy FCFS Heuristic",
            "chromosome": chrom,
            "evaluation": ev,
            "history": [{"generation": 0, "best_penalty": ev["total_penalty"], "hard_violations": ev["hard_violations"], "soft_violations": ev["soft_violations"]}],
            "runtime_ms": elapsed_ms,
            "schedule_df": self.chromosome_to_dataframe(chrom, ev)
        }

    def chromosome_to_dataframe(self, chrom: List[Tuple[int, int, int]], evaluation: Dict[str, Any]) -> pd.DataFrame:
        rows = []
        notes_map = evaluation["violation_notes"]
        for idx, (t_idx, r_idx, s_idx) in enumerate(chrom):
            b = self.bookings[idx]
            t = self.therapists[t_idx]
            r = self.rooms[r_idx]
            t_info = self.therapies[b["therapy_code"]]
            dur = t_info["duration_slots"]
            end_slot = s_idx + dur
            slot_dosha = get_slot_dosha(s_idx)
            flags = notes_map.get(idx, [])
            status_str = "OPTIMAL" if not flags else ", ".join(flags)

            rows.append({
                "Booking_ID": b["id"],
                "Patient_ID": b["patient_id"],
                "Patient_Name": b["patient_name"],
                "Gender": b["patient_gender"],
                "Therapy_Code": b["therapy_code"],
                "Therapy_Name": t_info["name"],
                "Priority": b["priority"],
                "Target_Dosha": b["preferred_dosha"],
                "Scheduled_Dosha": slot_dosha,
                "Therapist_ID": t["id"],
                "Therapist": t["name"],
                "Room_ID": r["id"],
                "Room": r["name"],
                "Start_Slot": s_idx,
                "End_Slot": end_slot,
                "Time_Window": format_time_window(s_idx, end_slot),
                "Duration_Mins": dur * 30,
                "Cleanup_Mins": t_info["prep_slots"] * 30,
                "Status": status_str
            })
        df = pd.DataFrame(rows).sort_values(by=["Start_Slot", "Room", "Patient_ID"]).reset_index(drop=True)
        return df
