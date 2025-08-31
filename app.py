# -*- coding: utf-8 -*-
# v2025-08-31a — compat Python 3.13, OpenAI 1.x, visão por imagens, sessões por telefone
import json, re  # já pode existir; garanta que estejam importados

def normalize_phone(p: str) -> str:
    """Mantém só dígitos do telefone."""
    return re.sub(r"\D+", "", p or "")

# Arquivo com a denylist (telefones bloqueados)
BLOCKED_FILE = os.path.join(os.path.dirname(__file__), "blocked.json")
try:
    with open(BLOCKED_FILE, "r", encoding="utf-8") as f:
        BLOCKED = set(json.load(f))
except Exception:
    BLOCKED = set()

import os, base64, uuid, re
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

APP_VERSION = "2025-08-31a"

# ---------- Flask app (precisa existir como variável chamada 'app') ----------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total upload
# ==================== ADMIN: BLOQUEIO POR TELEFONE ====================
import os, json, re
from flask import request, jsonify, abort

# token de admin (defina no Render: Settings → Environment → ADMIN_TOKEN)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# normaliza telefone para só dígitos
def normalize_phone(p: str) -> str:
    return re.sub(r"\D+", "", p or "")

# arquivo que guarda a lista de telefones bloqueados (persistente no repo)
BASE_DIR     = os.path.dirname(__file__)
BLOCKED_FILE = os.path.join(BASE_DIR, "blocked.json")

# carrega bloqueados na inicialização
try:
    with open(BLOCKED_FILE, "r", encoding="utf-8") as _f:
        BLOCKED = set(json.load(_f))
except Exception:
    BLOCKED = set()

def _save_blocked():
    """Salva a lista de bloqueados no arquivo JSON."""
    with open(BLOCKED_FILE, "w", encoding="utf-8") as _f:
        json.dump(sorted(BLOCKED), _f, ensure_ascii=False, indent=2)

# ---- endpoints admin -------------------------------------------------
@app.post("/admin/block")
def admin_block():
    token = request.headers.get("X-Admin-Token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        abort(401)  # não autorizado

    num = normalize_phone(request.form.get("phone"))
    if not num:
        abort(400)  # requisição ruim

    BLOCKED.add(num)
    _save_blocked()
    return {"ok": True, "blocked": sorted(BLOCKED)}

@app.post("/admin/unblock")
def admin_unblock():
    token = request.headers.get("X-Admin-Token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        abort(401)

    num = normalize_phone(request.form.get("phone"))
    if not num:
        abort(400)

    BLOCKED.discard(num)
    _save_blocked()
    return {"ok": True, "blocked": sorted(BLOCKED)}
# ================== FIM ADMIN: BLOQUEIO POR TELEFONE ===================

# Pasta para uploads (efêmera no Render, mas serve para visualizar se precisar)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remover proxies do ambiente (alguns hosts injetam e quebram o SDK novo)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# ---------- Config OpenAI (via Environment no Render) ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Memória simples por telefone (só em memória do processo) ----------
SESSIONS = {}  # { phone: [ {"role": "...", "content": ...}, ... ] }

# ---------- Prompts ----------
ASSISTANT_SYSTEM = (
    "Você é técnico de campo da Quanton3D (impressão 3D SLA/DLP). "
    "Fale em PT-BR, tom direto de oficina, educado, passo-a-passo.\n"
    "Modo Certeiro: se faltar dado crítico, faça APENAS 1 pergunta objetiva e pare, aguardando resposta.\n"
    "Se houver imagem, descreva o que observa e relacione com o defeito (sem checklist genérico fora do contexto).\n"
    "Quando o tema for LCD/tela, priorize testes: papel branco, grade/pixel, limpeza suave, difusor/backlight/FEP.\n"
    "Evite culpar 'resina com defeito' antes de validar parâmetros, mecânica e óptica.\n"
    "Sempre termine com bloco 'O QUE FAZER AGORA' (3 a 5 passos claros).\n"
)

# ---------- Utilidades ----------
def _allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def _detect_image_type(header: bytes):
    # assinatura simples (compat com Python 3.13 sem imghdr)
    if header.startswith(b"\xFF\xD8\xFF"):
        return "jpeg", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None

def _file_to_dataurl_and_size(fs):
    data = fs.read()
    if not data:
        return None, 0
    kind, mime = _detect_image_type(data[:16])
    if not kind:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# ---------- Rotas básicas ----------
@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "version": APP_VERSION,
        "openai_key_set": bool(OPENAI_API_KEY),
        "model": MODEL,
        "temperature": TEMP,
        "seed": SEED
    }), 200

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# esta rota permite usar url_for('static_files', filename='printers.json') no HTML
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

@app.get("/reset")
def reset():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "Informe ?phone=numero"}), 400
    SESSIONS.pop(phone, None)
    return jsonify({"ok": True, "phone": phone, "cleared": True})

# ---------- Chat ----------
@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone") or "").strip()
        scope   = (request.form.get("scope") or "desconhecido").strip()
        resin   = (request.form.get("resin") or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()
 phone = normalize_phone(phone)                   # usa a função do bloco ADMIN
        if phone in BLOCKED:
            return jsonify(ok=False, error="Acesso não autorizado. Contate a Quanton3D."), 403
        if not phone:
            return jsonify({"ok": False, "error": "Informe o telefone."}), 400
        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400
        if not OPENAI_API_KEY:
            return jsonify({"ok": False, "error": "OPENAI_API_KEY ausente no servidor."}), 500
 phone = normalize_phone(phone)                   # usa a função do bloco ADMIN
        if phone in BLOCKED:
            return jsonify(ok=False, error="Acesso não autorizado. Contate a Quanton3D."), 403
        # Fotos (até 5, máx 3MB cada)
        images = []
        if "photos" in request.files:
            for i, fs in enumerate(request.files.getlist("photos")[:5]):
                if not fs or fs.filename == "" or not _allowed_file(fs.filename):
                    continue
                size_hint = fs.content_length or 0
                if size_hint and size_hint > 3 * 1024 * 1024:
                    return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
                dataurl, real_size = _file_to_dataurl_and_size(fs)
                if real_size > 3 * 1024 * 1024:
                    return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
                images.append(dataurl)

        # Mensagem de usuário (texto + imagens) — ativa visão
        user_text = (
            f"Telefone: {phone}\n"
            f"Escopo informado: {scope}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )
        content = [{"type": "text", "text": user_text}]
        for url in images:
            content.append({"type": "image_url", "image_url": {"url": url}})

        # Monta histórico por telefone
        history = SESSIONS.setdefault(phone, [])
        messages = [{"role": "system", "content": ASSISTANT_SYSTEM}] + history + [{"role": "user", "content": content}]

        # Chamada ao modelo
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            messages=messages,
        )
        reply = (resp.choices[0].message.content or "").strip()

        # Guarda no histórico (mantemos texto do usuário e resposta)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})

        return jsonify({"ok": True, "reply": reply, "version": APP_VERSION})

    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------- Main (apenas para rodar localmente) ----------
if __name__ == "__main__":
    # Local: python app.py -> abre em http://127.0.0.1:5000
    app.run(host="0.0.0.0", port=5000, debug=True)
