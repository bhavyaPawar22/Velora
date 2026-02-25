let maps = {};
let routes = {};
let emp_trip = {};
let emp_markers = {};
let office_markers = {};
let depot_markers = {};
let unassigned_emp_route = {};
const office = data.Office;
let totalEmployees = data.summary.total_employees;
let compromised = data.summary.employees_compromised;
let assigned = data.summary.employees_assigned;
let satisfied = assigned - compromised;
let unassigned = totalEmployees - assigned;
const totalViolations = data.violations.total_violations;
const vehicleViol = data.violations.vehicle_type_violations.length;
const sharingViol = data.violations.sharing_violations.length;
const timeViol = data.violations.time_violations.length;

// ================= CUSTOM ICONS =================

const officeIcon = L.divIcon({
    className: "custom-marker",
    html: `<div class="marker office-marker">
              <i class="fa fa-building"></i>
           </div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 30]
});

const employeeIcon = L.divIcon({
    className: "custom-marker",
    html: `<div class="marker employee-marker">
              <i class="fa fa-user"></i>
           </div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 26]
});

const depotIcon = L.divIcon({
    className: "custom-marker",
    html: `<div class="marker depot-marker">
              <i class="fa fa-car"></i>
           </div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 28]
});

function showPane(id,btn){
    document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    document.querySelectorAll('.nav-button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');

    const mapDiv = document.querySelector(`#${id} .map`);
    if (mapDiv) {
        const mapKey = mapDiv.id;
        if (maps[mapKey]) {
            maps[mapKey].invalidateSize();
        }
    }
}

function goToVehicle(vehicleId) {

    const button = document.getElementById('button' + vehicleId);

    if (button) {
        showPane('vehicle' + vehicleId, button);
    }
}

function toggleTripDetails(vehicle, row_n) {

    var row = document.getElementById(`${vehicle}_${row_n}`);

    const detailsRow = row.nextElementSibling;

    const allDetails = document.querySelectorAll('.trip-details-row');

    // Close all other expanded rows
    allDetails.forEach(r => {
        if (r !== detailsRow) {
            r.style.display = "none";
        }
    });

    // Reset color of all routes
    for (let rn in routes[vehicle]){
        routes[vehicle][rn].setStyle({
            color: getGradientColor(rn - 1, routes[vehicle].length, cyan_shades)
        });
    }

    // Toggle current
    if (detailsRow.style.display === "table-row") {
        detailsRow.style.display = "none";
        routes[vehicle][row_n].setStyle({
            color: getGradientColor(row_n - 1, routes[vehicle].length, cyan_shades)
        });
    } else {
        detailsRow.style.display = "table-row";
        routes[vehicle][row_n].setStyle({
            color: "red"
        });
        routes[vehicle][row_n].bringToFront();
        maps[vehicle].fitBounds(routes[vehicle][row_n].getBounds(), {
            padding: [40, 40],   // space around route
            maxZoom: 15,
            animate: true,
            duration: 0.8
        });
    }
}

function showTripDetails(vehicle, row_n) {

    var row = document.getElementById(`${vehicle}_${row_n}`);

    const detailsRow = row.nextElementSibling;

    const allDetails = document.querySelectorAll('.trip-details-row');

    // Close all other expanded rows
    allDetails.forEach(r => {
        if (r !== detailsRow) {
            r.style.display = "none";
        }
    });

    // Reset color of all routes
    for (let rn in routes[vehicle]){
        routes[vehicle][rn].setStyle({
            color: getGradientColor(rn - 1, routes[vehicle].length, cyan_shades)
        });
    }

    // Show current
    detailsRow.style.display = "table-row";
    routes[vehicle][row_n].setStyle({
        color: "red"
    });
    routes[vehicle][row_n].bringToFront();
    maps[vehicle].fitBounds(routes[vehicle][row_n].getBounds(), {
        padding: [40, 40],   // space around route
        maxZoom: 15,
        animate: true,
        duration: 0.8
    });
}

function showEmployee(eid){

    if (!emp_markers[eid]) return;

    const { marker, vehicle } = emp_markers[eid];

    if (vehicle === "unassigned-map") {
        showPane("unassigned",
            document.querySelector('[onclick*="unassigned"]'));
    } else {
        showPane('vehicle' + vehicle,
            document.getElementById('button' + vehicle));
        showTripDetails(vehicle, emp_trip[eid]);
    }

    // Delay slightly to allow pane + map resize
    setTimeout(() => {

        const map = maps[vehicle];
        if (!map) return;

        map.setView(marker.getLatLng(), 16, {
            animate: true,
            duration: 0.8
        });

        marker.setZIndexOffset(1000);

        setTimeout(() => {
            marker.setZIndexOffset(0);
        }, 1200);

    }, 150);
}

function filterEmployees(category, cardElement = null) {

    const rows = document.querySelectorAll(".emp_row");
    const cards = document.querySelectorAll(".emp-filter");

    // Remove active highlight from all KPI cards
    cards.forEach(card => card.classList.remove("active-kpi"));

    // Highlight correct KPI card
    if (cardElement) {
        cardElement.classList.add("active-kpi");
    } else {
        cards.forEach(card => {
            if (card.getAttribute("onclick")?.includes(category)) {
                card.classList.add("active-kpi");
            }
        });
    }

    rows.forEach(row => {

        if (category === "all") {
            row.style.display = "table-row";
            return;
        }

        if (row.dataset.category === category) {
            row.style.display = "table-row";
        } else {
            row.style.display = "none";
        }
    });
}

function focusEmployee(eid) {

    if (!emp_markers[eid]) return;

    const { marker, vehicle } = emp_markers[eid];
    const map = maps[vehicle];

    // Zoom to employee
    map.setView(marker.getLatLng(), 16, {
        animate: true,
        duration: 0.8
    });

    // Optional bounce effect (simple visual pulse)
    marker.setZIndexOffset(1000);

    setTimeout(() => {
        marker.setZIndexOffset(0);
    }, 1200);
}

function showEmployeeRoute(id){
    if (unassigned == 0)
        return;
    unassigned_emp_route[id].bringToFront();
    maps["unassigned-map"].fitBounds(unassigned_emp_route[id].getBounds(), {
        padding: [40, 40],   // space around route
        maxZoom: 15,
        animate: true,
        duration: 0.8
    });
}

function goToViolation(sectionId) {    
    if (totalViolations == 0)
        return;
    filterViolation("all");

    // Find the Violations nav button
    const violationButton = [...document.querySelectorAll('.nav-button')]
        .find(btn => btn.textContent.trim() === "Violations");

    if (!violationButton) return;

    // Switch pane
    showPane('violations', violationButton);

    // Scroll to correct section after pane loads
    setTimeout(() => {

        const section = document.getElementById(sectionId);
        if (!section) return;

        section.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

        // Optional subtle highlight animation
        section.style.transition = "box-shadow 0.4s ease";
        section.style.boxShadow = "0 0 30px rgba(255,59,48,0.5)";

        setTimeout(() => {
            section.style.boxShadow = "";
        }, 1200);

    }, 150);
}

function showOffice(id) {
    const marker = office_markers[id];
    const map = maps[id];

    // Zoom to employee
    map.setView(marker.getLatLng(), 16, {
        animate: true,
        duration: 0.8
    });

    // Optional bounce effect (simple visual pulse)
    marker.setZIndexOffset(1000);

    setTimeout(() => {
        marker.setZIndexOffset(0);
    }, 1200);
}

function showDepot(id) {
    const marker = depot_markers[id];
    const map = maps[id];

    // Zoom to employee
    map.setView(marker.getLatLng(), 16, {
        animate: true,
        duration: 0.8
    });

    // Optional bounce effect (simple visual pulse)
    marker.setZIndexOffset(1000);

    setTimeout(() => {
        marker.setZIndexOffset(0);
    }, 1200);
}

function filterViolation(category, cardElement = null) {
    if (totalViolations == 0)
        return;

    const cards = document.querySelectorAll(".violation-filter");
    cards.forEach(card => card.classList.remove("active-kpi"));

    if (!cardElement) {
        if (category === "all")
            cardElement = document.getElementById("total-violations-kpi");
        else if (category === "vehicle")
            cardElement = document.getElementById("vehicle-type-violations-kpi");
        else if (category === "sharing")
            cardElement = document.getElementById("sharing-violations-kpi");
        else if (category === "time")
            cardElement = document.getElementById("time-violations-kpi");
    }
    cardElement.classList.add("active-kpi");

    const sections = {
        vehicle: "vehicle-type-section",
        sharing: "sharing-section",
        time: "time-section"
    };

    if (category === "all") {
        Object.values(sections).forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = "block";
        });
        return;
    }

    Object.entries(sections).forEach(([key, id]) => {
        const el = document.getElementById(id);
        if (!el) return;

        el.style.display = key === category ? "block" : "none";
    });
}

