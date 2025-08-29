# app.py
# v2025-08-29h — Flask + OpenAI 1.x (sem proxies), Python 3.13 (com shim imghdr), visão por imagens.
import os, uuid, re, base64, logging
from typing import List, Tuple
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

# -------------------- Compat: Python 3.13 removeu imghdr; criamos um shim simples --------------------
try:
    import imghdr  # até 3.12
except ModuleNotFoundError:
    class imghdr:  # compat básico para JPEG/PNG/WEBP
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

APP_VERSION = "2025-08-29h"

# -------------------- Flask --------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB (soma dos uploads)

# Pasta pública para ver uploads (se você quiser salvar arquivos)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -------------------- Ambiente --------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL          = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP           = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

# Cliente OpenAI (NÃO passar proxies)
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------- Regras de atendimento --------------------
ASSISTANT_SYSTEM = """
Você é técnico de campo da Quanton3D. Estilo: direto, passo a passo, português-BR.

MODO CERTEIRO:
- Faça NO MÁXIMO 1 pergunta objetiva se faltar dado essencial ao defeito.
- NÃO peça marca/modelo de impressora nem marca da resina por padrão.
- Só peça impressora/resina SE o usuário falar explicitamente de “perfil”, “exposição”, “fatiamento” ou pedir NÚMEROS de tempo/exposição.

PROTOCOLO-LCD (se tema for LCD/tela/foto da tela acesa):
1) Teste do PAPEL BRANCO (cuba fora) para ver UNIFORMIDADE:
   - Faixas/zonas repetidas = suspeita DIFUSOR/LED (backlight).
   - Pontos/linhas finas fixas = PIXELS MORTOS (LCD).
2) Teste de GRADE/pattern: quadrados apagados/linhas?
3) Limpeza LCD (microfibra + IPA 99%, sem encharcar), checar vazamento e FEP.
4) Concluir objetivamente (difusor/LED x LCD x reflexo/ângulo).
5) Termine com “O QUE FAZER AGORA” (3–5 passos práticos).

PROTOCOLO-GERAL (defeitos de peça: rachar, buracos, empeno, suporte soltando):
- Identificar quando acontece (durante impressão, na remoção, na lavagem ou pós-cura).
- Checar adesão de base, exposição relativa, alturas de elevação, velocidades, suportes, lavagem/pós-cura, FEP e temperatura ambiente.
- Não culpar resina antes de validar mecânica/óptica/parametrização.
"""

# Palavras que indicam que o usuário quer PERFIL/EXPOSIÇÃO (aí podemos pedir impressora/resina)
KEYWORDS_NEED_PARAMS = {
    "perfil", "profile", "configura", "parametri", "exposi", "tempo de tela",
    "exposure", "slicer", "fatiamento", "curva de exposição", "parametros",
}

def build_policy_message(problem_text: str, resin: str, printer: str) -> str:
    txt = f"{problem_text} {resin} {printer}".lower()
    need_params = any(k in txt for k in KEYWORDS_NEED_PARAMS)
    if need_params:
        return (
            "POLÍTICA: O usuário fala de perfil/exposição. "
            "Se faltar impressora e/ou resina para indicar NÚMEROS, faça APENAS 1 pergunta pedindo os dados; "
            "senão, siga direto com diagnóstico e passos."
        )
    else:
        return (
            "POLÍTICA: NÃO peça impressora nem resina. Trate esses campos como opcionais/cadastro. "
            "Faça no máximo 1 pergunta objetiva sobre o defeito (ex.: quando ocorre). "
            "Em seguida, dê diagnóstico provável e 'O QUE FAZER AGORA' (3–5 passos)."
        )

# -------------------- Utilidades --------------------
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in {"jpg", "jpeg", "png", "webp"}

def _file_to_dataurl_and_size(fs) -> Tuple[str, int]:
    """Lê um FileStorage do Flask e devolve (data_url, tamanho_bytes). Valida tipo."""
    data = fs.read()
    if not data:
        return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

def save_uploads(files) -> List[str]:
    """Se quiser salvar no disco e gerar URLs públicas (não é obrigatório)."""
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

# -------------------- Rotas básicas --------------------
@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION, model=MODEL)

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
    })

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# -------------------- Chat --------------------
@app.post("/chat")
def chat():
    try:
        # Campos do formulário
        phone   = (request.form.get("phone")   or "").strip()
        scope   = (request.form.get("scope")   or "desconhecido").strip()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400

        # Aceita input 'images' ou 'photos'
        files = []
        if "images" in request.files:
            files = request.files.getlist("images")
        elif "photos" in request.files:
            files = request.files.getlist("photos")

        # Converte imagens para data URL (sem salvar) e checa 3MB por imagem
        images_dataurls, total_bytes = [], 0
        for i, fs in enumerate(files[:5]):
            size_hint = fs.content_length or 0
            if size_hint > 3 * 1024 * 1024:
                return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
            dataurl, real_size = _file_to_dataurl_and_size(fs)
            if dataurl:
                total_bytes += real_size
                if real_size > 3 * 1024 * 1024:
                    return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
                images_dataurls.append(dataurl)

        app.logger.info(f"/chat imagens={len(images_dataurls)} bytes_totais={total_bytes}")

        # Texto do usuário que vai ao modelo
        user_text = (
            f"Escopo informado: {scope}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem}\n"
            f"Telefone: {phone or '-'}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )

        # Mensagem multimodal (texto + imagens)
        content = [{"type": "text", "text": user_text}]
        for url in images_dataurls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        # Política dinâmica (quando pedir parâmetros, aí sim pode perguntar impressora/resina)
        policy_msg = build_policy_message(problem, resin, printer)

        messages = [
            {"role": "system", "content": ASSISTANT_SYSTEM},
            {"role": "system", "content": policy_msg},
            {"role": "user",   "content": content},
        ]

        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            messages=messages,
        )

        reply = (resp.choices[0].message.content or "").strip()
        return jsonify({
            "ok": True,
            "answer": reply,     # para o seu front atual
            "reply": reply,      # compat extra (se o front usar 'reply')
            "version": APP_VERSION,
            "model": MODEL
        })

    except Exception as e:
        app.logger.exception("erro no /chat")
        # erro amigável pro usuário + detalhe técnico pros logs
        return jsonify({"ok": False, "error": f"Falhou ao gerar resposta: {str(e)}"}), 500

# Estáticos (se precisar servir algo dentro de /static)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Rodar local: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
