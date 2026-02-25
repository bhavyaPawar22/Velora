import math
import random
import time
import numpy as np
from typing import List, Dict, Tuple, Any
from .models import Solution, VehicleSchedule, Trip, Location
from .initial_solution import InitialSolutionBuilder


# =============================================================================
# PROBLEM STATE (imported from main.py, defined here for module independence)
# =============================================================================

class ProblemState:
    """Minimal ProblemState for ALNS engine - full version in main.py"""
    def __init__(self, employees, vehicles, office, metadata=None):
        from constraints import TripConstraints
        self.employees = {e.id: e for e in employees}
        self.vehicles = {v.id: v for v in vehicles}
        self.emp_list = employees
        self.veh_list = vehicles
        self.office = office
        self.constraints = TripConstraints(self.employees, self.vehicles, office)
        self.total_employees = len(employees)
        
        self.metadata = metadata if metadata else {}
        self.alpha = self.metadata.get('alpha', 0.7)
        self.beta = self.metadata.get('beta', 0.3)
        self.sum_baseline_cost = self.metadata.get('sum_baseline_cost', 1.0)
        self.sum_baseline_time = self.metadata.get('sum_baseline_time', 1.0)
    
    def solution_cost(self, sol: Solution) -> Tuple[float, Dict]:
        """Calculate weighted objective: alpha * cost + beta * time"""
        total_cost = 0.0
        total_dist = 0.0
        total_time = 0.0
        num_assigned = 0
        num_vehicles = 0
        num_trips = 0
        assigned = {emp: False for emp in self.employees}

        for schedule in sol.schedules:
            if schedule.trips:
                num_vehicles += 1
            for trip in schedule.trips:
                num_trips += 1
                for emp in trip.employees:
                    assigned[emp] = True
                    num_assigned += 1
                total_dist += trip.distance_km
                total_cost += trip.distance_km * schedule.vehicle.cost_per_km
                total_time += trip.arrival_at_office - trip.start_time
        
        for emp in self.employees:
            if not assigned[emp]:
                total_cost += self.employees[emp].baseline_cost
                total_time += self.employees[emp].baseline_time

        objective = self.alpha * total_cost / self.sum_baseline_cost + self.beta * total_time / self.sum_baseline_time
        
        return objective, {
            'objective': objective,
            'travel_cost': total_cost,
            'total_distance': total_dist,
            'total_time': total_time,
            'vehicles_used': num_vehicles,
            'total_trips': num_trips,
            'served': num_assigned
        }


# =============================================================================
# DESTROY / REPAIR OPERATORS
# =============================================================================

class DestroyOperators:
    def __init__(self, state: ProblemState):
        self.state = state
        # We need a builder instance to access the update logic
        self.builder = InitialSolutionBuilder(state)
    
    def random_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        s = sol.copy()
        assigned = s.all_assigned()
        q = min(q, len(assigned))
        if q == 0:
            return s, []
        
        removed = random.sample(assigned, q)
        for eid in removed:
            self._remove(s, eid)
        return s, removed
    
    def worst_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        s = sol.copy()
        removed = []
        
        for _ in range(q):
            assigned = s.all_assigned()
            if not assigned:
                break
            
            worst = max(assigned, key=lambda eid: random.uniform(0.8, 1.2))
            self._remove(s, worst)
            removed.append(worst)
        
        return s, removed
    
    def _remove(self, sol: Solution, eid: str):
        for schedule in sol.schedules:
            for trip_idx, trip in enumerate(schedule.trips):
                if eid in trip.employees:
                    trip.employees.remove(eid)
                    if eid in trip.pickup_sequence:
                        trip.pickup_sequence.remove(eid)
                    
                    # If trip is now empty, remove it; otherwise, update it
                    if not trip.employees:
                        schedule.trips.pop(trip_idx)
                    else:
                        # Recalculate this trip's timing
                        feasible, details = self.state.constraints.check_time_constraint(
                            schedule.vehicle, trip.employees, trip.pickup_sequence,
                            trip.start_time, trip.start_location
                        )
                        if feasible:
                            trip.arrival_at_office = details['arrival_at_office']
                            trip.pickup_times = details.get('pickup_times', {})
                            trip.distance_km = details.get('total_distance', 0)
                            trip.route = details.get('route', [])  # Added to match trail2.py
                    
                    # RIPPLE EFFECT: Shift all subsequent trips earlier
                    self.builder._update_subsequent_trips(schedule, trip_idx)
                    return

    def trip_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        s = sol.copy()
        all_trips = [(sched, idx, trip) for sched in s.schedules 
                     for idx, trip in enumerate(sched.trips) if trip.employees]
        
        if not all_trips:
            return s, []
        
        sched, idx, trip = random.choice(all_trips)
        removed = trip.employees.copy()
        
        # Remove the trip entirely
        sched.trips.pop(idx)
        
        # RIPPLE EFFECT: Subsequent trips can now start much earlier
        self.builder._update_subsequent_trips(sched, idx)
        
        return s, removed


