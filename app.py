import os, base64
from flask import Flask, render_template, request, jsonify, send_from_directory
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
            if head.startswith(b"\x89PNG\r\n\x1a\n"):           # PNG
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":   # WEBP
                return "webp"
            return None

APP_VERSION = "2025-08-29d"

# --- Variáveis de ambiente (para padronizar PC e Render)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# --- Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total de upload

# --- Rotas básicas
@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return {
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": MODEL_NAME,
        "temperature": TEMPERATURE,
        "seed": SEED
    }, 200

# --- Prompt com Modo Certeiro e protocolo LCD
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

# --- Utilitário: converte arquivo de imagem -> data URL, validando tipo
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

@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone")   or "").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        # Imagens (máx 5; 3MB cada)
        files = request.files.getlist("images")
        images_dataurls, total_bytes = [], 0
        for i, fs in enumerate(files[:5]):
            # tamanho via stream (pode ser None em alguns hosts)
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

        # Sinaliza “LCD” se texto falar em tela/LCD ou se tem imagem
        lcd_hint = ("lcd" in problem.lower()) or ("tela" in problem.lower()) or (len(images_dataurls) > 0)

        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # Monta conteúdo (texto + imagens) no formato que ativa visão
        content = [{"type": "text", "text": user_text}]
        for url in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            seed=SEED,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user",   "content": content},
            ],
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify(ok=True, answer=answer, version=APP_VERSION, model=MODEL_NAME)
    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify(ok=False, error=str(e)), 500

# Estáticos (logo etc.)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Local: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
