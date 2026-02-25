"""
ALNS for Velora Mobility Optimization - Constraint-First Multi-Trip Version
============================================================================

This version prioritizes constraint satisfaction:
1. Analyzes feasibility for ALL employees before building solution
2. Assigns critical (constrained) employees first
3. Uses configurable objective: alpha * cost + beta * time
4. Supports metadata sheet for configuration

Key changes from previous version:
- Constraint-first approach ensures maximum assignment
- Multi-trip aware scheduling
- Alpha/beta weighting from metadata
"""

import pandas as pd
import math
import random
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from enum import Enum
from itertools import permutations
import mapgraph as mp
mp.precompute()

# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class VehiclePreference(Enum):
    PREMIUM = "premium"
    NORMAL = "normal"
    ANY = "any"


class SharingPreference(Enum):
    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"


def sharing_to_max_passengers(pref: SharingPreference) -> int:
    return {"single": 1, "double": 2, "triple": 3}[pref.value]


def get_allowed_vehicle_types(pref: VehiclePreference) -> Set[str]:
    if pref == VehiclePreference.PREMIUM:
        return {"premium"}
    if pref == VehiclePreference.NORMAL:
        return {"normal"}
    return {"normal", "premium"}


@dataclass
class Location:
    lat: float
    lng: float
    
    def distance_to(self, other: 'Location') -> Tuple[float, List[Any]]:
        src = mp.nearest_node((self.lat, self.lng))
        dst = mp.nearest_node((other.lat, other.lng))
        route, len = mp.optimal_route(src, dst)
        return len, route
    
    def __repr__(self):
        return f"({self.lat:.4f}, {self.lng:.4f})"


@dataclass
class Employee:
    id: str
    priority: int
    pickup: Location
    dropoff: Location
    earliest_pickup: float
    latest_drop: float
    vehicle_preference: VehiclePreference
    sharing_preference: SharingPreference
    baseline_cost: float = 0.0
    baseline_time: float = 0.0
    # New attribute to store metadata-driven tolerances
    tolerance_map: Dict[int, int] = field(default_factory=dict)
    
    @property
    def max_passengers(self) -> int:
        return sharing_to_max_passengers(self.sharing_preference)
    
    @property
    def allowed_vehicle_types(self) -> Set[str]:
        return get_allowed_vehicle_types(self.vehicle_preference)
    
    @property
    def adjusted_latest_drop(self) -> float:
        # Uses the tolerance_map from metadata, or defaults to existing logic if not found
        tolerance = self.tolerance_map.get(self.priority, 
                    {1: 5, 2: 10, 3: 15, 4: 20, 5: 30}.get(self.priority, 20))
        return self.latest_drop + tolerance

@dataclass
class Vehicle:
    id: str
    fuel_type: str
    vehicle_type: str
    capacity: int
    cost_per_km: float
    avg_speed: float
    start_location: Location
    available_from: float
    category: str


@dataclass
class Trip:
    vehicle_id: str
    employees: List[str] = field(default_factory=list)
    pickup_sequence: List[str] = field(default_factory=list)
    start_time: float = 0.0
    start_location: Location = None
    arrival_at_office: float = 0.0
    pickup_times: Dict[str, float] = field(default_factory=dict)
    distance_km: float = 0.0
    route: List[Tuple[float]] = field(default_factory=list)
    
    def copy(self) -> 'Trip':
        return Trip(
            vehicle_id=self.vehicle_id,
            employees=self.employees.copy(),
            pickup_sequence=self.pickup_sequence.copy(),
            start_time=self.start_time,
            start_location=self.start_location,
            arrival_at_office=self.arrival_at_office,
            pickup_times=self.pickup_times.copy(),
            distance_km=self.distance_km,
            route=self.route
        )


@dataclass
class VehicleSchedule:
    vehicle: Vehicle
    trips: List[Trip] = field(default_factory=list)
    
    def copy(self) -> 'VehicleSchedule':
        return VehicleSchedule(
            vehicle=self.vehicle,
            trips=[t.copy() for t in self.trips]
        )
    
    def all_employees(self) -> List[str]:
        result = []
        for trip in self.trips:
            result.extend(trip.employees)
        return result
    
    def get_end_time(self) -> float:
        if not self.trips:
            return self.vehicle.available_from
        return self.trips[-1].arrival_at_office
    
    def get_current_location(self, office: Location) -> Location:
        if not self.trips:
            return self.vehicle.start_location
        return office


@dataclass
class Solution:
    schedules: List[VehicleSchedule] = field(default_factory=list)
    
    def copy(self) -> 'Solution':
        return Solution(schedules=[s.copy() for s in self.schedules])
    
    def all_assigned(self) -> List[str]:
        result = []
        for schedule in self.schedules:
            result.extend(schedule.all_employees())
        return result
    
    def total_trips(self) -> int:
        return sum(len(s.trips) for s in self.schedules)


