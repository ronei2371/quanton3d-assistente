# v2025-08-29k — conversa contínua por telefone (arquivo em /sessions),
# botão "Fechar assunto" via /reset, KB externo (kb.json), visão p/ imagens,
# compat Python 3.13 e OpenAI 1.x (sem proxies), respostas em JSON.

import os, re, json, base64, uuid
from flask import Flask, request, render_template, jsonify, send_from_directory
from openai import OpenAI

# ---- Compat: Python 3.13 sem imghdr
try:
    import imghdr  # até 3.12
except ModuleNotFoundError:
    class imghdr:
        @staticmethod
        def what(file=None, h=None):
            if h is None and hasattr(file, "read"):
                pos = file.tell()
                h = file.read(16)
                file.seek(pos)
            if not isinstance(h, (bytes, bytearray)):
                return None
            head = h[:16]
            if head.startswith(b"\xFF\xD8\xFF"):  # JPEG
                return "jpeg"
            if head.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
                return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":  # WEBP
                return "webp"
            return None

APP_VERSION = "2025-08-29k"

# Evita proxies herdados (SDK novo não aceita proxies no ctor)
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# ---- ENV
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL          = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP           = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))

client = OpenAI(api_key=OPENAI_API_KEY)

# ---- Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB total

# ---- Pastas
BASE_DIR   = os.getcwd()
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SESS_DIR   = os.path.join(BASE_DIR, "sessions")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SESS_DIR,  exist_ok=True)

# ------------ KB (guia de defeitos) --------------
DEFAULT_KB = [
    {"id":"adesao_base","gatilhos":["não gruda","nao gruda","descolando","soltando da base","adesão","adesao","não adere","nao adere"],
     "texto":"Provável adesão fraca. Nivelar base (método papel), limpar base com IPA 99%, aumentar camadas e tempo de base (+20–30s), manter 22–26 °C. Se gruda demais, reduzir base −10–15s e usar 5–6 camadas."},
    {"id":"mole_grudenta","gatilhos":["mole","grudenta","peça mole","peca mole","pegajosa","não cura direito","nao cura direito"],
     "texto":"Subexposição. Aumente exposição por camada (+0.3–0.5s), execute um teste XP2, verifique uniformidade do LCD e pós-cura (lavar com IPA e curar 2–5 min)."},
    {"id":"quebradica_rachando","gatilhos":["rachando","quebradiça","quebradica","quebrando","frágil","fragil","trincando","trincas"],
     "texto":"Pode ser superexposição ou frio. Reduza exposição −0.2–0.3s, mantenha 20–24 °C, evite pós-cura excessiva. Em frio (<20 °C), aqueça ambiente ou aumente exposição ~15%."},
    {"id":"suportes_quebram","gatilhos":["suporte quebra","suportes quebram","cai dos suportes","soltou dos suportes","arranca dos suportes"],
     "texto":"Suporte subdimensionado/posicionado. Use suporte médio 0.4–0.6 mm, densidade 60–80%, adicione manuais em áreas críticas. Lift 1–2 mm/s e um pequeno 'rest' antes do lift."},
    {"id":"linhas_camadas","gatilhos":["linhas","camadas visíveis","banding","faixas","serrilhado"],
     "texto":"Altura de camada/anti-aliasing/mecânica do Z. Use 0,05 mm, AA nível 2–3, lubrifique fuso Z e elimine folgas."},
    {"id":"para_no_meio","gatilhos":["para no meio","parou no meio","interrompe","trava impressão","trava impressao","falha no meio"],
     "texto":"Arquivo/hardware. Refatie, teste SD novo/formatado, atualize slicer/firmware e verifique fonte/driver do eixo Z."},
    {"id":"superficie_rugosa","gatilhos":["rugosa","granulada","granulado","superfície ruim","superficie ruim","aspera"],
     "texto":"Resina contaminada/temperatura/vibração. Filtre a resina, mantenha 22–25 °C e elimine vibrações."},
    {"id":"lcd_pixels","gatilhos":["pixel morto","pixels mortos","falha lcd","manchas lcd","faixas lcd","lcd","tela manchada","tela com faixa"],
     "texto":"Faça teste do papel branco e de grade. Padrões fixos → LCD; faixas que se repetem → difusor/LED. Limpeza suave; trocar LCD se muitos pixels."},
    {"id":"fep_danificado","gatilhos":["fep","nfep","filme perfurado","filme riscado","opaco fep","fep marcado"],
     "texto":"Cheque transparência/risco/furo e tensão (som de tambor). Troque e limpe tanque. Troca preventiva a cada 20–30 impressões."}
]

