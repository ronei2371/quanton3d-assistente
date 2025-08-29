import os, base64, imghdr
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from openai import OpenAI

APP_VERSION = "2025-08-29b"

# Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total

# Rotas básicas
@app.get("/")
def index():
    # Cache mais curto para o HTML (evita pegar versão antiga)
    resp = render_template("index.html", app_version=APP_VERSION)
    return resp

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "version": APP_VERSION
    })

# ====== OpenAI ======
def get_client():
    # Sem proxies, simples e compatível
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Prompt – MODO CERTEIRO + protocolo LCD
ASSISTANT_SYSTEM = """
Você é o técnico de campo da Quanton3D. Estilo: direto de oficina, frases curtas, passo-a-passo.

REGRAS GERAIS:
- Quando FALTAR dado essencial, FAÇA APENAS 1 pergunta objetiva e PARE (espere resposta).
- Se houver IMAGENS, descreva o que observa e priorize testes práticos.
- Nunca culpe “resina defeituosa” antes de validar mecânica/óptica/parametrização.
- Traga sempre um “O QUE FAZER AGORA” no final.

PROTOCOLO-LCD (quando a tela/LCD é citada ou há foto da tela acesa):
1) Teste do PAPEL BRANCO (cuba fora): ligar UV/Exposure e avaliar UNIFORMIDADE no papel.
   - Faixas/zonas que se repetem no papel = suspeita DIFUSOR/LED (backlight).
   - Pontos/linhas finas fixas = PIXELS MORTOS (LCD).
2) Teste de GRADE/pattern: procurar quadrados apagados/linhas.
3) Limpeza suave do LCD: microfibra + IPA 99%, sem encharcar, sem pressão.
4) Verificar FEP (opacidade/riscos) e VAZAMENTO de resina nas bordas do LCD.
5) Conclusão objetiva: se padrão se repete no papel → difusor/LED; se pontos/linhas → LCD; caso contrário, reflexo/ângulo.

PROTOCOLO-GERAL (quando não for LCD):
- Cheque adesão, nivelamento, exposição, suporte, FEP, altura de lift e velocidade; depois resina/temperatura.
"""

def _file_to_base64(f):
    data = f.read()
    # Segurança extra: valida mimetype simples
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Apenas JPG, PNG ou WEBP.")
    b64 = base64.b64encode(data).decode("utf-8")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    return f"data:{mime};base64,{b64}"

@app.post("/chat")
def chat():
    try:
        phone = (request.form.get("phone") or "").strip()
        resin = (request.form.get("resin") or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        files = request.files.getlist("images")
        # Validação leve de imagens
        images_b64 = []
        total_bytes = 0
        for i, f in enumerate(files[:5]):
            size = f.content_length or 0
            total_bytes += size
            if size and size > 3 * 1024 * 1024:
                return jsonify(ok=False, error=f"Imagem {i+1} excede 3MB."), 400
            if size == 0:
                continue
            images_b64.append(_file_to_base64(f))

        app.logger.info(f"/chat imagens={len(images_b64)} bytes_totais={total_bytes}")

        # Categoria sugerida (ajuda o modelo a escolher o protocolo)
        p_low = problem.lower()
        lcd_hint = ("lcd" in p_low) or ("tela" in p_low) or (len(images_b64) > 0)

        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # Monta conteúdo do usuário (texto + imagens)
        content = [{"type": "text", "text": user_text}]
        for url in images_b64:
            content.append({"type": "image_url", "image_url": url})

        client = get_client()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": ASSISTANT_SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify(ok=True, answer=answer, version=APP_VERSION)
    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify(ok=False, error=str(e)), 500

# Arquivos estáticos (logo etc.)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Local: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