# =============================================================================
# DATA LOADER (with metadata support)
# =============================================================================
from typing import List, Tuple, Dict, Any
class DataLoader:
    @staticmethod
    def parse_time(val) -> float:
        if pd.isna(val):
            return 480.0
        if isinstance(val, (int, float)):
            return float(val) * 60 if val < 24 else float(val)
        s = str(val).strip()
        if ':' in s:
            parts = s.split(':')
            return int(parts[0]) * 60 + int(parts[1])
        return 480.0
    
    @staticmethod
    def load(filepath: str) -> Tuple[List[Any], List[Any], Any, Dict]:
        """Load data and return employees, vehicles, office, and metadata."""
        # Load raw dataframes from Excel
        emp_df = pd.read_excel(filepath, sheet_name='employees')
        veh_df = pd.read_excel(filepath, sheet_name='vehicles')
        base_df = pd.read_excel(filepath, sheet_name='baseline')
        
        sum_baseline = base_df['baseline_cost'].sum()
        time_baseline = 0
        if 'baseline_time_min' in base_df.columns:
            time_baseline = base_df['baseline_time_min'].sum() 
        elif 'baseline_time' in base_df.columns:
            time_baseline = base_df['baseline_time'].sum() 

        # Initialize metadata defaults and the priority tolerance map
        metadata = {'alpha': 0.7, 'beta': 0.3, 'sum_baseline_cost': sum_baseline, 'sum_baseline_time': time_baseline}

        tolerance_map = {}
        
        try:
            meta_df = pd.read_excel(filepath, sheet_name='metadata')
            for _, row in meta_df.iterrows():
                # Standardize key names for robust parsing
                key = str(row.get('key', row.get('parameter', ''))).lower().strip()
                value = row.get('value', row.get('val'))

                if pd.notna(value):
                    # Parse objective weights
                    if key in ['alpha', 'objective_cost_weight']:
                        metadata['alpha'] = float(value)
                    elif key in ['beta', 'objective_time_weight']:
                        metadata['beta'] = float(value)
                    
                    # Parse priority tolerances (expected format: priority_X_tolerance)
                    elif 'priority_' in key and '_max_delay_min' in key:
                        try:
                            # Extract the integer priority level from the string
                            p_level = int(key.split('_')[1])
                            tolerance_map[p_level] = int(value)
                        except (ValueError, IndexError):
                            continue
                            
            print(f"Successfully loaded metadata: α={metadata['alpha']}, β={metadata['beta']}")
            if tolerance_map:
                print(f"Custom priority tolerances loaded: {tolerance_map}")

        except Exception as e:
            print(f"No valid metadata sheet found or error parsing: {e}. Using defaults.")
        
        # Load baseline metrics for savings calculation
        baseline_data = {}
        for _, r in base_df.iterrows():
            eid = r['employee_id']
            b_time = r.get('baseline_time', r.get('time', r.get('baseline_travel_time', 0)))
            baseline_data[eid] = {
                'cost': float(r['baseline_cost']),
                'time': float(b_time)
            }

        # Office location is derived from the first employee's dropoff point
        office = Location(emp_df['drop_lat'].iloc[0], emp_df['drop_lng'].iloc[0])
        
        employees = []
        for _, r in emp_df.iterrows():
            vp = str(r['vehicle_preference']).lower()
            sp = str(r['sharing_preference']).lower()
            
            b_info = baseline_data.get(r['employee_id'], {'cost': 0, 'time': 0})
            
            # Instantiate employee with the loaded tolerance map
            emp = Employee(
                id=r['employee_id'],
                priority=int(r['priority']),
                pickup=Location(r['pickup_lat'], r['pickup_lng']),
                dropoff=office,
                earliest_pickup=DataLoader.parse_time(r['earliest_pickup']),
                latest_drop=DataLoader.parse_time(r['latest_drop']),
                vehicle_preference=VehiclePreference(vp) if vp in ['premium','normal','any'] else VehiclePreference.ANY,
                sharing_preference=SharingPreference(sp) if sp in ['single','double','triple'] else SharingPreference.TRIPLE,
                baseline_cost=b_info['cost'],
                baseline_time=b_info['time'],
                tolerance_map=tolerance_map # Assign the dictionary here
            )
            
            # Calculate weighted baseline value for comparison
            emp.baseline_value = (metadata['alpha'] * b_info['cost']) + (metadata['beta'] * b_info['time'])
            employees.append(emp)
        
        vehicles = []
        for _, r in veh_df.iterrows():
            veh = Vehicle(
                id=r['vehicle_id'],
                fuel_type=r['fuel_type'],
                vehicle_type=r['vehicle_type'],
                capacity=int(r['capacity']),
                cost_per_km=float(r['cost_per_km']),
                avg_speed=float(r['avg_speed_kmph']),
                start_location=Location(r['current_lat'], r['current_lng']),
                available_from=DataLoader.parse_time(r['available_from']),
                category=r['category']
            )
            vehicles.append(veh)
        
        return employees, vehicles, office, metadata

# =============================================================================
# CONSTRAINT CHECKER
# =============================================================================

