import os, base64
try:
    import imghdr  # presente até Python 3.12
except ModuleNotFoundError:
    # Compat para Python 3.13+: recria imghdr.what() só p/ JPG, PNG e WEBP
    class imghdr:  # noqa: N801 (nome igual ao módulo antigo)
        @staticmethod
        def what(file=None, h=None):
            # Aceita tanto bytes (h) quanto um file-like (file)
            if h is None and hasattr(file, "read"):
                pos = file.tell()
                h = file.read(16)
                file.seek(pos)
            if not isinstance(h, (bytes, bytearray)):
                return None
            head = h[:16]
            # JPEG
            if head.startswith(b"\xFF\xD8\xFF"):
                return "jpeg"
            # PNG
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return "png"
            # WEBP (RIFF....WEBP)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return "webp"
            return None

from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from openai import OpenAI

APP_VERSION = "2025-08-29c"  # mostra no topo do site e em /diag

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total

@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "version": APP_VERSION
    })

# -------- OpenAI client (simples, sem proxies)
def get_client():
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# -------- Prompt com Modo Certeiro e protocolo LCD
ASSISTANT_SYSTEM = """
Você é técnico de campo da Quanton3D. Estilo: direto de oficina, frases curtas, passo-a-passo.

REGRAS GERAIS:
- Se FALTAR dado essencial, faça APENAS 1 pergunta objetiva e PARE (aguarde resposta).
- Se houver IMAGENS, descreva o que observa e use testes práticos; não entregue checklist genérico.
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

def _file_to_base64(f):
    data = f.read()
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Apenas JPG, PNG ou WEBP.")
    b64 = base64.b64encode(data).decode("utf-8")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    return f"data:{mime};base64,{b64}"

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

        files = request.files.getlist("images")
        images_b64, total_bytes = [], 0
        for i, f in enumerate(files[:5]):
            size = f.content_length or 0
            total_bytes += size
            if size and size > 3 * 1024 * 1024:
                return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
            if size == 0:
                continue
            images_b64.append(_file_to_base64(f))

        app.logger.info(f"/chat imagens_recebidas={len(images_b64)} bytes_totais={total_bytes}")

        # Dica ao modelo: LCD se tem foto ou o texto falar em tela/LCD
        lcd_hint = ("lcd" in problem.lower()) or ("tela" in problem.lower()) or (len(images_b64) > 0)

        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # >>>> AQUI ESTÁ A CORREÇÃO IMPORTANTE <<<<
        content = [{"type": "text", "text": user_text}]
        for url in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": url}   # precisa ser objeto com {"url": "..."}
            })

        client = get_client()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user",   "content": content},
            ],
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify(ok=True, answer=answer, version=APP_VERSION)
    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify(ok=False, error=str(e)), 500

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
