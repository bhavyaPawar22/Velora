import random
import time
from typing import List, Dict, Tuple, Optional, Set, Any
from .models import Employee, Vehicle, Solution, Trip, VehicleSchedule, Location
from .constraints import TripConstraints
from dataclasses import dataclass, field

# =============================================================================
# CONSTRAINT-FIRST INITIAL SOLUTION BUILDER
# =============================================================================


@dataclass
class SavingsEntry:
    """Represents savings from merging two employees"""
    employee_i: str
    employee_j: str
    cost_savings: float      # ΔC = individual_cost_i + individual_cost_j - combined_cost
    time_savings: float      # ΔT = individual_time_i + individual_time_j - combined_time
    weighted_savings: float  # S_ij = α·ΔC + β·ΔT
    compatible_vehicles: Set[str]
    best_vehicle: str = None
    best_sequence: List[str] = None


class InitialSolutionBuilder:
    """
    Probabilistic Constraint-First Solution Builder.
    Uses randomization in Savings calculation and Regret selection (GRASP)
    to generate diverse starting solutions.
    """
    
    def __init__(self, state, config: Dict = None):
        self.state = state
        self.constraints = state.constraints
        self.employees = state.employees
        self.vehicles = {v.id: v for v in state.veh_list}
        self.office = state.office
        
        # Get alpha/beta from state
        self.alpha = getattr(state, 'alpha', 1.0)
        self.beta = getattr(state, 'beta', 0.0)
        
        # Configuration
        self.config = config or {}
        self.max_trips_per_vehicle = self.config.get('max_trips', 10)
        self.top_k_seeds = self.config.get('top_k_seeds', None)
        
        # PROBABILISTIC PARAMETERS
        # Factor 0.0 = Deterministic, 0.2 = +/- 20% noise
        self.noise_factor = self.config.get('noise_factor', 0.1) 
        # Number of candidates to consider in Regret Insertion (RCL size)
        self.rcl_size = self.config.get('rcl_size', 3) 
        
        # Precomputed data
        self._savings_list: List[SavingsEntry] = []
        self._individual_costs: Dict[str, Dict[str, float]] = {}
        self._individual_times: Dict[str, Dict[str, float]] = {}
        self._compatible_vehicles: Dict[str, Set[str]] = {}
        
    def build(self) -> Solution:
        """Main entry point - builds solution using Probabilistic PWSA."""
        # Phase I: Savings Calculation (with Noise)
        self._phase1_savings_calculation()
        
        # Phase II: Hybrid Construction (with Restricted Candidate List)
        solution = self._phase2_hybrid_construction()
        
        # Phase III: Multi-Trip Consolidation
        solution = self._phase3_consolidation(solution)
        
        # Final validation
        solution = self._final_validation(solution)
        
        return solution
    
    # =========================================================================
    # PHASE I: SAVINGS CALCULATION (Now with Noise)
    # =========================================================================
    
    def _phase1_savings_calculation(self):
        """
        Phase I: Calculate savings for all pairs.
        S_ij = (α·ΔC + β·ΔT) * Random_Noise
        """
        self._compute_individual_metrics()
        
        self._savings_list = []
        emp_ids = [e.id for e in self.state.emp_list]
        n = len(emp_ids)
        
        for i in range(n):
            for j in range(i + 1, n):
                emp_i, emp_j = emp_ids[i], emp_ids[j]
                
                common_vehicles = (
                    self._compatible_vehicles.get(emp_i, set()) &
                    self._compatible_vehicles.get(emp_j, set())
                )
                
                if not common_vehicles:
                    continue
                
                entry = self._compute_pair_savings(emp_i, emp_j, common_vehicles)
                
                if entry and entry.weighted_savings > 0:
                    self._savings_list.append(entry)
        
        # Sort desc. Noise in calculation ensures diverse sorting order.
        self._savings_list.sort(key=lambda x: x.weighted_savings, reverse=True)

    def _compute_individual_metrics(self):
        """Compute individual cost and time for each employee with each vehicle."""
        for emp in self.state.emp_list:
            self._individual_costs[emp.id] = {}
            self._individual_times[emp.id] = {}
            self._compatible_vehicles[emp.id] = set()
            
            for vehicle in self.state.veh_list:
                if vehicle.category.lower() not in emp.allowed_vehicle_types:
                    continue
                
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [emp.id])
                if not cap_ok:
                    continue
                
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, [emp.id], [emp.id],
                    vehicle.available_from, vehicle.start_location
                )
                
                if time_ok:
                    cost = details['total_distance'] * vehicle.cost_per_km
                    time_val = details['arrival_at_office'] - vehicle.available_from
                    
                    self._individual_costs[emp.id][vehicle.id] = cost
                    self._individual_times[emp.id][vehicle.id] = time_val
                    self._compatible_vehicles[emp.id].add(vehicle.id)

    def _compute_pair_savings(self, emp_i: str, emp_j: str, 
                              common_vehicles: Set[str]) -> Optional[SavingsEntry]:
        best_cost_savings = float('-inf')
        best_time_savings = float('-inf')
        best_weighted = float('-inf')
        best_vehicle = None
        best_sequence = None
        feasible_vehicles = set()
        
        for vid in common_vehicles:
            vehicle = self.vehicles[vid]
            
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [emp_i, emp_j])
            if not cap_ok:
                continue
            
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [emp_i, emp_j])
            if not type_ok:
                continue
            
            best_seq = self.constraints.find_best_sequence(
                vehicle, [emp_i, emp_j],
                vehicle.available_from, vehicle.start_location
            )
            
            if not best_seq:
                continue
            
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [emp_i, emp_j], best_seq,
                vehicle.available_from, vehicle.start_location
            )
            
            if not time_ok:
                continue
            
            feasible_vehicles.add(vid)
            
            combined_cost = details['total_distance'] * vehicle.cost_per_km
            combined_time = details['arrival_at_office'] - vehicle.available_from
            
            cost_i = self._individual_costs.get(emp_i, {}).get(vid, float('inf'))
            cost_j = self._individual_costs.get(emp_j, {}).get(vid, float('inf'))
            time_i = self._individual_times.get(emp_i, {}).get(vid, float('inf'))
            time_j = self._individual_times.get(emp_j, {}).get(vid, float('inf'))
            
            if cost_i == float('inf') or cost_j == float('inf'):
                continue
            
            delta_c = cost_i + cost_j - combined_cost
            delta_t = time_i + time_j - combined_time
            s_ij = self.alpha * delta_c + self.beta * delta_t
            
            # --- MODIFICATION: APPLY RANDOM NOISE HERE ---
            # Randomize savings by +/- noise_factor (e.g., +/- 10%)
            noise = random.uniform(1.0 - self.noise_factor, 1.0 + self.noise_factor)
            s_ij *= noise
            # ---------------------------------------------
            
            if s_ij > best_weighted:
                best_cost_savings = delta_c
                best_time_savings = delta_t
                best_weighted = s_ij
                best_vehicle = vid
                best_sequence = best_seq
        
        if best_weighted <= 0 or not feasible_vehicles:
            return None
        
        return SavingsEntry(
            employee_i=emp_i, employee_j=emp_j,
            cost_savings=best_cost_savings, time_savings=best_time_savings,
            weighted_savings=best_weighted,
            compatible_vehicles=feasible_vehicles,
            best_vehicle=best_vehicle, best_sequence=best_sequence
        )

    # =========================================================================
    # PHASE II: HYBRID CONSTRUCTION (Now with GRASP / Restricted Candidate List)
    # =========================================================================
    
    def _phase2_hybrid_construction(self) -> Solution:
        solution = Solution()
        for v in self.state.veh_list:
            solution.schedules.append(VehicleSchedule(vehicle=v))
        
        assigned = set()
        
        # Step 1: Seed Routes (Savings list is already randomized via noise)
        assigned = self._step1_seed_routes(solution, assigned)
        
        # Step 2: Regret-k Insertion (Using RCL)
        assigned = self._step2_regret_insertion(solution, assigned)
        
        return solution
    
    def _step1_seed_routes(self, solution: Solution, assigned: Set[str]) -> Set[str]:
        if self.top_k_seeds is None:
            k = max(1, len(self.state.emp_list) // 4)
        else:
            k = self.top_k_seeds
        
        seeds_created = 0
        
        for entry in self._savings_list:
            if seeds_created >= k:
                break
            
            emp_i, emp_j = entry.employee_i, entry.employee_j
            if emp_i in assigned or emp_j in assigned:
                continue
            
            if not self._can_create_route(solution, [emp_i, emp_j], entry.best_vehicle):
                continue
            
            if self._create_trip(solution, [emp_i, emp_j], entry.best_vehicle, entry.best_sequence):
                assigned.add(emp_i)
                assigned.add(emp_j)
                seeds_created += 1
        
        return assigned

    def _step2_regret_insertion(self, solution: Solution, assigned: Set[str]) -> Set[str]:
        """
        Step 2: Regret-k Insertion using Restricted Candidate List (RCL).
        Instead of picking best regret, pick from top N best regrets.
        """
        unassigned = [e.id for e in self.state.emp_list if e.id not in assigned]
        
        while unassigned:
            candidates = []  # List of (regret, eid, best_insertion_tuple)
            
            # Calculate regret for ALL unassigned employees
            for eid in unassigned:
                insertion_options = self._get_insertion_options(solution, eid)
                
                if not insertion_options:
                    continue
                
                insertion_options.sort(key=lambda x: x[0])
                
                c1 = insertion_options[0][0]
                c2 = insertion_options[1][0] if len(insertion_options) >= 2 else float('inf')
                regret = c2 - c1
                
                candidates.append((regret, eid, insertion_options[0]))
            
            # If no candidates found, fallback to individual trips
            if not candidates:
                for eid in unassigned.copy():
                    if self._assign_to_new_trip(solution, eid):
                        assigned.add(eid)
                        unassigned.remove(eid)
                break
            
            # Sort candidates by Regret (Descending)
            candidates.sort(key=lambda x: x[0], reverse=True)
            
            # --- MODIFICATION: RCL SELECTION ---
            # Pick randomly from the top K candidates
            rcl_limit = min(len(candidates), self.rcl_size)
            selected_candidate = random.choice(candidates[:rcl_limit])
            # -----------------------------------
            
            best_regret, best_emp, best_insertion = selected_candidate
            
            # Insert selected employee
            obj, sched_idx, trip_idx, is_new, sequence = best_insertion
            
            success = False
            if is_new:
                success = self._create_trip_for_employee(solution, best_emp, sched_idx)
            else:
                success = self._insert_into_trip(solution, best_emp, sched_idx, trip_idx, sequence)
            
            if success:
                assigned.add(best_emp)
                unassigned.remove(best_emp)
            else:
                # Should not happen if logic is correct, but safety valve
                unassigned.remove(best_emp)  # Remove to avoid infinite loop
        
        return assigned

    def _get_insertion_options(self, solution: Solution, eid: str) -> List[Tuple]:
        """Get all feasible insertion options for an employee."""
        options = []
        emp = self.employees[eid]
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            # Try existing trips
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok:
                    continue
                
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok:
                    continue
                
                best_seq = self.constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                if not best_seq:
                    continue
                
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                
                if time_ok:
                    objective = self._compute_objective(details, vehicle)
                    options.append((objective, sched_idx, trip_idx, False, best_seq))
            
            # Try new trip
            start_time, start_loc = self._get_next_trip_start(schedule)
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            if not cap_ok:
                continue
            
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok:
                objective = self._compute_objective(details, vehicle)
                objective *= 1.05  # Penalty for new trip
                options.append((objective, sched_idx, -1, True, [eid]))
                
        return options

    def _compute_objective(self, details: Dict, vehicle) -> float:
        total_distance = details.get('total_distance', 0)
        start_time = details.get('start_time', 0)
        arrival_at_office = details.get('arrival_at_office', start_time)
        cost = total_distance * vehicle.cost_per_km
        time_val = arrival_at_office - start_time
        return self.alpha * cost + self.beta * time_val

    def _get_next_trip_start(self, schedule: VehicleSchedule) -> Tuple[float, Location]:
        if schedule.trips:
            last_trip = schedule.trips[-1]
            start_time = last_trip.arrival_at_office + self.constraints.DROP_TIME
            start_loc = self.office
        else:
            start_time = schedule.vehicle.available_from
            start_loc = schedule.vehicle.start_location
        return start_time, start_loc

    def _can_create_route(self, solution: Solution, emp_ids: List[str], vid: str) -> bool:
        vehicle = self.vehicles[vid]
        schedule = next((s for s in solution.schedules if s.vehicle.id == vid), None)
        if not schedule:
            return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, emp_ids)
        if not cap_ok:
            return False
        
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, emp_ids)
        if not type_ok:
            return False
        
        best_seq = self.constraints.find_best_sequence(vehicle, emp_ids, start_time, start_loc)
        if not best_seq:
            return False
        
        time_ok, _ = self.constraints.check_time_constraint(
            vehicle, emp_ids, best_seq, start_time, start_loc
        )
        return time_ok

    def _create_trip(self, solution: Solution, emp_ids: List[str], 
                     vid: str, sequence: List[str] = None) -> bool:
        vehicle = self.vehicles[vid]
        schedule = next((s for s in solution.schedules if s.vehicle.id == vid), None)
        if not schedule:
            return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        if not sequence:
            sequence = self.constraints.find_best_sequence(
                vehicle, emp_ids, start_time, start_loc
            )
        if not sequence:
            return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, emp_ids, sequence, start_time, start_loc
        )
        
        if not time_ok or 'arrival_at_office' not in details:
            return False
        
        trip = Trip(
            vehicle_id=vid,
            employees=emp_ids.copy(),
            pickup_sequence=sequence.copy(),
            start_time=start_time,
            start_location=start_loc,
            arrival_at_office=details['arrival_at_office'],
            pickup_times=details.get('pickup_times', {}),
            distance_km=details.get('total_distance', 0),
            route=details.get('route', [])  # Added to match trail2.py
        )
        
        schedule.trips.append(trip)
        return True

    def _create_trip_for_employee(self, solution: Solution, eid: str, sched_idx: int) -> bool:
        schedule = solution.schedules[sched_idx]
        vehicle = schedule.vehicle
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
        if not (cap_ok and type_ok):
            return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, [eid], [eid], start_time, start_loc
        )
        if not time_ok or 'arrival_at_office' not in details:
            return False
        
        trip = Trip(
            vehicle_id=vehicle.id,
            employees=[eid],
            pickup_sequence=[eid],
            start_time=start_time,
            start_location=start_loc,
            arrival_at_office=details['arrival_at_office'],
            pickup_times=details.get('pickup_times', {}),
            distance_km=details.get('total_distance', 0),
            route=details.get('route', [])  # Added to match trail2.py
        )
        schedule.trips.append(trip)
        return True

    def _insert_into_trip(self, solution: Solution, eid: str, 
                          sched_idx: int, trip_idx: int, sequence: List[str]) -> bool:
        schedule = solution.schedules[sched_idx]
        trip = schedule.trips[trip_idx]
        vehicle = schedule.vehicle
        new_emps = trip.employees + [eid]
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, new_emps)
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, new_emps)
        if not (cap_ok and type_ok):
            return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, new_emps, sequence, trip.start_time, trip.start_location
        )
        if not time_ok or 'arrival_at_office' not in details:
            return False
        
        trip.employees = new_emps
        trip.pickup_sequence = sequence
        trip.arrival_at_office = details['arrival_at_office']
        trip.pickup_times = details.get('pickup_times', {})
        trip.distance_km = details.get('total_distance', 0)
        trip.route = details.get('route', [])  # Added to match trail2.py
        
        if trip_idx + 1 < len(schedule.trips):
            self._update_subsequent_trips(schedule, trip_idx + 1)
        
        return True

    def _assign_to_new_trip(self, solution: Solution, eid: str) -> bool:
        emp = self.employees[eid]
        best_objective = float('inf')
        best_sched_idx = None
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            start_time, start_loc = self._get_next_trip_start(schedule)
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
            if not (cap_ok and type_ok):
                continue
            
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok:
                objective = self._compute_objective(details, vehicle)
                if objective < best_objective:
                    best_objective = objective
                    best_sched_idx = sched_idx
        
        if best_sched_idx is not None:
            return self._create_trip_for_employee(solution, eid, best_sched_idx)
        return False

    def _update_subsequent_trips(self, schedule: VehicleSchedule, start_idx: int):
        for i in range(start_idx, len(schedule.trips)):
            if i == 0:
                prev_end = schedule.vehicle.available_from
            else:
                prev_end = schedule.trips[i - 1].arrival_at_office + self.constraints.DROP_TIME
            
            trip = schedule.trips[i]
            trip.start_time = prev_end
            trip.start_location = self.office if i > 0 else schedule.vehicle.start_location
            
            time_ok, details = self.constraints.check_time_constraint(
                schedule.vehicle, trip.employees, trip.pickup_sequence,
                trip.start_time, trip.start_location
            )
            
            if 'arrival_at_office' in details:
                trip.arrival_at_office = details['arrival_at_office']
            if 'pickup_times' in details:
                trip.pickup_times = details['pickup_times']
            if 'total_distance' in details:
                trip.distance_km = details['total_distance']
            if 'route' in details:
                trip.route = details['route']
    
    # =========================================================================
    # PHASE III: MULTI-TRIP CONSOLIDATION
    # =========================================================================
    
    def _phase3_consolidation(self, solution: Solution) -> Solution:
        """Try to merge consecutive trips on same vehicle."""
        improved = True
        max_iterations = 50
        iteration = 0
        
        while improved and iteration < max_iterations:
            improved = False
            iteration += 1
            
            for schedule in solution.schedules:
                if len(schedule.trips) < 2:
                    continue
                i = 0
                while i < len(schedule.trips) - 1:
                    trip_i = schedule.trips[i]
                    trip_j = schedule.trips[i + 1]
                    if self._can_merge_trips(schedule, trip_i, trip_j):
                        self._merge_trips(schedule, i)
                        improved = True
                    else:
                        i += 1
        return solution

    def _can_merge_trips(self, schedule: VehicleSchedule, trip_i: Trip, trip_j: Trip) -> bool:
        vehicle = schedule.vehicle
        combined_emps = trip_i.employees + trip_j.employees
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, combined_emps)
        if not cap_ok:
            return False
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, combined_emps)
        if not type_ok:
            return False
        best_seq = self.constraints.find_best_sequence(
            vehicle, combined_emps, trip_i.start_time, trip_i.start_location
        )
        if not best_seq:
            return False
        time_ok, _ = self.constraints.check_time_constraint(
            vehicle, combined_emps, best_seq, trip_i.start_time, trip_i.start_location
        )
        return time_ok

    def _merge_trips(self, schedule: VehicleSchedule, trip_idx: int):
        trip_i = schedule.trips[trip_idx]
        trip_j = schedule.trips[trip_idx + 1]
        vehicle = schedule.vehicle
        combined_emps = trip_i.employees + trip_j.employees
        best_seq = self.constraints.find_best_sequence(
            vehicle, combined_emps, trip_i.start_time, trip_i.start_location
        )
        _, details = self.constraints.check_time_constraint(
            vehicle, combined_emps, best_seq, trip_i.start_time, trip_i.start_location
        )
        trip_i.employees = combined_emps
        trip_i.pickup_sequence = best_seq
        trip_i.arrival_at_office = details['arrival_at_office']
        trip_i.pickup_times = details['pickup_times']
        trip_i.distance_km = details['total_distance']
        trip_i.route = details.get('route', [])  # Added to match trail2.py
        schedule.trips.remove(trip_j)
        self._update_subsequent_trips(schedule, trip_idx + 1)

    # =========================================================================
    # FINAL VALIDATION
    # =========================================================================
    
    def _final_validation(self, solution: Solution) -> Solution:
        """Final validation pass - ensure all constraints are satisfied."""
        removed_employees = []
        for schedule in solution.schedules:
            valid_trips = []
            current_time = schedule.vehicle.available_from
            current_loc = schedule.vehicle.start_location
            for trip in schedule.trips:
                trip.start_time = current_time
                trip.start_location = current_loc
                feasible, details = self.constraints.is_trip_feasible(
                    schedule.vehicle, trip.employees, trip.pickup_sequence,
                    trip.start_time, trip.start_location
                )
                if feasible:
                    time_details = details.get('time', {})
                    if 'arrival_at_office' in time_details:
                        trip.arrival_at_office = time_details['arrival_at_office']
                    if 'pickup_times' in time_details:
                        trip.pickup_times = time_details['pickup_times']
                    if 'total_distance' in time_details:
                        trip.distance_km = time_details['total_distance']
                    if 'route' in time_details:
                        trip.route = time_details['route']
                    valid_trips.append(trip)
                    current_time = trip.arrival_at_office + self.constraints.DROP_TIME
                    current_loc = self.office
                else:
                    removed_employees.extend(trip.employees)
            schedule.trips = valid_trips
        for eid in removed_employees:
            self._assign_to_best_trip(solution, eid)
        return solution

    def _assign_to_best_trip(self, sol: Solution, eid: str) -> bool:
        """Assign employee to best feasible trip."""
        emp = self.employees[eid]
        best_objective = float('inf')
        best_option = None
        for sched_idx, schedule in enumerate(sol.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok:
                    continue
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok:
                    continue
                best_seq = self.constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                if not best_seq:
                    continue
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                    objective = self._compute_objective(details, vehicle)
                    objective *= 0.9  # Bonus for consolidation
                    if objective < best_objective:
                        best_objective = objective
                        best_option = (sched_idx, trip_idx, False, best_seq)
            
            start_time, start_loc = self._get_next_trip_start(schedule)
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
            if cap_ok and type_ok:
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, [eid], [eid], start_time, start_loc
                )
                if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                    objective = self._compute_objective(details, vehicle)
                    if objective < best_objective:
                        best_objective = objective
                        best_option = (sched_idx, -1, True, [eid])
                        
        if best_option is None:
            return False
        sched_idx, trip_idx, is_new, sequence = best_option
        if is_new:
            return self._create_trip_for_employee(sol, eid, sched_idx)
        else:
            return self._insert_into_trip(sol, eid, sched_idx, trip_idx, sequence)