class TripConstraints:
    SERVICE_TIME = 0
    DROP_TIME = 0
    
    def __init__(self, employees: Dict[str, Employee], vehicles: Dict[str, Vehicle], office: Location):
        self.employees = employees
        self.vehicles = vehicles
        self.office = office
        self._dist_cache = {}
    
    def distance(self, a: Location, b: Location) -> float:
        key = (a.lat, a.lng, b.lat, b.lng)
        if key not in self._dist_cache:
            self._dist_cache[key] = a.distance_to(b)
        return self._dist_cache[key]
    
    def travel_time(self, a: Location, b: Location, speed: float) -> float:
        return (self.distance(a, b)[0] / speed) * 60

    def check_capacity_sharing(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        n = len(employee_ids)
        if n == 0:
            return True, {'satisfied': True, 'n': 0, 'max_allowed': vehicle.capacity}
        
        max_allowed = vehicle.capacity
        if n > max_allowed: 
            return False, {'satisfied': False}
            
        for eid in employee_ids:
            limit = self.employees[eid].max_passengers
            if limit < max_allowed:
                max_allowed = limit
            # Early exit if constraint breaks
            if n > max_allowed:
                return False, {'satisfied': False}
                
        return True, {
            'satisfied': True, 'n': n, 'max_allowed': max_allowed,
            'vehicle_capacity': vehicle.capacity,
            'sharing_limits': {eid: self.employees[eid].max_passengers for eid in employee_ids}
        }

    def check_vehicle_type(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        if not employee_ids:
            return True, {'satisfied': True}
        
        # Use intersection_update for faster set logic
        allowed = set(self.employees[employee_ids[0]].allowed_vehicle_types)
        for eid in employee_ids[1:]:
            allowed.intersection_update(self.employees[eid].allowed_vehicle_types)
            if not allowed: # Early exit
                break
        
        vehicle_cat = vehicle.category.lower()
        if vehicle_cat not in allowed:
            return False, {'satisfied': False}
            
        return True, {
            'satisfied': True, 'vehicle_category': vehicle_cat,
            'allowed_intersection': list(allowed),
            'employee_preferences': {eid: self.employees[eid].vehicle_preference.value for eid in employee_ids}
        }

    def check_time_constraint(self, vehicle: Vehicle, employee_ids: List[str],
                               pickup_sequence: List[str], start_time: float,
                               start_location: Location, precalc_deadline: float = None) -> Tuple[bool, Dict]:
        if not pickup_sequence:
            return True, {'satisfied': True}
        
        # Accept pre-calculated deadline from permutations loop
        deadline = precalc_deadline if precalc_deadline is not None else min(self.employees[eid].adjusted_latest_drop for eid in employee_ids)
        
        current_time = start_time
        current_loc = start_location
        pickup_times = {}
        total_dist = 0.0
        final_route = []
        
        for eid in pickup_sequence:
            emp = self.employees[eid]
            dist, route = self.distance(current_loc, emp.pickup)
            travel = (dist / vehicle.avg_speed) * 60 # Inlined for speed
            total_dist += dist
            final_route += route[:-1]
            
            arrival_at_pickup = current_time + travel
            actual_pickup = arrival_at_pickup if arrival_at_pickup > emp.earliest_pickup else emp.earliest_pickup
            pickup_times[eid] = actual_pickup
            
            current_time = actual_pickup + self.SERVICE_TIME
            
            # EARLY EXIT: Route is already late
            if current_time > deadline:
                return False, {'satisfied': False}
                
            current_loc = emp.pickup
        
        dist, route = self.distance(current_loc, self.office)
        travel = (dist / vehicle.avg_speed) * 60
        total_dist += dist
        final_route += route
        arrival_at_office = current_time + travel
        
        if arrival_at_office > deadline:
            return False, {'satisfied': False}
            
        return True, {
            'satisfied': True, 
            'deadline': deadline, 
            'arrival_at_office': arrival_at_office,
            'slack': deadline - arrival_at_office, 
            'pickup_times': pickup_times,
            'total_distance': total_dist, 
            'route': final_route,
            'start_time': start_time, 
            'start_location': start_location
        }

    def is_trip_feasible(self, vehicle: Vehicle, employee_ids: List[str],
                         pickup_sequence: List[str], start_time: float,
                         start_location: Location) -> Tuple[bool, Dict]:
        
        cap_ok, cap_details = self.check_capacity_sharing(vehicle, employee_ids)
        if not cap_ok: 
            return False, {
                'feasible': False, 
                'capacity_sharing': cap_details,
                'vehicle_type': {'satisfied': False, 'reason': 'Skipped'},
                'time': {'satisfied': False, 'reason': 'Skipped'}
            }
            
        type_ok, type_details = self.check_vehicle_type(vehicle, employee_ids)
        if not type_ok: 
            return False, {
                'feasible': False, 
                'capacity_sharing': cap_details, 
                'vehicle_type': type_details,
                'time': {'satisfied': False, 'reason': 'Skipped'}
            }
            
        time_ok, time_details = self.check_time_constraint(
            vehicle, employee_ids, pickup_sequence, start_time, start_location
        )
            
        return time_ok, {
            'feasible': time_ok,
            'time': time_details,
            'capacity_sharing': cap_details,
            'vehicle_type': type_details
        }
    
    def find_best_sequence(self, vehicle: Vehicle, employee_ids: List[str],
                           start_time: float, start_location: Location) -> Optional[List[str]]:
        if not employee_ids:
            return []
        
        # Calculate deadline ONCE here
        deadline = min(self.employees[eid].adjusted_latest_drop for eid in employee_ids)
        
        if len(employee_ids) == 1:
            ok, _ = self.check_time_constraint(vehicle, employee_ids, employee_ids, start_time, start_location, deadline)
            return employee_ids if ok else None
        
        if len(employee_ids) <= 6:
            best_seq = None
            best_arrival = float('inf')
            
            for perm in permutations(employee_ids):
                seq = list(perm)
                ok, details = self.check_time_constraint(vehicle, employee_ids, seq, start_time, start_location, deadline)
                if ok and details['arrival_at_office'] < best_arrival:
                    best_arrival = details['arrival_at_office']
                    best_seq = seq
            
            return best_seq
        else:
            sorted_emps = sorted(employee_ids, key=lambda eid: self.employees[eid].earliest_pickup)
            ok, _ = self.check_time_constraint(vehicle, employee_ids, sorted_emps, start_time, start_location, deadline)
            return sorted_emps if ok else None

# =============================================================================
# PROBLEM STATE
# =============================================================================

class ProblemState:
    def __init__(self, employees: List[Employee], vehicles: List[Vehicle], 
                 office: Location, metadata: Dict = None):
        self.employees = {e.id: e for e in employees}
        self.vehicles = {v.id: v for v in vehicles}
        self.emp_list = employees
        self.veh_list = vehicles
        self.office = office
        self.constraints = TripConstraints(self.employees, self.vehicles, office)
        self.total_employees = len(employees)
        
        # Metadata for objective function
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
        
        for schedule in sol.schedules:
            for trip in schedule.trips:
                total_dist += trip.distance_km
                total_cost += trip.distance_km * schedule.vehicle.cost_per_km
                total_time += trip.arrival_at_office - trip.start_time
        
        objective = self.alpha * total_cost / self.sum_baseline_cost + self.beta * total_time / self.sum_baseline_time
        
        return objective, {
            'objective': objective,
            'travel_cost': total_cost,
            'total_distance': total_dist,
            'total_time': total_time,
            'vehicles_used': sum(1 for s in sol.schedules if s.trips),
            'total_trips': sol.total_trips(),
            'served': len(sol.all_assigned())
        }


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
        
    def build(self) -> 'Solution':
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
            if not cap_ok: continue
            
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [emp_i, emp_j])
            if not type_ok: continue
            
            best_seq = self.constraints.find_best_sequence(
                vehicle, [emp_i, emp_j],
                vehicle.available_from, vehicle.start_location
            )
            
            if not best_seq: continue
            
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [emp_i, emp_j], best_seq,
                vehicle.available_from, vehicle.start_location
            )
            
            if not time_ok: continue
            
            feasible_vehicles.add(vid)
            
            combined_cost = details['total_distance'] * vehicle.cost_per_km
            combined_time = details['arrival_at_office'] - vehicle.available_from
            
            cost_i = self._individual_costs.get(emp_i, {}).get(vid, float('inf'))
            cost_j = self._individual_costs.get(emp_j, {}).get(vid, float('inf'))
            time_i = self._individual_times.get(emp_i, {}).get(vid, float('inf'))
            time_j = self._individual_times.get(emp_j, {}).get(vid, float('inf'))
            
            if cost_i == float('inf') or cost_j == float('inf'): continue
            
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
    
    def _phase2_hybrid_construction(self) -> 'Solution':
        solution = Solution()
        for v in self.state.veh_list:
            solution.schedules.append(VehicleSchedule(vehicle=v))
        
        assigned = set()
        
        # Step 1: Seed Routes (Savings list is already randomized via noise)
        assigned = self._step1_seed_routes(solution, assigned)
        
        # Step 2: Regret-k Insertion (Using RCL)
        assigned = self._step2_regret_insertion(solution, assigned)
        
        return solution
    
    def _step1_seed_routes(self, solution: 'Solution', assigned: Set[str]) -> Set[str]:
        if self.top_k_seeds is None:
            k = max(1, len(self.state.emp_list) // 4)
        else:
            k = self.top_k_seeds
        
        seeds_created = 0
        
        for entry in self._savings_list:
            if seeds_created >= k: break
            
            emp_i, emp_j = entry.employee_i, entry.employee_j
            if emp_i in assigned or emp_j in assigned: continue
            
            if not self._can_create_route(solution, [emp_i, emp_j], entry.best_vehicle):
                continue
            
            if self._create_trip(solution, [emp_i, emp_j], entry.best_vehicle, entry.best_sequence):
                assigned.add(emp_i)
                assigned.add(emp_j)
                seeds_created += 1
        
        return assigned

    def _step2_regret_insertion(self, solution: 'Solution', assigned: Set[str]) -> Set[str]:
        """
        Step 2: Regret-k Insertion using Restricted Candidate List (RCL).
        Instead of picking best regret, pick from top N best regrets.
        """
        unassigned = [e.id for e in self.state.emp_list if e.id not in assigned]
        
        while unassigned:
            candidates = [] # List of (regret, eid, best_insertion_tuple)
            
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
                pass
        
        return assigned

    # ... [Rest of the helper methods remain exactly the same] ...
    def _get_insertion_options(self, solution: 'Solution', eid: str) -> List[Tuple]:
        # (Same as original code)
        options = []
        emp = self.employees[eid]
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types: continue
            
            # Try existing trips
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok: continue
                
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok: continue
                
                best_seq = self.constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                if not best_seq: continue
                
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                
                if time_ok:
                    objective = self._compute_objective(details, vehicle)
                    options.append((objective, sched_idx, trip_idx, False, best_seq))
            
            # Try new trip
            start_time, start_loc = self._get_next_trip_start(schedule)
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            if not cap_ok: continue
            
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok:
                objective = self._compute_objective(details, vehicle)
                objective *= 1.05 # Penalty for new trip
                options.append((objective, sched_idx, -1, True, [eid]))
                
        return options

    def _compute_objective(self, details: Dict, vehicle) -> float:
        total_distance = details.get('total_distance', 0)
        start_time = details.get('start_time', 0)
        arrival_at_office = details.get('arrival_at_office', start_time)
        cost = total_distance * vehicle.cost_per_km
        time_val = arrival_at_office - start_time
        return self.alpha * cost + self.beta * time_val

    def _get_next_trip_start(self, schedule: 'VehicleSchedule') -> Tuple[float, 'Location']:
        if schedule.trips:
            last_trip = schedule.trips[-1]
            start_time = last_trip.arrival_at_office + self.constraints.DROP_TIME
            start_loc = self.office
        else:
            start_time = schedule.vehicle.available_from
            start_loc = schedule.vehicle.start_location
        return start_time, start_loc

    def _can_create_route(self, solution: 'Solution', emp_ids: List[str], vid: str) -> bool:
        vehicle = self.vehicles[vid]
        schedule = next((s for s in solution.schedules if s.vehicle.id == vid), None)
        if not schedule: return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, emp_ids)
        if not cap_ok: return False
        
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, emp_ids)
        if not type_ok: return False
        
        best_seq = self.constraints.find_best_sequence(vehicle, emp_ids, start_time, start_loc)
        if not best_seq: return False
        
        time_ok, _ = self.constraints.check_time_constraint(
            vehicle, emp_ids, best_seq, start_time, start_loc
        )
        return time_ok

    def _create_trip(self, solution: 'Solution', emp_ids: List[str], 
                     vid: str, sequence: List[str] = None) -> bool:
        vehicle = self.vehicles[vid]
        schedule = next((s for s in solution.schedules if s.vehicle.id == vid), None)
        if not schedule: return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        if not sequence:
            sequence = self.constraints.find_best_sequence(
                vehicle, emp_ids, start_time, start_loc
            )
        if not sequence: return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, emp_ids, sequence, start_time, start_loc
        )
        
        if not time_ok or 'arrival_at_office' not in details: return False
        
        trip = Trip(
            vehicle_id=vid,
            employees=emp_ids.copy(),
            pickup_sequence=sequence.copy(),
            start_time=start_time,
            start_location=start_loc,
            arrival_at_office=details['arrival_at_office'],
            pickup_times=details.get('pickup_times', {}),
            distance_km=details.get('total_distance', 0),
            route=details.get('route', [])
        )
        
        schedule.trips.append(trip)
        return True

    def _create_trip_for_employee(self, solution: 'Solution', eid: str, sched_idx: int) -> bool:
        schedule = solution.schedules[sched_idx]
        vehicle = schedule.vehicle
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
        if not (cap_ok and type_ok): return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, [eid], [eid], start_time, start_loc
        )
        if not time_ok or 'arrival_at_office' not in details: return False
        
        trip = Trip(
            vehicle_id=vehicle.id,
            employees=[eid],
            pickup_sequence=[eid],
            start_time=start_time,
            start_location=start_loc,
            arrival_at_office=details['arrival_at_office'],
            pickup_times=details.get('pickup_times', {}),
            distance_km=details.get('total_distance', 0),
            route=details.get('route', [])
        )
        schedule.trips.append(trip)
        return True

    def _insert_into_trip(self, solution: 'Solution', eid: str, 
                          sched_idx: int, trip_idx: int, sequence: List[str]) -> bool:
        schedule = solution.schedules[sched_idx]
        trip = schedule.trips[trip_idx]
        vehicle = schedule.vehicle
        new_emps = trip.employees + [eid]
        
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, new_emps)
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, new_emps)
        if not (cap_ok and type_ok): return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, new_emps, sequence, trip.start_time, trip.start_location
        )
        if not time_ok or 'arrival_at_office' not in details: return False
        
        trip.employees = new_emps
        trip.pickup_sequence = sequence
        trip.arrival_at_office = details['arrival_at_office']
        trip.pickup_times = details.get('pickup_times', {})
        trip.distance_km = details.get('total_distance', 0)
        trip.route = details.get('route', [])
        
        if trip_idx + 1 < len(schedule.trips):
            self._update_subsequent_trips(schedule, trip_idx + 1)
        
        return True

    def _assign_to_new_trip(self, solution: 'Solution', eid: str) -> bool:
        emp = self.employees[eid]
        best_objective = float('inf')
        best_sched_idx = None
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types: continue
            
            start_time, start_loc = self._get_next_trip_start(schedule)
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
            if not (cap_ok and type_ok): continue
            
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

    def _update_subsequent_trips(self, schedule: 'VehicleSchedule', start_idx: int):
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
    
    # ... [Phase 3 and Final Validation methods remain exactly the same] ...
    def _phase3_consolidation(self, solution: 'Solution') -> 'Solution':
        # (Same as original code)
        improved = True
        max_iterations = 50
        iteration = 0
        
        while improved and iteration < max_iterations:
            improved = False
            iteration += 1
            
            for schedule in solution.schedules:
                if len(schedule.trips) < 2: continue
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

    def _can_merge_trips(self, schedule: 'VehicleSchedule', trip_i: 'Trip', trip_j: 'Trip') -> bool:
        vehicle = schedule.vehicle
        combined_emps = trip_i.employees + trip_j.employees
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, combined_emps)
        if not cap_ok: return False
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, combined_emps)
        if not type_ok: return False
        best_seq = self.constraints.find_best_sequence(
            vehicle, combined_emps, trip_i.start_time, trip_i.start_location
        )
        if not best_seq: return False
        time_ok, _ = self.constraints.check_time_constraint(
            vehicle, combined_emps, best_seq, trip_i.start_time, trip_i.start_location
        )
        return time_ok

    def _merge_trips(self, schedule: 'VehicleSchedule', trip_idx: int):
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
        trip_i.route = details['route']
        schedule.trips.remove(trip_j)
        self._update_subsequent_trips(schedule, trip_idx + 1)

    def _final_validation(self, solution: 'Solution') -> 'Solution':
        # (Same as original code)
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
                    valid_trips.append(trip)
                    current_time = trip.arrival_at_office + self.constraints.DROP_TIME
                    current_loc = self.office
                else:
                    removed_employees.extend(trip.employees)
            schedule.trips = valid_trips
        for eid in removed_employees:
            self._assign_to_best_trip(solution, eid)
        return solution

    def _assign_to_best_trip(self, sol: 'Solution', eid: str) -> bool:
        # (Same as original code)
        emp = self.employees[eid]
        best_objective = float('inf')
        best_option = None
        for sched_idx, schedule in enumerate(sol.schedules):
            vehicle = schedule.vehicle
            if vehicle.category.lower() not in emp.allowed_vehicle_types: continue
            
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok: continue
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok: continue
                best_seq = self.constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                if not best_seq: continue
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                    objective = self._compute_objective(details, vehicle)
                    objective *= 0.9
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
                        
        if best_option is None: return False
        sched_idx, trip_idx, is_new, sequence = best_option
        if is_new: return self._create_trip_for_employee(sol, eid, sched_idx)
        else: return self._insert_into_trip(sol, eid, sched_idx, trip_idx, sequence)


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
                            trip.route = details.get('route', [])
                    
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
            print(f"{'Run':<5} | {'Init Cost':<10} | {'Final Cost':<10} | {'Improv %':<10} | {'Status'}")
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
                print(f"{run_idx+1:<5} | {initial_cost:<10.2f} | {run_best_cost:<10.2f} | {improv_pct:<9.1f}% | {status}")

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