class RepairOperators:
    def __init__(self, state: ProblemState):
        self.state = state
        self.builder = InitialSolutionBuilder(state)
    
    def greedy_insertion(self, sol: Solution, removed: List[str]) -> Solution:
        s = sol.copy()
        
        # Sort by priority and constraint tightness
        sorted_removed = sorted(
            removed,
            key=lambda eid: (
                self.state.employees[eid].priority,
                self.state.employees[eid].adjusted_latest_drop
            )
        )
        
        for eid in sorted_removed:
            self.builder._assign_to_best_trip(s, eid)
        
        return s
    
    def regret_insertion(self, sol: Solution, removed: List[str], k: int = 2) -> Solution:
        return self.greedy_insertion(sol, removed)


# =============================================================================
# ALNS
# =============================================================================

class ALNSConfig:
    def __init__(self):
        self.q_min = 1
        self.q_max = 4
        self.max_iter = 2000
        self.max_no_improve = 400
        self.temp_start = 0.05
        self.cooling = 0.9995
        # NEW: Number of probabilistic initial attempts
        self.num_runs = 25


class ALNS:
    def __init__(self, state: ProblemState, config: ALNSConfig = None):
        self.state = state
        self.cfg = config or ALNSConfig()
        
        self.destroy = DestroyOperators(state)
        self.repair = RepairOperators(state)
        
        self.destroy_ops = [
            self.destroy.random_removal,
            self.destroy.worst_removal,
            self.destroy.trip_removal
        ]
        
        # Lambda used to pass k=2 to regret insertion
        self.repair_ops = [
            self.repair.greedy_insertion,
            lambda s, r: self.repair.regret_insertion(s, r, 2)
        ]
        
        # Global best across ALL runs
        self.global_best_sol = None
        self.global_best_cost = float('inf')
        self.global_best_breakdown = {}

    def solve(self, verbose=True) -> Tuple[Solution, Dict]:
        t0_total = time.time()
        
        if verbose:
            print(f"Starting Multi-Start ALNS ({self.cfg.num_runs} runs)...")
            print(f"{'Run':<5} | {'Init Cost':<10} | {'Final Cost':<10} | {'Assigned':<10} | {'Improv %':<10} | {'Status'}")
            print("-" * 65)

        # Initialize Global Best with a dummy empty solution
        self.global_best_sol = None
        self.global_best_cost = float('inf')
        self.global_best_breakdown = {}

        for run_idx in range(self.cfg.num_runs):
            t0_run = time.time()
            
            # 1. Build Probabilistic Initial Solution
            builder = InitialSolutionBuilder(self.state)
            current_sol = builder.build()
            
            initial_cost, _ = self.state.solution_cost(current_sol)
            
            # Local best for this specific run
            run_best_sol = current_sol.copy()
            run_best_cost = initial_cost
            
            # 2. Run ALNS Optimization for this run
            # Note: We pass copies to avoid reference issues
            final_sol, run_best_sol, run_best_cost = self._run_alns_loop(
                current_sol, run_best_sol, run_best_cost
            )
            
            # 3. Check against Global Best
            run_assigned = len(run_best_sol.all_assigned())
            
            if self.global_best_sol is None:
                global_assigned = 0
            else:
                global_assigned = len(self.global_best_sol.all_assigned())
            
            is_new_global_best = False
            
            # PRIORITY 1: Assign MORE employees
            if run_assigned > global_assigned:
                is_new_global_best = True
            
            # PRIORITY 2: Same employees, LOWER cost
            elif run_assigned == global_assigned:
                if run_best_cost < self.global_best_cost:
                    is_new_global_best = True
            
            status = ""
            if is_new_global_best:
                self.global_best_sol = run_best_sol.copy()
                self.global_best_cost = run_best_cost
                _, self.global_best_breakdown = self.state.solution_cost(run_best_sol)
                status = "🏆 NEW BEST"

            if verbose:
                improv_pct = ((initial_cost - run_best_cost) / initial_cost * 100) if initial_cost > 0 else 0.0
                print(f"{run_idx+1:<5} | {initial_cost:<10.5f} | {run_best_cost:<10.5f} | {run_assigned:<10} | {improv_pct:<9.1f}% | {status}")

        elapsed_total = time.time() - t0_total
        self.global_best_breakdown['time_sec'] = elapsed_total
        self.global_best_breakdown['total_runs'] = self.cfg.num_runs
        
        if verbose:
            print("-" * 65)
            print(f"Total Optimization Time: {elapsed_total:.2f}s")
            if self.global_best_sol:
                final_assigned = len(self.global_best_sol.all_assigned())
                print(f"Final Best Cost: {self.global_best_cost:.2f} (Assigned: {final_assigned}/{self.state.total_employees})")
        
        return self.global_best_sol, self.global_best_breakdown

    def _run_alns_loop(self, current_sol: Solution, best_sol: Solution, best_cost: float):
        """
        Internal method to run one complete ALNS cycle on a given solution.
        Returns: (final_current_sol, best_found_sol, best_found_cost)
        """
        current = current_sol
        curr_cost = best_cost
        
        temp = self.cfg.temp_start * curr_cost if curr_cost > 0 else 100
        
        iteration = 0
        no_improve = 0
        
        while iteration < self.cfg.max_iter and no_improve < self.cfg.max_no_improve:
            iteration += 1
            
            # Select Operators
            d_op = random.choice(self.destroy_ops)
            r_op = random.choice(self.repair_ops)
            
            # Determine removal size q
            n_assigned = len(current.all_assigned())
            if n_assigned == 0:
                # If empty, reset to best and continue
                current = best_sol.copy()
                curr_cost = best_cost
                no_improve += 1
                continue
                
            q = random.randint(self.cfg.q_min, min(self.cfg.q_max, n_assigned))
            
            # Execute Destroy & Repair
            partial, removed = d_op(current, q)
            new_sol = r_op(partial, removed)
            new_cost, _ = self.state.solution_cost(new_sol)
            
            # Calculate Acceptance Criteria
            new_assigned = len(new_sol.all_assigned())
            curr_assigned = len(current.all_assigned())
            best_assigned = len(best_sol.all_assigned())
            
            accept = False
            
            # Priority 1: maximize assigned employees
            if new_assigned > curr_assigned:
                accept = True
            elif new_assigned == curr_assigned:
                # Priority 2: minimize cost (Simulated Annealing)
                if new_cost < best_cost:
                    accept = True
                elif new_cost < curr_cost:
                    accept = True
                else:
                    # SA Probability
                    prob = math.exp(-(new_cost - curr_cost) / max(temp, 0.01))
                    if random.random() < prob:
                        accept = True
            
            # Update State
            if accept:
                current = new_sol
                curr_cost = new_cost
                
                # Check if this is a new local best for this run
                if new_assigned > best_assigned:
                    best_sol = new_sol.copy()
                    best_cost = new_cost
                    no_improve = 0
                elif new_assigned == best_assigned and new_cost < best_cost:
                    best_sol = new_sol.copy()
                    best_cost = new_cost
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1
            
            temp *= self.cfg.cooling
            
        return current, best_sol, best_cost