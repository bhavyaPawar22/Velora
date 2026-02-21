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
import numpy as np
import math
import random
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from enum import Enum
from itertools import permutations
import mapgraph as mp

# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

def precompute():    
    mp.precompute()

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
    
    @property
    def max_passengers(self) -> int:
        return sharing_to_max_passengers(self.sharing_preference)
    
    @property
    def allowed_vehicle_types(self) -> Set[str]:
        return get_allowed_vehicle_types(self.vehicle_preference)
    
    @property
    def adjusted_latest_drop(self) -> float:
        tolerance = {1: 5, 2: 10, 3: 15, 4: 20, 5: 30}.get(self.priority, 20)
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
        """Load data and return employees, vehicles, office, and metadata"""
        emp_df = pd.read_excel(filepath, sheet_name='employees')
        veh_df = pd.read_excel(filepath, sheet_name='vehicles')
        base_df = pd.read_excel(filepath, sheet_name='baseline')

        sum_baseline = base_df['baseline_cost'].sum()
        time_baseline = base_df['baseline_time_min'].sum() if 'baseline_time_min' in base_df.columns else 0
        
        # Try to load metadata
        metadata = {'alpha': 0.7, 'beta': 0.3}
        try:
            meta_df = pd.read_excel(filepath, sheet_name='metadata')
            for _, row in meta_df.iterrows():
                key = str(row.get('key', row.get('parameter', ''))).lower().strip()
                value = row.get('value', row.get('val'))

                if pd.notna(value):
                    # Map the specific Excel keys to alpha and beta
                    if key in ['alpha', 'objective_cost_weight']:
                        metadata['alpha'] = float(value)
                    elif key in ['beta', 'objective_time_weight']:
                        metadata['beta'] = float(value)
                        
            print(f"Successfully loaded metadata: α={metadata['alpha']}, β={metadata['beta']}")

        except Exception as e:
            print(f"No metadata sheet found, using defaults (alpha=0.7, beta=0.3)")
        
        # --- MODIFIED SECTION START ---
        # Load both baseline cost and baseline time
        baseline_data = {}

        metadata['sum_baseline_cost'] = sum_baseline
        metadata['sum_baseline_time'] = time_baseline

        for _, r in base_df.iterrows():
            eid = r['employee_id']
            # safely get time, assuming column might be 'baseline_time', 'time', or 'baseline_travel_time'
            b_time = r.get('baseline_time', r.get('time', r.get('baseline_travel_time', 0)))
            
            baseline_data[eid] = {
                'cost': float(r['baseline_cost']),
                'time': float(b_time)
            }
        # --- MODIFIED SECTION END ---

        office = Location(emp_df['drop_lat'].iloc[0], emp_df['drop_lng'].iloc[0])
        
        employees = []
        for _, r in emp_df.iterrows():
            vp = r['vehicle_preference'].lower()
            sp = r['sharing_preference'].lower()
            
            # Get baseline info
            b_info = baseline_data.get(r['employee_id'], {'cost': 0, 'time': 0})
            
            emp = Employee(
                id=r['employee_id'],
                priority=int(r['priority']),
                pickup=Location(r['pickup_lat'], r['pickup_lng']),
                dropoff=office,
                earliest_pickup=DataLoader.parse_time(r['earliest_pickup']),
                latest_drop=DataLoader.parse_time(r['latest_drop']),
                vehicle_preference=VehiclePreference(vp) if vp in ['premium','normal','any'] else VehiclePreference.ANY,
                sharing_preference=SharingPreference(sp) if sp in ['single','double','triple'] else SharingPreference.TRIPLE,
                baseline_cost=b_info['cost']
            )
            
            # --- MODIFIED: Attach the calculated weighted baseline value ---
            # Value = (alpha * baseline_cost) + (beta * baseline_time)
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
    
    def distance(self, a: Location, b: Location) -> Tuple[float, List[Any]]:
        key = (a.lat, a.lng, b.lat, b.lng)
        if key not in self._dist_cache:
            self._dist_cache[key] = a.distance_to(b)
        return self._dist_cache[key]
    
    def travel_time(self, a: Location, b: Location, speed: float) -> float:
        return (self.distance(a, b)[0] / speed) * 60
    
    def check_time_constraint(self, vehicle: Vehicle, employee_ids: List[str],
                               pickup_sequence: List[str], start_time: float,
                               start_location: Location) -> Tuple[bool, Dict]:
        if not pickup_sequence:
            return True, {'satisfied': True}
        
        deadline = min(self.employees[eid].adjusted_latest_drop for eid in employee_ids)
        
        current_time = start_time
        current_loc = start_location
        pickup_times = {}
        total_dist = 0.0
        final_route = []
        
        for eid in pickup_sequence:
            emp = self.employees[eid]
            
            dist, route = self.distance(current_loc, emp.pickup)
            travel = self.travel_time(current_loc, emp.pickup, vehicle.avg_speed)
            total_dist += dist
            final_route += route[:-1]
            
            arrival_at_pickup = current_time + travel
            actual_pickup = max(arrival_at_pickup, emp.earliest_pickup)
            pickup_times[eid] = actual_pickup
            
            current_time = actual_pickup + self.SERVICE_TIME
            current_loc = emp.pickup
        
        dist, route = self.distance(current_loc, self.office)
        travel = self.travel_time(current_loc, self.office, vehicle.avg_speed)
        total_dist += dist
        final_route += route
        arrival_at_office = current_time + travel
        
        is_feasible = arrival_at_office <= deadline
        
        return is_feasible, {
            'satisfied': is_feasible,
            'deadline': deadline,
            'arrival_at_office': arrival_at_office,
            'slack': deadline - arrival_at_office,
            'pickup_times': pickup_times,
            'total_distance': total_dist,
            'route': final_route,
            'start_time': start_time,
            'start_location': str(start_location)
        }
    
    def check_capacity_sharing(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        n = len(employee_ids)
        if n == 0:
            return True, {'satisfied': True, 'n': 0, 'max_allowed': vehicle.capacity}
        
        sharing_limits = [self.employees[eid].max_passengers for eid in employee_ids]
        max_allowed = min(vehicle.capacity, *sharing_limits)
        
        is_feasible = n <= max_allowed
        
        return is_feasible, {
            'satisfied': is_feasible,
            'n': n,
            'max_allowed': max_allowed,
            'vehicle_capacity': vehicle.capacity,
            'sharing_limits': {eid: self.employees[eid].max_passengers for eid in employee_ids}
        }
    
    def check_vehicle_type(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        if not employee_ids:
            return True, {'satisfied': True}
        
        allowed = self.employees[employee_ids[0]].allowed_vehicle_types.copy()
        for eid in employee_ids[1:]:
            allowed = allowed & self.employees[eid].allowed_vehicle_types
        
        vehicle_cat = vehicle.category.lower()
        is_feasible = vehicle_cat in allowed
        
        return is_feasible, {
            'satisfied': is_feasible,
            'vehicle_category': vehicle_cat,
            'allowed_intersection': list(allowed),
            'employee_preferences': {eid: self.employees[eid].vehicle_preference.value for eid in employee_ids}
        }
    
    def is_trip_feasible(self, vehicle: Vehicle, employee_ids: List[str],
                         pickup_sequence: List[str], start_time: float,
                         start_location: Location) -> Tuple[bool, Dict]:
        
        cap_ok, cap_details = self.check_capacity_sharing(vehicle, employee_ids)
        type_ok, type_details = self.check_vehicle_type(vehicle, employee_ids)
        
        if not (cap_ok and type_ok):
            return False, {
                'feasible': False,
                'capacity_sharing': cap_details,
                'vehicle_type': type_details,
                'time': {'satisfied': False, 'reason': 'Skipped'}
            }
        
        time_ok, time_details = self.check_time_constraint(
            vehicle, employee_ids, pickup_sequence, start_time, start_location
        )
        
        return time_ok and cap_ok and type_ok, {
            'feasible': time_ok and cap_ok and type_ok,
            'time': time_details,
            'capacity_sharing': cap_details,
            'vehicle_type': type_details
        }
    
    def find_best_sequence(self, vehicle: Vehicle, employee_ids: List[str],
                           start_time: float, start_location: Location) -> Optional[List[str]]:
        if not employee_ids:
            return []
        
        if len(employee_ids) == 1:
            ok, _ = self.check_time_constraint(vehicle, employee_ids, employee_ids, start_time, start_location)
            return employee_ids if ok else None
        
        if len(employee_ids) <= 4:
            # Exact: try all permutations (max 4! = 24)
            best_seq = None
            best_arrival = float('inf')
            
            for perm in permutations(employee_ids):
                seq = list(perm)
                ok, details = self.check_time_constraint(vehicle, employee_ids, seq, start_time, start_location)
                if ok and details['arrival_at_office'] < best_arrival:
                    best_arrival = details['arrival_at_office']
                    best_seq = seq
            
            return best_seq
        else:
            # Heuristic for 5+ employees: try multiple strategies, pick best feasible
            best_seq = None
            best_arrival = float('inf')
            
            # Strategy 1: Sort by earliest pickup
            seq1 = sorted(employee_ids, key=lambda eid: self.employees[eid].earliest_pickup)
            ok, details = self.check_time_constraint(vehicle, employee_ids, seq1, start_time, start_location)
            if ok and details['arrival_at_office'] < best_arrival:
                best_arrival = details['arrival_at_office']
                best_seq = seq1
            
            # Strategy 2: Sort by tightest deadline first
            seq2 = sorted(employee_ids, key=lambda eid: self.employees[eid].adjusted_latest_drop)
            ok, details = self.check_time_constraint(vehicle, employee_ids, seq2, start_time, start_location)
            if ok and details['arrival_at_office'] < best_arrival:
                best_arrival = details['arrival_at_office']
                best_seq = seq2
            
            # Strategy 3: Nearest-neighbor from start location
            remaining = list(employee_ids)
            nn_seq = []
            current_loc = start_location
            while remaining:
                best_eid = min(remaining, key=lambda eid: self.distance(current_loc, self.employees[eid].pickup)[0])
                nn_seq.append(best_eid)
                current_loc = self.employees[best_eid].pickup
                remaining.remove(best_eid)
            
            ok, details = self.check_time_constraint(vehicle, employee_ids, nn_seq, start_time, start_location)
            if ok and details['arrival_at_office'] < best_arrival:
                best_arrival = details['arrival_at_office']
                best_seq = nn_seq
            
            return best_seq


# =============================================================================
# PROBLEM STATE
# =============================================================================

class ProblemState:
    def __init__(self, employees: List[Employee], vehicles: List[Vehicle], 
                 office: Location, metadata: Dict = None):
        self.employees = {e.id: e for e in employees}
        self.vehicles = {v.id: v for v in vehicles}
        self.EMPLOYEES = list(employees)
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
    Enhanced PWSA following the algorithm specification exactly.
    All constraint checks are performed before any assignment.
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
        self.top_k_seeds = self.config.get('top_k_seeds', None)  # None = auto
        
        # Precomputed data
        self._savings_list: List[SavingsEntry] = []
        self._individual_costs: Dict[str, Dict[str, float]] = {}  # emp_id -> vid -> cost
        self._individual_times: Dict[str, Dict[str, float]] = {}  # emp_id -> vid -> time
        self._compatible_vehicles: Dict[str, Set[str]] = {}       # emp_id -> set of vids
        
    def build(self) -> 'Solution':
        """
        Main entry point - builds solution using PWSA algorithm.
        """
        # Phase I: Savings Calculation
        self._phase1_savings_calculation()
        
        # Phase II: Hybrid Construction
        solution = self._phase2_hybrid_construction()
        
        # Phase III: Multi-Trip Consolidation
        solution = self._phase3_consolidation(solution)
        
        # Final validation
        solution = self._final_validation(solution)
        
        return solution
    
    # =========================================================================
    # PHASE I: SAVINGS CALCULATION
    # =========================================================================
    
    def _phase1_savings_calculation(self):
        """
        Phase I: Calculate savings for all pairs.
        S_ij = α·ΔC + β·ΔT where:
        - ΔC = cost_i + cost_j - cost_combined
        - ΔT = time_i + time_j - time_combined
        """
        # Step 1: Compute individual costs and times for each employee
        self._compute_individual_metrics()
        
        # Step 2: Calculate savings for all pairs
        self._savings_list = []
        emp_ids = [e.id for e in self.state.emp_list]
        n = len(emp_ids)
        
        for i in range(n):
            for j in range(i + 1, n):
                emp_i, emp_j = emp_ids[i], emp_ids[j]
                
                # Find common compatible vehicles
                common_vehicles = (
                    self._compatible_vehicles.get(emp_i, set()) &
                    self._compatible_vehicles.get(emp_j, set())
                )
                
                if not common_vehicles:
                    continue
                
                # Check if merge satisfies Capacity & Time Windows (line 4 of algorithm)
                entry = self._compute_pair_savings(emp_i, emp_j, common_vehicles)
                
                if entry and entry.weighted_savings > 0:
                    self._savings_list.append(entry)
        
        # Sort by weighted savings S_ij in descending order (line 11)
        self._savings_list.sort(key=lambda x: x.weighted_savings, reverse=True)
    
    def _compute_individual_metrics(self):
        """Compute individual cost and time for each employee with each vehicle."""
        for emp in self.state.emp_list:
            self._individual_costs[emp.id] = {}
            self._individual_times[emp.id] = {}
            self._compatible_vehicles[emp.id] = set()
            
            for vehicle in self.state.veh_list:
                # CONSTRAINT 3: Check vehicle type compatibility
                if vehicle.category.lower() not in emp.allowed_vehicle_types:
                    continue
                
                # CONSTRAINT 2: Check capacity (single employee)
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [emp.id])
                if not cap_ok:
                    continue
                
                # CONSTRAINT 1: Check time feasibility
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
        """
        Compute savings for a pair of employees.
        Only returns entry if merge satisfies Capacity & Time Windows.
        """
        best_cost_savings = float('-inf')
        best_time_savings = float('-inf')
        best_weighted = float('-inf')
        best_vehicle = None
        best_sequence = None
        feasible_vehicles = set()
        
        for vid in common_vehicles:
            vehicle = self.vehicles[vid]
            
            # CONSTRAINT 2: Check capacity & sharing for pair
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [emp_i, emp_j])
            if not cap_ok:
                continue
            
            # CONSTRAINT 3: Check vehicle type for pair
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [emp_i, emp_j])
            if not type_ok:
                continue
            
            # Find best sequence that satisfies CONSTRAINT 1 (time)
            best_seq = self.constraints.find_best_sequence(
                vehicle, [emp_i, emp_j],
                vehicle.available_from, vehicle.start_location
            )
            
            if not best_seq:
                continue  # No feasible sequence found
            
            # CONSTRAINT 1: Verify time constraint
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [emp_i, emp_j], best_seq,
                vehicle.available_from, vehicle.start_location
            )
            
            if not time_ok:
                continue
            
            # All constraints satisfied - calculate savings
            feasible_vehicles.add(vid)
            
            combined_cost = details['total_distance'] * vehicle.cost_per_km
            combined_time = details['arrival_at_office'] - vehicle.available_from
            
            # Get individual metrics
            cost_i = self._individual_costs.get(emp_i, {}).get(vid, float('inf'))
            cost_j = self._individual_costs.get(emp_j, {}).get(vid, float('inf'))
            time_i = self._individual_times.get(emp_i, {}).get(vid, float('inf'))
            time_j = self._individual_times.get(emp_j, {}).get(vid, float('inf'))
            
            if cost_i == float('inf') or cost_j == float('inf'):
                continue
            
            # Calculate savings (lines 5-7 of algorithm)
            delta_c = cost_i + cost_j - combined_cost
            delta_t = time_i + time_j - combined_time
            s_ij = self.alpha * delta_c + self.beta * delta_t
            
            if s_ij > best_weighted:
                best_cost_savings = delta_c
                best_time_savings = delta_t
                best_weighted = s_ij
                best_vehicle = vid
                best_sequence = best_seq
        
        if best_weighted <= 0 or not feasible_vehicles:
            return None
        
        return SavingsEntry(
            employee_i=emp_i,
            employee_j=emp_j,
            cost_savings=best_cost_savings,
            time_savings=best_time_savings,
            weighted_savings=best_weighted,
            compatible_vehicles=feasible_vehicles,
            best_vehicle=best_vehicle,
            best_sequence=best_sequence
        )
    
    # =========================================================================
    # PHASE II: HYBRID CONSTRUCTION
    # =========================================================================
    
    def _phase2_hybrid_construction(self) -> 'Solution':
        """
        Phase II: Hybrid Construction
        - Step 1: Seed Routes from top k pairs
        - Step 2: Regret-k Insertion for remaining
        """
        # Initialize empty routes (line 13)
        solution = Solution()
        for v in self.state.veh_list:
            solution.schedules.append(VehicleSchedule(vehicle=v))
        
        assigned = set()
        
        # Step 1: Seed Routes (lines 14-20)
        assigned = self._step1_seed_routes(solution, assigned)
        
        # Step 2: Regret-k Insertion (lines 21-30)
        assigned = self._step2_regret_insertion(solution, assigned)
        
        return solution
    
    def _step1_seed_routes(self, solution: 'Solution', assigned: Set[str]) -> Set[str]:
        """
        Step 1: Create seed routes from top k pairs in L_savings.
        """
        # Determine k (number of seeds)
        if self.top_k_seeds is None:
            k = max(1, len(self.state.emp_list) // 4)
        else:
            k = self.top_k_seeds
        
        seeds_created = 0
        
        for entry in self._savings_list:
            if seeds_created >= k:
                break
            
            emp_i, emp_j = entry.employee_i, entry.employee_j
            
            # Skip if either already assigned
            if emp_i in assigned or emp_j in assigned:
                continue
            
            # Check if pair can initialize a route (line 16)
            if not self._can_create_route(solution, [emp_i, emp_j], entry.best_vehicle):
                continue
            
            # Create new route with pair (line 17)
            if self._create_trip(solution, [emp_i, emp_j], entry.best_vehicle, entry.best_sequence):
                assigned.add(emp_i)
                assigned.add(emp_j)
                seeds_created += 1
        
        return assigned
    
    def _step2_regret_insertion(self, solution: 'Solution', assigned: Set[str]) -> Set[str]:
        """
        Step 2: Regret-k Insertion for unassigned requests.
        """
        unassigned = [e.id for e in self.state.emp_list if e.id not in assigned]
        
        while unassigned:
            best_emp = None
            best_regret = float('-inf')
            best_insertion = None  # (objective, schedule_idx, trip_idx, is_new_trip)
            
            for eid in unassigned:
                # Calculate insertion costs for all feasible positions (lines 24-25)
                insertion_options = self._get_insertion_options(solution, eid)
                
                if not insertion_options:
                    continue
                
                # Sort by objective (cost)
                insertion_options.sort(key=lambda x: x[0])
                
                c1 = insertion_options[0][0]  # Best cost
                c2 = insertion_options[1][0] if len(insertion_options) >= 2 else float('inf')
                
                # Regret = c2 - c1 (line 26)
                regret = c2 - c1
                
                if regret > best_regret:
                    best_regret = regret
                    best_emp = eid
                    best_insertion = insertion_options[0]
            
            if best_emp is None:
                # No feasible insertion - try to create individual trips
                for eid in unassigned.copy():
                    if self._assign_to_new_trip(solution, eid):
                        assigned.add(eid)
                        unassigned.remove(eid)
                break
            
            # Insert u* into best position (line 29)
            obj, sched_idx, trip_idx, is_new, sequence = best_insertion
            
            if is_new:
                # Create new trip
                schedule = solution.schedules[sched_idx]
                vid = schedule.vehicle.id
                if self._create_trip_for_employee(solution, best_emp, sched_idx):
                    assigned.add(best_emp)
            else:
                # Insert into existing trip
                if self._insert_into_trip(solution, best_emp, sched_idx, trip_idx, sequence):
                    assigned.add(best_emp)
            
            unassigned.remove(best_emp)
        
        return assigned
    
    def _get_insertion_options(self, solution: 'Solution', eid: str) -> List[Tuple]:
        """
        Get all feasible insertion options for an employee.
        Returns list of (objective, schedule_idx, trip_idx, is_new_trip, sequence)
        """
        options = []
        emp = self.employees[eid]
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            
            # CONSTRAINT 3: Check vehicle type
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            # Try inserting into existing trips
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                
                # CONSTRAINT 2: Check capacity & sharing
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok:
                    continue
                
                # CONSTRAINT 3: Check vehicle type for combined group
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok:
                    continue
                
                # Find best sequence satisfying CONSTRAINT 1
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
            
            # Try creating new trip on this vehicle
            start_time, start_loc = self._get_next_trip_start(schedule)
            
            # CONSTRAINT 2: Check capacity
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            if not cap_ok:
                continue
            
            # CONSTRAINT 1: Check time
            time_ok, details = self.constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok:
                objective = self._compute_objective(details, vehicle)
                # Small penalty for new trip to prefer consolidation
                objective *= 1.05
                options.append((objective, sched_idx, -1, True, [eid]))
        
        return options
    
    def _compute_objective(self, details: Dict, vehicle) -> float:
        """Compute objective: α·cost + β·time"""
        total_distance = details.get('total_distance', 0)
        start_time = details.get('start_time', 0)
        arrival_at_office = details.get('arrival_at_office', start_time)
        
        cost = total_distance * vehicle.cost_per_km
        time_val = arrival_at_office - start_time
        
        return self.alpha * cost + self.beta * time_val
    
    def _get_next_trip_start(self, schedule: 'VehicleSchedule') -> Tuple[float, 'Location']:
        """Get start time and location for next trip on this vehicle."""
        if schedule.trips:
            last_trip = schedule.trips[-1]
            start_time = last_trip.arrival_at_office + self.constraints.DROP_TIME
            start_loc = self.office
        else:
            start_time = schedule.vehicle.available_from
            start_loc = schedule.vehicle.start_location
        return start_time, start_loc
    
    def _can_create_route(self, solution: 'Solution', emp_ids: List[str], vid: str) -> bool:
        """Check if we can create a route for these employees on this vehicle."""
        vehicle = self.vehicles[vid]
        
        # Find the schedule for this vehicle
        schedule = None
        for s in solution.schedules:
            if s.vehicle.id == vid:
                schedule = s
                break
        
        if not schedule:
            return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        # Check all constraints
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
    
    def _create_trip(self, solution: 'Solution', emp_ids: List[str], 
                     vid: str, sequence: List[str] = None) -> bool:
        """Create a new trip with the given employees."""
        vehicle = self.vehicles[vid]
        
        # Find schedule
        schedule = None
        for s in solution.schedules:
            if s.vehicle.id == vid:
                schedule = s
                break
        
        if not schedule:
            return False
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        # Find best sequence if not provided
        if not sequence:
            sequence = self.constraints.find_best_sequence(
                vehicle, emp_ids, start_time, start_loc
            )
        
        if not sequence:
            return False
        
        # Verify constraints one more time
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, emp_ids, sequence, start_time, start_loc
        )
        
        # FIX: Verify we have all required details
        if not time_ok:
            return False
        
        if 'arrival_at_office' not in details:
            return False
        
        # Create trip
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
        """Create a new trip for a single employee on the given schedule."""
        schedule = solution.schedules[sched_idx]
        vehicle = schedule.vehicle
        
        start_time, start_loc = self._get_next_trip_start(schedule)
        
        # Verify all constraints
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
        
        if not (cap_ok and type_ok):
            return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, [eid], [eid], start_time, start_loc
        )
        
        # FIX: Verify we have all required details
        if not time_ok:
            return False
        
        if 'arrival_at_office' not in details:
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
            route=details.get('route', [])
        )
        
        schedule.trips.append(trip)
        return True
    
    def _insert_into_trip(self, solution: 'Solution', eid: str, 
                          sched_idx: int, trip_idx: int, sequence: List[str]) -> bool:
        """Insert employee into existing trip."""
        schedule = solution.schedules[sched_idx]
        trip = schedule.trips[trip_idx]
        vehicle = schedule.vehicle
        
        new_emps = trip.employees + [eid]
        
        # Verify all constraints
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, new_emps)
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, new_emps)
        
        if not (cap_ok and type_ok):
            return False
        
        time_ok, details = self.constraints.check_time_constraint(
            vehicle, new_emps, sequence, trip.start_time, trip.start_location
        )
        
        if not time_ok:
            return False
        
        # FIX: Verify details has required keys before updating
        if 'arrival_at_office' not in details:
            return False
        
        # Update trip
        trip.employees = new_emps
        trip.pickup_sequence = sequence
        trip.arrival_at_office = details['arrival_at_office']
        trip.pickup_times = details.get('pickup_times', {})
        trip.distance_km = details.get('total_distance', 0)
        trip.route = details.get('route', [])
        
        # Update subsequent trips' start times (only if there are more trips)
        if trip_idx + 1 < len(schedule.trips):
            self._update_subsequent_trips(schedule, trip_idx + 1)
        
        return True
    
    def _assign_to_new_trip(self, solution: 'Solution', eid: str) -> bool:
        """Try to assign employee to a new trip on any compatible vehicle."""
        emp = self.employees[eid]
        
        best_objective = float('inf')
        best_sched_idx = None
        
        for sched_idx, schedule in enumerate(solution.schedules):
            vehicle = schedule.vehicle
            
            # Check vehicle type
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            start_time, start_loc = self._get_next_trip_start(schedule)
            
            # Check all constraints
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
    
    def _update_subsequent_trips(self, schedule: 'VehicleSchedule', start_idx: int):
        """Update start times for all trips from start_idx onwards."""
        for i in range(start_idx, len(schedule.trips)):
            if i == 0:
                prev_end = schedule.vehicle.available_from
            else:
                prev_end = schedule.trips[i - 1].arrival_at_office + self.constraints.DROP_TIME
            
            trip = schedule.trips[i]
            trip.start_time = prev_end
            trip.start_location = self.office if i > 0 else schedule.vehicle.start_location
            
            # Recalculate trip details
            time_ok, details = self.constraints.check_time_constraint(
                schedule.vehicle, trip.employees, trip.pickup_sequence,
                trip.start_time, trip.start_location
            )
            
            # FIX: Safely access details with defaults (details always has these keys)
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
    
    def _phase3_consolidation(self, solution: 'Solution') -> 'Solution':
        """
        Phase III: Multi-Trip Consolidation
        Try to merge trips on same vehicle while respecting constraints.
        """
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
                    
                    # Try to merge
                    if self._can_merge_trips(schedule, trip_i, trip_j):
                        self._merge_trips(schedule, i)
                        improved = True
                        # Don't increment i - check same position again
                    else:
                        i += 1
        
        return solution
    
    def _can_merge_trips(self, schedule: 'VehicleSchedule', 
                          trip_i: 'Trip', trip_j: 'Trip') -> bool:
        """Check if two consecutive trips can be merged."""
        vehicle = schedule.vehicle
        combined_emps = trip_i.employees + trip_j.employees
        
        # CONSTRAINT 2: Check capacity & sharing
        cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, combined_emps)
        if not cap_ok:
            return False
        
        # CONSTRAINT 3: Check vehicle type
        type_ok, _ = self.constraints.check_vehicle_type(vehicle, combined_emps)
        if not type_ok:
            return False
        
        # Find best sequence satisfying CONSTRAINT 1
        best_seq = self.constraints.find_best_sequence(
            vehicle, combined_emps, trip_i.start_time, trip_i.start_location
        )
        
        if not best_seq:
            return False
        
        time_ok, _ = self.constraints.check_time_constraint(
            vehicle, combined_emps, best_seq, trip_i.start_time, trip_i.start_location
        )
        
        return time_ok
    
    def _merge_trips(self, schedule: 'VehicleSchedule', trip_idx: int):
        """Merge trip at trip_idx with trip at trip_idx+1."""
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
        
        # Update trip_i with merged data
        trip_i.employees = combined_emps
        trip_i.pickup_sequence = best_seq
        trip_i.arrival_at_office = details['arrival_at_office']
        trip_i.pickup_times = details['pickup_times']
        trip_i.distance_km = details['total_distance']
        trip_i.route = details['route']
        
        # Remove trip_j
        schedule.trips.remove(trip_j)
        
        # Update subsequent trips
        self._update_subsequent_trips(schedule, trip_idx + 1)
    
    # =========================================================================
    # FINAL VALIDATION
    # =========================================================================
    
    def _final_validation(self, solution: 'Solution') -> 'Solution':
        """
        Final validation pass - ensure all constraints are satisfied.
        Remove any violating trips and try to reassign those employees.
        """
        removed_employees = []
        
        for schedule in solution.schedules:
            valid_trips = []
            current_time = schedule.vehicle.available_from
            current_loc = schedule.vehicle.start_location
            
            for trip in schedule.trips:
                # Update start time based on actual position
                trip.start_time = current_time
                trip.start_location = current_loc
                
                # Verify ALL constraints
                feasible, details = self.constraints.is_trip_feasible(
                    schedule.vehicle, trip.employees, trip.pickup_sequence,
                    trip.start_time, trip.start_location
                )
                
                if feasible:
                    # FIX: Safely access nested details
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
                    # Trip violates constraints - remove employees for reassignment
                    removed_employees.extend(trip.employees)
            
            schedule.trips = valid_trips
        
        # Try to reassign removed employees
        for eid in removed_employees:
            self._assign_to_best_trip(solution, eid)
        
        return solution
    
    # =========================================================================
    # METHOD REQUIRED BY ALNS REPAIR OPERATORS
    # =========================================================================
    
    def _assign_to_best_trip(self, sol: 'Solution', eid: str) -> bool:
        """
        Assign employee to best feasible trip.
        Used by ALNS repair operators and final validation.
        """
        emp = self.employees[eid]
        best_objective = float('inf')
        best_option = None  # (sched_idx, trip_idx, is_new, sequence)
        
        for sched_idx, schedule in enumerate(sol.schedules):
            vehicle = schedule.vehicle
            
            # CONSTRAINT 3: Check vehicle type
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            # Try existing trips
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                
                # CONSTRAINT 2: Check capacity & sharing
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok:
                    continue
                
                # CONSTRAINT 3: Check vehicle type for group
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok:
                    continue
                
                # Find sequence satisfying CONSTRAINT 1
                best_seq = self.constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                
                if not best_seq:
                    continue
                
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                
                # FIX: Verify we have valid details
                if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                    objective = self._compute_objective(details, vehicle)
                    objective *= 0.9  # Bonus for consolidation
                    
                    if objective < best_objective:
                        best_objective = objective
                        best_option = (sched_idx, trip_idx, False, best_seq)
            
            # Try new trip
            start_time, start_loc = self._get_next_trip_start(schedule)
            
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
            
            if cap_ok and type_ok:
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, [eid], [eid], start_time, start_loc
                )
                
                # FIX: Verify we have valid details
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
    
    def _calculate_solution_cost(self, solution: 'Solution') -> float:
        """Calculate total objective for solution (for compatibility)."""
        total_cost = 0.0
        total_time = 0.0
        
        for schedule in solution.schedules:
            for trip in schedule.trips:
                total_cost += trip.distance_km * schedule.vehicle.cost_per_km
                total_time += trip.arrival_at_office - trip.start_time
        
        return self.alpha * total_cost + self.beta * total_time

