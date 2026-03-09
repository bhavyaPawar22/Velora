from flask import *
import optimize.main as sol

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

@app.route('/')
def home():
    return redirect('/input')

@app.route('/input')
def input():
    return render_template('upload.html')
 
@app.route('/output', methods = ['GET', 'POST'])
def output():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part", 400
        file = request.files['file']
        if file.filename == '':
            return "No selected file", 400
        if file and file.filename.endswith(('.xls', '.xlsx')):
            res = sol.optimize(file, verbose=False)
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
    print("APP STARTING...")
    sol.precompute()
    print("APP RUNNING...")
    app.run(host='0.0.0.0', port=7860, debug = True)
    print("APP STOPPED RUNNING")
else:
    print("APP PRECOMPUTING...")
    sol.precompute()