/* ================= EMPLOYEES LEGEND ================= */

const legendContainer = document.getElementById("employeeLegend");

const labels = [
    { name: "Satisfied", value: satisfied, color: "#22c55e", category: "satisfied" },
    { name: "Compromised", value: compromised, color: "#ff3b30", category: "compromised" },
    { name: "Unassigned", value: unassigned, color: "#6b7280", category: "unassigned" }
];

legendContainer.innerHTML = "";

labels.forEach(item => {

    const legendItem = document.createElement("div");
    legendItem.classList.add("legend-item");
    legendItem.onclick = () => filterEmployees(item.category);

    legendItem.innerHTML = `
        <span class="legend-dot" style="background:${item.color}"></span>
        <span class="legend-label">${item.name}</span>
        <span class="legend-value">${item.value}</span>
    `;

    legendContainer.appendChild(legendItem);
});


/* ================= MAP INITIALIZATION ================= */

data.vehicle_schedules.forEach(vehicle =>{
    const map = L.map(vehicle.vehicle.id).setView([office[0],office[1]],12);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
        attribution:'© OpenStreetMap'
    }).addTo(map);

    maps[vehicle.vehicle.id] = map; 
    routes[vehicle.vehicle.id] = {};
    
    const start = vehicle.vehicle.start_location.replace(/[()]/g,"").split(",");
    let depotMarker = L.marker([parseFloat(start[0]), parseFloat(start[1])], { icon: depotIcon })
    .addTo(map);
    depotMarker.bindTooltip(`
        <div class="map-tooltip">
            <strong>Vehicle:</strong> ${vehicle.vehicle.id}<br>
            <strong>Depot:</strong> Start Location<br>
            <strong>Capacity:</strong> ${vehicle.vehicle.capacity}
        </div>
    `, {
        direction: "top",
        offset: [0, -10],
        sticky: true
    });

    depot_markers[vehicle.vehicle.id] = depotMarker;
    
    let officeMarker = L.marker([office[0], office[1]], { icon: officeIcon })
    .addTo(map);
    officeMarker.bindTooltip(`
        <div class="map-tooltip">
            <strong>Office</strong><br>
            Central Drop-off Hub
        </div>
    `, {
        direction: "top",
        offset: [0, -10],
        sticky: true
    });

    office_markers[vehicle.vehicle.id] = officeMarker;

    vehicle.trips.forEach(trip=>{
        const coordinates = trip.route.map(coord => [coord[0], coord[1]]);
        routes[vehicle.vehicle.id][trip.trip_number] = L.polyline(coordinates,{
            color: getGradientColor(trip.trip_number - 1, vehicle.trips.length, cyan_shades),
            zIndexOffset: 0,
            weight:4
        }).addTo(map);

        routes[vehicle.vehicle.id][trip.trip_number].on('click', function(){
            toggleTripDetails(vehicle.vehicle.id, trip.trip_number);
        });
        
        trip.employees.forEach(emp=>{
            emp_trip[emp] = trip.trip_number;
            let point = data.employees[emp].pickup;
            let emp_marker = L.marker([point.lat, point.lng], { icon: employeeIcon }).addTo(map);
            emp_marker.bindTooltip(`
                <div class="map-tooltip">
                    <strong>Employee:</strong> ${emp}<br>
                    <strong>Priority:</strong> ${data.employees[emp].priority}<br>
                    <strong>Trip:</strong> ${trip.trip_number}<br>
                    <strong>Pickup:</strong> ${data.employees[emp].pickup_time}<br>
                    <strong>Drop:</strong> ${data.employees[emp].dropoff_time}
                </div>
            `, {
                direction: "top",
                offset: [0, -10],
                opacity: 1,
                sticky: true
            });
            emp_marker.on('click', function(){
                toggleTripDetails(vehicle.vehicle.id, trip.trip_number);
            });
            emp_markers[emp] = {
                marker: emp_marker,
                vehicle: vehicle.vehicle.id
            };
        });
    });
});