# =============================================================================
# DESTROY / REPAIR OPERATORS
# =============================================================================

class DestroyOperators:
    def __init__(self, state: ProblemState):
        self.state = state
        # We need a builder instance to access the update logic
        self.builder = InitialSolutionBuilder(state)
        # Determinism parameter for worst/shaw removal (higher = more deterministic)
        self.p = 3
    
    # =========================================================================
    # 1. RANDOM REMOVAL (existing, unchanged)
    # =========================================================================
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
    
    # =========================================================================
    # 2. WORST REMOVAL (fixed - actual cost-based)
    # =========================================================================
    def worst_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        """
        Remove employees with the highest marginal cost contribution.
        Uses trip-level approximation instead of full solution recomputation.
        Marginal cost ≈ (detour distance caused by this employee) * cost_per_km.
        Uses determinism parameter p to control randomness.
        """
        s = sol.copy()
        
        # Pre-calculate marginal cost for ALL assigned employees in one pass
        marginal_costs = []
        
        for schedule in s.schedules:
            vehicle = schedule.vehicle
            for trip in schedule.trips:
                if len(trip.employees) == 1:
                    # Single employee: marginal = entire trip cost
                    cost = trip.distance_km * vehicle.cost_per_km
                    marginal_costs.append((trip.employees[0], cost))
                    continue
                
                # Multi-employee: estimate each employee's detour contribution
                for idx, eid in enumerate(trip.pickup_sequence):
                    emp = self.state.employees[eid]
                    
                    # Get previous and next locations in sequence
                    if idx == 0:
                        prev_loc = trip.start_location
                    else:
                        prev_eid = trip.pickup_sequence[idx - 1]
                        prev_loc = self.state.employees[prev_eid].pickup
                    
                    if idx < len(trip.pickup_sequence) - 1:
                        next_eid = trip.pickup_sequence[idx + 1]
                        next_loc = self.state.employees[next_eid].pickup
                    else:
                        next_loc = self.state.office
                    
                    # Detour = dist(prev→emp) + dist(emp→next) - dist(prev→next)
                    d_to, _ = self.state.constraints.distance(prev_loc, emp.pickup)
                    d_from, _ = self.state.constraints.distance(emp.pickup, next_loc)
                    d_skip, _ = self.state.constraints.distance(prev_loc, next_loc)
                    detour = max(0.0, (d_to + d_from - d_skip))
                    
                    marginal = detour * vehicle.cost_per_km
                    marginal_costs.append((eid, marginal))
        
        if not marginal_costs:
            return s, []
        
        # Sort descending (highest cost contribution first)
        marginal_costs.sort(key=lambda x: x[1], reverse=True)
        
        removed = []
        removed_set = set()
        
        for _ in range(min(q, len(marginal_costs))):
            # Filter out already-removed employees
            available = [(eid, mc) for eid, mc in marginal_costs if eid not in removed_set]
            if not available:
                break
            
            # Select using determinism parameter p
            idx = int(random.random() ** self.p * len(available))
            idx = min(idx, len(available) - 1)
            
            chosen_eid = available[idx][0]
            self._remove(s, chosen_eid)
            removed.append(chosen_eid)
            removed_set.add(chosen_eid)
        
        return s, removed
    
    # =========================================================================
    # 3. TRIP REMOVAL (existing, unchanged)
    # =========================================================================
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
    
    # =========================================================================
    # 4. SHAW REMOVAL (new - relatedness-based)
    # =========================================================================
    def shaw_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        """
        Remove employees that are 'related' to a randomly chosen seed employee.
        Relatedness is based on: pickup proximity, time window overlap, and
        vehicle type compatibility. Related employees are likely interchangeable,
        so removing them creates a focused gap the repair can fill better.
        """
        s = sol.copy()
        assigned = s.all_assigned()
        q = min(q, len(assigned))
        if q == 0:
            return s, []
        
        # Pick a random seed employee
        seed_eid = random.choice(assigned)
        seed_emp = self.state.employees[seed_eid]
        
        # Compute relatedness scores for all other assigned employees
        # Lower score = more related (closer in all dimensions)
        relatedness_scores = []
        
        for eid in assigned:
            if eid == seed_eid:
                continue
            emp = self.state.employees[eid]
            
            # Spatial relatedness: Euclidean distance between pickups (fast, no graph calls)
            dlat = seed_emp.pickup.lat - emp.pickup.lat
            dlng = seed_emp.pickup.lng - emp.pickup.lng
            avg_lat_rad = math.radians((seed_emp.pickup.lat + emp.pickup.lat) / 2)
            dist = math.sqrt((dlat * 111.0) ** 2 + (dlng * 111.0 * math.cos(avg_lat_rad)) ** 2)
            
            # Temporal relatedness: difference in time windows
            time_diff = abs(seed_emp.latest_drop - emp.latest_drop)
            
            # Vehicle type relatedness: 0 if same preference, 1 if different
            vtype_diff = 0.0 if seed_emp.vehicle_preference == emp.vehicle_preference else 1.0
            
            # Sharing relatedness: 0 if same, 1 if different
            share_diff = 0.0 if seed_emp.sharing_preference == emp.sharing_preference else 1.0
            
            # Weighted relatedness (lower = more related)
            # Normalize dist by a reasonable max (~50km), time by max (~120 min)
            score = (dist / 50.0) + (time_diff / 120.0) + vtype_diff + share_diff
            relatedness_scores.append((eid, score))
        
        # Sort by relatedness ascending (most related first)
        relatedness_scores.sort(key=lambda x: x[1])
        
        removed = [seed_eid]
        self._remove(s, seed_eid)
        
        # Pick q-1 more employees, biased toward most related
        for eid, _ in relatedness_scores:
            if len(removed) >= q:
                break
            # Use determinism parameter p to bias toward most related
            if random.random() ** self.p < (len(removed) / q):
                self._remove(s, eid)
                removed.append(eid)
        
        # If we still need more, fill greedily from the front
        for eid, _ in relatedness_scores:
            if len(removed) >= q:
                break
            if eid not in removed:
                self._remove(s, eid)
                removed.append(eid)
        
        return s, removed
    
    # =========================================================================
    # 5. TIME-WINDOW REMOVAL (new - detour/wait based)
    # =========================================================================
    def time_window_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        """
        Remove employees that cause the most 'time inefficiency' in their trips.
        Time inefficiency = waiting time (vehicle arrives before earliest_pickup)
        + detour time (extra travel caused by this pickup location).
        """
        s = sol.copy()
        assigned = s.all_assigned()
        q = min(q, len(assigned))
        if q == 0:
            return s, []
        
        # Calculate time inefficiency for each assigned employee
        inefficiency_scores = []
        
        for schedule in s.schedules:
            vehicle = schedule.vehicle
            for trip in schedule.trips:
                if len(trip.employees) <= 1:
                    # For single-employee trips, measure total trip time vs direct time
                    if trip.employees:
                        eid = trip.employees[0]
                        trip_duration = trip.arrival_at_office - trip.start_time
                        # Direct time from start to pickup to office
                        direct_travel = self.state.constraints.travel_time(
                            trip.start_location, self.state.employees[eid].pickup, vehicle.avg_speed
                        ) + self.state.constraints.travel_time(
                            self.state.employees[eid].pickup, self.state.office, vehicle.avg_speed
                        )
                        inefficiency = trip_duration - direct_travel
                        inefficiency_scores.append((eid, max(0.0, inefficiency)))
                    continue
                
                # For multi-employee trips, measure each employee's contribution
                for eid in trip.employees:
                    emp = self.state.employees[eid]
                    
                    # Waiting time: how long vehicle waits at this pickup
                    pickup_time = trip.pickup_times.get(eid, trip.start_time)
                    
                    # Find arrival time at this pickup from the sequence
                    seq_idx = trip.pickup_sequence.index(eid) if eid in trip.pickup_sequence else 0
                    
                    if seq_idx == 0:
                        prev_loc = trip.start_location
                        prev_time = trip.start_time
                    else:
                        prev_eid = trip.pickup_sequence[seq_idx - 1]
                        prev_loc = self.state.employees[prev_eid].pickup
                        prev_time = trip.pickup_times.get(prev_eid, trip.start_time)
                    
                    travel_to_pickup = self.state.constraints.travel_time(
                        prev_loc, emp.pickup, vehicle.avg_speed
                    )
                    arrival_at_pickup = prev_time + travel_to_pickup
                    wait_time = max(0.0, emp.earliest_pickup - arrival_at_pickup)
                    
                    # Detour: extra distance compared to skipping this pickup
                    if seq_idx < len(trip.pickup_sequence) - 1:
                        next_eid = trip.pickup_sequence[seq_idx + 1]
                        next_loc = self.state.employees[next_eid].pickup
                    else:
                        next_loc = self.state.office
                    
                    dist_with, _ = self.state.constraints.distance(prev_loc, emp.pickup)
                    dist_with2, _ = self.state.constraints.distance(emp.pickup, next_loc)
                    dist_without, _ = self.state.constraints.distance(prev_loc, next_loc)
                    detour_km = (dist_with + dist_with2) - dist_without
                    detour_time = (detour_km / vehicle.avg_speed) * 60
                    
                    inefficiency = wait_time + max(0.0, detour_time)
                    inefficiency_scores.append((eid, inefficiency))
        
        if not inefficiency_scores:
            return s, []
        
        # Sort descending (most inefficient first)
        inefficiency_scores.sort(key=lambda x: x[1], reverse=True)
        
        removed = []
        for eid, _ in inefficiency_scores:
            if len(removed) >= q:
                break
            # Use determinism parameter
            idx = int(random.random() ** self.p * len(inefficiency_scores))
            candidate = inefficiency_scores[idx][0]
            if candidate not in removed:
                self._remove(s, candidate)
                removed.append(candidate)
        
        # Fill up if needed
        for eid, _ in inefficiency_scores:
            if len(removed) >= q:
                break
            if eid not in removed:
                self._remove(s, eid)
                removed.append(eid)
        
        return s, removed
    
    # =========================================================================
    # 6. VEHICLE-TYPE CLUSTER REMOVAL (new - constraint pressure based)
    # =========================================================================
    def vehicle_cluster_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        """
        Remove employees that create vehicle-type constraint pressure.
        Targets trips where a premium-only employee forces use of an expensive
        premium vehicle, or where mixed preferences limit flexibility.
        """
        s = sol.copy()
        assigned = s.all_assigned()
        q = min(q, len(assigned))
        if q == 0:
            return s, []
        
        # Score each employee by constraint pressure
        pressure_scores = []
        
        for schedule in s.schedules:
            vehicle = schedule.vehicle
            is_premium_vehicle = vehicle.category.lower() == 'premium'
            
            for trip in schedule.trips:
                for eid in trip.employees:
                    emp = self.state.employees[eid]
                    pressure = 0.0
                    
                    # Premium employees on premium vehicles create cost pressure
                    if emp.vehicle_preference == VehiclePreference.PREMIUM and is_premium_vehicle:
                        # Count how many non-premium employees share this trip
                        non_premium_count = sum(
                            1 for other_eid in trip.employees
                            if self.state.employees[other_eid].vehicle_preference != VehiclePreference.PREMIUM
                        )
                        # High pressure if premium employee forces others onto expensive vehicle
                        pressure += 2.0 + non_premium_count
                    
                    # Single-sharing employees limit trip consolidation
                    if emp.sharing_preference == SharingPreference.SINGLE:
                        pressure += 3.0  # Forces dedicated trip
                    elif emp.sharing_preference == SharingPreference.DOUBLE:
                        pressure += 1.0  # Limits group size
                    
                    # Tight time window creates scheduling pressure
                    tolerance = emp.adjusted_latest_drop - emp.latest_drop
                    if tolerance <= 5:
                        pressure += 1.5
                    elif tolerance <= 10:
                        pressure += 0.5
                    
                    pressure_scores.append((eid, pressure))
        
        if not pressure_scores:
            return s, []
        
        # Sort descending (highest pressure first)
        pressure_scores.sort(key=lambda x: x[1], reverse=True)
        
        removed = []
        for eid, _ in pressure_scores:
            if len(removed) >= q:
                break
            if eid not in removed:
                self._remove(s, eid)
                removed.append(eid)
        
        return s, removed
    
    # =========================================================================
    # 7. TIME-SEED REMOVAL (new - from paper Eq. 20)
    # =========================================================================
    def time_seed_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        """
        Paper-inspired time-related removal (Eq. 20).
        Pick a random seed time ts, then remove employees whose service time
        is closest to ts. This creates a temporal cluster gap.
        Unlike time_window_removal which targets inefficiency, this targets
        a specific time band.
        """
        s = sol.copy()
        assigned = s.all_assigned()
        q = min(q, len(assigned))
        if q == 0:
            return s, []
        
        # Collect all pickup times to determine the time range
        all_pickup_times = []
        for schedule in s.schedules:
            for trip in schedule.trips:
                for eid in trip.employees:
                    pt = trip.pickup_times.get(eid, trip.start_time)
                    all_pickup_times.append((eid, pt))
        
        if not all_pickup_times:
            return s, []
        
        # Pick a random seed time within the range of actual pickup times
        times_only = [t for _, t in all_pickup_times]
        ts = random.uniform(min(times_only), max(times_only))
        
        # Calculate distance from seed time for each employee (paper Eq. 20)
        # Jk = |pickup_time - ts| (simplified since we only have pickup, not service window)
        time_distances = []
        for eid, pt in all_pickup_times:
            emp = self.state.employees[eid]
            # Use the paper's three-case formula adapted to our context:
            # If ts < earliest_pickup: distance = earliest_pickup - ts
            # If earliest_pickup <= ts <= latest_drop: distance = 0
            # If ts > latest_drop: distance = ts - latest_drop
            if ts < emp.earliest_pickup:
                dist = emp.earliest_pickup - ts
            elif ts > emp.adjusted_latest_drop:
                dist = ts - emp.adjusted_latest_drop
            else:
                dist = 0.0
            time_distances.append((eid, dist))
        
        # Sort ascending (closest to seed time first)
        time_distances.sort(key=lambda x: x[1])
        
        # Remove using determinism parameter
        removed = []
        seen = set()
        for eid, _ in time_distances:
            if len(removed) >= q:
                break
            if eid in seen:
                continue
            seen.add(eid)
            # Use determinism parameter p
            idx = int(random.random() ** self.p * len(time_distances))
            idx = min(idx, len(time_distances) - 1)
            candidate = time_distances[idx][0]
            if candidate not in removed:
                self._remove(s, candidate)
                removed.append(candidate)
        
        # Fill up if needed
        for eid, _ in time_distances:
            if len(removed) >= q:
                break
            if eid not in removed:
                self._remove(s, eid)
                removed.append(eid)
        
        return s, removed
    
    # =========================================================================
    # SHARED: _remove helper
    # =========================================================================
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