def _load_kb():
    try:
        p = os.path.join(BASE_DIR, "kb.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            ok = isinstance(data, list) and all(isinstance(x, dict) and "gatilhos" in x and "texto" in x for x in data)
            if ok:
                return data
    except Exception as err:
        try:
            app.logger.warning(f"kb.json inválido: {err}")
        except Exception:
            pass
    return DEFAULT_KB

KB_SNIPPETS = _load_kb()

# ------------ Prompts --------------
ASSISTANT_SYSTEM = (
    "Você é o Assistente Técnico Quanton3D para impressoras SLA/DLP.\n"
    "Responda em português do Brasil, direto, educado e prático.\n"
    "MODO CERTEIRO: Se faltar informação realmente essencial, faça APENAS 1 pergunta objetiva e PARE.\n"
    "Evite pedir resina ou modelo da impressora a menos que o usuário peça perfil/parametrização.\n"
    "Quando houver imagem, descreva o que observa e relacione com o defeito.\n"
    "Se o caso for LCD, use: papel branco, teste de grade, limpeza, difusor/backlight, vazamento e FEP.\n"
    "SEM checklist genérico fora de contexto. Sempre finalize com 'O QUE FAZER AGORA' (3–5 passos).\n"
)

JSON_FORMAT_RULE = (
    "Responda SOMENTE em JSON válido com os campos:\n"
    "{"
    "\"classificacao\":\"A) Geral / B) LCD / C) Resina / D) Mecânico / E) Arquivo\","
    "\"resposta\":\"texto completo para o cliente com passos finais em 'O QUE FAZER AGORA'\""
    "}"
)

# ------------ Sessões por telefone (persistência em arquivo) --------------
def _phone_key(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", raw or "")[:32] or "anon"

def _sess_path(phone_key: str) -> str:
    return os.path.join(SESS_DIR, f"{phone_key}.json")

def _load_thread(phone_key: str):
    p = _sess_path(phone_key)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            return []
    return []

def _save_thread(phone_key: str, thread):
    # guarda só os últimos 10 turnos para economizar tokens
    slim = thread[-10:]
    p = _sess_path(phone_key)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False)
        os.replace(tmp, p)  # gravação atômica
    except Exception:
        pass

def _reset_thread(phone_key: str):
    p = _sess_path(phone_key)
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass

# ------------ Utilidades --------------
def _file_to_dataurl_and_size(fs):
    data = fs.read()
    if not data:
        return None, 0
    if len(data) > 3 * 1024 * 1024:
        raise ValueError("Imagem excede 3MB.")
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

def _detect_lcd_hint(problem_text: str, has_images: bool) -> bool:
    t = (problem_text or "").lower()
    lcd_words = ("lcd", "tela", "mancha na tela", "faixa na tela", "pixel")
    return has_images or any(w in t for w in lcd_words)

def _need_params_question(problem_text: str) -> bool:
    t = (problem_text or "").lower()
    triggers = (
        "perfil", "parametro", "parâmetro", "parametrização", "parametrizacao",
        "exposição", "exposicao", "tempo de exposição", "tempo de exposicao",
        "configuração", "configuracao", "preset", "profile", "parametros", "parâmetros"
    )
    return any(w in t for w in triggers)

def _kb_context(problem_text: str) -> str:
    t = (problem_text or "").lower()
    hits = []
    for item in KB_SNIPPETS:
        if any(g in t for g in item.get("gatilhos", [])):
            hits.append(f"- {item['texto']}")
    return "\n".join(hits[:5])

# ------------ Rotas básicas --------------
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

@app.get("/uploads/<path:fname>")
def get_upload(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# Fechar assunto (reset de conversa)
@app.route("/reset", methods=["POST", "GET"])
def reset():
    phone = request.values.get("phone") or (request.json or {}).get("phone") if request.is_json else ""
    key = _phone_key(phone)
    _reset_thread(key)
    return jsonify({"ok": True, "message": "Assunto fechado para este telefone."})

# ------------ Chat --------------
@app.post("/chat")
def chat():
    try:
        phone     = (request.form.get("phone")     or "").strip()
        scope     = (request.form.get("scope")     or "desconhecido").strip()
        resin     = (request.form.get("resin")     or "").strip()
        printer   = (request.form.get("printer")   or "").strip()
        problem   = (request.form.get("problem")   or "").strip()
        keep_ctx  = (request.form.get("continue")  or "1").strip()  # default ligado ("1")
        keep_ctx  = keep_ctx in ("1", "true", "on", "yes")

        if not phone:
            return jsonify(ok=False, error="Informe o telefone."), 400
        if not problem:
            return jsonify(ok=False, error="Descreva o problema."), 400

        # imagens (aceita 'images' ou 'photos')
        file_key = "images" if "images" in request.files else ("photos" if "photos" in request.files else None)
        data_urls = []
        if file_key:
            for fs in request.files.getlist(file_key)[:5]:
                if not fs or not getattr(fs, "filename", ""):
                    continue
                dataurl, _ = _file_to_dataurl_and_size(fs)
                if dataurl:
                    data_urls.append(dataurl)

        # ---- Contexto por telefone
        key    = _phone_key(phone)
        thread = _load_thread(key) if keep_ctx else []

        # ---- KB e lógica de pergunta única
        lcd_hint = _detect_lcd_hint(problem, bool(data_urls))
        kb_text  = _kb_context(problem)
        wants_params = _need_params_question(problem)

        missing_question = None
        if wants_params and (not resin or not printer):
            missing_question = "Você quer um PERFIL de parâmetros. Qual é a RESINA e o MODELO da sua IMPRESSORA?"

        # ---- Mensagens p/ o modelo
        user_text = (
            f"Telefone: {phone}\n"
            f"Escopo informado: {scope}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem}\n"
            f"Categoria_sugerida: {'LCD' if lcd_hint else 'GERAL'}\n"
            f"Contexto: suporte técnico Quanton3D (SLA/DLP)."
        )
        current_content = [{"type": "text", "text": user_text}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in data_urls
        ]

        system_blocks = [ASSISTANT_SYSTEM]
        if kb_text:
            system_blocks.append("Pistas úteis (KB da Quanton3D):\n" + kb_text)
        system_blocks.append(JSON_FORMAT_RULE)

        messages = [{"role": "system", "content": blk} for blk in system_blocks]

        # Reaproveita histórico (somente texto para economizar)
        for msg in thread[-10:]:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Mensagem atual
        messages.append({"role": "user", "content": current_content})

        if missing_question:
            messages.append({"role": "system", "content": "Faça APENAS a pergunta abaixo e PARE; não dê solução ainda."})
            messages.append({"role": "system", "content": missing_question})

        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            messages=messages
        )

        raw = (resp.choices[0].message.content or "").strip()

        # Tenta JSON
        classification = None
        answer = None
        try:
            data = json.loads(raw)
            classification = data.get("classificacao")
            answer = data.get("resposta")
        except Exception:
            answer = raw

        # ---- Atualiza histórico se manter contexto
        if keep_ctx:
            # guardo só texto, sem imagens
            thread.append({"role": "user", "content": f"[TEXTO]\n{user_text}"})
            thread.append({"role": "assistant", "content": answer})
            _save_thread(key, thread)

        return jsonify({
            "ok": True,
            "classification": classification,
            "answer": answer,
            "images": len(data_urls),
            "version": APP_VERSION
        }), 200

    except Exception as e:
        try:
            app.logger.exception("erro no /chat")
        except Exception:
            pass
        return jsonify({
            "ok": False,
            "error": "Falha ao gerar a resposta. Tente novamente.",
            "debug": str(e)
        }), 500

# ---- Local
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
