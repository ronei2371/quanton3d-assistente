# app.py
# Quanton3D Assistente – v2025-08-31c
# - Bloqueio por telefone (admin: /admin, /admin/block, /admin/unblock, /admin/list)
# - Compatível Python 3.13 (fallback do imghdr)
# - OpenAI SDK 1.x (sem proxies)
# - Visão por imagem (até 5 imagens, 3MB cada)
# - /diag e /healthz para diagnósticos

import os, json, base64, uuid, re, logging
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, abort
from openai import OpenAI

# ---- Compat: Python 3.13 removeu imghdr; criamos um fallback simples
try:
    import imghdr  # ok em Python <= 3.12
except ModuleNotFoundError:
    class imghdr:  # fallback básico (JPEG/PNG/WEBP)
        @staticmethod
        def what(file=None, h=None):
            if h is None and hasattr(file, "read"):
                pos = file.tell()
                h = file.read(16)
                file.seek(pos)
            if not isinstance(h, (bytes, bytearray)):
                return None
            head = h[:16]
            if head.startswith(b"\xFF\xD8\xFF"):                 # JPEG
                return "jpeg"
            if head.startswith(b"\x89PNG\r\n\x1a\n"):           # PNG
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":   # WEBP
                return "webp"
            return None

APP_VERSION = "2025-08-31c"

# ---- Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total (uploads)

# Cria pasta de uploads
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remove proxies do ambiente para evitar erro "proxies" no client OpenAI
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# ---- ENV
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME       = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE      = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED             = int(os.getenv("OPENAI_SEED", "123"))
ADMIN_TOKEN      = os.getenv("ADMIN_TOKEN", "")

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# ---- SYSTEM PROMPTS
ASSISTANT_SYSTEM = """
Você é técnico de campo da Quanton3D. Estilo: direto, oficina, prático.
REGRAS:
- Se faltar dado essencial, faça APENAS 1 pergunta objetiva e PARE (aguarde resposta).
- Se houver IMAGEM, descreva o que observa e relacione com o defeito.
- Evite checklists genéricos fora do contexto.
- Priorize diagnóstico por testes práticos.

PROTOCOLO-LCD (quando tema/ imagem sugerirem LCD):
1) Teste do PAPEL BRANCO (cuba fora) para uniformidade:
   - Faixas/zonas que se repetem = suspeitar de DIFUSOR/LED (backlight).
   - Pontos/linhas fixos = PIXELS MORTOS (LCD).
2) Teste de GRADE/pattern: procurar quadrados apagados/linhas.
3) Limpeza suave do LCD: microfibra + IPA 99%, sem encharcar, sem pressão.
4) Checar FEP (opacidades/riscos) e vazamento de resina nas bordas do LCD.
5) Concluir objetivamente: difusor/LED x LCD x reflexo/ângulo.

No final, traga “O QUE FAZER AGORA” em 3–5 passos objetivos.
"""

SYSTEM_PROMPT = (
    "Você é o Assistente Técnico Quanton3D (SLA/DLP). Português-BR. "
    "Fale curto, direto e educado. Se faltar informação crítica, faça 1 pergunta e pare."
)

# ==================== ADMIN: BLOQUEIO POR TELEFONE ====================
def normalize_phone(p: str) -> str:
    return re.sub(r"\D+", "", p or "")

# Persistência do arquivo de bloqueados
# Se um Disk '/data' estiver presente no Render, use-o. Senão, usa a pasta do app.
BLOCKED_DIR = os.getenv("BLOCKED_DIR", "/data")
if not os.path.isdir(BLOCKED_DIR):
    BLOCKED_DIR = os.path.dirname(__file__)
BLOCKED_FILE = os.path.join(BLOCKED_DIR, "blocked.json")

def _load_blocked():
    try:
        with open(BLOCKED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_blocked(blocked: set):
    try:
        with open(BLOCKED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(blocked), f, ensure_ascii=False, indent=2)
    except Exception as e:
        app.logger.error(f"Falha ao salvar blocked.json: {e}")

BLOCKED = _load_blocked()

@app.post("/admin/block")
def admin_block():
    token = request.headers.get("X-Admin-Token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        abort(401)
    num = normalize_phone(request.form.get("phone"))
    if not num:
        abort(400)
    BLOCKED.add(num)
    _save_blocked(BLOCKED)
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
    _save_blocked(BLOCKED)
    return {"ok": True, "blocked": sorted(BLOCKED)}

@app.get("/admin/list")
def admin_list():
    token = request.headers.get("X-Admin-Token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        abort(401)
    return {"ok": True, "blocked": sorted(BLOCKED)}
# ================== FIM ADMIN: BLOQUEIO POR TELEFONE ===================

# ---- Utilidades de upload/visão
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def _file_to_dataurl_and_size(fs):
    data = fs.read()
    if not data:
        return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# ------------------- ROTAS BÁSICAS -------------------
@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/admin")
def admin_page():
    # página simples de administração (templates/admin.html)
    return render_template("admin.html")

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": MODEL_NAME,
        "temperature": TEMPERATURE,
        "seed": SEED
    }), 200

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# ------------------- CHAT -------------------
@app.post("/chat")
def chat():
    if not OPENAI_API_KEY:
        return jsonify(ok=False, error="OPENAI_API_KEY ausente no servidor."), 500

    try:
        # 1) lê os campos do formulário
        phone   = (request.form.get("phone")   or "").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        # 2) bloqueio por telefone (admin)
        phone = normalize_phone(phone)
        if phone in BLOCKED:
            return jsonify(ok=False, error="Acesso não autorizado. Contate a Quanton3D."), 403

        # 3) imagens (até 5; 3MB cada)
        files = request.files.getlist("images")
        images_dataurls, total_bytes = [], 0
        for i, fs in enumerate(files[:5]):
            if not fs or fs.filename == "":
                continue
            size_hint = fs.content_length or 0
            if size_hint > 3 * 1024 * 1024:
                return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
            dataurl, real_size = _file_to_dataurl_and_size(fs)
            if dataurl:
                total_bytes += real_size
                if real_size > 3 * 1024 * 1024:
                    return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
                images_dataurls.append(dataurl)

        # 4) heurística LCD
        lcd_hint = ("lcd" in problem.lower()) or ("tela" in problem.lower()) or (len(images_dataurls) > 0)

        # 5) monta conteúdo p/ modelo
        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )
        content = [{"type": "text", "text": user_text}]
        for url in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        # 6) chamada OpenAI
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            seed=SEED,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user",   "content": content},
                {"role": "system", "content": SYSTEM_PROMPT},
            ],
        )
        answer = (resp.choices[0].message.content or "").strip()

        return jsonify(ok=True, answer=answer, version=APP_VERSION, model=MODEL_NAME})

    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify(ok=False, error=str(e)), 500

# ------------------- ESTÁTICOS -------------------
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ---- LOCAL
if __name__ == "__main__":
    # local: set OPENAI_API_KEY no seu terminal antes de rodar
    # $env:OPENAI_API_KEY="sua_chave"
    app.run(host="0.0.0.0", port=5000, debug=True)
