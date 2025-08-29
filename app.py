# app.py
# Quanton3D Assistente – v2025-08-29g
# Flask + OpenAI (SDK 1.x), compat Python 3.13, visão por imagens
# Retorno padronizado: {"ok": True, "reply": "...", "answer": "..."} (ambos)
# Limite de upload: 5 imagens JPG/PNG/WEBP até 3MB cada

import os
import re
import uuid
import base64
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for

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
            if head.startswith(b"\x89PNG\r\n\x1a\n"):           # PNG
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":   # WEBP
                return "webp"
            return None

APP_VERSION = "2025-08-29g"

# --- Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total (segurança)

# Pasta pública/efêmera para uploads
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remove proxies do ambiente (evita interferência no SDK novo)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# -------- Config OpenAI pelo ambiente --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Sistema (tom) do assistente
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
- Checar adesão/nivelamento/exposição/suportes/FEP/velocidades; depois resina e temperatura ambiente.
"""

# -------- Utilidades --------
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def _read_validate_image(fs):
    """
    Lê o arquivo em memória, valida tipo e tamanho.
    Retorna (data_url, size) ou (None, 0).
    """
    try:
        data = fs.read()
    except Exception:
        data = b""
    if not data:
        return None, 0

    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")

    if len(data) > 3 * 1024 * 1024:
        raise ValueError("Imagem excede 3MB.")

    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# -------- Rotas --------
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
        "model": MODEL_NAME,
        "temperature": TEMPERATURE,
        "seed": SEED
    }), 200

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone")   or "").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()
        scope   = (request.form.get("scope")   or "").strip()

        if not phone:
            msg = "Informe o telefone."
            return jsonify({"ok": False, "error": msg, "answer": msg}), 200
        if not problem:
            msg = "Descreva o problema."
            return jsonify({"ok": False, "error": msg, "answer": msg}), 200

        # Aceita 'images' (padrão do front) e 'photos' (retrocompatibilidade)
        files = []
        if "images" in request.files:
            files = request.files.getlist("images")
        elif "photos" in request.files:
            files = request.files.getlist("photos")

        images_dataurls, total_bytes = [], 0
        for i, fs in enumerate(files[:5]):
            if not fs or not fs.filename:
                continue
            if not allowed_file(fs.filename):
                msg = f"Imagem {i+1}: formato inválido."
                return jsonify({"ok": False, "error": msg, "answer": msg}), 200

            dataurl, size = _read_validate_image(fs)
            if dataurl:
                images_dataurls.append(dataurl)
                total_bytes += size

        app.logger.info(f"/chat phone={phone} imgs={len(images_dataurls)} bytes={total_bytes}")

        # LCD hint
        lcd_hint = ("lcd" in problem.lower()) or ("tela" in problem.lower()) or bool(images_dataurls)

        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Escopo informado: {scope or ('lcd' if lcd_hint else 'geral')}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        content = [{"type": "text", "text": user_text}]
        for url_data in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": url_data}})

        messages = [
            {"role": "system", "content": ASSISTANT_SYSTEM},
            {"role": "user",   "content": content},
        ]

        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            seed=SEED,
            messages=messages,
        )
        reply = (resp.choices[0].message.content or "").strip()

        # Devolve as duas chaves para o front não dar "undefined"
        return jsonify({"ok": True, "reply": reply, "answer": reply, "images": len(images_dataurls)}), 200

    except Exception as e:
        app.logger.exception("erro no /chat")
        # 200 para o front exibir a string em vez de "undefined"
        return jsonify({"ok": False, "error": str(e), "answer": f"Erro: {e}"}), 200

# Estáticos (logo etc.)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Para rodar localmente: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