/* ================= UNASSIGNED MAP ================= */

if (unassigned > 0) {
    const unassignedMap = L.map("unassigned-map");
    unassignedMap.setView([office[0], office[1]], 12);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
        attribution:'© OpenStreetMap'
    }).addTo(unassignedMap);

    maps["unassigned-map"] = unassignedMap;

    // Add office marker
    L.marker([office[0], office[1]], { icon: officeIcon })
        .addTo(unassignedMap);

    var index = 0;
    Object.entries(data.employees).forEach(([id, emp]) => {

        if (!emp.assigned) {
            const coordinates = emp.route.map(coord => [coord[0], coord[1]]);
            const latLng = [emp.pickup.lat, emp.pickup.lng];
            
            unassigned_emp_route[id] = L.polyline(coordinates,{
                color: getGradientColor(index, unassigned, gray_shades),
                zIndexOffset: 0,
                weight:4
            }).addTo(unassignedMap);

            const marker = L.marker(latLng, { icon: employeeIcon })
                .addTo(unassignedMap);

            // Store marker reference
            emp_markers[id] = {
                marker: marker,
                vehicle: "unassigned-map"
            };

            marker.bindTooltip(`
                <div class="map-tooltip">
                    <strong>Employee:</strong> ${id}<br>
                    <strong>Priority:</strong> ${emp.priority}<br>
                    <strong>Sharing Preference:</strong> ${emp.sharing_preference}<br>
                    <strong>Vehicle Preference:</strong> ${emp.vehicle_preference}
                </div>
            `);

            index += 1;
        }
    });
}

