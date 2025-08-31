import os, re, uuid, base64, json
from typing import List, Dict, Any
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

# ---- Compat: imghdr removido no Python 3.13
try:
    import imghdr
except ModuleNotFoundError:
    class imghdr:
        @staticmethod
        def what(file=None, h=None):
            if h is None and hasattr(file, "read"):
                pos = file.tell(); h = file.read(16); file.seek(pos)
            if not isinstance(h, (bytes, bytearray)): return None
            head = h[:16]
            if head.startswith(b"\xFF\xD8\xFF"): return "jpeg"
            if head.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "webp"
            return None

# ---------------------- Config
APP_VERSION = "2025-08-30a"
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# remove proxies herdados (evita erro no SDK novo)
for k in ("HTTP_PROXY","http_proxy","HTTPS_PROXY","https_proxy"):
    os.environ.pop(k, None)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")
MODEL = os.getenv("OPENAI_MODEL","gpt-4o-mini")
TEMP  = float(os.getenv("OPENAI_TEMPERATURE","0.2"))
SEED  = int(os.getenv("OPENAI_SEED","123"))

RAG_TOPK      = int(os.getenv("RAG_TOPK","4"))
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE","0.18"))
RAG_STRICT    = os.getenv("RAG_STRICT","1") not in ("0","false","False","no","NO")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- RAG
import rag
try:
    ok, n_chunks = rag.init(client, "kb/kb_index.json")
except Exception:
    ok, n_chunks = (False, 0)

# ---------- Histórico simples por telefone (memória do processo)
HISTORY: Dict[str, List[Dict[str,str]]] = {}

# ---------- Prompts fixos (regras)
ASSISTANT_SYSTEM = """
Você é suporte técnico da Quanton3D, especialista em impressão 3D SLA/DLP e resinas Quanton3D.
Regras de atendimento (siga SEMPRE):
- Responda em português do Brasil, prático e educado, tom de técnico de bancada.
- Se faltar dado essencial, faça APENAS 1 pergunta objetiva e PARE.
- Nunca invente valores ou parâmetros. Se não souber com certeza, diga que vai encaminhar para validação.
- Quando houver imagens, descreva o que observa antes de concluir.
- Quando houver CONTEXTO da base (RAG) no formato [KB1], [KB2]…:
  • Use primeiro esses trechos para construir a resposta.
  • Mencione "Fontes: [KB1] [KB2]…" no final. NÃO crie fonte.
- LCD/backlight: priorize teste do papel branco, grade/pixels, limpeza microfibra+IPA 99%, checar vazamento nas bordas e opacidade do FEP.
Finalize SEMPRE com “O QUE FAZER AGORA” (3–5 passos).
"""

def _allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".",1)[-1] if "." in filename else "").lower()
    return ext in {"jpg","jpeg","png","webp"}

def _file_to_dataurl(fs):
    data = fs.read()
    if not data: return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg","png","webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}[kind]
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# -------- Rotas estáticas
@app.get("/uploads/<path:fname>")
def get_upload(fname): return send_from_directory(UPLOAD_DIR, fname)

@app.get("/")
def index(): return render_template("index.html", app_version=APP_VERSION)

@app.get("/healthz")
def healthz(): return "ok", 200

@app.get("/diag")
def diag():
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": MODEL,
        "temperature": TEMP,
        "seed": SEED,
        "rag_loaded": ok,
        "rag_chunks": n_chunks,
        "rag_topk": RAG_TOPK,
        "rag_min_score": RAG_MIN_SCORE,
        "rag_strict": RAG_STRICT
    }), 200

@app.get("/reset")
def reset():
    phone = (request.args.get("phone") or "").strip()
    if phone and phone in HISTORY:
        HISTORY.pop(phone, None)
        return "reset", 200
    return "ok", 200

