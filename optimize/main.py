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
import time
from typing import List, Dict, Tuple, Any

# Custom module imports
from .models import Location, Employee, Vehicle, VehiclePreference, SharingPreference, Solution
from .constraints import TripConstraints
from .alns_engine import ALNS, ALNSConfig
from .initial_solution import InitialSolutionBuilder
import optimize.mapgraph as mp

# Note: Ensure precompute() is called at the very start
def precompute():
    mp.precompute()

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
    def load(filepath: str, verbose : bool = True) -> Tuple[List[Any], List[Any], Any, Dict]:
        """Load data and return employees, vehicles, office, and metadata."""
        # Load raw dataframes from Excel
        emp_df = pd.read_excel(filepath, sheet_name='employees')
        veh_df = pd.read_excel(filepath, sheet_name='vehicles')
        base_df = pd.read_excel(filepath, sheet_name='baseline')
        
        # Calculate baseline sums
        sum_baseline = base_df['baseline_cost'].sum()
        time_baseline = 0
        if 'baseline_time_min' in base_df.columns:
            time_baseline = base_df['baseline_time_min'].sum() 
        elif 'baseline_time' in base_df.columns:
            time_baseline = base_df['baseline_time'].sum()
        
        # Initialize metadata defaults and the priority tolerance map
        metadata = {'alpha': 0.7, 'beta': 0.3}
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
                            
            if verbose:
                print(f"Successfully loaded metadata: α={metadata['alpha']}, β={metadata['beta']}")
                if tolerance_map:
                    print(f"Custom priority tolerances loaded: {tolerance_map}")

        except Exception as e:
            print(f"No valid metadata sheet found or error parsing: {e}. Using defaults.")
        
        # Store baseline sums in metadata
        metadata['sum_baseline_cost'] = sum_baseline
        metadata['sum_baseline_time'] = time_baseline
        
        # Load baseline metrics for savings calculation
        baseline_data = {}
        for _, r in base_df.iterrows():
            eid = r['employee_id']
            b_time = r.get('baseline_time', r.get('time', r.get('baseline_travel_time', r.get('baseline_time_min', 0))))
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
                baseline_time=b_info['time'],  # Added to match trail2.py
                tolerance_map=tolerance_map
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
# PROBLEM STATE
# =============================================================================

class ProblemState:
    def __init__(self, employees: List[Employee], vehicles: List[Vehicle], 
                 office: Location, metadata: Dict = None):
        self.employees = {e.id: e.copy() for e in employees}
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
# RESULTS VERIFIER (matching trail2.py output format)
# =============================================================================

