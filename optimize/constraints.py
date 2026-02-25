from typing import List, Dict, Tuple, Optional, Set, Any
from itertools import permutations
from .models import Location, Employee, Vehicle, VehiclePreference  # Import core types
import optimize.mapgraph as mp

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
        """Returns (distance_km, route) tuple - matching trail2.py signature"""
        key = (a.lat, a.lng, b.lat, b.lng)
        if key not in self._dist_cache:
            self._dist_cache[key] = a.distance_to(b)  # Returns (length, route)
        return self._dist_cache[key]
    
    def travel_time(self, a: Location, b: Location, speed: float) -> float:
        return (self.distance(a, b)[0] / speed) * 60

    def check_capacity_sharing(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        n = len(employee_ids)
        if n == 0:
            return True, {'satisfied': True, 'unsatisfied_employees': [], 'n': 0, 'max_allowed': vehicle.capacity}
        
        unsatisfied_employees = []
        is_feasible = True
        max_allowed = vehicle.capacity

        if n > max_allowed:
            return False, {
                'satisfied': False,
                'n': n,
                'max_allowed': max_allowed,
                'vehicle_capacity': vehicle.capacity,
                'sharing_limits': {eid: self.employees[eid].max_passengers for eid in employee_ids}
            }

        for eid in employee_ids:
            max_allowed = min(max_allowed, self.employees[eid].max_passengers)
            if n > self.employees[eid].max_passengers:
                is_feasible = False
                unsatisfied_employees.append(eid)
        
        return is_feasible, {
            'satisfied': is_feasible,
            'unsatisfied_employees': unsatisfied_employees,
            'n': n,
            'max_allowed': max_allowed,
            'vehicle_capacity': vehicle.capacity,
            'sharing_limits': {eid: self.employees[eid].max_passengers for eid in employee_ids}
        }

    def check_vehicle_type(self, vehicle: Vehicle, employee_ids: List[str]) -> Tuple[bool, Dict]:
        if not employee_ids:
            return True, {'satisfied': True}
        
        unsatisfied_employees = []
        vehicle_cat = vehicle.category.lower()
        # Use intersection for set logic - matching trail2.py approach
        allowed = self.employees[employee_ids[0]].allowed_vehicle_types.copy()
        for eid in employee_ids[1:]:
            if vehicle_cat not in self.employees[eid].allowed_vehicle_types:
                unsatisfied_employees.append(eid)
            allowed = allowed & self.employees[eid].allowed_vehicle_types
        
        is_feasible = vehicle_cat in allowed
        
        return is_feasible, {
            'satisfied': is_feasible,
            'unsatisfied_employees': unsatisfied_employees,
            'vehicle_category': vehicle_cat,
            'allowed_intersection': list(allowed),
            'employee_preferences': {eid: self.employees[eid].vehicle_preference.value for eid in employee_ids}
        }

    def check_time_constraint(self, vehicle: Vehicle, employee_ids: List[str],
                               pickup_sequence: List[str], start_time: float,
                               start_location: Location) -> Tuple[bool, Dict]:
        """
        Check time constraint - matching trail2.py signature exactly.
        Returns route in details for trip construction.
        """
        if not pickup_sequence:
            return True, {'satisfied': True}
        
        deadline = min(self.employees[eid].adjusted_latest_drop for eid in employee_ids)
        
        current_time = start_time
        current_loc = start_location
        pickup_times = {}
        total_dist = 0.0
        final_route = []
        unsatisfied_employees = []
        
        for eid in pickup_sequence:
            emp = self.employees[eid]
            
            # distance() returns (dist, route) tuple
            dist, route = self.distance(current_loc, emp.pickup)
            travel = self.travel_time(current_loc, emp.pickup, vehicle.avg_speed)
            total_dist += dist
            final_route += route[:-1]  # Exclude last node to avoid duplicates
            
            arrival_at_pickup = current_time + travel
            actual_pickup = max(arrival_at_pickup, emp.earliest_pickup)
            pickup_times[eid] = actual_pickup
            
            current_time = actual_pickup + self.SERVICE_TIME
            current_loc = emp.pickup
        
        # Final leg to office
        dist, route = self.distance(current_loc, self.office)
        travel = self.travel_time(current_loc, self.office, vehicle.avg_speed)
        total_dist += dist
        final_route += route
        arrival_at_office = current_time + travel
        
        for eid in employee_ids:
            if arrival_at_office > self.employees[eid].adjusted_latest_drop:
                unsatisfied_employees.append(eid)
                
        is_feasible = arrival_at_office <= deadline
        
        return is_feasible, {
            'satisfied': is_feasible,
            'unsatisfied_employees': unsatisfied_employees,
            'deadline': deadline,
            'arrival_at_office': arrival_at_office,
            'slack': deadline - arrival_at_office,
            'pickup_times': pickup_times,
            'total_distance': total_dist,
            'route': final_route,
            'start_time': start_time,
            'start_location': str(start_location)
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