# ------------- CHAT
@app.post("/chat")
def chat():
    try:
        phone   = (request.form.get("phone") or "").strip()
        scope   = (request.form.get("scope") or "desconhecido").strip()
        resin   = (request.form.get("resin") or "").strip()
        printer = (request.form.get("printer") or "").strip()
        problem = (request.form.get("problem") or "").strip()

        if not phone:   return jsonify({"ok":False,"error":"Informe o telefone."}), 400
        if not problem: return jsonify({"ok":False,"error":"Descreva o problema."}), 400

        # uploads (até 5 imagens, 3MB cada)
        image_urls = []
        if "photos" in request.files:
            for fs in request.files.getlist("photos")[:5]:
                if not fs or fs.filename == "": continue
                if not _allowed_file(fs.filename): continue
                size_hint = fs.content_length or 0
                if size_hint > 3*1024*1024:
                    return jsonify({"ok":False,"error":"Imagem excede 3MB."}), 400
                dataurl, real_size = _file_to_dataurl(fs)
                if real_size > 3*1024*1024:
                    return jsonify({"ok":False,"error":"Imagem excede 3MB."}), 400
                # salva no /uploads para eventualmente referenciar
                fname = f"{uuid.uuid4().hex}_{re.sub(r'[^a-zA-Z0-9_.-]','_', fs.filename)}"
                with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
                    f.write(base64.b64decode(dataurl.split(",")[1]))
                image_urls.append(url_for("get_upload", fname=fname, _external=True))

        # ===== RAG: consulta base
        query_text = (
            f"Escopo: {scope}\nImpressora: {printer or '-'}\nResina: {resin or '-'}\n"
            f"Problema: {problem}\n"
        )
        rag_hits = []
        try:
            rag_hits = rag.search(query_text, top_k=RAG_TOPK, min_score=RAG_MIN_SCORE)
        except Exception:
            rag_hits = []

        ctx_lines, cite_lines = [], []
        for i, h in enumerate(rag_hits, 1):
            tag = f"[KB{i}]"
            ctx_lines.append(f"{tag} {h['text']}".strip())
            cite_lines.append(f"{tag} {h['source']}")

        kb_context = "\n\n".join(ctx_lines)
        kb_citations = " ".join(c.split()[0] for c in cite_lines) if cite_lines else ""
        kb_footer = ""
        if cite_lines:
            kb_footer = "\n\nFontes: " + "  ".join(cite_lines)

        # ===== validação: se RAG estrito e sem base relevante, peça confirmação
        if RAG_STRICT and not rag_hits:
            text = (
                "Preciso validar antes: não encontrei referência na nossa base para responder com segurança.\n"
                "Pode enviar mais detalhes (modelo da impressora, resina, alturas/tempos) ou uma foto? "
                "Se preferir, encaminho para um técnico humano agora."
            )
            # ainda assim continuamos, mas marcamos contexto vazio:
            kb_context = ""

        # monta conteúdo para visão (texto + imagens)
        content = [{
            "type": "text",
            "text": (
                f"Telefone: {phone}\nEscopo: {scope}\nImpressora: {printer or '-'}\nResina: {resin or '-'}\n"
                f"Problema: {problem}\n"
                "Contexto de atendimento Quanton3D."
            )
        }]
        for u in image_urls:
            content.append({"type":"image_url","image_url":{"url":u}})

        messages = [
            {"role":"system","content": ASSISTANT_SYSTEM},
            {"role":"system","content":
                ("Contexto da base (RAG). Use se relevante e CITE como [KB1], [KB2]…; "
                 "se vazio, responda com boas práticas e peça confirmação:\n" +
                 (kb_context or "— sem contexto —"))
            },
            {"role":"user","content": content},
        ]

        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMP,
            seed=SEED,
            top_p=0.8,
            max_tokens=700,
            messages=messages
        )
        answer = (resp.choices[0].message.content or "").strip()
        # acrescenta as fontes quando houver
        if kb_footer and "[KB" in answer and "Fontes:" not in answer:
            answer += kb_footer

        # guarda histórico leve
        HISTORY.setdefault(phone, []).append({"u": problem, "a": answer})

        return jsonify({"ok": True, "reply": answer, "version": APP_VERSION, "citations": cite_lines})

    except Exception as e:
        app.logger.exception("erro no /chat")
        return jsonify({"ok":False,"error":str(e)}), 500

# ---- estáticos
@app.get("/static/<path:filename>")
def static_files(filename): return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