class RepairOperators:
    def __init__(self, state: ProblemState):
        self.state = state
        self.builder = InitialSolutionBuilder(state)
        # Paper Eq. 25: noise parameter η = 0.25
        self.noise_eta = 0.25
        # Precompute dmax (maximum distance between any two employee pickups)
        self._dmax = self._compute_dmax()
    
    def _compute_dmax(self) -> float:
        """Compute approximate max distance between employee pickups using Euclidean distance.
        Uses coordinate math instead of graph-based distance calls for speed."""
        emps = list(self.state.employees.values())
        if len(emps) < 2:
            return 1.0
        
        dmax = 0.0
        # Sample if too many employees
        candidates = emps if len(emps) <= 30 else random.sample(emps, 30)
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                # Approximate Euclidean distance in km
                # 1 degree lat ≈ 111 km, 1 degree lng ≈ 111 * cos(lat) km
                dlat = candidates[i].pickup.lat - candidates[j].pickup.lat
                dlng = candidates[i].pickup.lng - candidates[j].pickup.lng
                avg_lat_rad = math.radians((candidates[i].pickup.lat + candidates[j].pickup.lat) / 2)
                dist = math.sqrt((dlat * 111.0) ** 2 + (dlng * 111.0 * math.cos(avg_lat_rad)) ** 2)
                if dist > dmax:
                    dmax = dist
        return max(dmax, 1.0)  # Avoid zero
    
    # =========================================================================
    # 1. GREEDY INSERTION (existing, unchanged)
    # =========================================================================
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
    
    # =========================================================================
    # 2. REGRET-K INSERTION (fixed - actual regret logic)
    # =========================================================================
    def regret_insertion(self, sol: Solution, removed: List[str], k: int = 2) -> Solution:
        """
        Actual Regret-k insertion. For each unassigned employee, compute insertion
        costs at all feasible positions. Regret = c_k - c_1 (difference between
        k-th best and best insertion cost). Insert the employee with highest regret
        first, since they have the most to lose if not inserted in their best spot.
        
        Performance: Falls back to greedy for large removal sets (>10) since
        regret computation is O(n² × m) per iteration.
        """
        # Performance guard: regret is expensive for large removals
        if len(removed) > 10:
            return self.greedy_insertion(sol, removed)
        
        s = sol.copy()
        unassigned = list(removed)
        
        while unassigned:
            best_emp = None
            best_regret = float('-inf')
            best_insertion_info = None  # (sched_idx, trip_idx, is_new, sequence)
            
            for eid in unassigned:
                # Get all feasible insertion options
                options = self._get_all_insertion_options(s, eid)
                
                if not options:
                    continue
                
                # Sort by objective (ascending = cheapest first)
                options.sort(key=lambda x: x[0])
                
                c1 = options[0][0]  # Best insertion cost
                
                if len(options) >= k:
                    ck = options[k - 1][0]  # k-th best
                else:
                    ck = options[-1][0] if len(options) > 1 else float('inf')
                
                regret = ck - c1
                
                # Tie-break by insertion cost (prefer cheaper)
                if regret > best_regret or (regret == best_regret and 
                        best_insertion_info is not None and c1 < best_insertion_info[0]):
                    best_regret = regret
                    best_emp = eid
                    best_insertion_info = options[0]
            
            if best_emp is None:
                # No feasible insertion found for any remaining employee
                # Try individual new trips as last resort
                for eid in unassigned.copy():
                    if self._try_new_trip_anywhere(s, eid):
                        unassigned.remove(eid)
                break
            
            # Insert the employee with highest regret into their best position
            obj, sched_idx, trip_idx, is_new, sequence = best_insertion_info
            
            if is_new:
                self.builder._create_trip_for_employee(s, best_emp, sched_idx)
            else:
                self.builder._insert_into_trip(s, best_emp, sched_idx, trip_idx, sequence)
            
            unassigned.remove(best_emp)
        
        return s
    
    # =========================================================================
    # 3. NOISE INSERTION (new - greedy with perturbation)
    # =========================================================================
    def noise_insertion(self, sol: Solution, removed: List[str]) -> Solution:
        """
        Greedy insertion with random noise added to the objective evaluation.
        This diversifies the search by sometimes choosing suboptimal insertions
        that can lead to better global solutions.
        """
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
            self._assign_with_noise(s, eid)
        
        return s
    
    def _assign_with_noise(self, sol: Solution, eid: str) -> bool:
        """
        Assign employee to a position with noise-perturbed objective.
        Paper Eq. 25: cost_i = cost_i + η * z * dmax
        where η=0.25, z∈[-1,1], dmax is max distance between employees.
        """
        emp = self.state.employees[eid]
        options = self._get_all_insertion_options(sol, eid)
        
        if not options:
            return self._try_new_trip_anywhere(sol, eid)
        
        # Paper Eq. 25: noise = η * z * dmax, z ∈ [-1, 1]
        noisy_options = []
        for obj, sched_idx, trip_idx, is_new, sequence in options:
            z = random.uniform(-1.0, 1.0)
            noise = self.noise_eta * z * self._dmax
            noisy_obj = obj + noise
            noisy_options.append((noisy_obj, sched_idx, trip_idx, is_new, sequence))
        
        # Pick the best under noisy objective
        noisy_options.sort(key=lambda x: x[0])
        _, sched_idx, trip_idx, is_new, sequence = noisy_options[0]
        
        if is_new:
            return self.builder._create_trip_for_employee(sol, eid, sched_idx)
        else:
            return self.builder._insert_into_trip(sol, eid, sched_idx, trip_idx, sequence)
    
    # =========================================================================
    # 4. PRIORITY-AWARE INSERTION (new - most constrained first)
    # =========================================================================
    def priority_aware_insertion(self, sol: Solution, removed: List[str]) -> Solution:
        """
        Insert the most constrained employees first. Constrainedness is measured
        by: number of compatible vehicles (fewer = more constrained), time window
        tightness, sharing restriction, and vehicle type restriction.
        """
        s = sol.copy()
        
        # Score each employee's constrainedness (higher = more constrained)
        scored = []
        for eid in removed:
            emp = self.state.employees[eid]
            score = 0.0
            
            # Vehicle type constraint: premium-only is most constrained
            if emp.vehicle_preference == VehiclePreference.PREMIUM:
                score += 10.0
            elif emp.vehicle_preference == VehiclePreference.NORMAL:
                score += 5.0
            # ANY = least constrained, no bonus
            
            # Sharing constraint: single is most constrained
            if emp.sharing_preference == SharingPreference.SINGLE:
                score += 10.0
            elif emp.sharing_preference == SharingPreference.DOUBLE:
                score += 5.0
            
            # Time window tightness: tighter = more constrained
            window = emp.adjusted_latest_drop - emp.earliest_pickup
            if window < 60:
                score += 8.0
            elif window < 90:
                score += 4.0
            elif window < 120:
                score += 2.0
            
            # Priority: higher priority employees should be placed first
            # (lower priority number = higher importance)
            score += max(0, 6 - emp.priority)
            
            # Estimate constrainedness from vehicle compatibility (fast, no constraint checks)
            # Count how many vehicles match this employee's type preference
            compatible_count = sum(
                1 for v in self.state.vehicles.values()
                if v.category.lower() in emp.allowed_vehicle_types
            )
            total_vehicles = len(self.state.vehicles)
            if compatible_count == 0:
                score += 20.0
            elif compatible_count <= total_vehicles * 0.25:
                score += 8.0
            elif compatible_count <= total_vehicles * 0.5:
                score += 3.0
            
            scored.append((eid, score))
        
        # Sort descending (most constrained first)
        scored.sort(key=lambda x: x[1], reverse=True)
        
        for eid, _ in scored:
            self.builder._assign_to_best_trip(s, eid)
        
        return s
    
    # =========================================================================
    # SHARED HELPERS
    # =========================================================================
    def _get_all_insertion_options(self, sol: Solution, eid: str) -> List[Tuple]:
        """
        Get all feasible insertion options for an employee.
        Returns list of (objective, schedule_idx, trip_idx, is_new_trip, sequence).
        """
        options = []
        emp = self.state.employees[eid]
        constraints = self.state.constraints
        
        for sched_idx, schedule in enumerate(sol.schedules):
            vehicle = schedule.vehicle
            
            # CONSTRAINT 3: Check vehicle type
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            # Try inserting into existing trips
            for trip_idx, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                
                # CONSTRAINT 2: Check capacity & sharing
                cap_ok, _ = constraints.check_capacity_sharing(vehicle, test_emps)
                if not cap_ok:
                    continue
                
                # CONSTRAINT 3: Check vehicle type for combined group
                type_ok, _ = constraints.check_vehicle_type(vehicle, test_emps)
                if not type_ok:
                    continue
                
                # Find best sequence satisfying CONSTRAINT 1
                best_seq = constraints.find_best_sequence(
                    vehicle, test_emps, trip.start_time, trip.start_location
                )
                
                if not best_seq:
                    continue
                
                time_ok, details = constraints.check_time_constraint(
                    vehicle, test_emps, best_seq, trip.start_time, trip.start_location
                )
                
                if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                    objective = self.builder._compute_objective(details, vehicle)
                    options.append((objective, sched_idx, trip_idx, False, best_seq))
            
            # Try creating new trip on this vehicle
            start_time, start_loc = self.builder._get_next_trip_start(schedule)
            
            # CONSTRAINT 2: Check capacity
            cap_ok, _ = constraints.check_capacity_sharing(vehicle, [eid])
            if not cap_ok:
                continue
            
            # CONSTRAINT 1: Check time
            time_ok, details = constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok and 'arrival_at_office' in details and 'total_distance' in details:
                objective = self.builder._compute_objective(details, vehicle)
                # Small penalty for new trip to prefer consolidation
                objective *= 1.05
                options.append((objective, sched_idx, -1, True, [eid]))
        
        return options
    
    def _try_new_trip_anywhere(self, sol: Solution, eid: str) -> bool:
        """Try to assign employee to a new trip on any compatible vehicle."""
        emp = self.state.employees[eid]
        constraints = self.state.constraints
        
        best_objective = float('inf')
        best_sched_idx = None
        
        for sched_idx, schedule in enumerate(sol.schedules):
            vehicle = schedule.vehicle
            
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            start_time, start_loc = self.builder._get_next_trip_start(schedule)
            
            cap_ok, _ = constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = constraints.check_vehicle_type(vehicle, [eid])
            
            if not (cap_ok and type_ok):
                continue
            
            time_ok, details = constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            if time_ok and 'arrival_at_office' in details:
                objective = self.builder._compute_objective(details, vehicle)
                if objective < best_objective:
                    best_objective = objective
                    best_sched_idx = sched_idx
        
        if best_sched_idx is not None:
            return self.builder._create_trip_for_employee(sol, eid, best_sched_idx)
        
        return False


