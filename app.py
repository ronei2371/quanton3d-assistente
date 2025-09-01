
from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def termo():
    return render_template('termo.html')

@app.route('/chat')
def chat():
    return render_template('index.html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
