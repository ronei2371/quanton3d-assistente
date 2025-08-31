# app.py — Quanton3D Assistente (v2025-08-31c)
import os, base64, uuid, re, json
from pathlib import Path

from flask import (
    Flask, render_template, render_template_string,
    request, jsonify, send_from_directory, url_for
)
from jinja2 import TemplateNotFound
from openai import OpenAI

# --- Compat: Python 3.13 removeu imghdr; recriamos o suficiente (JPEG/PNG/WEBP)
try:
    import imghdr  # ok até 3.12
except ModuleNotFoundError:
    class imghdr:  # compat simples
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
            if head.startswith(b"\x89PNG\r\n\x1a\n"):            # PNG
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":    # WEBP
                return "webp"
            return None

APP_VERSION = "2025-08-31c"

# --- Pastas e Flask ----------------------------------------------------------
BASE_DIR   = os.getcwd()
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB por requisição

# --- Ambiente OpenAI ---------------------------------------------------------
# Remove proxies do ambiente para evitar bug do SDK
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- Termos de uso (pode desligar com REQUIRE_TOS=0 no Render) --------------
REQUIRE_TOS = (os.getenv("REQUIRE_TOS", "1") == "1")

def has_tos_accept(form) -> bool:
    # aceita vários nomes de campo (checkbox)
    val = (
        form.get("tos") or form.get("tos_accept") or
        form.get("terms") or form.get("terms_accept") or ""
    )
    return str(val).strip().lower() in {"1","true","on","yes","sim"}

# --- Admin: bloqueio por telefone -------------------------------------------
BLOCKED_PATH = Path("blocked.json")
BLOCKED: set[str] = set()

def normalize_phone(p: str) -> str:
    return re.sub(r"\D+", "", p or "")

def load_blocked():
    global BLOCKED
    if BLOCKED_PATH.exists():
        try:
            data = json.loads(BLOCKED_PATH.read_text(encoding="utf-8"))
            BLOCKED = set(map(normalize_phone, data))
        except Exception as e:
            app.logger.error(f"Falha ao ler blocked.json: {e}")
            BLOCKED = set()
    else:
        BLOCKED = set()

def save_blocked():
    try:
        BLOCKED_PATH.write_text(
            json.dumps(sorted(BLOCKED), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        app.logger.error(f"Falha ao salvar blocked.json: {e}")

load_blocked()

# --- Utilidades --------------------------------------------------------------
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

# --- Prompt do Assistente ----------------------------------------------------
ASSISTANT_SYSTEM = """
Você é o Assistente Técnico Quanton3D (SLA/DLP), direto de oficina, gentil e objetivo.
- Modo Certeiro: se faltar dado crítico, faça APENAS 1 pergunta objetiva e PARE.
- Se houver IMAGENS: descreva o que observa e use testes práticos. Evite checklist genérico.
- Não culpe “resina com defeito” antes de validar mecânica/óptica/parametrização.
- Sempre feche com: O QUE FAZER AGORA (3–5 passos práticos).

PROTOCOLO LCD (foto de tela acesa ou escopo lcd):
1) Teste PAPEL BRANCO (sem cuba): faixas repetidas = difusor/LED; pontos/linhas finas = pixels mortos (LCD).
2) Teste de GRADE/pattern: procurar quadrados apagados/linhas.
3) Limpeza suave: microfibra + IPA 99%, sem encharcar, sem pressão.
4) Conferir FEP (opacidade/riscos) e vazamento de resina nas bordas do LCD.
5) Concluir: difusor/LED x LCD x reflexo/ângulo, sem generalidades.
"""

# --- Rotas básicas -----------------------------------------------------------
@app.get("/")
def index():
    try:
        return render_template("index.html", app_version=APP_VERSION)
    except TemplateNotFound:
        # fallback simples
        return (
            f"<h1>Quanton3D Assistente</h1>"
            f"<p>Backend ativo. Versão {APP_VERSION}</p>", 200
        )

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
    })

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# --- Admin (página simples + bloquear/desbloquear) --------------------------
@app.get("/admin")
def admin_page():
    try:
        return render_template("admin.html")
    except TemplateNotFound:
        return render_template_string("""
<!doctype html><meta charset="utf-8">
<title>Admin — Quanton3D</title>
<h1>Admin — Bloqueio por telefone</h1>
<p>Digite o telefone com DDI+DDD+Número (somente números). Ex.: 5531983500634</p>
<input id="phone" placeholder="55319xxxxxxxx" style="padding:8px;width:320px">
<button onclick="go('block')">Bloquear</button>
<button onclick="go('unblock')">Desbloquear</button>
<script>
  function go(action){
    const p = document.getElementById('phone').value.trim();
    if(!p){ alert('Informe o telefone.'); return; }
    location.href = `/admin/${action}?phone=${encodeURIComponent(p)}`;
  }
</script>
""")