/* ================= DONUT CHARTS ================= */

let ids=[],costs=[],times=[];
data.vehicle_schedules.forEach(vehicle=>{
    ids.push(vehicle.vehicle.id);
    costs.push(vehicle.vehicle.cost);
    times.push(vehicle.vehicle.time);
});

const centerTextPlugin = {
    id: 'centerText',
    beforeDraw(chart, args, options) {
        const { width, height } = chart;
        const ctx = chart.ctx;

        ctx.restore();

        // MUCH smaller text
        const fontSize = height / 18;

        ctx.font = `600 ${fontSize}px Orbitron`;
        ctx.fillStyle = "#ffffff";   // Velora white
        ctx.textBaseline = "middle";

        const text = options.text;

        const textX = (width - ctx.measureText(text).width) / 2;
        const textY = height / 2;

        ctx.fillText(text, textX, textY);
        ctx.save();
    }
};

new Chart(document.getElementById("costDonut"), {
    type: "doughnut",
    data: {
        labels: ids,
        datasets: [{
            data: costs,
            backgroundColor: pie_colors,
            borderColor: "#0b1437",
            borderWidth: 0,
            hoverOffset: 15
        }]
    },
    options: {
        cutout: "65%",
        onHover: (event, elements) => {
            event.native.target.style.cursor =
                elements.length ? 'pointer' : 'default';
        },
        layout: { padding: 20 },
        plugins: {
            legend: { display: false },
            centerText: { text: "COST" },
            tooltip: {
                backgroundColor: "#111827",
                borderColor: "#4f7cff",
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                titleFont: {
                    family: "Orbitron",
                    size: 14,
                    weight: "600"
                },
                bodyFont: {
                    family: "Inter",
                    size: 13
                },
                titleColor: "#ffffff",
                bodyColor: "#d1d5db",
                displayColors: false,

                callbacks: {
                    title: function(context) {
                        return "Vehicle " + context[0].label;
                    },
                    label: function(context) {
                        const value = context.raw;
                        const total = context.chart._metasets[0].total;
                        const percent = ((value / total) * 100).toFixed(1);

                        return [
                            "Total: ₹ " + value.toFixed(2),
                            "Share: " + percent + " %"
                        ];
                    }
                }
            }
        },
        onClick: (evt, elements) => {
            if (elements.length > 0) {

                const index = elements[0].index;
                const vehicleId = ids[index]; 

                showPane('vehicle'+vehicleId, document.getElementById('button'+vehicleId));
            }
        }
    },
    plugins: [centerTextPlugin]
});

