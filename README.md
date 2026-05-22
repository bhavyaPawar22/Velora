# Velora — Mobility Optimization using ALNS

Velora is a full-stack Vehicle Routing Problem (VRP) system designed to optimize large-scale, mixed-fleet employee transportation. It balances strict operational constraints with multi-objective optimization to generate highly efficient, multi-trip vehicle schedules.

---

## ✨ Core Features

* **Constraint-First Routing:** Guarantees 100% feasible routes by evaluating rules *before* assignment:
    * Strict time windows (Earliest pickup & latest drop-off).
    * Vehicle capacities and sharing limits (Single/Double/Triple occupancy).
    * Vehicle type preferences (Premium vs. Normal).
* **Multi-Objective Optimization:** Minimizes a configurable objective function: $Objective = \alpha \cdot Cost + \beta \cdot Time$. Weights are dynamically injected via an Excel metadata sheet.
* **Multi-Trip Fleet Consolidation:** Automatically recycles vehicles for subsequent trips once they return to the office, maximizing fleet utilization.
* **Automated Benchmarking:** Ingests baseline company metrics and automatically calculates exact cost and time savings of the newly generated routes.

---

## 🛠 Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Backend** | Python 3.13, Flask, Gunicorn |
| **Data Processing** | Pandas, NumPy, OpenPyXL |
| **Pathfinding & Graphing** | SciPy (KDTree), Osmium, Custom A* |
| **Visualization** | Matplotlib, Base64 Image Streaming |
| **Deployment** | Docker |

---

## 🧠 Architecture & Algorithm

Velora solves the Capacitated Vehicle Routing Problem with Time Windows (CVRPTW) through a structured, 4-phase algorithmic pipeline designed to escape local optima.

### 1. Graph Precomputation (`mapgraph.py`)
* **OSM Parsing:** Reads raw `.osm` XML data for Bengaluru to create a highly accurate directed road graph.
* **Coordinate Snapping:** Uses a **SciPy KDTree** to instantly map raw employee GPS coordinates to the nearest valid road node.
* **Real-World Distances:** Utilizes an **A* Pathfinding Algorithm** over the precomputed graph to calculate realistic travel distances and times, avoiding inaccurate Euclidean approximations.

### 2. Phase I: Probabilistic PWSA (Parallel Wright Savings Algorithm)
* **Savings Calculation:** Evaluates the savings of combining every possible pair of employees into a single cab: $S_{ij} = \alpha(\Delta C) + \beta(\Delta T)$.
* **Noise Injection (GRASP):** Injects a randomized noise factor ($\pm 10-20\%$) into the savings calculation to ensure diverse "seed" routes across multiple runs.

### 3. Phase II: Hybrid Route Construction
* **Route Seeding:** Initializes routes using the top $k$ pairs from the probabilistic savings list.
* **Regret-$k$ Insertion:** For unassigned employees, calculates a **Regret Score** (the cost difference between their *best* and *second-best* insertion option). Employees with the highest regret are prioritized and inserted first.

### 4. Phase III: ALNS (Adaptive Large Neighborhood Search)
Iteratively improves the initial solution over thousands of cycles:
* **Destroy Operators:**
    * *Worst Removal:* Removes the most expensive or delayed employees.
    * *Trip Removal:* Deletes entire trips to force fleet consolidation.
    * *Random Removal:* Injects entropy to prevent algorithmic stagnation.
* **Repair Operators:** Uses Greedy and Regret insertion to stitch removed employees back into better routes.
* **Simulated Annealing:** Accepts worse route configurations early in the execution based on a cooling temperature probability ($e^{-\Delta / T}$), preventing the algorithm from getting trapped in local minima.

---

## 💻 Local Setup

### Prerequisites
* Python 3.10+
* Docker (Optional)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/bhavyaPawar22/Velora.git](https://github.com/bhavyaPawar22/Velora.git)
    cd Velora
    ```

2.  **Install dependencies:**
    ```bash
    pip install --upgrade pip
    pip install -r requirements.txt
    ```

3.  **Run the application locally:**
    ```bash
    python app.py
    ```
    *Note: The app will precompute the map graph on startup (this takes a few moments) and then start the Flask server on `http://localhost:7860`.*

### Running via Docker

Build and run the container using the provided Dockerfile:
```bash
docker build -t velora-app .
docker run -p 7860:7860 velora-app