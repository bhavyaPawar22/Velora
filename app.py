from flask import *
import solution as sol
import pandas as pd
import sys

sol.precompute()

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
            res = sol.optimize(file)
            return render_template('output.html', result = res)
        else:
            return "Invalid file format. Please upload an Excel file.", 400

    return render_template('input.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug = True)