new Chart(document.getElementById("timeDonut"), {
    type: "doughnut",
    data: {
        labels: ids,
        datasets: [{
            data: times,
            backgroundColor: pie_colors,
            borderColor: "#0b1437",
            borderWidth: 0,
            hoverOffset: 15
        }]
    },
    options: {
        cutout: "65%",
        onHover: (event, elements) => {
            event.native.target.style.cursor =
                elements.length ? 'pointer' : 'default';
        },
        layout: { padding: 20 },
        plugins: {
            legend: { display: false },
            centerText: { text: "TIME" },
            tooltip: {
                backgroundColor: "#111827",
                borderColor: "#4f7cff",
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                titleFont: {
                    family: "Orbitron",
                    size: 14,
                    weight: "600"
                },
                bodyFont: {
                    family: "Inter",
                    size: 13
                },
                titleColor: "#ffffff",
                bodyColor: "#d1d5db",
                displayColors: false,

                callbacks: {
                    title: function(context) {
                        return "Vehicle " + context[0].label;
                    },
                    label: function(context) {
                        const value = context.raw;
                        const total = context.chart._metasets[0].total;
                        const percent = ((value / total) * 100).toFixed(1);

                        return [
                            "Total: " + value.toFixed(2) + " mins",
                            "Share: " + percent + " %"
                        ];
                    }
                }
            }
        },
        onClick: (evt, elements) => {
            if (elements.length > 0) {

                const index = elements[0].index;
                const vehicleId = ids[index]; 

                showPane('vehicle'+vehicleId, document.getElementById('button'+vehicleId));
            }
        }
    },
    plugins: [centerTextPlugin]
});

