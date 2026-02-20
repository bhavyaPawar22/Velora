from flask import *
import trail2 as sol
import trail5 as sol2

sol.precompute()

#APP
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

@app.route('/input')
def input():
    return render_template('upload.html')

@app.route('/inputlow')
def inputlow():
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
            res = sol.optimize(file, verbose=True)
            return render_template('final.html', data = res)
        else:
            return "Invalid file format. Please upload an Excel file.", 400

    return render_template('upload.html')


@app.route('/outputlow', methods = ['GET', 'POST'])
def outputlow():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part", 400
        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400
        if file and file.filename.endswith(('.xls', '.xlsx')):
            res = sol2.optimize(file, verbose=True)
            return render_template('final.html', data = res)
        else:
            return "Invalid file format. Please upload an Excel file.", 400

    return render_template('upload.html')

@app.route('/result', methods = ['GET', 'POST'])
def result():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part", 400
        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400
        if file and file.filename.endswith(('.xls', '.xlsx')):
            res = sol.optimize(file, verbose=False)
            return jsonify(res)
        else:
            return "Invalid file format. Please upload an Excel file.", 400

    return jsonify({})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug = True)