
from flask import render_template

@app.route('/')
def termos():
    # primeira tela SEMPRE é o termo
    return render_template('termo.html')

@app.route('/chat')
def chat():
    # aqui abre o bot
    return render_template('index.html')

app = Flask(__name__)

@app.route('/chat')
def termo():
    return render_template('termo.html')

@app.route('/chat')
def chat():
    return render_template('index.html')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