const employeeChart = new Chart(document.getElementById("employeeDonut"), {
    type: "doughnut",
    data: {
        labels: ["Satisfied", "Compromised", "Unassigned"],
        datasets: [{
            data: [satisfied, compromised, unassigned],
            backgroundColor: [
                "#22c55e",   // satisfied - green
                "#ff3b30",   // compromised - stronger red
                "#6b7280"    // unassigned - neutral gray
            ],
            borderColor: "#0b1437",
            borderWidth: 0,
            hoverOffset: 15
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: true,
        aspectRatio: 1,
        animation: {
            duration: 0
        },
        cutout: "65%",
        onHover: (event, elements) => {
            event.native.target.style.cursor =
                elements.length ? 'pointer' : 'default';
        },
        layout: { padding: 30 },
        plugins: {
            legend: {
                display: false,
                position: 'right',
                labels: {
                    color: "#e8ecff",
                    font: {
                        family: "Orbitron",
                        size: 13,
                        weight: "600"
                    },
                    padding: 20,
                    boxWidth: 18,
                    boxHeight: 18,
                    usePointStyle: true,
                    pointStyle: 'square'
                }
            },
            centerText: { text: "EMPLOYEES" },
            tooltip: {
                backgroundColor: "#111827",
                borderColor: "#4f7cff",
                borderWidth: 1,
                cornerRadius: 8,
                padding: 12,
                titleFont: {
                    family: "Orbitron",
                    size: 14,
                    weight: "600"
                },
                bodyFont: {
                    family: "Inter",
                    size: 13
                },
                titleColor: "#ffffff",
                bodyColor: "#d1d5db",
                displayColors: false,
                callbacks: {
                    title: function(context) {
                        return context[0].label;
                    },
                    label: function(context) {
                        const value = context.raw;
                        const total = satisfied + compromised + unassigned;
                        const percent = ((value / total) * 100).toFixed(1);
                        return [
                            "Count: " + value,
                            "Share: " + percent + " %"
                        ];
                    }
                }
            }
        },
        onClick: (evt, elements) => {

            if (elements.length === 0) return;

            const index = elements[0].index;

            let category;

            if (index === 0) category = "satisfied";
            if (index === 1) category = "compromised";
            if (index === 2) category = "unassigned";

            filterEmployees(category);
        }
    },
    plugins: [centerTextPlugin]
});

/* ================= VIOLATION DONUT ================= */

if (totalViolations > 0){

    new Chart(document.getElementById("violationDonut"), {
        type: "doughnut",
        data: {
            labels: ["Vehicle Type", "Sharing", "Time"],
            datasets: [{
                data: [vehicleViol, sharingViol, timeViol],
                backgroundColor: [
                    "#ff3b30",   // red
                    "#ff9500",   // orange
                    "#facc15"    // yellow
                ],
                borderWidth: 0,
                hoverOffset: 15
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 1,
            cutout: "65%",
            animation: { duration: 0 },
            onHover: (event, elements) => {
                event.native.target.style.cursor =
                    elements.length ? 'pointer' : 'default';
            },
            layout: { padding: 30 },
            plugins: {
                legend: { display: false },
                centerText: { text: "VIOLATIONS" },
                tooltip: {  
                    backgroundColor: "#111827",
                    borderColor: "#4f7cff",
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    titleFont: {
                        family: "Orbitron",
                        size: 14,
                        weight: "600"
                    },
                    bodyFont: {
                        family: "Inter",
                        size: 13
                    },
                    titleColor: "#ffffff",
                    bodyColor: "#d1d5db",
                    displayColors: false,              
                    callbacks: {
                        title: function(context) {
                            return context[0].label;
                        },
                        label: function(context) {
                            const value = context.raw;
                            const percent = ((value / totalViolations) * 100).toFixed(1);
                            return [
                                "Count: " + value,
                                "Share: " + percent + " %"
                            ];
                        }
                    }
                }
            },
            onClick: (evt, elements) => {
                if (!elements.length) return;

                const index = elements[0].index;

                if (index === 0) filterViolation("vehicle");
                if (index === 1) filterViolation("sharing");
                if (index === 2) filterViolation("time");
            }
        },
        plugins: [centerTextPlugin]
    });

    const violationLegend = document.getElementById("violationLegend");

    const violationLabels = [
        { name: "Vehicle Type", value: vehicleViol, color: "#ff3b30", category: "vehicle" },
        { name: "Sharing", value: sharingViol, color: "#ff9500", category: "sharing" },
        { name: "Time", value: timeViol, color: "#facc15", category: "time" }
    ];

    violationLegend.innerHTML = "";

    violationLabels.forEach(item => {

        const legendItem = document.createElement("div");
        legendItem.classList.add("legend-item");
        legendItem.onclick = () => filterViolation(item.category);

        legendItem.innerHTML = `
            <span class="legend-dot" style="background:${item.color}"></span>
            <span class="legend-label">${item.name}</span>
            <span class="legend-value">${item.value}</span>
        `;

        violationLegend.appendChild(legendItem);
    });
}

/* ================= OPTIMIZATION GAUGE ================= */

const timeoptimizedValue = 100 * (1.0 - data.summary.total_time / data.summary.baseline_time);

const timegaugeCtx = document.getElementById("timeoptimizedGauge");

new Chart(timegaugeCtx, {
    type: "doughnut",
    data: {
        datasets: [{
            data: [timeoptimizedValue, 100 - timeoptimizedValue],
            backgroundColor: [
                "transparent",
                "rgba(255,255,255,0.05)"
            ],
            borderWidth: 0,
            circumference: 180,
            rotation: 270
        }]
    },
    options: {
        responsive: true,
        cutout: "75%",
        plugins: {
            legend: { display: false },
            tooltip: { enabled: false }
        }
    },
    plugins: [{
        id: "gaugeGradient",
        beforeDraw(chart) {
            const { ctx, chartArea } = chart;
            if (!chartArea) return;

            const gradient = ctx.createLinearGradient(
                chartArea.left,
                0,
                chartArea.right,
                0
            );

            gradient.addColorStop(0, "#ef4444");   // red
            gradient.addColorStop(0.5, "#facc15"); // yellow
            gradient.addColorStop(1, "#22c55e");   // green

            chart.data.datasets[0].backgroundColor[0] = gradient;
        }
    }]
}); 

const costoptimizedValue = 100 * (1.0 - data.summary.travel_cost / data.summary.baseline_cost);

const costgaugeCtx = document.getElementById("costoptimizedGauge");

new Chart(costgaugeCtx, {
    type: "doughnut",
    data: {
        datasets: [{
            data: [costoptimizedValue, 100 - costoptimizedValue],
            backgroundColor: [
                "transparent",
                "rgba(255,255,255,0.05)"
            ],
            borderWidth: 0,
            circumference: 180,
            rotation: 270
        }]
    },
    options: {
        responsive: true,
        cutout: "75%",
        plugins: {
            legend: { display: false },
            tooltip: { enabled: false }
        }
    },
    plugins: [{
        id: "gaugeGradient",
        beforeDraw(chart) {
            const { ctx, chartArea } = chart;
            if (!chartArea) return;

            const gradient = ctx.createLinearGradient(
                chartArea.left,
                0,
                chartArea.right,
                0
            );

            gradient.addColorStop(0, "#ef4444");   // red
            gradient.addColorStop(0.5, "#facc15"); // yellow
            gradient.addColorStop(1, "#22c55e");   // green

            chart.data.datasets[0].backgroundColor[0] = gradient;
        }
    }]
}); 

/* ================= CONSTRAINT ROW POPUP ================= */

const popup = document.getElementById("constraintPopup");

document.addEventListener("mouseover", function(e) {

    const cell = e.target.closest(".constraint-cell");
    if (!cell) return;

    const info = cell.dataset.constraints.split("||");
    if (!info) return;

    const eid = info[0];
    const constraints = info[1].split(" | ");
    const sharing_word_count = {1: 'single', 2: 'double', 3: 'triple'};

    var TYPE_ROW = `
        <tr> 
            <td>Vehicle Type</td>
            <td>${data.employees[eid].vehicle_preference}</td>
            <td>${data.vehicles[data.employees[eid].vehicle].category}</td>
        </tr>`;

    if (constraints.includes('vehicle_type')){
        TYPE_ROW = `
        <tr style="background-color: #830202"> 
            <td>Vehicle Type</td>
            <td>${data.employees[eid].vehicle_preference}</td>
            <td>${data.vehicles[data.employees[eid].vehicle].category}</td>
        </tr>`;
    }

    var SHARING_ROW = `
        <tr> 
            <td>Sharing</td>
            <td>${data.employees[eid].sharing_preference}</td>
            <td>${sharing_word_count[data.employees[eid].sharing]}</td>
        </tr>`;

    if (constraints.includes('capacity_sharing')){
        SHARING_ROW = `
        <tr style="background-color:  #a50000""> 
            <td>Sharing</td>
            <td>${data.employees[eid].sharing_preference}</td>
            <td>${sharing_word_count[data.employees[eid].sharing]}</td>
        </tr>`;
    }

    var DROP_ROW = `
        <tr> 
            <td>Drop Time</td>
            <td>${data.employees[eid].drop_max}</td>
            <td>${data.employees[eid].dropoff_time}</td>
        </tr>`;
    
    if (constraints.includes('time')){
        DROP_ROW = `
        <tr style="background-color:  #d70000""> 
            <td>Drop Time</td>
            <td>${data.employees[eid].drop_max}</td>
            <td>${data.employees[eid].dropoff_time}</td>
        </tr>`;
    }

    var HTML = `
    <table>
        <tr>
            <th>Constraint</th>
            <th>Prefered</th>
            <th>Actual</th>
        </tr>
        ${TYPE_ROW}
        ${SHARING_ROW}
        ${DROP_ROW}
    </table>`;
    popup.innerHTML = HTML;

    // Position above row
    const rect = cell.getBoundingClientRect();

    popup.style.left = (rect.left + rect.right) / 2 + window.scrollX - popup.offsetWidth  / 2 + "px";
    popup.style.top  = rect.top + window.scrollY - popup.offsetHeight - 12 + "px";

    popup.classList.add("active");
});

document.addEventListener("mouseout", function(e) {

    const cell = e.target.closest(".constraint-cell");
    if (!cell) return;

    popup.classList.remove("active");
});