@app.get("/admin/block")
def admin_block():
    phone = normalize_phone(request.args.get("phone", ""))
    if not phone:
        return "Informe ?phone=559999999999", 400
    BLOCKED.add(phone)
    save_blocked()
    return f"{phone} bloqueado.", 200

@app.get("/admin/unblock")
def admin_unblock():
    phone = normalize_phone(request.args.get("phone", ""))
    if not phone:
        return "Informe ?phone=559999999999", 400
    BLOCKED.discard(phone)
    save_blocked()
    return f"{phone} desbloqueado.", 200

# --- Chat -------------------------------------------------------------------
@app.post("/chat")
def chat():
    try:
        # Campos do formulário
        phone   = (request.form.get("phone")   or "").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        # Normaliza e checa bloqueio
        phone = normalize_phone(phone)
        if phone in BLOCKED:
            return jsonify(ok=False, error="Acesso não autorizado. Contate a Quanton3D."), 403

        # Termos de uso (se exigido)
        if REQUIRE_TOS and not has_tos_accept(request.form):
            return jsonify(ok=False, error="É necessário aceitar os termos de uso."), 400

        # Validações simples
        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        # Imagens (aceita 'images' ou 'photos')
        field_name = "images" if "images" in request.files else ("photos" if "photos" in request.files else None)
        files = request.files.getlist(field_name) if field_name else []
        images_dataurls, total_bytes = [], 0

        for i, fs in enumerate(files[:5]):
            # limite 3MB por imagem
            size_hint = fs.content_length or 0
            if size_hint and size_hint > 3 * 1024 * 1024:
                return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400

            dataurl, real_size = _file_to_dataurl_and_size(fs)
            if dataurl:
                if real_size > 3 * 1024 * 1024:
                    return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
                images_dataurls.append(dataurl)
                total_bytes += real_size

        app.logger.info(f"/chat imagens_recebidas={len(images_dataurls)} bytes_totais={total_bytes}")

        # Sinaliza “LCD” se texto falar em tela/LCD ou se tem imagem
        lcd_hint = ("lcd" in problem.lower()) or ("tela" in problem.lower()) or (len(images_dataurls) > 0)

        # Texto que acompanha as imagens
        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # Monta conteúdo (texto + imagens) para visão
        content = [{"type": "text", "text": user_text}]
        for url in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        # Chamada ao modelo
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            seed=SEED,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user",   "content": content},
            ],
        )
        answer = (resp.choices[0].message.content or "").strip()

        # >>> retornos CERTOS (sem '}' sobrando) <<<
        return jsonify(ok=True, answer=answer, version=APP_VERSION, model=MODEL_NAME)

    except Exception as e:
        app.logger.exception("Erro no /chat")
        return jsonify(ok=False, error=str(e), version=APP_VERSION, model=MODEL_NAME), 500

# --- Dev local ---------------------------------------------------------------
if __name__ == "__main__":
    # Rodar localmente: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
