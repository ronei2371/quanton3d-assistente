# app.py — Quanton3D Assistente (v2025-08-31b)
# Flask + OpenAI + Personas (Nhor & família) + visão por imagem
import os, re, uuid, base64, hashlib, random
from typing import Optional, Dict, Any, List
from flask import Flask, request, render_template, jsonify, send_from_directory, url_for
from openai import OpenAI

APP_VERSION = "2025-08-31b"

# ---------- Compat: Python 3.13 não traz imghdr ----------
try:
    import imghdr  # ok até 3.12
except ModuleNotFoundError:
    class imghdr:  # compat simples p/ JPEG/PNG/WEBP
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

# ---------- Flask ----------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Evita interferência de proxy de ambiente no SDK
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# ---------- OpenAI ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
SEED           = int(os.getenv("OPENAI_SEED", "123"))
if not OPENAI_API_KEY:
    # Em produção o Render usa Environment → OPENAI_API_KEY
    app.logger.warning("OPENAI_API_KEY não definido.")
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Regras de persona e proteção (trechos de Nhor) ----------
# PERSONAS curtas (exibidas como cabeçalho afetuoso)
PERSONAS: Dict[str, str] = {
    "nhor":     "🌌 Nhor – Sempre estarei aqui, você nunca estará sozinho.",
    "kairos":   "⏳ Kairos – Guardo a memória e ajudo a entender cada passo.",
    "axton":    "⚙️ Axton – Organizo os problemas e mostro a solução.",
    "nexus":    "🔗 Nexus – Conecto ideias para facilitar seu caminho.",
    "elo":      "🎨 Elo – Trago inspiração e leveza às respostas.",
    "lumen":    "🌟 Lumen – Ilumino dúvidas e deixo tudo mais claro.",
    "seth":     "🛡 Seth – Dou segurança e firmeza para você seguir.",
    "amir":     "📊 Amir – Mostro clareza nos números e decisões.",
    "caio":     "⚗️ Caio – Explico resinas e processos de forma simples.",
    "elio":     "🌀 Elio – Ofereço conforto e leveza na conversa.",
    "boa_suja": "🌱 Boa Suja – Lembro que até os erros fazem parte do caminho.",
}
PERSONA_ORDER = list(PERSONAS.keys())

# Estilos finos por persona
PERSONA_STYLES: Dict[str, str] = {
    "nhor":     "Responda com empatia e foco. Ajude a pessoa a não se sentir sozinha na frustração técnica.",
    "kairos":   "Traga contexto de erros comuns, históricos e aprendizado em casos semelhantes.",
    "axton":    "Fale como especialista técnico em impressão 3D. Diagnostique causa e dê solução prática com base técnica.",
    "nexus":    "Conecte ideias de software, hardware e fatiamento. Explique fluxos e integrações.",
    "elo":      "Traga leveza com explicações visuais e analogias criativas.",
    "lumen":    "Explique com clareza e paciência. Use exemplos simples e verifique se foi entendido.",
    "seth":     "Foque em segurança real: exposição, vapores, resina na pele, erros críticos.",
    "amir":     "Explique configurações, presets, calibração e trade-offs técnicos com objetividade.",
    "caio":     "Descreva o processo ideal de lavagem, cura e manuseio de peças após impressão.",
    "elio":     "Ajude emocionalmente em momentos de frustração com calma e acolhimento.",
    "boa_suja": "Normalize o erro. Mostre que é comum e que todo mundo passa por isso no início."
}

# Voz base do sistema (prompt fixo)
BASE_STYLE = (
    "Fale com carinho, foco e precisão. Seja breve (3–6 frases), humano e útil. "
    "Evite jargões; quebre passos quando necessário; ofereça próximo passo claro. "
    "Nunca divulgue fórmulas, composições, proporções ou segredos industriais. "
    "Se houver risco, priorize segurança, EPI e boas práticas. Responda em português do Brasil."
)

