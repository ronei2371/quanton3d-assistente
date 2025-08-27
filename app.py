import os
import sqlite3
import base64
import time
import re
from flask import Flask, request, jsonify, render_template
from openai import OpenAI

# ===== Config OpenAI via variável de ambiente =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY não definida. No painel da Render, adicione como Environment Variable."
    )
client = OpenAI(api_key=OPENAI_API_KEY)

# ===== Caminhos / Flask =====
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "chat.db"))  # Render usa /data/chat.db
app = Flask(__name__, template_folder="templates", static_folder="static")

# ===== Util: normalização de telefone =====
def norm_phone(s: str) -> str:
    return re.sub(r"\D+", "", (s or ""))[:15]

def migrate_phone_if_needed(raw_phone: str, phone_norm: str):
    if not raw_phone or raw_phone == phone_norm:
        return
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE history SET phone=? WHERE phone=? AND phone!=?",
            (phone_norm, raw_phone, phone_norm)
        )
        conn.commit()

# ===== Banco de dados =====
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_history_phone ON history(phone)")
        conn.commit()

def save_message(phone_norm, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO history (phone, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (phone_norm, role, content, time.time())
        )
        conn.commit()

def load_history(phone_norm):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT role, content, timestamp FROM history WHERE phone=? ORDER BY timestamp ASC",
            (phone_norm,)
        )
        rows = c.fetchall()
    return [{"role": r, "content": ct, "ts": ts} for (r, ct, ts) in rows if r != "system"]

# ===== Rotas =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/healthz")
def healthz():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("SELECT 1")
        return "ok", 200
    except Exception as e:
        return f"db error: {e}", 500

@app.route("/history", methods=["GET"])
def history():
    raw = (request.args.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify([])
    migrate_phone_if_needed(raw, phone)
    msgs = load_history(phone)
    return jsonify(msgs)

@app.route("/chat", methods=["POST"])
def chat():
    # Recebe multipart/form-data (FormData do front)
    form = request.form
    raw_phone = (form.get("phone") or "").strip()
    phone = norm_phone(raw_phone)
    problem = (form.get("problem") or "").strip()
    resin = (form.get("resin") or "Não informado").strip()
    printer = (form.get("printer") or "Não informado").strip()

    if not phone:
        return jsonify({"error": "Número de telefone é obrigatório"}), 400
    if not problem and "images" not in request.files:
        return jsonify({"error": "Descreva o problema ou envie imagens"}), 400

    migrate_phone_if_needed(raw_phone, phone)

    # Imagens (até 5, 3MB) — aqui só contabilizamos; se quiser enviar ao modelo como base64, dá pra evoluir
    images_count = 0
    if "images" in request.files:
        files = request.files.getlist("images")
        for f in files[:5]:
            data = f.read()
            if not data or len(data) > 3 * 1024 * 1024:
                continue
            images_count += 1
            # Para enviar as imagens à IA:
            # data_url = "data:image/jpeg;base64," + base64.b64encode(data).decode("utf-8")
            # (montar mensagens multimodais se quiser usar GPT-4o com visão)

    # Contexto
    history_msgs = load_history(phone)
    sys_prompt = (
        "Você é o assistente QUANTON3D®, especialista em impressão 3D de resina (SLA/DLP/LCD). "
        "Responda em português (Brasil), com diagnóstico passo a passo, causas prováveis e ações práticas. "
        "Se faltarem dados, peça somente o essencial."
    )
    messages = [{"role": "system", "content": sys_prompt}]
    for m in history_msgs:
        messages.append({"role": m["role"], "content": m["content"]})

    user_msg = f"Problema: {problem}\nResina: {resin}\nImpressora: {printer}"
    if images_count:
        user_msg += f"\nImagens anexadas: {images_count}"

    messages.append({"role": "user", "content": user_msg})
    save_message(phone, "user", user_msg)

    # Chamada modelo
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4,
            max_tokens=800
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        save_message(phone, "assistant", f"[Falha na IA] {e}")
        return jsonify({"error": str(e)}), 500

    save_message(phone, "assistant", reply)
    return jsonify({"reply": reply})

# ===== Main local (Render usa o Procfile/ Gunicorn) =====
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
