# v2025-08-29e — compat OpenAI 1.x (sem proxies), Python 3.13, visão por imagens
import os, uuid, re
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

app = Flask(__name__)

# Pasta para uploads (Render: disco efêmero, mas público durante a execução)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Garante que nenhum proxy do ambiente quebre o SDK novo (não é obrigatório, mas evita dor de cabeça)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# -------- Config OpenAI pelo ambiente --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "Você é o Assistente Técnico Quanton3D para impressoras SLA/DLP.\n"
    "Responda em português do Brasil, prático e educado.\n"
    "Modo Certeiro: se faltar informação crítica, faça APENAS 1 pergunta objetiva e pare.\n"
    "Quando houver imagem, descreva o que observa e relacione com o defeito.\n"
    "Quando o escopo indicar 'lcd', priorize testes: papel branco, grade/pixel test, limpeza, difusor/backlight, "
    "sem dar checklists genéricos fora do contexto.\n"
)

# -------- Utilidades --------
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def save_uploads(files):
    urls = []
    for f in files[:5]:
        if not f or f.filename == "":
            continue
        if not allowed_file(f.filename):
            continue
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", f.filename)
        fname = f"{uuid.uuid4().hex}_{safe}"
        path = os.path.join(UPLOAD_DIR, fname)
        f.save(path)
        urls.append(url_for("get_upload", fname=fname, _external=True))
    return urls

# -------- Rotas --------
@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

@app.get("/")
def index():
    # Usa o template existente
    return render_template("index.html")

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": "2025-08-29e",
        "model": MODEL,
        "temperature": TEMP,
        "seed": SEED,
    })

@app.post("/chat")
def chat():
    phone   = (request.form.get("phone") or "").strip()
    scope   = (request.form.get("scope") or "desconhecido").strip()
    resin   = (request.form.get("resin") or "").strip()
    printer = (request.form.get("printer") or "").strip()
    problem = (request.form.get("problem") or "").strip()

    # Uploads (campo "photos" no formulário)
    image_urls = []
    if "photos" in request.files:
        image_urls = save_uploads(request.files.getlist("photos"))

    # Conteúdo que vai para o modelo
    user_text = (
        f"Escopo informado: {scope}\n"
        f"Resina: {resin or '-'}\n"
        f"Impressora: {printer or '-'}\n"
        f"Problema: {problem or '-'}\n"
        f"Telefone: {phone or '-'}"
    )
    content = [{"type": "text", "text": user_text}]
    for u in image_urls:
        content.append({"type": "image_url", "image_url": {"url": u}})

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        reply = (resp.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "reply": reply, "images": len(image_urls)})
    except Exception as e:
        # log enxuto de erro para facilitar debug nos Events
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Para rodar localmente: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
