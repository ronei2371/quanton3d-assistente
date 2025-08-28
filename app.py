import os, sqlite3, base64, time, re
from flask import Flask, request, jsonify, render_template, Response
from openai import OpenAI
import httpx

# ========= OpenAI Client =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida.")
client = OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client())

# ========= App/DB =========
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "chat.db"))
app = Flask(__name__, template_folder="templates", static_folder="static")

def norm_phone(s: str) -> str:
    return re.sub(r"\D+", "", (s or ""))[:15]

def migrate_phone_if_needed(raw_phone: str, phone_norm: str):
    if not raw_phone or raw_phone == phone_norm:
        return
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE history SET phone=? WHERE phone=? AND phone!=?",
                  (phone_norm, raw_phone, phone_norm))
        c.execute("UPDATE profile SET phone=? WHERE phone=? AND phone!=?",
                  (phone_norm, raw_phone, phone_norm))
        conn.commit()

def init_db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS history(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              phone TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              timestamp REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_history_phone ON history(phone)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS profile(
              phone TEXT PRIMARY KEY,
              story TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
        """)
        conn.commit()
init_db()

def save_message(phone_norm, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO history(phone,role,content,timestamp) VALUES(?,?,?,?)",
            (phone_norm, role, content, time.time())
        )
        conn.commit()

def load_history(phone_norm, order="ASC"):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if order.upper() == "DESC":
            c.execute("SELECT role,content,timestamp FROM history WHERE phone=? ORDER BY timestamp DESC",
                      (phone_norm,))
        else:
            c.execute("SELECT role,content,timestamp FROM history WHERE phone=? ORDER BY timestamp ASC",
                      (phone_norm,))
        rows = c.fetchall()
    return [{"role": r, "content": ct, "ts": ts} for (r, ct, ts) in rows if r != "system"]

def clamp_story_for_qa(story: str, limit_chars: int = 9000) -> str:
    """Evita 429/TPM cortando textos enormes: mantém começo e fim."""
    s = story or ""
    if len(s) <= limit_chars:
        return s
    head = s[:int(limit_chars * 0.65)]
    tail = s[-int(limit_chars * 0.25):]
    return head + "\n\n[trecho intermediário omitido]\n\n" + tail

def sanitize_response(text: str) -> str:
    """Remove markdown (###, **bold**, _itálico_) e normaliza bullets/numeração."""
    if not text:
        return ""
    t = (text or "").replace("\r\n", "\n")

    # 1) remover cabeçalhos markdown #####
    t = re.sub(r'(^|\n)#{1,6}\s*', r'\1', t)

    # 2) remover negrito/itálico (**...**, __...__, *...*)
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'__(.+?)__', r'\1', t)
    # itálico com *texto* — evitar capturar bullets "* "
    t = re.sub(r'(?<!\S)\*(?!\s)(.+?)(?<!\s)\*(?!\S)', r'\1', t)

    # 3) normalizar numeração "1. " -> "1) "
    def _num(match):
        lead = match.group(1)
        num = match.group(2)
        rest = match.group(3) or ""
        return f"{lead}{num}) {rest}"
    t = re.sub(r'(^|\n)\s*(\d+)\.\s*(.*)', _num, t)

    # 4) normalizar bullets para "- "
    t = re.sub(r'(^|\n)\s*[-*]\s+', r'\1- ', t)

    # 5) remover linhas de regra --- e excesso de linhas vazias
    t = re.sub(r'\n-{3,}\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)

    return t.strip()

# ========= Rotas =========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/history")
def history():
    raw = (request.args.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify([])
    migrate_phone_if_needed(raw, phone)
    return jsonify(load_history(phone, order="DESC"))

@app.route("/history_export")
def history_export():
    raw = (request.args.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return Response("Telefone obrigatório", 400, mimetype="text/plain; charset=utf-8")
    migrate_phone_if_needed(raw, phone)
    msgs = load_history(phone, order="ASC")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"Histórico — Tel: {phone}", f"Exportado em: {now}", "-" * 60]
    for m in msgs:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["ts"]))
        role = "USUÁRIO" if m["role"] == "user" else "ASSISTENTE"
        lines.append(f"[{ts}] {role}:\n{(m['content'] or '').replace('\r\n', '\n')}\n")
    text = "\n".join(lines)
    fname = f"historico_{phone}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    return Response(text,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'},
                    mimetype="text/plain; charset=utf-8")

@app.route("/profile", methods=["GET"])
def get_profile():
    raw = (request.args.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify({"story": ""})
    migrate_phone_if_needed(raw, phone)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT story FROM profile WHERE phone=?", (phone,)).fetchone()
    return jsonify({"story": (row[0] if row else "")})

@app.route("/profile", methods=["POST"])
def put_profile():
    raw = (request.form.get("phone") or "").strip()
    story = (request.form.get("story") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify({"error": "Número de telefone é obrigatório"}), 400
    if not story:
        return jsonify({"error": "Cole/escreva a história antes de salvar"}), 400
    migrate_phone_if_needed(raw, phone)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO profile(phone,story,updated_at)
            VALUES(?,?,?)
            ON CONFLICT(phone) DO UPDATE SET
                story=excluded.story,
                updated_at=excluded.updated_at
        """, (phone, story, time.time()))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/summary", methods=["POST"])
def summary():
    raw = (request.form.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify({"error": "Número de telefone é obrigatório"}), 400
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT story FROM profile WHERE phone=?", (phone,)).fetchone()
    if not row or not (row[0] or "").strip():
        return jsonify({"error": "Nenhuma história salva para este telefone."}), 404
    story = clamp_story_for_qa(row[0], 7000)
    out = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Resuma com carinho (3-5 linhas), PT-BR, tom amoroso; não invente."},
            {"role": "user", "content": f"Resuma brevemente:\n\n{story}"}
        ],
        temperature=0.3, max_tokens=160
    )
    return jsonify({"summary": sanitize_response(out.choices[0].message.content)})

@app.route("/story_export")
def story_export():
    raw = (request.args.get("phone") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return Response("Telefone obrigatório", 400, mimetype="text/plain; charset=utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT story,updated_at FROM profile WHERE phone=?", (phone,)).fetchone()
    if not row or not (row[0] or "").strip():
        return Response("Nenhuma história salva.", 404, mimetype="text/plain; charset=utf-8")
    story, upd = row[0], row[1]
    upd_h = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(upd)) if upd else "N/D"
    text = "\n".join([f"Nossa História — Tel: {phone}", f"Última atualização: {upd_h}", "-" * 60, story])
    fname = f"minha_historia_{phone}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    return Response(text,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'},
                    mimetype="text/plain; charset=utf-8")

@app.route("/story_ask", methods=["POST"])
def story_ask():
    raw = (request.form.get("phone") or "").strip()
    question = (request.form.get("question") or "").strip()
    phone = norm_phone(raw)
    if not phone:
        return jsonify({"error": "Número de telefone é obrigatório"}), 400
    if not question:
        return jsonify({"error": "Escreva sua pergunta."}), 400
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT story FROM profile WHERE phone=?", (phone,)).fetchone()
    if not row or not (row[0] or "").strip():
        return jsonify({"error": "Nenhuma história salva. Cole/Salve sua história na página principal."}), 404

    story_snippet = clamp_story_for_qa(row[0], 9000)
    out = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content":
             "Responda SOMENTE com base na história fornecida. "
             "Se a resposta não estiver no texto, responda exatamente: "
             "'Não encontrei isso na história. Se quiser, acrescente mais detalhes e pergunte novamente.' — Nhor "
             "Tom afetuoso, claro, PT-BR. Não use markdown, nem **negrito**."},
            {"role": "user", "content": f"História (trecho):\n{story_snippet}\n\nPergunta: {question}"}
        ],
        temperature=0.2, max_tokens=280
    )
    answer = sanitize_response(out.choices[0].message.content)
    return jsonify({"answer": answer})

@app.route("/chat", methods=["POST"])
def chat():
    f = request.form
    raw_phone = (f.get("phone") or "").strip()
    phone = norm_phone(raw_phone)
    problem = (f.get("problem") or "").strip()
    resin = (f.get("resin") or "").strip()
    printer = (f.get("printer") or "").strip()
    scope = (f.get("scope") or "").strip().lower()  # peca | lcd | fep | cuba | desconhecido

    if not phone:
        return jsonify({"error": "Número de telefone é obrigatório"}), 400
    if not problem and "images" not in request.files:
        return jsonify({"error": "Descreva o problema ou envie imagens"}), 400
    migrate_phone_if_needed(raw_phone, phone)

    # imagens
    image_parts = []
    if "images" in request.files:
        files = request.files.getlist("images")
        for img in files[:5]:
            data = img.read()
            if not data or len(data) > 3 * 1024 * 1024:
                continue
            mime = (img.mimetype or "image/jpeg").lower()
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                mime = "image/jpeg"
            url = f"data:{mime};base64," + base64.b64encode(data).decode("utf-8")
            image_parts.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})

    hist = load_history(phone, order="ASC")

    # ===== Prompt refinado com escopo =====
    sys_prompt = (
        "Você é o assistente QUANTON3D®, especialista em impressão 3D de resina.\n"
        "Objetivo: diagnosticar e orientar com precisão, SEM sugerir trocas de marca e sem atribuir defeito a produtos. "
        "Use linguagem neutra (validade/armazenamento/contaminação/temperatura) e nunca culpe marcas.\n\n"
        "Regras de saída: NÃO use markdown, NEM '###', NEM **negrito**. Liste em texto plano com '1)' e '-'.\n\n"
        "• Se houver 'Escopo informado: <valor>' em peca/lcd/fep/cuba, USE esse escopo e NÃO faça pergunta de esclarecimento.\n"
        "  – 'peca' = defeitos/manchas NA PEÇA.\n"
        "  – 'lcd'  = artefatos/manchas NA TELA LCD (pixel queimado, luz vazando, polarizador/backlight).\n"
        "  – 'fep'  = problemas no FILME FEP (risco, opaco, folga, tensão/instalação, vazamento).\n"
        "  – 'cuba' = contaminação/resíduos/deformações na CUBA/RESERVATÓRIO.\n"
        "• Se o escopo for 'desconhecido' ou não vier, em UMA linha classifique: A) PEÇA, B) LCD, C) FEP, D) CUBA, E) outro. "
        "  Se ambíguo, faça apenas 1 pergunta breve antes de listar passos.\n\n"
        "Ao listar passos, foque no RELEVANTE ao escopo:\n"
        "• LCD: limpeza suave do LCD (microfibra, sem encharcar), teste de pixels/tela branca, polarizador/backlight, "
        "  vazamento de resina (FEP/cuba), riscos/contaminação no FEP, uniformidade de luz. NÃO sugerir nivelamento/suportes/firmware.\n"
        "• PEÇA: lavagem/pós-cura (banhos limpos e tempo adequado), exposição (sub/super), mistura/idade/armazenamento da resina, "
        "  temperatura/viscosidade, orientação e suportes (sombras/acúmulo), contaminação no FEP/cuba. "
        "  Nivelamento só se houver sintoma de adesão/deslocamento.\n"
        "• FEP: risco/opacificação/folga, tensão correta ao instalar, aperto do anel, vazamento, limpeza/contaminação.\n"
        "• CUBA: resíduos/partículas, rachaduras/deformações, limpeza e inspeção, alinhamento e vedação da cuba.\n\n"
        "Estilo: técnico, gentil e direto. Máx. 8 passos, personalizados ao relato/imagens.\n"
        "Encerramento: 'Conte com o time QUANTON3D; seguimos com você até dar certo.'"
    )

    messages = [{"role": "system", "content": sys_prompt}]
    for m in hist:
        messages.append({"role": m["role"], "content": m["content"]})

    lines = []
    if scope:
        lines.append(f"Escopo informado: {scope}")
    if problem:
        lines.append(f"Problema: {problem}")
    if resin:
        lines.append(f"Resina: {resin}")
    if printer:
        lines.append(f"Impressora: {printer}")

    user_content = [{"type": "text", "text": "\n".join(lines) or "Avalie as imagens."}]
    user_content.extend(image_parts)
    messages.append({"role": "user", "content": user_content})

    saved = "\n".join(lines) if lines else "Imagens enviadas."
    if image_parts:
        saved += f"\n(Imagens anexadas: {len(image_parts)})"
    save_message(phone, "user", saved)

    out = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.35,
        max_tokens=1200  # evita resposta cortada
    )
    reply = sanitize_response(out.choices[0].message.content)
    save_message(phone, "assistant", reply)
    return jsonify({"reply": reply})

if __name__ == "__main__":
    # Local: http://localhost:5000
    app.run(host="0.0.0.0", port=5000, debug=True)