# =============================================================================
# ALNS
# =============================================================================

class ALNSConfig:
    def __init__(self):
        # q_min and q_max are set dynamically in ALNS.__init__ based on problem size
        self.q_min = 1
        self.q_max = 4
        self.max_iter = 2000
        self.max_no_improve = 400
        self.temp_start = 0.05
        self.cooling = 0.9995
        self.temp_accept_prob = 0.5  # θ: probability of accepting a τ-worse solution
        # Number of probabilistic initial attempts
        self.num_runs = 10
        # Local search perturbation interval (paper: ψ = 200, use 300 for speed)
        self.perturbation_interval = 300
        # Segment size for adaptive weight updates (paper: ϕ = 200)
        self.segment_size = 200
        # Reaction factor for weight updates (paper: ρ = 0.9)
        self.reaction_factor = 0.9


class ALNS:
    def __init__(self, state: ProblemState, config: ALNSConfig = None):
        self.state = state
        self.cfg = config or ALNSConfig()
        
        self.destroy = DestroyOperators(state)
        self.repair = RepairOperators(state)
        
        self.destroy_ops = [
            self.destroy.random_removal,         # 0. Random
            self.destroy.worst_removal,           # 1. Worst (cost-based)
            self.destroy.trip_removal,            # 2. Trip removal
            self.destroy.shaw_removal,            # 3. Shaw (relatedness)
            self.destroy.time_window_removal,     # 4. Time-window inefficiency
            self.destroy.vehicle_cluster_removal, # 5. Vehicle-type pressure
            self.destroy.time_seed_removal        # 6. Time-seed (paper Eq. 20)
        ]
        
        self.repair_ops = [
            self.repair.greedy_insertion,                        # 0. Greedy
            lambda s, r: self.repair.regret_insertion(s, r, 2),  # 1. Regret-2
            lambda s, r: self.repair.regret_insertion(s, r, 3),  # 2. Regret-3
            self.repair.noise_insertion,                          # 3. Noise (dmax-scaled)
            self.repair.priority_aware_insertion                  # 4. Priority-aware
        ]
        
        self.n_destroy = len(self.destroy_ops)
        self.n_repair = len(self.repair_ops)
        
        # --- Change #1: Dynamic q range based on problem size ---
        n = state.total_employees
        self.cfg.q_min = max(1, n // 10)
        self.cfg.q_max = max(4, n // 3)
        
        # --- Change #4: Paired (d,r) weight tracking (paper Eq. 14-15) ---
        # pair_weights[d][r] = weight for destroy d + repair r
        self.pair_weights = [[1.0] * self.n_repair for _ in range(self.n_destroy)]
        self.pair_scores = [[0.0] * self.n_repair for _ in range(self.n_destroy)]
        self.pair_counts = [[0] * self.n_repair for _ in range(self.n_destroy)]
        
        # Adaptive weight parameters (paper Table 1)
        self.sigma1 = 33   # New global best
        self.sigma2 = 9    # Better than current
        self.sigma3 = 3    # Accepted (SA)
        
        # Global best across ALL runs
        self.global_best_sol = None
        self.global_best_cost = float('inf')
        self.global_best_breakdown = {}

    def solve(self, verbose=True) -> Tuple[Solution, Dict]:
        t0_total = time.time()
        
        if verbose:
            print(f"Starting Multi-Start ALNS ({self.cfg.num_runs} runs)...")
            print(f"  q range: [{self.cfg.q_min}, {self.cfg.q_max}]")
            print(f"  Destroy ops: {self.n_destroy}, Repair ops: {self.n_repair}, Pairs: {self.n_destroy * self.n_repair}")
            print(f"  Perturbation interval: {self.cfg.perturbation_interval}")
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

    # =========================================================================
    # PAIRED OPERATOR SELECTION (Paper Eq. 14)
    # =========================================================================
    def _select_pair(self) -> Tuple[int, int]:
        """
        Roulette-wheel selection over (destroy, repair) pairs.
        Paper Eq. 14: ρ_dr = ω_dr / Σ ω_dr
        Returns (destroy_idx, repair_idx).
        """
        # Flatten pair weights into a list with corresponding indices
        flat_weights = []
        flat_indices = []
        for d in range(self.n_destroy):
            for r in range(self.n_repair):
                flat_weights.append(self.pair_weights[d][r])
                flat_indices.append((d, r))
        
        total = sum(flat_weights)
        if total == 0:
            # Fallback: random selection
            return (random.randint(0, self.n_destroy - 1),
                    random.randint(0, self.n_repair - 1))
        
        rnd = random.uniform(0, total)
        cumsum = 0.0
        for i, w in enumerate(flat_weights):
            cumsum += w
            if rnd <= cumsum:
                return flat_indices[i]
        
        return flat_indices[-1]
    
    # =========================================================================
    # PAIRED WEIGHT UPDATE (Paper Eq. 15)
    # =========================================================================
    def _update_pair_weights(self):
        """
        Paper Eq. 15:
        ω_dr = ρ * (π_dr / o_dr) + (1-ρ) * ω_dr   if o_dr > 0
        ω_dr = ω_dr                                   if o_dr = 0
        """
        rho = self.cfg.reaction_factor
        
        for d in range(self.n_destroy):
            for r in range(self.n_repair):
                if self.pair_counts[d][r] > 0:
                    avg_score = self.pair_scores[d][r] / self.pair_counts[d][r]
                    self.pair_weights[d][r] = (
                        rho * avg_score +
                        (1 - rho) * self.pair_weights[d][r]
                    )
                    # Ensure minimum weight to avoid starvation
                    self.pair_weights[d][r] = max(0.1, self.pair_weights[d][r])
                # else: weight stays unchanged
                
                # Reset scores and counts for next segment
                self.pair_scores[d][r] = 0.0
                self.pair_counts[d][r] = 0
    
    # =========================================================================
    # LOCAL SEARCH PERTURBATION (Paper §3.2 - 2-opt* later perturbation)
    # =========================================================================
    def _local_search_perturbation(self, sol: Solution) -> Tuple[Solution, float]:
        """
        Apply local search perturbation to the current solution:
        1. Intra-trip: 2-opt on pickup sequences (try reordering pickups within trips)
        2. Inter-trip: Try relocating employees between trips on the same vehicle
        Limited to max_moves total improvements to keep runtime bounded.
        Returns improved solution and its cost.
        """
        max_moves = 10  # Cap total number of improvement moves
        moves_made = 0
        
        # --- Phase 1: Intra-trip sequence optimization (2-opt style) ---
        for schedule in sol.schedules:
            if moves_made >= max_moves:
                break
            vehicle = schedule.vehicle
            for trip in schedule.trips:
                if moves_made >= max_moves:
                    break
                if len(trip.employees) < 2:
                    continue
                
                current_seq = trip.pickup_sequence[:]
                best_arrival = trip.arrival_at_office
                best_seq = current_seq[:]
                best_details = None
                
                n_emps = len(current_seq)
                # Pairwise swaps only (skip 2-opt reversal for speed)
                for i in range(n_emps):
                    for j in range(i + 1, n_emps):
                        new_seq = current_seq[:]
                        new_seq[i], new_seq[j] = new_seq[j], new_seq[i]
                        
                        time_ok, details = self.state.constraints.check_time_constraint(
                            vehicle, trip.employees, new_seq,
                            trip.start_time, trip.start_location
                        )
                        
                        if time_ok and details['arrival_at_office'] < best_arrival:
                            best_arrival = details['arrival_at_office']
                            best_seq = new_seq[:]
                            best_details = details
                
                if best_details is not None and best_seq != current_seq:
                    trip.pickup_sequence = best_seq
                    trip.arrival_at_office = best_details['arrival_at_office']
                    trip.pickup_times = best_details.get('pickup_times', {})
                    trip.distance_km = best_details.get('total_distance', 0)
                    trip.route = best_details.get('route', [])
                    moves_made += 1
        
        # --- Phase 2: Inter-trip employee relocation (sampled, not exhaustive) ---
        # Build list of candidate moves, then try a random sample
        candidate_moves = []
        for schedule in sol.schedules:
            vehicle = schedule.vehicle
            if len(schedule.trips) < 2:
                continue
            for src_idx in range(len(schedule.trips)):
                src_trip = schedule.trips[src_idx]
                if len(src_trip.employees) < 2:
                    continue
                for eid in src_trip.employees:
                    for dst_idx in range(len(schedule.trips)):
                        if dst_idx != src_idx:
                            candidate_moves.append((schedule, vehicle, src_idx, dst_idx, eid))
        
        # Limit to random sample of candidates
        max_candidates = min(len(candidate_moves), 20)
        if candidate_moves:
            sampled = random.sample(candidate_moves, max_candidates)
            
            for schedule, vehicle, src_idx, dst_idx, eid in sampled:
                if moves_made >= max_moves:
                    break
                
                # Bounds check (trips may have changed from Phase 1)
                if src_idx >= len(schedule.trips) or dst_idx >= len(schedule.trips):
                    continue
                
                src_trip = schedule.trips[src_idx]
                dst_trip = schedule.trips[dst_idx]
                
                if eid not in src_trip.employees or len(src_trip.employees) < 2:
                    continue
                
                new_dst_emps = dst_trip.employees + [eid]
                
                # Check constraints for destination trip
                cap_ok, _ = self.state.constraints.check_capacity_sharing(vehicle, new_dst_emps)
                if not cap_ok:
                    continue
                
                type_ok, _ = self.state.constraints.check_vehicle_type(vehicle, new_dst_emps)
                if not type_ok:
                    continue
                
                dst_seq = self.state.constraints.find_best_sequence(
                    vehicle, new_dst_emps, dst_trip.start_time, dst_trip.start_location
                )
                if not dst_seq:
                    continue
                
                time_ok, dst_details = self.state.constraints.check_time_constraint(
                    vehicle, new_dst_emps, dst_seq, dst_trip.start_time, dst_trip.start_location
                )
                if not time_ok:
                    continue
                
                # Check source trip without this employee
                new_src_emps = [e for e in src_trip.employees if e != eid]
                new_src_seq = [e for e in src_trip.pickup_sequence if e != eid]
                
                src_ok, src_details = self.state.constraints.check_time_constraint(
                    vehicle, new_src_emps, new_src_seq, src_trip.start_time, src_trip.start_location
                )
                if not src_ok:
                    continue
                
                # Compare: old total cost vs new total cost
                old_dist = src_trip.distance_km + dst_trip.distance_km
                new_dist = src_details.get('total_distance', 0) + dst_details.get('total_distance', 0)
                
                if new_dist < old_dist:
                    src_trip.employees = new_src_emps
                    src_trip.pickup_sequence = new_src_seq
                    src_trip.arrival_at_office = src_details['arrival_at_office']
                    src_trip.pickup_times = src_details.get('pickup_times', {})
                    src_trip.distance_km = src_details.get('total_distance', 0)
                    src_trip.route = src_details.get('route', [])
                    
                    dst_trip.employees = new_dst_emps
                    dst_trip.pickup_sequence = dst_seq
                    dst_trip.arrival_at_office = dst_details['arrival_at_office']
                    dst_trip.pickup_times = dst_details.get('pickup_times', {})
                    dst_trip.distance_km = dst_details.get('total_distance', 0)
                    dst_trip.route = dst_details.get('route', [])
                    
                    moves_made += 1
        
        cost, _ = self.state.solution_cost(sol)
        return sol, cost
    
    # =========================================================================
    # MAIN ALNS LOOP
    # =========================================================================
    def _run_alns_loop(self, current_sol: Solution, best_sol: Solution, best_cost: float):
        """
        Internal method to run one complete ALNS cycle on a given solution.
        Returns: (final_current_sol, best_found_sol, best_found_cost)
        """
        current = current_sol
        curr_cost = best_cost
        
        # Paper Eq. 17: T = -τ / ln(θ) * F(s₀)
        temp = -(self.cfg.temp_start / math.log(self.cfg.temp_accept_prob)) * curr_cost if curr_cost > 0 else 100
        
        iteration = 0
        no_improve = 0
        segment_size = self.cfg.segment_size
        psi = self.cfg.perturbation_interval
        
        # Reset paired weights for this run
        self.pair_weights = [[1.0] * self.n_repair for _ in range(self.n_destroy)]
        self.pair_scores = [[0.0] * self.n_repair for _ in range(self.n_destroy)]
        self.pair_counts = [[0] * self.n_repair for _ in range(self.n_destroy)]
        
        # Cache assigned counts to avoid repeated all_assigned() calls
        curr_assigned = len(current.all_assigned())
        best_assigned = len(best_sol.all_assigned())
        
        while iteration < self.cfg.max_iter and no_improve < self.cfg.max_no_improve:
            iteration += 1
            
            # Select paired (d,r) operator
            d_idx, r_idx = self._select_pair()
            d_op = self.destroy_ops[d_idx]
            r_op = self.repair_ops[r_idx]
            
            self.pair_counts[d_idx][r_idx] += 1
            
            # Determine removal size q
            if curr_assigned == 0:
                current = best_sol.copy()
                curr_cost = best_cost
                curr_assigned = best_assigned
                no_improve += 1
                continue
                
            q = random.randint(self.cfg.q_min, min(self.cfg.q_max, curr_assigned))
            
            # Execute Destroy & Repair
            partial, removed = d_op(current, q)
            new_sol = r_op(partial, removed)
            new_cost, _ = self.state.solution_cost(new_sol)
            
            # Count new assigned ONCE
            new_assigned = len(new_sol.all_assigned())
            
            accept = False
            score = 0
            
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
            
            # Compute adaptive weight score for the pair
            if accept:
                if (new_assigned > best_assigned or 
                    (new_assigned == best_assigned and new_cost < best_cost)):
                    score = self.sigma1
                elif new_cost < curr_cost:
                    score = self.sigma2
                else:
                    score = self.sigma3
            
            # Update paired scores
            self.pair_scores[d_idx][r_idx] += score
            
            # Update State
            if accept:
                current = new_sol
                curr_cost = new_cost
                curr_assigned = new_assigned  # Update cached count
                
                if new_assigned > best_assigned:
                    best_sol = new_sol.copy()
                    best_cost = new_cost
                    best_assigned = new_assigned  # Update cached count
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
            
            # Update paired weights every segment (Paper Eq. 15)
            if iteration % segment_size == 0:
                self._update_pair_weights()
            
            # Local search perturbation every ψ iterations
            if iteration % psi == 0:
                current, curr_cost = self._local_search_perturbation(current)
                curr_assigned = len(current.all_assigned())
                
                if (curr_assigned > best_assigned or
                    (curr_assigned == best_assigned and curr_cost < best_cost)):
                    best_sol = current.copy()
                    best_cost = curr_cost
                    best_assigned = curr_assigned
                    no_improve = 0
            
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
        baseline_cost_total = sum(self.state.employees[eid].baseline_cost for eid in assigned_ids)
        
        # --- MODIFIED: Calculate Total Weighted Baseline Value ---
        # Summing the pre-calculated baseline_value (alpha*cost + beta*time)
        baseline_weighted_total = sum(getattr(self.state.employees[eid], 'baseline_value', 0) for eid in assigned_ids)
        
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
            'beta': 1 - round(self.state.alpha, 2),
            'baseline_cost': round(baseline_cost_total, 2),
            # --- MODIFIED: Add Weighted Baseline to summary ---
            'baseline_weighted': round(baseline_weighted_total, 2),
            'savings': round(baseline_cost_total - breakdown['travel_cost'], 2),
            'savings_pct': round((baseline_cost_total - breakdown['travel_cost']) / baseline_cost_total * 100, 2) if baseline_cost_total > 0 else 0
        }
        
        results['employees'] = {}
        for emp in self.state.EMPLOYEES:
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
        # --- MODIFIED: Print the new weighted baseline ---
        print(f"   Baseline Value:   {s['baseline_weighted']:.2f}")
        print(f"   Savings (Cost):     ₹{s['savings']:.2f} ({s['savings_pct']:.1f}%)")
        
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
    employees, vehicles, office, metadata = DataLoader.load(filepath)
    
    if verbose:
        print(f"Loaded {len(employees)} employees, {len(vehicles)} vehicles")
        print(f"Office: {office}")
        print(f"Metadata: α={metadata.get('alpha', 0.7)}, β={metadata.get('beta', 0.3)}")
        
        print(f"\nVehicles:")
        for v in vehicles:
            print(f"  {v.id}: {v.category}, cap={v.capacity}, start={v.start_location}, "
                  f"avail={int(v.available_from//60):02d}:{int(v.available_from%60):02d}")
        
        print(f"\nEmployees requiring PREMIUM vehicle:")
        for e in employees:
            if e.vehicle_preference == VehiclePreference.PREMIUM:
                print(f"  {e.id}: pref={e.vehicle_preference.value}, sharing={e.sharing_preference.value}, "
                      f"deadline={int(e.latest_drop//60):02d}:{int(e.latest_drop%60):02d}")
    
    state = ProblemState(employees, vehicles, office, metadata)
    
    config = ALNSConfig()
    config.max_iter = min(3000, 200 * len(employees))
    
    alns = ALNS(state, config)
    solution, breakdown = alns.solve(verbose=verbose)
    
    verifier = ResultsVerifier(state)
    results = verifier.verify_and_display(solution)
    results['breakdown'] = breakdown
    
    if verbose:
        verifier.print_results(results)
    
    return results

if __name__ == "__main__":
    import sys
    
    precompute()
    filepath = "TestCases/TestCase_TC02.xlsx"
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    
    print(f"Optimizing: {filepath}\n")
    results = optimize(filepath)