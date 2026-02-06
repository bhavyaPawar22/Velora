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
    
    def distance_to(self, other: 'Location') -> float:
        src = mp.nearest_node((self.lat, self.lng))
        dst = mp.nearest_node((other.lat, other.lng))
        route, len = mp.optimal_route(src, dst)
        return len
    
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
    
    def copy(self) -> 'Trip':
        return Trip(
            vehicle_id=self.vehicle_id,
            employees=self.employees.copy(),
            pickup_sequence=self.pickup_sequence.copy(),
            start_time=self.start_time,
            start_location=self.start_location,
            arrival_at_office=self.arrival_at_office,
            pickup_times=self.pickup_times.copy(),
            distance_km=self.distance_km
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
    def load(filepath: str) -> Tuple[List[Employee], List[Vehicle], Location, Dict]:
        """Load data and return employees, vehicles, office, and metadata"""
        emp_df = pd.read_excel(filepath, sheet_name='employees')
        veh_df = pd.read_excel(filepath, sheet_name='vehicles')
        base_df = pd.read_excel(filepath, sheet_name='baseline')
        
        # Try to load metadata
        metadata = {'alpha': 0.7, 'beta': 0.3}
        try:
            meta_df = pd.read_excel(filepath, sheet_name='metadata')
            for _, row in meta_df.iterrows():
                key = str(row.get('key', row.get('parameter', ''))).lower().strip()
                value = row.get('value', row.get('val'))

                if pd.notna(value):
                    # 2. Map the specific Excel keys to alpha and beta
                    if key in ['alpha', 'objective_cost_weight']:
                        metadata['alpha'] = float(value)
                    elif key in ['beta', 'objective_time_weight']:
                        metadata['beta'] = float(value)
                        
            print(f"Successfully loaded metadata: α={metadata['alpha']}, β={metadata['beta']}")

        except Exception as e:
            print(f"No metadata sheet found, using defaults (alpha=0.7, beta=0.3)")
        
        baseline = {r['employee_id']: r['baseline_cost'] for _, r in base_df.iterrows()}
        office = Location(emp_df['drop_lat'].iloc[0], emp_df['drop_lng'].iloc[0])
        
        employees = []
        for _, r in emp_df.iterrows():
            vp = r['vehicle_preference'].lower()
            sp = r['sharing_preference'].lower()
            emp = Employee(
                id=r['employee_id'],
                priority=int(r['priority']),
                pickup=Location(r['pickup_lat'], r['pickup_lng']),
                dropoff=office,
                earliest_pickup=DataLoader.parse_time(r['earliest_pickup']),
                latest_drop=DataLoader.parse_time(r['latest_drop']),
                vehicle_preference=VehiclePreference(vp) if vp in ['premium','normal','any'] else VehiclePreference.ANY,
                sharing_preference=SharingPreference(sp) if sp in ['single','double','triple'] else SharingPreference.TRIPLE,
                baseline_cost=baseline.get(r['employee_id'], 0)
            )
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
        return (self.distance(a, b) / speed) * 60
    
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
        
        for eid in pickup_sequence:
            emp = self.employees[eid]
            
            dist = self.distance(current_loc, emp.pickup)
            travel = self.travel_time(current_loc, emp.pickup, vehicle.avg_speed)
            total_dist += dist
            
            arrival_at_pickup = current_time + travel
            actual_pickup = max(arrival_at_pickup, emp.earliest_pickup)
            pickup_times[eid] = actual_pickup
            
            current_time = actual_pickup + self.SERVICE_TIME
            current_loc = emp.pickup
        
        dist = self.distance(current_loc, self.office)
        travel = self.travel_time(current_loc, self.office, vehicle.avg_speed)
        total_dist += dist
        arrival_at_office = current_time + travel
        
        is_feasible = arrival_at_office <= deadline
        
        return is_feasible, {
            'satisfied': is_feasible,
            'deadline': deadline,
            'arrival_at_office': arrival_at_office,
            'slack': deadline - arrival_at_office,
            'pickup_times': pickup_times,
            'total_distance': total_dist,
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
        
        if len(employee_ids) <= 6:
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
            sorted_emps = sorted(employee_ids, key=lambda eid: self.employees[eid].earliest_pickup)
            ok, _ = self.check_time_constraint(vehicle, employee_ids, sorted_emps, start_time, start_location)
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
        
        objective = self.alpha * total_cost + self.beta * total_time
        
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
class EmployeeFeasibility:
    """Tracks feasible assignment options for an employee"""
    employee_id: str
    priority: int
    deadline: float
    earliest_pickup: float
    feasible_options: List[Tuple[str, int, float, float]] = field(default_factory=list)
    num_options: int = 0
    is_critical: bool = False
    
    def analyze(self):
        self.num_options = len(self.feasible_options)
        self.is_critical = self.num_options <= 2 or (self.deadline - self.earliest_pickup) < 60


class InitialSolutionBuilder:
    """
    Constraint-First Solution Builder
    1. Analyzes feasibility for ALL employees
    2. Assigns most constrained employees first
    3. Ensures maximum possible assignment
    """
    
    def __init__(self, state: ProblemState, config: Dict = None):
        self.state = state
        self.constraints = state.constraints
        self.employees = state.employees
        self.vehicles = {v.id: v for v in state.veh_list}
        self.office = state.office
        
        self.config = config or {}
        self.alpha = state.alpha
        self.beta = state.beta
        self.max_trips_per_vehicle = self.config.get('max_trips', 10)
        
        self._feasibility: Dict[str, EmployeeFeasibility] = {}
    
    def build(self) -> Solution:
        """Build solution using constraint-first approach"""
        # Step 1: Analyze feasibility
        self._analyze_feasibility()
        
        # Step 2: Report any infeasible employees
        infeasible = [eid for eid, f in self._feasibility.items() if f.num_options == 0]
        if infeasible:
            print(f"\n⚠️  WARNING: {len(infeasible)} employees have NO feasible options!")
            for eid in infeasible:
                self._debug_infeasibility(eid)
        
        # Step 3: Build solution
        solution = self._build_solution()
        
        # Step 4: Post-optimize
        solution = self._post_optimize(solution)
        
        return solution
    
    def _analyze_feasibility(self):
        """Analyze which (vehicle, trip) combinations work for each employee"""
        self._feasibility = {}
        
        for emp in self.state.emp_list:
            feas = EmployeeFeasibility(
                employee_id=emp.id,
                priority=emp.priority,
                deadline=emp.adjusted_latest_drop,
                earliest_pickup=emp.earliest_pickup
            )
            
            for vehicle in self.state.veh_list:
                if vehicle.category.lower() not in emp.allowed_vehicle_types:
                    continue
                
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [emp.id])
                if not cap_ok:
                    continue
                
                # Try multiple trip slots
                for trip_num in range(self.max_trips_per_vehicle):
                    start_time, start_loc = self._estimate_trip_start(vehicle, trip_num)
                    
                    # Skip if trip would start too late
                    if start_time > emp.adjusted_latest_drop:
                        break
                    
                    time_ok, details = self.constraints.check_time_constraint(
                        vehicle, [emp.id], [emp.id], start_time, start_loc
                    )
                    
                    if time_ok:
                        cost = details['total_distance'] * vehicle.cost_per_km
                        arrival = details['arrival_at_office']
                        feas.feasible_options.append((vehicle.id, trip_num, cost, arrival))
            
            feas.analyze()
            self._feasibility[emp.id] = feas
    
    def _estimate_trip_start(self, vehicle: Vehicle, trip_num: int) -> Tuple[float, Location]:
        """Estimate when/where a trip would start"""
        if trip_num == 0:
            return vehicle.available_from, vehicle.start_location
        
        # Estimate ~25 min per prior trip
        est_trip_duration = 25
        start_time = vehicle.available_from + trip_num * (est_trip_duration + self.constraints.DROP_TIME)
        return start_time, self.office
    
    def _debug_infeasibility(self, eid: str):
        """Print debug info for infeasible employee"""
        emp = self.employees[eid]
        print(f"\n  {eid}:")
        print(f"    - Vehicle types allowed: {emp.allowed_vehicle_types}")
        print(f"    - Max sharing: {emp.max_passengers}")
        print(f"    - Time window: {self._fmt_time(emp.earliest_pickup)} - {self._fmt_time(emp.adjusted_latest_drop)}")
        
        for vehicle in self.state.veh_list:
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                print(f"    - {vehicle.id}: ❌ wrong vehicle type ({vehicle.category})")
                continue
            
            for trip_num in range(3):
                start_time, start_loc = self._estimate_trip_start(vehicle, trip_num)
                
                travel_to_emp = self.constraints.travel_time(start_loc, emp.pickup, vehicle.avg_speed)
                arrival_at_emp = start_time + travel_to_emp
                pickup_time = max(arrival_at_emp, emp.earliest_pickup)
                
                travel_to_office = self.constraints.travel_time(emp.pickup, self.office, vehicle.avg_speed)
                arrival_at_office = pickup_time + travel_to_office
                
                status = "✅" if arrival_at_office <= emp.adjusted_latest_drop else "❌"
                slack = emp.adjusted_latest_drop - arrival_at_office
                print(f"    - {vehicle.id} trip {trip_num}: {status} arrive={self._fmt_time(arrival_at_office)}, "
                      f"deadline={self._fmt_time(emp.adjusted_latest_drop)}, slack={slack:.0f}min")
    
    def _build_solution(self) -> Solution:
        """Build solution by assigning most constrained employees first"""
        solution = Solution()
        for v in self.state.veh_list:
            solution.schedules.append(VehicleSchedule(vehicle=v))
        
        # Sort employees: fewest options first, then by priority, then by deadline
        sorted_emps = sorted(
            self.state.emp_list,
            key=lambda e: (
                self._feasibility[e.id].num_options,
                e.priority,
                e.adjusted_latest_drop
            )
        )
        
        for emp in sorted_emps:
            if self._feasibility[emp.id].num_options == 0:
                continue
            self._assign_to_best_trip(solution, emp.id)
        
        return solution
    
    def _assign_to_best_trip(self, sol: Solution, eid: str) -> bool:
        """Assign employee to the best feasible trip"""
        emp = self.employees[eid]
        best_score = float('inf')
        best_schedule = None
        best_trip_idx = None
        best_sequence = None
        is_new_trip = False
        
        for schedule in sol.schedules:
            vehicle = schedule.vehicle
            
            if vehicle.category.lower() not in emp.allowed_vehicle_types:
                continue
            
            # Try existing trips
            for i, trip in enumerate(schedule.trips):
                test_emps = trip.employees + [eid]
                
                cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, test_emps)
                type_ok, _ = self.constraints.check_vehicle_type(vehicle, test_emps)
                
                if not (cap_ok and type_ok):
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
                    cost = details['total_distance'] * vehicle.cost_per_km
                    time_val = details['arrival_at_office']
                    score = self.alpha * cost + self.beta * time_val
                    score *= 0.85  # Consolidation bonus
                    
                    if score < best_score:
                        best_score = score
                        best_schedule = schedule
                        best_trip_idx = i
                        best_sequence = best_seq
                        is_new_trip = False
            
            # Try new trip
            if schedule.trips:
                start_time = schedule.get_end_time() + self.constraints.DROP_TIME
                start_loc = self.office
            else:
                start_time = vehicle.available_from
                start_loc = vehicle.start_location
            
            cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, [eid])
            type_ok, _ = self.constraints.check_vehicle_type(vehicle, [eid])
            
            if cap_ok and type_ok:
                time_ok, details = self.constraints.check_time_constraint(
                    vehicle, [eid], [eid], start_time, start_loc
                )
                
                if time_ok:
                    cost = details['total_distance'] * vehicle.cost_per_km
                    time_val = details['arrival_at_office']
                    score = self.alpha * cost + self.beta * time_val
                    
                    if score < best_score:
                        best_score = score
                        best_schedule = schedule
                        best_trip_idx = -1
                        best_sequence = [eid]
                        is_new_trip = True
        
        if best_schedule is None:
            return False
        
        if is_new_trip:
            vehicle = best_schedule.vehicle
            if best_schedule.trips:
                start_time = best_schedule.get_end_time() + self.constraints.DROP_TIME
                start_loc = self.office
            else:
                start_time = vehicle.available_from
                start_loc = vehicle.start_location
            
            _, details = self.constraints.check_time_constraint(
                vehicle, [eid], [eid], start_time, start_loc
            )
            
            new_trip = Trip(
                vehicle_id=vehicle.id,
                employees=[eid],
                pickup_sequence=[eid],
                start_time=start_time,
                start_location=start_loc,
                arrival_at_office=details['arrival_at_office'],
                pickup_times=details['pickup_times'],
                distance_km=details['total_distance']
            )
            best_schedule.trips.append(new_trip)
        else:
            trip = best_schedule.trips[best_trip_idx]
            trip.employees.append(eid)
            trip.pickup_sequence = best_sequence
            
            _, details = self.constraints.check_time_constraint(
                best_schedule.vehicle, trip.employees, best_sequence,
                trip.start_time, trip.start_location
            )
            trip.arrival_at_office = details['arrival_at_office']
            trip.pickup_times = details['pickup_times']
            trip.distance_km = details['total_distance']
        
        return True
    
    def _post_optimize(self, solution: Solution) -> Solution:
        """Try to merge trips and improve solution"""
        improved = True
        iterations = 0
        
        while improved and iterations < 20:
            improved = False
            iterations += 1
            
            for schedule in solution.schedules:
                if len(schedule.trips) < 2:
                    continue
                
                i = 0
                while i < len(schedule.trips) - 1:
                    trip_i = schedule.trips[i]
                    trip_j = schedule.trips[i + 1]
                    
                    combined = trip_i.employees + trip_j.employees
                    vehicle = schedule.vehicle
                    
                    cap_ok, _ = self.constraints.check_capacity_sharing(vehicle, combined)
                    type_ok, _ = self.constraints.check_vehicle_type(vehicle, combined)
                    
                    if cap_ok and type_ok:
                        best_seq = self.constraints.find_best_sequence(
                            vehicle, combined, trip_i.start_time, trip_i.start_location
                        )
                        
                        if best_seq:
                            time_ok, details = self.constraints.check_time_constraint(
                                vehicle, combined, best_seq,
                                trip_i.start_time, trip_i.start_location
                            )
                            
                            if time_ok:
                                trip_i.employees = combined
                                trip_i.pickup_sequence = best_seq
                                trip_i.arrival_at_office = details['arrival_at_office']
                                trip_i.pickup_times = details['pickup_times']
                                trip_i.distance_km = details['total_distance']
                                
                                schedule.trips.remove(trip_j)
                                self._update_subsequent_trips(schedule, i + 1)
                                improved = True
                                continue
                    
                    i += 1
        
        return solution
    
    def _update_subsequent_trips(self, schedule: VehicleSchedule, start_idx: int):
        """Update start times for trips after a merge"""
        for i in range(start_idx, len(schedule.trips)):
            prev_trip = schedule.trips[i - 1] if i > 0 else None
            trip = schedule.trips[i]
            
            if prev_trip:
                trip.start_time = prev_trip.arrival_at_office + self.constraints.DROP_TIME
                trip.start_location = self.office
            else:
                trip.start_time = schedule.vehicle.available_from
                trip.start_location = schedule.vehicle.start_location
            
            _, details = self.constraints.check_time_constraint(
                schedule.vehicle, trip.employees, trip.pickup_sequence,
                trip.start_time, trip.start_location
            )
            trip.arrival_at_office = details['arrival_at_office']
            trip.pickup_times = details['pickup_times']
            trip.distance_km = details['total_distance']
    
    def _fmt_time(self, mins: float) -> str:
        h, m = int(mins // 60), int(mins % 60)
        return f"{h:02d}:{m:02d}"


# =============================================================================
# DESTROY / REPAIR OPERATORS
# =============================================================================

class DestroyOperators:
    def __init__(self, state: ProblemState):
        self.state = state
    
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
    
    def trip_removal(self, sol: Solution, q: int) -> Tuple[Solution, List[str]]:
        s = sol.copy()
        
        all_trips = [(sched, trip) for sched in s.schedules for trip in sched.trips if trip.employees]
        if not all_trips:
            return s, []
        
        sched, trip = random.choice(all_trips)
        removed = trip.employees.copy()
        sched.trips.remove(trip)
        
        return s, removed
    
    def _remove(self, sol: Solution, eid: str):
        for schedule in sol.schedules:
            for trip in schedule.trips:
                if eid in trip.employees:
                    trip.employees.remove(eid)
                    if eid in trip.pickup_sequence:
                        trip.pickup_sequence.remove(eid)
                    return
        
        for schedule in sol.schedules:
            schedule.trips = [t for t in schedule.trips if t.employees]


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
        self.repair_ops = [
            self.repair.greedy_insertion,
            lambda s, r: self.repair.regret_insertion(s, r, 2)
        ]
        
        self.best_sol = None
        self.best_cost = float('inf')
    
    def solve(self, verbose=True) -> Tuple[Solution, Dict]:
        t0 = time.time()
        
        builder = InitialSolutionBuilder(self.state)
        current = builder.build()
        curr_cost, _ = self.state.solution_cost(current)
        
        self.best_sol = current.copy()
        self.best_cost = curr_cost
        
        assigned = len(current.all_assigned())
        if verbose:
            print(f"Initial: objective={curr_cost:.2f}, trips={current.total_trips()}, "
                  f"assigned={assigned}/{self.state.total_employees}")
        
        temp = self.cfg.temp_start * curr_cost if curr_cost > 0 else 100
        
        iteration = 0
        no_improve = 0
        
        while iteration < self.cfg.max_iter and no_improve < self.cfg.max_no_improve:
            iteration += 1
            
            d_op = random.choice(self.destroy_ops)
            r_op = random.choice(self.repair_ops)
            
            n_assigned = len(current.all_assigned())
            if n_assigned == 0:
                current = self.best_sol.copy()
                curr_cost = self.best_cost
                continue
            
            q = random.randint(self.cfg.q_min, min(self.cfg.q_max, n_assigned))
            
            partial, removed = d_op(current, q)
            new_sol = r_op(partial, removed)
            new_cost, _ = self.state.solution_cost(new_sol)
            
            new_assigned = len(new_sol.all_assigned())
            curr_assigned = len(current.all_assigned())
            
            accept = False
            if new_assigned > curr_assigned:
                accept = True
            elif new_assigned == curr_assigned:
                if new_cost < self.best_cost:
                    self.best_sol = new_sol.copy()
                    self.best_cost = new_cost
                    accept = True
                    no_improve = 0
                elif new_cost < curr_cost:
                    accept = True
                elif random.random() < math.exp(-(new_cost - curr_cost) / max(temp, 0.01)):
                    accept = True
            
            if accept:
                current = new_sol
                curr_cost = new_cost
                if new_cost < self.best_cost and new_assigned >= len(self.best_sol.all_assigned()):
                    self.best_sol = new_sol.copy()
                    self.best_cost = new_cost
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                no_improve += 1
            
            temp *= self.cfg.cooling
        
        elapsed = time.time() - t0
        _, breakdown = self.state.solution_cost(self.best_sol)
        breakdown['time_sec'] = elapsed
        breakdown['iterations'] = iteration
        
        if verbose:
            print(f"\nDone in {elapsed:.2f}s, {iteration} iterations")
        
        return self.best_sol, breakdown


# =============================================================================
# RESULTS VERIFIER
# =============================================================================

class ResultsVerifier:
    def __init__(self, state: ProblemState):
        self.state = state
        self.constraints = state.constraints
    
    def verify_and_display(self, solution: Solution) -> Dict:
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
        baseline = sum(self.state.employees[eid].baseline_cost for eid in assigned_ids)
        
        results['summary'] = {
            'total_employees': self.state.total_employees,
            'employees_assigned': len(assigned_ids),
            'all_assigned': len(unassigned) == 0,
            'total_trips': solution.total_trips(),
            'vehicles_used': breakdown['vehicles_used'],
            'total_distance_km': round(breakdown['total_distance'], 2),
            'travel_cost': round(breakdown['travel_cost'], 2),
            'objective': round(breakdown['objective'], 2),
            'alpha': self.state.alpha,
            'beta': self.state.beta,
            'baseline_cost': round(baseline, 2),
            'savings': round(baseline - breakdown['travel_cost'], 2),
            'savings_pct': round((baseline - breakdown['travel_cost']) / baseline * 100, 2) if baseline > 0 else 0
        }
        
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
                
                trip_info = {
                    'trip_number': i + 1,
                    'employees': trip.employees,
                    'pickup_sequence': trip.pickup_sequence,
                    'start_time': self._fmt_time(trip.start_time),
                    'start_location': str(trip.start_location),
                    'arrival_at_office': self._fmt_time(trip.arrival_at_office),
                    'distance_km': round(trip.distance_km, 2),
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
        print(f"   Savings:            ₹{s['savings']:.2f} ({s['savings_pct']:.1f}%)")
        
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
    
    def _fmt_time(self, mins) -> str:
        if isinstance(mins, str):
            return mins
        h, m = int(mins // 60), int(mins % 60)
        return f"{h:02d}:{m:02d}"


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
    
    if verbose:
        verifier.print_results(results)
    
    return results


if __name__ == "__main__":
    import sys
    
    filepath = "TestCases/TestCase_TC02.xlsx"
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    
    print(f"Optimizing: {filepath}\n")
    results = optimize(filepath)