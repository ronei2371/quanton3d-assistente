# v2025-08-29h — Parâmetros (CSV), consultas /params, Modo Certeiro, visão por imagens, compat OpenAI 1.x
import os, re, csv, io, uuid, base64, json
from flask import Flask, request, jsonify, render_template, send_from_directory, url_for
from openai import OpenAI

# --- Compat: Python 3.13 removeu imghdr; implemento mínimo (jpeg/png/webp)
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

APP_VERSION = "2025-08-29h"

# -----------------------------------------------------------------------------
# Configuração básica
# -----------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB upload total

# Evita interferência de proxy com o SDK novo
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMP      = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_SEED      = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# Onde guardamos uploads (Render: disco efêmero durante a execução)
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# Base de parâmetros (CSV)
# -----------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
PARAMS_CSV = os.path.join(DATA_DIR, "params.csv")

# Cache em memória
PARAMS = []          # lista de dicts (linhas do CSV)
BRANDS = set()
MODELS = set()
RESINS = set()

CSV_COLUMNS = [
    "brand",             # Marca da impressora (ex.: ANYCUBIC, ELEGOO, CREALITY...)
    "model",             # Modelo (ex.: PHOTON MONO M3 MAX)
    "resin",             # Nome da resina (ex.: QUANTON3D ATHOM WASHABLE)
    "layer_height_mm",   # Altura de camada (mm) — ex.: 0.05
    "exp_layer_s",       # Tempo de exposição por camada (s)
    "exp_base_s",        # Tempo de exposição da base (s)
    "off_delay_s",       # Retardo desligar UV (s)
    "on_delay_base_s",   # Retardo ligar UV base (s)
    "rest_before_lift_s",
    "rest_after_lift_s",
    "rest_after_retract_s",
    "uv_power_pct"       # Potência UV (%), se aplicável
]

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def load_params_csv():
    """Carrega data/params.csv para memória. Ignora linhas vazias."""
    global PARAMS, BRANDS, MODELS, RESINS
    PARAMS, BRANDS, MODELS, RESINS = [], set(), set(), set()
    if not os.path.exists(PARAMS_CSV):
        # arquivo ainda não criado; segue vazio (rotas retornarão listagens vazias)
        return
    with io.open(PARAMS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item = {k: (row.get(k, "") or "").strip() for k in CSV_COLUMNS}
            PARAMS.append(item)
            BRANDS.add(item["brand"])
            MODELS.add(item["model"])
            RESINS.add(item["resin"])

def find_params(brand=None, model=None, resin=None, max_results=10):
    """Busca por marca/modelo/resina por contains (case-insensitive)."""
    bq, mq, rq = _norm(brand), _norm(model), _norm(resin)
    hits = []
    for item in PARAMS:
        score = 0
        if bq and bq in _norm(item["brand"]):  score += 1
        if mq and mq in _norm(item["model"]):  score += 1
        if rq and rq in _norm(item["resin"]):  score += 1
        # Se não veio nenhum filtro, não retorna tudo pra não poluir
        if (bq or mq or rq) and score > 0:
            hits.append((score, item))
    hits.sort(key=lambda x: -x[0])
    return [h[1] for h in hits[:max_results]]

# Carrega na subida
load_params_csv()

# -----------------------------------------------------------------------------
# Sistema do assistente
# -----------------------------------------------------------------------------
ASSISTANT_SYSTEM = """
Você é técnico de campo da Quanton3D. Estilo: direto de oficina, frases curtas e acionáveis.

MODO CERTEIRO:
- Se FALTAR dado essencial para orientar, faça APENAS 1 pergunta objetiva e PARE (aguarde).
- Só pergunte marca/modelo/resina quando (a) o usuário pedir PARÂMETROS/PERFIL/EXPOSIÇÃO, ou (b) for impossível orientar sem isso.
- Para defeitos comuns (rachadura, delaminação, buracos, warping, adesão, etc.), NÃO exija marca/resina por padrão; siga o protocolo.

PROTOCOLO-LCD (tela acesa ou escopo 'lcd'):
1) Teste do papel branco (cuba fora) para UNIFORMIDADE (faixas repetidas = difusor/LED; pontos fixos = pixels mortos do LCD).
2) Teste de grade/pixel (quadrados/linhas apagadas).
3) Limpeza suave (microfibra + IPA 99%, sem encharcar/pressão).
4) Checar FEP (opacidade/riscos) e vazamento de resina.
5) Concluir: difusor/LED x LCD x reflexo/ângulo. Dar próximos passos objetivos.

PROTOCOLO-GERAL:
- Ordem: adesão -> nivelamento -> exposição -> suportes -> FEP -> velocidades -> temperatura ambiente -> resina.
- Sempre termine com 'O QUE FAZER AGORA' (3 a 5 passos práticos).
"""

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

def save_uploads(files):
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

# -----------------------------------------------------------------------------
# Rotas utilitárias
# -----------------------------------------------------------------------------
@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": OPENAI_MODEL,
        "temperature": OPENAI_TEMP,
        "seed": OPENAI_SEED,
        "params_loaded": len(PARAMS)
    }), 200

