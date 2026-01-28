from flask import *
import mapgraph as mp
import pandas as pd

sheets = ['employees', 'vehicles', 'baseline', 'metadata']

def load_excel(filename):
    testcase = dict()
    try:
        for sheet in sheets:
            testcase[sheet] = pd.read_excel(filename, sheet_name=sheet)
        print(f"Input {filename} Successful.")
        return testcase
    except Exception as e:
        print(f"Error: {e}")
        return None

mp.precompute()

def solve(file):
    answers = []
    tc = load_excel(file)
    employees, vehicles, baseline, metadata = tc['employees'], tc['vehicles'], tc['baseline'], tc['metadata']

    tc_id = metadata.iat[0, 1]
    print(f'ID: {tc_id}')

    drop = mp.nearest_node((employees.iat[0, 4], employees.iat[0, 5]))
    #dist = dijkstras(drop)

    for emp in employees.itertuples():
        pick = mp.nearest_node((emp.pickup_lat, emp.pickup_lng))

        #print(f'{emp.employee_id}:')
        #print('Optimal Path Length:', dist[pick])
        answers.append(mp.optimal_route(pick, drop))
    return answers

#APP
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

@app.route('/input')
def input():
    return render_template('input.html')

@app.route('/output', methods = ['GET', 'POST'])
def output():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part", 400
        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400
        if file and file.filename.endswith(('.xls', '.xlsx')):
            res = solve(file)
            return render_template('output.html', result = res)
        else:
            return "Invalid file format. Please upload an Excel file.", 400

    return render_template('input.html')

if __name__ == '__main__':
    app.run(debug = True)