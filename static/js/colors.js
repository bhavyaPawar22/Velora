const blue_shades = [{r: 8, g: 22, b: 51}, {r: 57, g: 100, b: 255}];
const cyan_shades = [{r: 10, g: 26, b: 79}, {r: 0, g: 198, b: 245}];
const green_shades = [{r: 14, g: 68, b: 14}, {r: 57, g: 255, b: 57}];
const red_shades = [{r: 84, g: 0, b: 0}, {r: 255, g: 50, b: 50}];
const gray_shades = [{r: 26, g: 26, b: 26}, {r: 104, g: 104, b: 104}];

const pie_colors = [
"#FF3B30","#FF6B00","#FF9500","#FFCC00","#FFD60A",
"#34C759","#30D158","#00C853","#00E676","#00BFA5",
"#00BCD4","#00B0FF","#0091EA","#2979FF","#3D5AFE",
"#5856D6","#7C4DFF","#9C27B0","#E040FB","#D500F9",
"#FF2D55","#FF4081","#F50057","#C51162","#E91E63",
"#FF1744","#D50000","#FF5252","#FF7043","#FF8A65",
"#FFAB40","#FFC400","#AEEA00","#76FF03","#64DD17",
"#1DE9B6","#00E5FF","#18FFFF","#40C4FF","#448AFF",
"#536DFE","#651FFF","#AA00FF","#C51162","#FF80AB",
"#FF4081","#F50057","#FF6EC7","#FF1493","#FF00FF"
];

function getGradientColor(index, total, shade) {

    const startColor = shade[0]; 
    const endColor   = shade[1]; 

    var ratio = 0;
    if (total > 1)
        ratio = index / (total - 1);

    const r = Math.round(startColor.r + ratio * (endColor.r - startColor.r));
    const g = Math.round(startColor.g + ratio * (endColor.g - startColor.g));
    const b = Math.round(startColor.b + ratio * (endColor.b - startColor.b));

    return `rgb(${r}, ${g}, ${b})`;
}

function shadeCategoryTable(table, shades, min_row = 0) {
    if (!table) 
        return;

    const rows = table.querySelectorAll("tr");

    const total = Math.max(min_row, rows.length - 1); // exclude header

    rows.forEach((row, index) => {
        if (index === 0) 
            return; // skip header
        for (let i = 0; i < shades.length; i++) {
            const shade = shades[i];

            if (row.dataset.category === shade.category) {
                row.style.backgroundColor =
                    getGradientColor(index - 1, total, shade.shade);
                break;   
            }
        }
    });
}

function shadeTable(table, shade, min_row = 0) {
    if (!table) 
        return;

    const rows = table.querySelectorAll("tr");

    const total = Math.max(min_row, rows.length - 1); // exclude header

    rows.forEach((row, index) => {
        if (index === 0) 
            return; // skip header
        row.style.backgroundColor = getGradientColor(index - 1, total, shade);
    });
}

/* ================= APPLY SHADES ================= */

const vehicle_shades = [{category: "used", shade: blue_shades}, 
                        {category: "un-used", shade: gray_shades}];
shadeCategoryTable(document.getElementById('vehicle-table'), vehicle_shades, 20);

const employee_shades = [{category: "satisfied", shade: green_shades}, 
                        {category: "compromised", shade: red_shades}, 
                        {category: "unassigned", shade: gray_shades}];
shadeCategoryTable(document.getElementById('employees-table'), employee_shades, 50);

const details_shades = [{category: "satisfied", shade: blue_shades}, 
                        {category: "compromised", shade: red_shades}];
data.vehicle_schedules.forEach(vehicle => {
    shadeCategoryTable(document.getElementById(vehicle.vehicle.id+"-trip-table"), [{category: "trip", shade: cyan_shades}]);
    vehicle.trips.forEach(trip => {
        shadeCategoryTable(document.getElementById(vehicle.vehicle.id+"-details-table-"+trip.trip_number), details_shades, 10);
    });
});

shadeTable(document.getElementById('unassigned-table'), gray_shades);
shadeTable(document.getElementById('vehicle-type-violations-table'), blue_shades, 10);
shadeTable(document.getElementById('sharing-violations-table'), blue_shades, 10);
shadeTable(document.getElementById('time-violations-table'), blue_shades, 10);
