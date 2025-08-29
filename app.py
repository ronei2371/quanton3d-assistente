# app.py — v2025-08-29f (OpenAI 1.x, sem proxies, visão por imagens, protocolo LCD)
import os
import uuid
import re
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

# --- Compat: Python 3.13 removeu imghdr; implemento mínimo (jpeg/png/webp)
try:
    import imghdr  # ainda existe até 3.12
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
            if head.startswith(b"\xFF\xD8\xFF"):
                return "jpeg"
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return "webp"
            return None

APP_VERSION = "2025-08-29f"

# --- Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB total por requisição

# Diretório de uploads (disco efêmero no Render, mas acessível durante a execução)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remove proxies do ambiente (evita conflito com SDK novo)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# --- Config OpenAI por variáveis de ambiente
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
MODEL            = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP             = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED             = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)  # Sem proxies aqui!

# --- Prompts
ASSISTANT_SYSTEM = """
Você é técnico de campo da Quanton3D. Estilo: direto de oficina, frases curtas, passo-a-passo.

REGRAS GERAIS:
- Se FALTAR dado essencial, faça APENAS 1 pergunta objetiva e PARE (aguarde resposta).
- Se houver IMAGENS, descreva o que observa e use testes práticos; evite checklist genérico.
- Não culpe “resina com defeito” antes de validar mecânica/óptica/parametrização.
- Sempre termine com: O QUE FAZER AGORA (3 a 5 passos).

PROTOCOLO-LCD (quando for LCD ou houver foto da tela acesa):
1) Teste do PAPEL BRANCO (cuba fora) para avaliar UNIFORMIDADE:
   - Faixas/zonas que se repetem no papel = suspeita DIFUSOR/LED (backlight).
   - Pontos/linhas finas fixas = PIXELS MORTOS (LCD).
2) Teste de GRADE/pattern: procurar quadrados apagados/linhas.
3) Limpeza suave do LCD: microfibra + IPA 99%, sem encharcar, sem pressão.
4) Verificar FEP (opacidade/riscos) e VAZAMENTO de resina nas bordas do LCD.
5) Concluir objetivamente: difusor/LED x LCD x reflexo/ângulo. Evite generalidades.

PROTOCOLO-GERAL (outros casos):
- Checar adesão, nivelamento, tempos de exposição, suportes, estado do FEP, velocidades.
- Depois resina (validades/armazenamento) e temperatura ambiente.
"""

SYSTEM_PROMPT = (
    "Você é o Assistente Técnico Quanton3D para impressoras SLA/DLP.\n"
    "Responda em português do Brasil, prático e educado.\n"
    "Modo Certeiro: se faltar informação crítica, faça APENAS 1 pergunta objetiva e pare.\n"
    "Quando houver imagem, descreva o que observa e relacione com o defeito.\n"
    "Quando o escopo indicar 'lcd', priorize os testes (papel branco, grade, limpeza, difusor/backlight).\n"
)

# --- Utilidades
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp"}

def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in ALLOWED_EXTS

def save_uploads(files):
    """Salva até 5 imagens (máx 3MB cada) e retorna URLs públicas."""
    urls = []
    for f in files[:5]:
        if not f or f.filename == "":
            continue
        if not allowed_file(f.filename):
            continue
        # valida tipo real
        head = f.stream.read(16)
        f.stream.seek(0)
        kind = imghdr.what(None, h=head)
        if kind not in {"jpeg", "png", "webp"}:
            continue
        # valida tamanho
        size_hint = f.content_length or 0
        if size_hint and size_hint > 3 * 1024 * 1024:
            continue
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", f.filename)
        fname = f"{uuid.uuid4().hex}_{safe}"
        path = os.path.join(UPLOAD_DIR, fname)
        f.save(path)
        urls.append(url_for("get_upload", fname=fname, _external=True))
    return urls

# --- Rotas básicas
@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": MODEL,
        "temperature": TEMP,
        "seed": SEED
    }), 200

# arquivos estáticos de upload
@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# (opcional) estáticos do /static
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# --- Chat (form principal)
@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone")   or "").strip()
        scope   = (request.form.get("scope")   or "desconhecido").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify({"ok": False, "error": "Informe o telefone."}), 400
        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400

        image_urls = save_uploads(request.files.getlist("photos")) if "photos" in request.files else []

        # Texto do usuário
        user_text = (
            f"Escopo informado: {scope}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem or '-'}\n"
            f"Telefone: {phone or '-'}"
        )

        # Conteúdo multimodal
        content = [{"type": "text", "text": user_text}]
        for u in image_urls:
            content.append({"type": "image_url", "image_url": {"url": u}})

        # Chamada OpenAI
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": content},
            ],
        )
        reply = (resp.choices[0].message.content or "").strip()
        return jsonify({"ok": True, "reply": reply, "images": len(image_urls)}), 200

    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Para rodar localmente
    app.run(host="0.0.0.0", port=5000, debug=True)
