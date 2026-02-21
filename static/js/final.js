let maps = {};
let routes = {};
const office = data.Office;

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

    setTimeout(()=>{
        const mapDiv = document.querySelector(`#${id} .map`);
        if(mapDiv && maps[mapDiv.id]){
            maps[mapDiv.id].invalidateSize();
        }
    }, 100);
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
            color: route_colors[(rn - 1) % route_colors.length]
        });
    }

    // Toggle current
    if (detailsRow.style.display === "table-row") {
        detailsRow.style.display = "none";
        routes[vehicle][row_n].setStyle({
            color: route_colors[(row_n - 1) % route_colors.length]
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

/* ================= MAP INITIALIZATION ================= */

data.vehicle_schedules.forEach((vehicle,index)=>{
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

    vehicle.trips.forEach(trip=>{
        const coordinates = trip.route.map(coord => [coord[0], coord[1]]);
        routes[vehicle.vehicle.id][trip.trip_number] = L.polyline(coordinates,{
            color: route_colors[(trip.trip_number - 1) % route_colors.length],
            zIndexOffset: 0,
            weight:4
        }).addTo(map);

        routes[vehicle.vehicle.id][trip.trip_number].on('click', function(){
            toggleTripDetails(vehicle.vehicle.id, trip.trip_number);
        });
        
        trip.employees.forEach(emp=>{
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
        });
    });
});

/* ================= DONUT CHARTS ================= */

let ids=[],costs=[],times=[];
data.vehicle_schedules.forEach(vehicle=>{
    ids.push(vehicle.vehicle.id);
    costs.push(vehicle.vehicle.cost);
    times.push(vehicle.vehicle.time);
});

new Chart(document.getElementById("timeDonut"), {
    type: "doughnut",
    data: {
        labels: ids,
        datasets: [{
            data: times,
            backgroundColor: pie_colors,
            borderColor: "#0b1437",
            borderWidth: 3,
            hoverOffset: 12
        }]
    },
    options: {
        cutout: "65%",
        plugins: {
            legend: {
                position: "top",
                labels: {
                    color: "#e8ecff",
                    padding: 20,
                    boxWidth: 18
                }
            }
        }
    }
});

new Chart(document.getElementById("costDonut"), {
    type: "doughnut",
    data: {
        labels: ids,
        datasets: [{
            data: costs,
            backgroundColor: pie_colors,
            borderColor: "#0b1437",
            borderWidth: 3,
            hoverOffset: 12
        }]
    },
    options: {
        cutout: "65%",
        plugins: {
            legend: {
                position: "top",
                labels: {
                    color: "#e8ecff",
                    padding: 20,
                    boxWidth: 18
                }
            }
        }
    }
});