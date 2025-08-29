# app.py  — Quanton3D Assistente
# v2025-08-29f  (Python 3.13 + OpenAI SDK 1.x, sem proxies, visão por imagem)

import os, uuid, re, base64
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from openai import OpenAI

# --- Compat: Python 3.13 removeu imghdr; recriamos o suficiente (JPEG/PNG/WEBP)
try:
    import imghdr  # ainda existe até 3.12
except ModuleNotFoundError:
    class imghdr:  # fallback simples
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


APP_VERSION = "2025-08-29f"

# --- Flask e limites
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB por requisição

# Pasta para uploads (se algum dia quisermos servir arquivos)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Remove proxies do ambiente (evita erro "proxies" no SDK novo)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# --- Config OpenAI via ambiente
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- Prompts
SYSTEM_PROMPT = (
    "Você é o Assistente Técnico Quanton3D para impressoras SLA/DLP.\n"
    "Responda em português do Brasil, prático e educado.\n"
    "Modo Certeiro: se faltar informação crítica, faça APENAS 1 pergunta objetiva e pare.\n"
    "Quando houver imagem, descreva o que observa e relacione com o defeito.\n"
    "Quando o escopo indicar 'lcd', priorize testes: papel branco, grade/pixel test, limpeza, difusor/backlight.\n"
)

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

# ---------- Utilidades ----------
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def _file_to_dataurl_and_size(fs):
    """
    Lê o FileStorage e retorna (data_url, bytes). Valida JPEG/PNG/WEBP.
    """
    data = fs.read()
    if not data:
        return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# (Opcional) servir arquivos enviados
@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# ---------- Rotas básicas ----------
@app.get("/")
def index():
    # Seu templates/index.html já funciona; só passamos a versão:
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
    })

# ---------- Chat ----------
@app.post("/chat")
def chat():
    try:
        # Campos do formulário
        phone   = (request.form.get("phone")   or "").strip()
        scope   = (request.form.get("scope")   or "desconhecido").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        # Imagens podem vir como 'images' (padrão do seu HTML) ou 'photos'
        files = request.files.getlist("images")
        if not files:
            files = request.files.getlist("photos")

        images_dataurls, total_bytes = [], 0
        for i, fs in enumerate(files[:5]):
            if not fs or not fs.filename:
                continue
            if not allowed_file(fs.filename):
                return jsonify(ok=False, error=f"Imagem {i+1}: formato inválido."), 400

            size_hint = fs.content_length or 0
            if size_hint > 3 * 1024 * 1024:
                return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400

            dataurl, real_size = _file_to_dataurl_and_size(fs)
            if dataurl:
                total_bytes += real_size
                if real_size > 3 * 1024 * 1024:
                    return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
                images_dataurls.append(dataurl)

        app.logger.info(f"/chat imagens_recebidas={len(images_dataurls)} bytes_totais={total_bytes}")

        # Dica para o modelo: LCD se escopo/descrição indicar ou se houver imagem
        lcd_hint = ("lcd" in scope.lower()) or ("lcd" in problem.lower()) or ("tela" in problem.lower()) or (len(images_dataurls) > 0)

        user_text = (
            f"Escopo informado: {scope}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem or '-'}\n"
            f"Telefone: {phone or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # Conteúdo (texto + imagens) no formato de visão
        content = [{"type": "text", "text": user_text}]
        for dataurl in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": dataurl}})

        # Chamada ao modelo
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            seed=SEED,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user",   "content": content},
            ],
        )

        answer = (resp.choices[0].message.content or "").strip()
        return jsonify(ok=True, answer=answer, version=APP_VERSION, model=MODEL_NAME)

    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify(ok=False, error=str(e)), 500


# (Opcional) servir estáticos manualmente
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


if __name__ == "__main__":
    # Local: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
