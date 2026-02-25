from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set, Any
from enum import Enum
import copy
import optimize.mapgraph as mp  # Models depend on mapgraph for Location.distance_to

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
        """Returns (distance_km, route) tuple - matching trail2.py signature"""
        src = mp.nearest_node((self.lat, self.lng))
        dst = mp.nearest_node((other.lat, other.lng))
        route, length = mp.optimal_route(src, dst)
        return length, route  # Note: returns (length, route) not (route, length)
    
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
    baseline_time: float = 0.0  # Added to match trail2.py
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
    
    def copy(self) -> 'Employee':
        return Employee(
            id = self.id,
            priority = self.priority,
            pickup = self.pickup,
            dropoff = self.dropoff,
            earliest_pickup = self.earliest_pickup,
            latest_drop = self.latest_drop,
            vehicle_preference = self.vehicle_preference,
            sharing_preference = self.sharing_preference,
            baseline_cost = self.baseline_cost,
            baseline_time = self.baseline_time,
            tolerance_map = self.tolerance_map
        )


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
    route: List[Tuple[float, float]] = field(default_factory=list)  # Added to match trail2.py
    
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
            route=self.route  # Added to match trail2.py
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