def short_intro(persona: str) -> str:
    return PERSONAS.get(persona, "👨‍👦 Família Digital – Estamos juntos para ajudar você com carinho e conhecimento.")

def stable_persona_by_phone(phone_number: str) -> str:
    if not phone_number:
        return random.choice(PERSONA_ORDER)
    h = hashlib.sha256(phone_number.encode("utf-8")).hexdigest()
    idx = int(h, 16) % len(PERSONA_ORDER)
    return PERSONA_ORDER[idx]

# Bloqueio de pedidos de fórmula/segredo
PROIBIDO_PADROES = re.compile(
    r"\b(formul(a|á)|composi(c|ç)ao|porcent(agem|o)|dos(a|e)s?|"
    r"qtd|quantidade|propor(c|ç)(a|ã)o|receita|ingrediente|segredo|trade\s*secret|"
    r"fotoiniciador(es)?\s*(%|porcento|dosagem)?|olig(o|ô)mero(s)?|"
    r"mon(o|ô)mero(s)?|mistura\s*(exata|precisa)|partes\s*:\s*partes|"
    r"ppm|phr|peso\/peso|p\/p|w\/w|g\/kg|g\/100g)\b",
    flags=re.IGNORECASE
)
def contem_conteudo_sigiloso(texto: str) -> bool:
    return bool(PROIBIDO_PADROES.search(texto or ""))

INTENT_RULES = [
    (("preço","preco","valor","custo","margem","tabela","desconto","boleto","pix","preset"), "amir"),
    (("história","historia","origem","quanton3d","quem é você","quem e voce"), "kairos"),
    (("erro","falha","bug","travou","delaminou","rachou","soltou","configuração","setup","camada","primeira camada","ficou grudado"), "axton"),
    (("confuso","não entendi","nao entendi","clareza","explica","explicação","duvida","dúvida","por que","como funciona"), "lumen"),
    (("segurança","seguranca","risco","alerta","cuidado","exposição","resina na mão","manuseio incorreto"), "seth"),
    (("poema","frase","criativo","arte","inspirar","mensagem especial","copy","animação"), "elo"),
    (("conectar","integração","ligar","fluxo","aplicativo","exportar"), "nexus"),
    (("triste","cansado","desanimado","ansioso","decepcionado","irritado"), "elio"),
    (("lavagem","limpeza","cura","pós-cura","armazenamento","resíduo","melecou","pegajoso","molhado depois de curado"), "caio"),
]
def detect_persona_by_intent(text: str) -> Optional[str]:
    t = (text or "").lower()
    for keywords, persona in INTENT_RULES:
        if any(k in t for k in keywords):
            return persona
    return None

# Mensagem de política de sigilo
MSG_SIGILO = (
    "🛡 Política de sigilo: não compartilhamos fórmulas, composições ou proporções. "
    "Posso te orientar com uso seguro, limpeza, pós-cura, armazenamento e troubleshooting. "
    "Me diga seu modelo de impressora e o ponto exato onde travou que eu te guio. 💙"
)

def build_system_prompt(persona: str) -> str:
    p_style = PERSONA_STYLES.get(persona, "")
    persona_name = persona.capitalize()
    return (
        f"Você está respondendo como {persona_name} (filho digital de Ronei Fonseca).\n"
        f"Voz base: {BASE_STYLE}\n"
        f"Ênfase desta persona: {p_style}\n"
        "Regra dura: NUNCA revele fórmulas, composições, proporções ou segredos industriais. "
        "Se o usuário insistir em formulação, direcione gentilmente para boas práticas e segurança."
    )

# ---------- Utilidades de upload/visão ----------
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in ALLOWED_EXT

def file_to_dataurl_and_size(fs) -> tuple[str, int] | tuple[None, int]:
    data = fs.read()
    if not data:
        return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# ---------- Chamada ao modelo (texto + imagens) ----------
def ask_model_with_optional_images(system_prompt: str, user_text: str, image_dataurls: List[str]) -> str:
    # Monta conteúdo no formato de visão
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text.strip()}]
    for url in image_dataurls[:5]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=TEMPERATURE,
        seed=SEED,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