@app.get("/reset")
def reset():
    # Reseta contexto por telefone (se você guardar histórico por telefone em cache/DB)
    # Mantemos no-op por enquanto para compat.
    return jsonify({"ok": True}), 200

# -----------------------------------------------------------------------------
# Rotas de PARÂMETROS
# -----------------------------------------------------------------------------
@app.get("/params")
def params_lookup():
    brand = request.args.get("brand", "").strip()
    model = request.args.get("model", "").strip()
    resin = request.args.get("resin", "").strip()
    results = find_params(brand, model, resin, max_results=20)
    return jsonify({"ok": True, "count": len(results), "items": results})

@app.get("/params/list")
def params_list():
    return jsonify({
        "ok": True,
        "brands": sorted(BRANDS),
        "models": sorted(MODELS),
        "resins": sorted(RESINS),
        "columns": CSV_COLUMNS
    })

# -----------------------------------------------------------------------------
# Rota principal de chat
# -----------------------------------------------------------------------------
@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone")   or "").strip()
        scope   = (request.form.get("scope")   or "").strip().lower()
        resin   = (request.form.get("resin")   or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:
            return jsonify({"ok": False, "error": "Informe o telefone."}), 400
        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400

        # Uploads (campo "photos")
        image_urls = []
        if "photos" in request.files:
            image_urls = save_uploads(request.files.getlist("photos"))

        # Monta conteúdo para visão
        user_text = (
            f"Telefone: {phone}\n"
            f"Escopo: {scope or '-'}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem}\n"
        )
        content = [{"type": "text", "text": user_text}]
        for u in image_urls:
            content.append({"type": "image_url", "image_url": {"url": u}})

        # Detecta intenção de PARÂMETROS
        wants_params = False
        txt_low = (problem + " " + scope).lower()
        if any(k in txt_low for k in ["parametro", "parâmetro", "exposi", "perfil", "configura", "setting", "exposure"]):
            wants_params = True

        # Se pediu parâmetros e temos marca/resina/modelo no texto do usuário, tenta buscar
        params_block = ""
        if wants_params and (printer or resin):
            # Tenta quebrar printer em brand/model (ex.: "ANYCUBIC PHOTON MONO M3 MAX")
            brand_guess, model_guess = "", ""
            if printer:
                parts = printer.strip().split()
                if len(parts) >= 2:
                    brand_guess = parts[0]
                    model_guess = " ".join(parts[1:])
                else:
                    brand_guess = printer

            results = find_params(brand_guess or printer, model_guess or "", resin)
            if results:
                best = results[0]
                params_block = (
                    "Parâmetros sugeridos (da base Quanton3D):\n"
                    f"- Marca: {best['brand']} | Modelo: {best['model']} | Resina: {best['resin']}\n"
                    f"- Altura cam.: {best['layer_height_mm']} mm | Exp. camada: {best['exp_layer_s']} s | Base: {best['exp_base_s']} s\n"
                    f"- Delays: off {best['off_delay_s']}s | on_base {best['on_delay_base_s']}s | "
                    f"desc_lift {best['rest_before_lift_s']}s / {best['rest_after_lift_s']}s / {best['rest_after_retract_s']}s\n"
                    f"- UV: {best['uv_power_pct']}%\n"
                    "Use como ponto de partida; ajuste fino pela peça/ambiente."
                )
                # Injeta contexto com parâmetros
                content.insert(0, {"type": "text", "text": params_block})
            else:
                params_block = "Não encontrei parâmetros na base para essa combinação. Se quiser, me passe Marca/Modelo/Resina mais exatos."

        messages = [
            {"role": "system", "content": ASSISTANT_SYSTEM},
            {"role": "user",   "content": content}
        ]

        # Chamada ao modelo
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMP,
            seed=OPENAI_SEED,
            messages=messages
        )
        reply = (resp.choices[0].message.content or "").strip()

        return jsonify({
            "ok": True,
            "reply": reply,
            "version": APP_VERSION,
            "used_params": bool(params_block),
            "images": len(image_urls)
        })
    except Exception as e:
        app.logger.exception("erro no /chat")
        # erro amigável para interface
        return jsonify({"ok": False, "error": str(e)}), 500

# -----------------------------------------------------------------------------
# Página principal (aproveita seu template atual)
# -----------------------------------------------------------------------------
@app.get("/")
def index():
    # Se o seu template já existe, essa linha mantém.
    return render_template("index.html", app_version=APP_VERSION)

# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