# =============================================================================
# RESULTS VERIFIER
# =============================================================================

class ResultsVerifier:
    def __init__(self, state):
        self.state = state
        self.constraints = state.constraints
    
    def verify_and_display(self, solution) -> Dict:
        results = {
            'summary': {},
            'vehicle_schedules': [],
            'all_constraints_satisfied': True,
            'unassigned': []
        }
        
        all_emp_ids = set(self.state.employees.keys())
        assigned_ids = set(solution.all_assigned())
        unassigned = all_emp_ids - assigned_ids
        results['unassigned'] = list(unassigned)
        
        if unassigned:
            results['all_constraints_satisfied'] = False
        
        total_cost, breakdown = self.state.solution_cost(solution)
        
        # Calculate Base metrics
        baseline_cost_total = float(self.state.sum_baseline_cost)
        baseline_time_total = float(self.state.sum_baseline_time)
        
        # --- MODIFIED: Calculate Total Weighted Baseline Value ---
        # Summing the pre-calculated baseline_value (alpha*cost + beta*time)
        baseline_weighted_total = sum(
            getattr(emp, 'baseline_value', 0)
            for emp in self.state.employees.values()
        )
        results['summary'] = {
            'total_employees': self.state.total_employees,
            'employees_assigned': len(assigned_ids),
            'all_assigned': len(unassigned) == 0,
            'total_trips': solution.total_trips(),
            'vehicles_used': breakdown['vehicles_used'],
            'total_distance_km': round(breakdown['total_distance'], 2),
            'travel_cost': round(breakdown['travel_cost'], 2),
            'objective': round(breakdown['objective'], 2),
            'alpha': round(self.state.alpha, 2),
            'beta': round(self.state.beta, 2),
            'baseline_cost': round(baseline_cost_total, 2),
            'baseline_time': round(baseline_time_total, 2),
            # --- MODIFIED: Add Weighted Baseline to summary ---
            'baseline_weighted': round(baseline_weighted_total, 2),
            'savings': round(baseline_cost_total - breakdown['travel_cost'], 2),
            'savings_pct': round((baseline_cost_total - breakdown['travel_cost']) / baseline_cost_total * 100, 2) if baseline_cost_total > 0 else 0,
            'optimized_pct': round((1.0 - total_cost) * 100, 2)
        }
        
        results['employees'] = {}
        for emp in self.state.employees.values():
            results['employees'][emp.id] = {}
            results['employees'][emp.id]['priority'] = emp.priority
            results['employees'][emp.id]['pickup'] = emp.pickup
            results['employees'][emp.id]['vehicle'] = '-'
            results['employees'][emp.id]['pickup_time'] = '-'
            results['employees'][emp.id]['dropoff_time'] = '-'
            
        results['Office'] = (float(self.state.office.lat), float(self.state.office.lng))
        
        for schedule in solution.schedules:
            if not schedule.trips:
                continue
            
            sched_info = {
                'vehicle': {
                    'id': schedule.vehicle.id,
                    'category': schedule.vehicle.category,
                    'capacity': schedule.vehicle.capacity,
                    'start_location': str(schedule.vehicle.start_location),
                    'available_from': self._fmt_time(schedule.vehicle.available_from)
                },
                'trips': []
            }
            
            cost = 0.0
            time = 0.0
            dist = 0.0

            for i, trip in enumerate(schedule.trips):
                feasible, details = self.constraints.is_trip_feasible(
                    schedule.vehicle, trip.employees, trip.pickup_sequence,
                    trip.start_time, trip.start_location
                )
                
                if not feasible:
                    results['all_constraints_satisfied'] = False
                
                for emp in trip.employees:
                    results['employees'][emp]['vehicle'] = schedule.vehicle.id
                    results['employees'][emp]['pickup_time'] = self._fmt_time(trip.pickup_times[emp])
                    results['employees'][emp]['dropoff_time'] = self._fmt_time(trip.arrival_at_office)
                
                dist += trip.distance_km
                cost += trip.distance_km * schedule.vehicle.cost_per_km
                time += trip.arrival_at_office - trip.start_time

                trip_info = {
                    'trip_number': i + 1,
                    'employees': trip.employees,
                    'pickup_sequence': trip.pickup_sequence,
                    'start_time': self._fmt_time(trip.start_time),
                    'start_location': str(trip.start_location),
                    'arrival_at_office': self._fmt_time(trip.arrival_at_office),
                    'distance_km': round(trip.distance_km, 2),
                    'route': trip.route,
                    'cost': round(trip.distance_km * schedule.vehicle.cost_per_km, 2),
                    'feasible': feasible,
                    'constraints': {
                        'time': details['time'],
                        'capacity_sharing': details['capacity_sharing'],
                        'vehicle_type': details['vehicle_type']
                    }
                }
                
                sched_info['trips'].append(trip_info)
            
            sched_info['vehicle']['cost'] = cost
            sched_info['vehicle']['time'] = time
            sched_info['vehicle']['distance'] = dist
            results['vehicle_schedules'].append(sched_info)
        
        return results
    
    def _fmt_time(self, mins) -> str:
        if isinstance(mins, str):
            return mins
        h, m = int(mins // 60), int(mins % 60)
        return f"{h:02d}:{m:02d}"
    
    def print_results(self, results: Dict):
        print("\n" + "="*90)
        print("VELORA MOBILITY OPTIMIZATION - CONSTRAINT-FIRST RESULTS")
        print("="*90)
        
        s = results['summary']
        print(f"\n📊 SUMMARY")
        print(f"   Total Employees:    {s['total_employees']}")
        print(f"   Employees Assigned: {s['employees_assigned']}")
        print(f"   All Assigned:       {'✅ YES' if s['all_assigned'] else '❌ NO'}")
        print(f"   Total Trips:        {s['total_trips']}")
        print(f"   Vehicles Used:      {s['vehicles_used']}")
        print(f"\n   Objective Weights:  α={s['alpha']}, β={s['beta']}")
        print(f"   Total Distance:     {s['total_distance_km']:.2f} km")
        print(f"   Travel Cost:        ₹{s['travel_cost']:.2f}")
        print(f"   Objective Value:    {s['objective']:.2f}")
        print(f"   Baseline Cost:      ₹{s['baseline_cost']:.2f}")
        print(f"   Baseline Time:      {s['baseline_time']:.2f} min")
        # --- MODIFIED: Print the new weighted baseline ---
        print(f"   Baseline Value:   {s['baseline_weighted']:.2f}")
        print(f"   Savings (Cost):     ₹{s['savings']:.2f} ({s['savings_pct']:.1f}%)")
        print(f"   Percentage Optimized:     {s['optimized_pct']:.2f}%")
        
        print(f"\n✓ ALL CONSTRAINTS: {'✅ SATISFIED' if results['all_constraints_satisfied'] else '❌ VIOLATIONS'}")
        
        print(f"\n{'─'*90}")
        print("🚗 VEHICLE SCHEDULES")
        print(f"{'─'*90}")
        
        for sched in results['vehicle_schedules']:
            v = sched['vehicle']
            print(f"\n╔{'═'*88}╗")
            print(f"║ VEHICLE {v['id']} ({v['category']}) - {len(sched['trips'])} trip(s)".ljust(89) + "║")
            print(f"║   Start: {v['start_location']} at {v['available_from']} | Capacity: {v['capacity']}".ljust(89) + "║")
            print(f"╠{'═'*88}╣")
            
            for trip in sched['trips']:
                status = '✅' if trip['feasible'] else '❌'
                print(f"║".ljust(89) + "║")
                print(f"║ {status} TRIP {trip['trip_number']}".ljust(89) + "║")
                print(f"║   Employees: {', '.join(trip['employees'])}".ljust(89) + "║")
                print(f"║   Sequence: {' → '.join(trip['pickup_sequence'])} → Office".ljust(89) + "║")
                print(f"║   Start: {trip['start_location']} at {trip['start_time']}".ljust(89) + "║")
                print(f"║   Arrive Office: {trip['arrival_at_office']}".ljust(89) + "║")
                print(f"║   Distance: {trip['distance_km']} km | Cost: ₹{trip['cost']:.2f}".ljust(89) + "║")
                
                t = trip['constraints']['time']
                t_status = '✅' if t['satisfied'] else '❌'
                print(f"║   {t_status} Time: deadline={self._fmt_time(t['deadline'])}, arrival={self._fmt_time(t['arrival_at_office'])}, slack={t['slack']:.1f}min".ljust(89) + "║")
                
                c = trip['constraints']['capacity_sharing']
                c_status = '✅' if c['satisfied'] else '❌'
                print(f"║   {c_status} Capacity: n={c['n']} <= max_allowed={c['max_allowed']}".ljust(89) + "║")
                
                vt = trip['constraints']['vehicle_type']
                vt_status = '✅' if vt['satisfied'] else '❌'
                print(f"║   {vt_status} Type: '{vt['vehicle_category']}' ∈ {vt['allowed_intersection']}".ljust(89) + "║")
            
            print(f"╚{'═'*88}╝")
        
        if results['unassigned']:
            print(f"\n⚠️  UNASSIGNED: {', '.join(results['unassigned'])}")
        
        print(f"\n┌{'─'*88}┐")
        all_assigned = results['summary']['all_assigned']
        c4_status = '✅' if all_assigned else '❌'
        print(f"│ {c4_status} CONSTRAINT 4: ALL EMPLOYEES PICKED EXACTLY ONCE".ljust(89) + "│")
        print(f"│   {results['summary']['employees_assigned']}/{results['summary']['total_employees']} employees assigned".ljust(89) + "│")
        print(f"└{'─'*88}┘")
# =============================================================================
# MAIN
# =============================================================================

def optimize(filepath: str, verbose: bool = True) -> Dict:
    # --- PHASE 1: Strict Solve ---
    employees, vehicles, office, metadata = DataLoader.load(filepath)
    state_strict = ProblemState(employees, vehicles, office, metadata)
    
    config = ALNSConfig()
    config.max_iter = min(3000, 200 * len(employees))
    
    if verbose: print("Running Strict Optimization...")
    alns_strict = ALNS(state_strict, config)
    sol_strict, breakdown_strict = alns_strict.solve(verbose=verbose)
    
    final_sol, final_state, final_breakdown = sol_strict, state_strict, breakdown_strict

    # --- PHASE 2: Check Fallback ---
    if len(sol_strict.all_assigned()) < len(employees):
        if verbose:
            print(f"\n⚠️ Fallback Triggered. Loosening constraints for {len(employees) - len(sol_strict.all_assigned())} unassigned employees...")

        # Manually loosen constraints for the relaxed solve
        for emp in employees:
            emp.vehicle_preference = VehiclePreference.ANY
            emp.sharing_preference = SharingPreference.TRIPLE
        
        state_relaxed = ProblemState(employees, vehicles, office, metadata)
        alns_relaxed = ALNS(state_relaxed, config)
        sol_relaxed, breakdown_relaxed = alns_relaxed.solve(verbose=verbose)
        
        # Compare: Only keep relaxed version if it actually improves assignment count
        if len(sol_relaxed.all_assigned()) > len(sol_strict.all_assigned()):
            if verbose: print("✅ Improvement found! Using relaxed solution.")
            final_sol, final_state, final_breakdown = sol_relaxed, state_relaxed, breakdown_relaxed
        else:
            if verbose: print("❌ No improvement with relaxed constraints. Reverting to strict.")

    # --- PHASE 3: Output ---
    verifier = ResultsVerifier(final_state)
    results = verifier.verify_and_display(final_sol)
    results['breakdown'] = final_breakdown
    if verbose: verifier.print_results(results)
    
    return results


if __name__ == "__main__":
    import sys
    
    filepath = "TestCases/TestCase_TC03.xlsx"
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    
    print(f"Optimizing: {filepath}\n")
    results = optimize(filepath)