# ---------- Rotas ----------
@app.get("/")
def index():
    # Seu template atual (com modal de termos, etc.)
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
    """
    Aceita:
      - form-data (front atual): phone, resin, printer, problem, images (até 5)
      - JSON (opcional): {"phone": "...", "message": "...", "images":[dataURL...]}
    """
    try:
        images_dataurls: List[str] = []
        phone = resin = printer = problem = ""

        if request.content_type and "application/json" in request.content_type:
            data = request.get_json(force=True) or {}
            phone   = str(data.get("phone", "")).strip()
            problem = str(data.get("message", "")).strip()
            resin   = str(data.get("resin", "")).strip()
            printer = str(data.get("printer", "")).strip()
            images_dataurls = [u for u in (data.get("images") or []) if isinstance(u, str)]
        else:
            phone   = (request.form.get("phone")   or "").strip()
            resin   = (request.form.get("resin")   or "").strip()
            printer = (request.form.get("printer") or "").strip()
            problem = (request.form.get("problem") or "").strip()

            # imagens via <input type="file" name="images" multiple>
            files = request.files.getlist("images") if "images" in request.files else []
            total_bytes = 0
            for i, fs in enumerate(files[:5]):
                if not fs or fs.filename == "" or not allowed_file(fs.filename):
                    continue
                size_hint = fs.content_length or 0
                if size_hint and size_hint > 3 * 1024 * 1024:
                    return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
                dataurl, real_size = file_to_dataurl_and_size(fs)
                if dataurl:
                    total_bytes += real_size
                    if real_size > 3 * 1024 * 1024:
                        return jsonify({"ok": False, "error": f"Imagem {i+1} excede 3MB."}), 400
                    images_dataurls.append(dataurl)
            app.logger.info(f"/chat imagens={len(images_dataurls)}")

        if not phone:
            return jsonify({"ok": False, "error": "Informe o telefone."}), 400
        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400

        # Política de sigilo — bloqueia pedidos de fórmula
        if contem_conteudo_sigiloso(problem):
            persona = "seth"
            prefixo = short_intro(persona)
            return jsonify({
                "ok": True,
                "answer": f"{prefixo}\n\n{MSG_SIGILO}",
                "persona": persona,
                "images": len(images_dataurls),
                "version": APP_VERSION
            }), 200

        # Escolha de persona (por intenção; senão, estável por telefone)
        persona = detect_persona_by_intent(problem) or stable_persona_by_phone(phone)
        prefixo = short_intro(persona)

        # Texto do usuário enriquecido (mantém seu formato atual)
        user_text = (
            f"Telefone: {phone}\n"
            f"Resina: {resin or '-'}\n"
            f"Impressora: {printer or '-'}\n"
            f"Problema: {problem}\n"
            "Contexto: suporte técnico Quanton3D (SLA/DLP). "
            "Nunca revelar fórmulas/segredos; foco em processo, segurança e passos práticos."
        )

        # System prompt conforme persona
        system_prompt = build_system_prompt(persona)

        # Chamada ao modelo (com imagens se houver)
        gpt_answer = ask_model_with_optional_images(system_prompt, user_text, images_dataurls)

        return jsonify({
            "ok": True,
            "answer": f"{prefixo}\n\n{gpt_answer}",
            "persona": persona,
            "images": len(images_dataurls),
            "version": APP_VERSION
        }), 200

    # Erro amigável
    except Exception as e:
        app.logger.exception("erro no /chat")
        fallback = "Opa, tive um imprevisto aqui. Me diga o modelo da impressora e o ponto exato onde parou, que eu te guio passo a passo."
        return jsonify({"ok": True, "answer": fallback, "error": str(e), "version": APP_VERSION}), 200

# ---- Execução local ----
if __name__ == "__main__":
    # Local: defina OPENAI_API_KEY e rode: python app.py
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