class ResultsVerifier:
    def __init__(self, state, constraints):
        self.state = state
        self.constraints = constraints
    
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
        
        # Calculate Total Weighted Baseline Value
        baseline_weighted_total = sum(
            getattr(emp, 'baseline_value', 0)
            for emp in self.state.employees.values()
        )

        assigned_employees = len(assigned_ids)

        violations = 0
        vehicle_type_violations = 0
        sharing_violations = 0
        time_violations = 0

        results['summary'] = {
            'total_employees': self.state.total_employees,
            'employees_assigned': assigned_employees,
            'employees_compromised': 0,
            'all_assigned': len(unassigned) == 0,
            'total_trips': solution.total_trips(),
            'vehicles_used': breakdown['vehicles_used'],
            'total_distance_km': round(breakdown['total_distance'], 2),
            'travel_cost': round(breakdown['travel_cost'], 2),
            'total_time': round(breakdown['total_time'], 2),
            'objective': round(breakdown['objective'], 2),
            'alpha': round(self.state.alpha, 2),
            'beta': round(self.state.beta, 2),
            'baseline_cost': round(baseline_cost_total, 2),
            'baseline_time': round(baseline_time_total, 2),
            'baseline_weighted': round(baseline_weighted_total, 2),
            'savings': round(baseline_cost_total - breakdown['travel_cost'], 2),
            'savings_pct': round((baseline_cost_total - breakdown['travel_cost']) / baseline_cost_total * 100, 2) if baseline_cost_total > 0 else 0,
            'optimized_pct': round((1.0 - breakdown['objective']) * 100, 2),
            'vehicle_type_violation_pct': 0,
            'sharing_violation_pct': 0,
            'time_violation_pct': 0,
            'satisfied_pct': 100
        }
        
        # Initialize employees dict (matching trail2.py)
        results['employees'] = {}
        for emp in self.state.employees.values():
            results['employees'][emp.id] = {
                'priority': emp.priority,
                'vehicle_preference': emp.vehicle_preference.value,
                'sharing_preference': emp.sharing_preference.value,
                'drop_preference': self._fmt_time(emp.latest_drop),
                'drop_max': self._fmt_time(emp.adjusted_latest_drop),
                'pickup': emp.pickup,
                'vehicle': '-',
                'pickup_time': '-',
                'dropoff_time': '-',
                'sharing': '-',
                'baseline_cost': emp.baseline_cost,
                'baseline_time': emp.baseline_time,
                'assigned': False,
                'unsatisfied': []
            }

        results['vehicles'] = {}
        for veh in self.state.vehicles.values():
            results['vehicles'][veh.id] = {
                'vehicle_id': veh.id,
                'vehicle_type': veh.vehicle_type,
                'capacity': veh.capacity,
                'cost_per_km': veh.cost_per_km,
                'avg_speed': veh.avg_speed,
                'start_location': veh.start_location,
                'available_from': veh.available_from,
                'category': veh.category,
                'cost': 0,
                'time': 0,
                'distance': 0,
                'used': False
            }
        
        # Office location (matching trail2.py)
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
            
            # Aggregate per-vehicle metrics
            cost = 0
            time_total = 0
            dist = 0
            
            for i, trip in enumerate(schedule.trips):
                feasible, details = self.constraints.is_trip_feasible(
                    schedule.vehicle, trip.employees, trip.pickup_sequence,
                    trip.start_time, trip.start_location
                )
                
                if not feasible:
                    results['all_constraints_satisfied'] = False
                
                # Update employee info (matching trail2.py)
                for emp_id in trip.employees:
                    results['employees'][emp_id]['vehicle'] = schedule.vehicle.id
                    results['employees'][emp_id]['pickup_time'] = self._fmt_time(trip.pickup_times.get(emp_id, '-'))
                    results['employees'][emp_id]['dropoff_time'] = self._fmt_time(trip.arrival_at_office)
                    results['employees'][emp_id]['sharing'] = len(trip.employees)
                    results['employees'][emp_id]['assigned'] = True

                # Unsatisfied constraints
                for emp_id in details['time'].get('unsatisfied_employees', []):
                    results['employees'][emp_id]['unsatisfied'].append('time')
                    time_violations += 1
                    violations += 1

                for emp_id in details['capacity_sharing'].get('unsatisfied_employees', []):
                    results['employees'][emp_id]['unsatisfied'].append('capacity_sharing')
                    sharing_violations += 1
                    violations += 1

                for emp_id in details['vehicle_type'].get('unsatisfied_employees', []):
                    results['employees'][emp_id]['unsatisfied'].append('vehicle_type')
                    vehicle_type_violations += 1
                    violations += 1
                
                # Aggregate metrics
                dist += trip.distance_km
                cost += trip.distance_km * schedule.vehicle.cost_per_km
                time_total += trip.arrival_at_office - trip.start_time
                
                trip_info = {
                    'trip_number': i + 1,
                    'employees': trip.employees,
                    'pickup_sequence': trip.pickup_sequence,
                    'start_time': self._fmt_time(trip.start_time),
                    'start_location': str(trip.start_location),
                    'arrival_at_office': self._fmt_time(trip.arrival_at_office),
                    'distance_km': round(trip.distance_km, 2),
                    'route': trip.route,  # Added to match trail2.py
                    'cost': round(trip.distance_km * schedule.vehicle.cost_per_km, 2),
                    'feasible': feasible,
                    'constraints': {
                        'time': details['time'],
                        'capacity_sharing': details['capacity_sharing'],
                        'vehicle_type': details['vehicle_type']
                    }
                }
                
                sched_info['trips'].append(trip_info)
            
            # Add vehicle-level aggregates (matching trail2.py)
            sched_info['vehicle']['cost'] = cost
            sched_info['vehicle']['time'] = time_total
            sched_info['vehicle']['distance'] = dist
            results['vehicles'][schedule.vehicle.id]['cost'] = cost
            results['vehicles'][schedule.vehicle.id]['time'] = time_total
            results['vehicles'][schedule.vehicle.id]['distance'] = dist
            results['vehicles'][schedule.vehicle.id]['used'] = True
            results['vehicle_schedules'].append(sched_info)
        

        
        employees_compromised = 0
        for id in self.state.employees:
            if not results['employees'][id]['assigned']:
                results['employees'][id]['route'], results['employees'][id]['distance'] = mp.optimal_route(
                    mp.nearest_node((results['employees'][id]['pickup'].lat, results['employees'][id]['pickup'].lng)), 
                    mp.nearest_node((self.state.office.lat, self.state.office.lng)))
            elif results['employees'][id]['unsatisfied']:
                employees_compromised += 1
                
        fully_satisfied = assigned_employees - employees_compromised
        results['summary']['employees_compromised'] = employees_compromised

        results['summary']['satisfied_pct'] = round(100 * fully_satisfied / self.state.total_employees, 2)
        if violations > 0:
            results['summary']['vehicle_type_violation_pct'] = round(100 * vehicle_type_violations / violations, 2)
            results['summary']['sharing_violation_pct'] = round(100 * sharing_violations / violations, 2)
            results['summary']['time_violation_pct'] = round(100 * time_violations / violations, 2)

        vehicle_type_violations = []
        sharing_violations = []
        time_violations = []

        for emp_id, emp in results['employees'].items():
            if not emp['assigned']:
                continue
            for cons in emp['unsatisfied']:
                if cons == "vehicle_type":
                    vehicle_type_violations.append({
                        "employee": emp_id,
                        "vehicle": emp['vehicle'],
                        "preferred": emp['vehicle_preference'],
                        "actual": results['vehicles'][emp['vehicle']]['vehicle_type']
                    })

                elif cons == "capacity_sharing":
                    vehicle_word = {1: "single", 2: "double", 3: "triple"}
                    sharing_violations.append({
                        "employee": emp_id,
                        "vehicle": emp['vehicle'],
                        "preferred": emp['sharing_preference'],
                        "actual": vehicle_word.get(emp['sharing'], emp['sharing'])
                    })

                elif cons == "time":
                    time_violations.append({
                        "employee": emp_id,
                        "vehicle": emp['vehicle'],
                        "preferred": emp['drop_preference'],
                        "max": emp['drop_max'],
                        "actual": emp['dropoff_time']
                    })

        results['violations'] = {
            'vehicle_type_violations': vehicle_type_violations,
            'sharing_violations': sharing_violations,
            'time_violations': time_violations,
            'total_violations': (
                len(vehicle_type_violations)
                + len(sharing_violations)
                + len(time_violations)
            )
        }

        return results
    
    def _fmt_time(self, mins) -> str:
        if isinstance(mins, str):
            return mins
        h, m = int(mins // 60), int(mins % 60)
        meridiem = 'am'
        if h >= 12:
            if m > 0:
                meridiem = 'pm'
            else:
                meridiem = 'noon'
        return f"{h:02d}:{m:02d} {meridiem}"
    
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
        print(f"   Total Time:         {s['total_time']:.2f} min")
        print(f"   Objective Value:    {s['objective']:.2f}")
        print(f"   Baseline Cost:      ₹{s['baseline_cost']:.2f}")
        print(f"   Baseline Time:      {s['baseline_time']:.2f} min")
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
                
                # t = trip['constraints']['time']
                # t_status = '✅' if t['satisfied'] else '❌'
                # print(f"║   {t_status} Time: deadline={self._fmt_time(t['deadline'])}, arrival={self._fmt_time(t['arrival_at_office'])}, slack={t['slack']:.1f}min".ljust(89) + "║")
                
                # c = trip['constraints']['capacity_sharing']
                # c_status = '✅' if c['satisfied'] else '❌'
                # print(f"║   {c_status} Capacity: n={c['n']} <= max_allowed={c['max_allowed']}".ljust(89) + "║")
                
                # vt = trip['constraints']['vehicle_type']
                # vt_status = '✅' if vt['satisfied'] else '❌'
                # print(f"║   {vt_status} Type: '{vt['vehicle_category']}' ∈ {vt['allowed_intersection']}".ljust(89) + "║")
            
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
    print('TestCase:', filepath, 'Input!')

    t0 = time.time()
    employees, vehicles, office, metadata = DataLoader.load(filepath, verbose=verbose)
    
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
    
    constraints_final = TripConstraints({e.id : e.copy() for e in employees}, {v.id: v for v in vehicles}, office)
    state_strict = ProblemState(employees, vehicles, office, metadata)
    
    config = ALNSConfig()
    config.max_iter = min(3000, 200 * len(employees))
    
    if verbose:
        print("\nRunning Strict Optimization...")
    alns_strict = ALNS(state_strict, config)
    sol_strict, breakdown_strict = alns_strict.solve(verbose=verbose)
    
    final_sol, final_breakdown = sol_strict, breakdown_strict

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
            if verbose:
                print("✅ Improvement found! Using relaxed solution.")
            final_sol, final_breakdown = sol_relaxed, breakdown_relaxed
        else:
            if verbose:
                print("❌ No improvement with relaxed constraints. Reverting to strict.")

    # --- PHASE 3: Output ---
    verifier = ResultsVerifier(state_strict, constraints_final)
    results = verifier.verify_and_display(final_sol)
    final_breakdown['time_sec'] = time.time() - t0
    results['breakdown'] = final_breakdown
    
    if verbose:
        verifier.print_results(results)
    
    print('TestCase:', filepath, 'Output!')
    return results

if __name__ == "__main__":
    import sys
    precompute()
    filepath = "../TestCases/TestCase_TC04.xlsx"
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    
    print(f"Optimizing: {filepath}\n")
    results = optimize(filepath)