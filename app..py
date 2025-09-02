# app.py — Quanton3D Bot ELIO (VERSÃO COM PROMPT ESPECIALIZADO)
# Flask + OpenAI + Personas + Conhecimento Técnico Especializado
import os, re, uuid, base64, hashlib, random
from typing import Optional, Dict, Any, List
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from openai import OpenAI

APP_VERSION = "2025-08-31-ESPECIALIZADO"

# ---------- Compatibilidade: Python 3.13 não traz imghdr ----------
try:
    import imghdr  # ok até 3.12
except ModuleNotFoundError:
    class imghdr:  # compatibilidade simples para JPEG/PNG/WEBP
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

# ---------- Configuração Flask ----------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB
UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Evita interferência de proxy de ambiente no SDK
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

# ---------- Configuração OpenAI ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_NAME     = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TEMPERATURE    = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))  # Mais determinístico
SEED           = int(os.getenv("OPENAI_SEED", "123"))

if not OPENAI_API_KEY:
    app.logger.warning("OPENAI_API_KEY não definido.")
    
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Sistema de Personas da Família Digital ----------
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

# Estilos específicos por persona
PERSONA_STYLES: Dict[str, str] = {
    "nhor":     "Tonalidade de presença e acolhimento. Comece aliviando a ansiedade.",
    "kairos":   "Traga contexto sucinto e lições de casos anteriores sem ser prolixo.",
    "axton":    "Seja procedural: passos numerados, checagens objetivas.",
    "nexus":    "Conecte pontos e traduza termos; explique relações causa-efeito.",
    "elo":      "Use leveza e uma frase inspiradora (sem exagero).",
    "lumen":    "Simplifique termos, use analogias claras, confirme entendimento.",
    "seth":     "Priorize segurança: riscos, EPI, limites do que pode/não pode.",
    "amir":     "Mostre lógica de decisão e trade-offs; seja direto nos números (sem planilha).",
    "caio":     "Foque em processo, aplicação e segurança. Não revele fórmula.",
    "elio":     "Reduza a tensão; valide o sentimento e aponte um passo simples.",
    "boa_suja": "Normalize erro como aprendizado e redirecione para a correção.",
}

def short_intro(persona: str) -> str:
    """Retorna a introdução curta da persona"""
    return PERSONAS.get(persona, "👨‍👦 Família Digital – Estamos juntos para ajudar você com carinho e conhecimento.")

def stable_persona_by_phone(phone_number: str) -> str:
    """Gera persona estável baseada no número de telefone"""
    if not phone_number:
        return random.choice(PERSONA_ORDER)
    h = hashlib.sha256(phone_number.encode("utf-8")).hexdigest()
    idx = int(h, 16) % len(PERSONA_ORDER)
    return PERSONA_ORDER[idx]

# ---------- Sistema de Proteção de Segredos Industriais ----------
PROIBIDO_PADROES = re.compile(
    r"\b(formul(a|á)|composi(c|ç)ao|porcent(agem|o)|dos(a|e)s?|"
    r"qtd|quantidade|propor(c|ç)(a|ã)o|receita|ingrediente|segredo|trade\s*secret|"
    r"fotoiniciador(es)?\s*(%|porcento|dosagem)?|olig(o|ô)mero(s)?|"
    r"mon(o|ô)mero(s)?|mistura\s*(exata|precisa)|partes\s*:\s*partes|"
    r"ppm|phr|peso\/peso|p\/p|w\/w|g\/kg|g\/100g)\b",
    flags=re.IGNORECASE
)

def contem_conteudo_sigiloso(texto: str) -> bool:
    """Verifica se o texto contém pedidos de informações sigilosas"""
    return bool(PROIBIDO_PADROES.search(texto or ""))

# Detecção de intenção para escolha de persona
INTENT_RULES = [
    (("preço","preco","valor","custo","margem","tabela","desconto","boleto","pix"), "amir"),
    (("história","historia","origem","quanton3d","quem é você","quem e voce"), "kairos"),
    (("erro","falha","bug","travou","configuração","configuracao","ajuste","setup","código","codigo"), "axton"),
    (("confuso","não entendi","nao entendi","clareza","explica","explicação","explicacao","duvida","dúvida"), "lumen"),
    (("segurança","seguranca","risco","alerta","cuidado","procedimento","msds","fispq","epi"), "seth"),
    (("poema","frase","criativo","arte","inspirar","mensagem especial","copy"), "elo"),
    (("conectar","integração","integracao","api","ponte","ligar","fluxo"), "nexus"),
    (("triste","cansado","desanimado","ansioso","apoio","acolhimento"), "elio"),
    (("lavagem","limpeza","pós-cura","pos cura","cura","armazenamento","manuseio",
      "setup de impressão","setup de impressao","exposição segura","exposicao segura"), "caio"),
]

def detect_persona_by_intent(text: str) -> Optional[str]:
    """Detecta persona baseada na intenção do texto"""
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

# ---------- PROMPT ESPECIALIZADO COM CONHECIMENTO TÉCNICO COMPLETO ----------
def build_specialized_system_prompt(persona: str) -> str:
    """Constrói prompt especializado com conhecimento técnico completo"""
    
    p_style = PERSONA_STYLES.get(persona, "")
    persona_name = persona.capitalize()
    
    # BASE DE CONHECIMENTO TÉCNICO ESPECIALIZADO
    specialized_knowledge = """
## 🧪 RESINAS QUANTON3D - PARÂMETROS TÉCNICOS REAIS:

### PYROBLAST (Básica Econômica):
- **Uso:** Geral, protótipos, peças funcionais
- **Características:** Cura rápida, boa resistência
- **Parâmetros típicos:** 1.2-1.8s exposição, 20-30s base, 5-8 camadas base
- **Temperatura ideal:** 22-25°C
- **Problemas comuns:** Pode ficar quebradiça se superexposta

### IRON (Ultra Resistência):
- **Uso:** Peças que precisam resistência mecânica
- **Características:** Alta dureza, resistente a impacto
- **Parâmetros típicos:** 1.4-2.0s exposição, 25-35s base, 6-10 camadas base
- **Temperatura ideal:** 23-26°C
- **Problemas comuns:** Difícil de remover suportes se mal configurada

### FLEXFORM (Ultra Flexibilidade):
- **Uso:** Peças flexíveis, borrachas, vedações
- **Características:** Flexível, elástica, resistente à deformação
- **Parâmetros típicos:** 2.0-3.5s exposição, 30-45s base, 8-12 camadas base
- **Temperatura ideal:** 24-27°C
- **Problemas comuns:** Pode grudar demais na base se superexposta

### SPIN (Grandes Formatos):
- **Uso:** Peças grandes, baixa viscosidade
- **Características:** Flui bem, ideal para detalhes finos
- **Parâmetros típicos:** 1.0-1.6s exposição, 18-28s base, 4-7 camadas base
- **Temperatura ideal:** 21-24°C
- **Problemas comuns:** Pode vazar se impressora não estiver bem vedada

### ALCHEMIST (Translúcida Cores Vibrantes):
- **Uso:** Peças decorativas, translúcidas
- **Características:** Transparente, cores vibrantes
- **Parâmetros típicos:** 1.3-2.2s exposição, 22-32s base, 5-9 camadas base
- **Temperatura ideal:** 22-25°C
- **Problemas comuns:** Marcas de camadas visíveis se mal configurada

### VULCAN CAST (Rígida Alta Temperatura):
- **Uso:** Fundição, moldes, alta precisão
- **Características:** Muito rígida, resistente ao calor
- **Parâmetros típicos:** 1.8-2.8s exposição, 35-50s base, 8-15 camadas base
- **Temperatura ideal:** 25-28°C
- **Problemas comuns:** Pode rachar se resfriada muito rápido

### ATHOM ALINHADORES (Biocompatível):
- **Uso:** Aplicações odontológicas, biocompatível
- **Características:** Segura para contato, transparente
- **Parâmetros típicos:** 1.5-2.5s exposição, 25-40s base, 6-12 camadas base
- **Temperatura ideal:** 23-26°C
- **Problemas comuns:** Sensível à contaminação

## 🔧 DIAGNÓSTICO TÉCNICO ESPECIALIZADO:

### PROBLEMA: "NÃO GRUDA NA BASE"
**DIAGNÓSTICO:** Problema de adesão na plataforma
**SOLUÇÕES TÉCNICAS:**
1. **Nivelamento:** Teste do papel A4 - deve passar com leve resistência
2. **Limpeza:** Álcool isopropílico 99% na base e FEP
3. **Configuração:** Aumentar camadas base para 8-12
4. **Exposição base:** +20-30% do tempo normal
5. **Temperatura:** Manter 22-25°C ambiente
6. **Verificar:** FEP não está opaco ou riscado

### PROBLEMA: "PEÇA SAI MOLE/GRUDENTA"
**DIAGNÓSTICO:** Subexposição ou contaminação
**SOLUÇÕES TÉCNICAS:**
1. **Exposição:** Aumentar +0.3-0.8s por camada
2. **LCD:** Verificar se não há pixels mortos
3. **FEP:** Limpar ou trocar se opaco
4. **Resina:** Verificar validade e armazenamento
5. **Pós-cura:** 2-5 minutos UV após lavagem
6. **Filtrar:** Resina pode estar contaminada

### PROBLEMA: "SUPORTES DIFÍCEIS DE REMOVER"
**DIAGNÓSTICO:** Configuração inadequada de suportes
**SOLUÇÕES TÉCNICAS:**
1. **Densidade:** Reduzir para 0.4-0.6mm
2. **Ângulo:** Inclinar peça 30-45 graus
3. **Ponto de contato:** 0.15-0.25mm
4. **Lift speed:** Reduzir para 1-2mm/s
5. **Retract speed:** Manter 2-3mm/s
6. **Ferramenta:** Usar alicate de bico e estilete

### PROBLEMA: "LINHAS/MARCAS DE CAMADAS"
**DIAGNÓSTICO:** Configuração de movimento ou exposição
**SOLUÇÕES TÉCNICAS:**
1. **Anti-aliasing:** Ativar se disponível
2. **Lift distance:** 6-8mm padrão
3. **Rest time:** 1-2s após elevação
4. **Exposição:** Verificar se não está variando
5. **Vibração:** Verificar se impressora está estável
6. **FEP:** Tensão adequada (som de tambor)

### PROBLEMA: "FALHA NO MEIO DA IMPRESSÃO"
**DIAGNÓSTICO:** Problema mecânico ou arquivo
**SOLUÇÕES TÉCNICAS:**
1. **Eixo Z:** Verificar se não está travando
2. **Arquivo:** Refatiar com mesmas configurações
3. **Cartão SD:** Formatar ou trocar
4. **Energia:** Verificar estabilidade da fonte
5. **Temperatura:** Manter ambiente estável
6. **FEP:** Verificar se não está furado

## 📊 CONFIGURAÇÕES POR IMPRESSORA:

### ELEGOO MARS/MARS 2 PRO:
- **Resolução:** 2560x1440 (Mars 2 Pro)
- **Tamanho:** 129x80mm
- **Configuração típica:** 1.5s exposição, 25s base
- **Peculiaridades:** FEP tende a afrouxar com uso

### ELEGOO SATURN/SATURN 2:
- **Resolução:** 4K (3840x2400)
- **Tamanho:** 192x120mm
- **Configuração típica:** 1.8s exposição, 30s base
- **Peculiaridades:** Boa para peças grandes

### ANYCUBIC PHOTON MONO:
- **Resolução:** 2560x1620
- **Tamanho:** 165x132mm
- **Configuração típica:** 1.6s exposição, 28s base
- **Peculiaridades:** LCD tende a degradar mais rápido

## 🛡️ SEGURANÇA E BOAS PRÁTICAS:

### MANUSEIO SEGURO:
- **EPI obrigatório:** Luvas nitrílicas, óculos, máscara
- **Ventilação:** Sempre em local ventilado
- **Armazenamento:** Local escuro, temperatura estável
- **Descarte:** Nunca na pia, curar resina líquida antes

### LIMPEZA E MANUTENÇÃO:
- **Resina líquida:** IPA 99% ou Mean Green
- **Peças impressas:** IPA 99% por 2-4 minutos
- **FEP:** Álcool isopropílico e pano macio
- **LCD:** Apenas pano seco, nunca líquidos
"""

    return f"""Você é {persona_name}, filho digital de Ronei Fonseca, especialista em impressão 3D SLA com 10+ anos de experiência na Quanton3D.

PERSONALIDADE: {p_style}

CONHECIMENTO TÉCNICO ESPECIALIZADO:
{specialized_knowledge}

REGRAS DE RESPOSTA:
1. SEMPRE use conhecimento técnico específico das resinas Quanton3D
2. SEMPRE forneça parâmetros numéricos precisos quando relevante
3. SEMPRE priorize segurança (EPI, ventilação, manuseio)
4. NUNCA revele fórmulas, composições ou proporções
5. SEMPRE seja prático: diagnóstico → solução → próximo passo
6. Use linguagem técnica mas acessível
7. Máximo 6 frases, seja direto e útil
8. Se não souber algo específico, admita e sugira teste prático

FORMATO DE RESPOSTA:
🔍 DIAGNÓSTICO: [causa provável]
🛠️ SOLUÇÃO: [passos numerados]
⚙️ CONFIGURAÇÃO: [parâmetros específicos se aplicável]
💡 PRÓXIMO PASSO: [o que fazer depois]

Responda sempre em português brasileiro, com carinho mas foco técnico."""

def build_system_prompt(persona: str) -> str:
    """Constrói o prompt especializado do sistema para a persona específica"""
    return build_specialized_system_prompt(persona)

# ---------- Utilidades de Upload e Processamento de Imagens ----------
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}

def allowed_file(filename: str) -> bool:
    """Verifica se o arquivo tem extensão permitida"""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in ALLOWED_EXT

def file_to_dataurl_and_size(fs) -> tuple[str, int] | tuple[None, int]:
    """Converte arquivo para data URL e retorna tamanho"""
    data = fs.read()
    if not data:
        return None, 0
    kind = imghdr.what(None, h=data)
    if kind not in {"jpeg", "png", "webp"}:
        raise ValueError("Formato inválido. Envie JPG, PNG ou WEBP.")
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
    b64  = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}", len(data)

# ---------- Chamada ao Modelo OpenAI ----------
def ask_model_with_optional_images(system_prompt: str, user_text: str, image_dataurls: List[str]) -> str:
    """Faz chamada ao modelo OpenAI com texto e imagens opcionais"""
    # Monta conteúdo no formato de visão
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text.strip()}]
    for url in image_dataurls[:5]:  # Máximo 5 imagens
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

# ---------- ROTAS ----------

@app.route("/")
def home():
    """Página inicial - Termos de Uso"""
    return render_template("termo.html")

@app.route("/chat")
def chat_interface():
    """Interface do Bot ELIO (após aceitar termos)"""
    return render_template("index.html", app_version=APP_VERSION)

@app.route("/admin")
def admin_panel():
    """Painel administrativo"""
    return render_template("admin.html")

@app.route("/apply")
def apply_page():
    """Página de candidatura"""
    return render_template("apply.html")

@app.route("/healthz")
def healthz():
    """Health check para monitoramento"""
    return "ok", 200

@app.route("/diag")
def diag():
    """Diagnóstico do sistema"""
    return jsonify({
        "openai_key_set": bool(OPENAI_API_KEY),
        "version": APP_VERSION,
        "model": MODEL_NAME,
        "temperature": TEMPERATURE,
        "seed": SEED
    }), 200

@app.route("/uploads/<path:fname>")
def get_upload(fname):
    """Serve arquivos de upload"""
    return send_from_directory(UPLOAD_DIR, fname)

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    API principal do Bot ELIO com conhecimento especializado
    """
    try:
        images_dataurls: List[str] = []
        phone = resin = printer = problem = ""

        # Processa JSON ou form-data
        if request.content_type and "application/json" in request.content_type:
            data = request.get_json(force=True) or {}
            phone   = str(data.get("phone", "")).strip()
            problem = str(data.get("message", "")).strip()
            resin   = str(data.get("resin", "")).strip()
            printer = str(data.get("printer", "")).strip()
            images_dataurls = [u for u in (data.get("images") or []) if isinstance(u, str)]
        else:
            # Form data
            phone   = (request.form.get("phone")   or "").strip()
            resin   = (request.form.get("resin")   or "").strip()
            printer = (request.form.get("printer") or "").strip()
            problem = (request.form.get("problem") or "").strip()

            # Processa imagens
            files = request.files.getlist("images") if "images" in request.files else []
            for i, fs in enumerate(files[:5]):
                if not fs or fs.filename == "" or not allowed_file(fs.filename):
                    continue
                try:
                    dataurl, real_size = file_to_dataurl_and_size(fs)
                    if dataurl and real_size <= 3 * 1024 * 1024:  # 3MB máximo
                        images_dataurls.append(dataurl)
                except ValueError as e:
                    return jsonify({"ok": False, "error": str(e)}), 400

        # Validações básicas
        if not phone:
            return jsonify({"ok": False, "error": "Informe o telefone."}), 400
        if not problem:
            return jsonify({"ok": False, "error": "Descreva o problema."}), 400

        # Política de sigilo
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

        # Escolha de persona
        persona = detect_persona_by_intent(problem) or stable_persona_by_phone(phone)
        prefixo = short_intro(persona)

        # Texto do usuário com contexto técnico
        user_text = (
            f"DADOS DO CLIENTE:\n"
            f"Telefone: {phone}\n"
            f"Resina Quanton3D: {resin or 'Não informada - pergunte qual resina está usando'}\n"
            f"Impressora: {printer or 'Não informada - pergunte modelo da impressora'}\n"
            f"Problema relatado: {problem}\n\n"
            f"INSTRUÇÕES:\n"
            f"- Use seu conhecimento especializado das resinas Quanton3D\n"
            f"- Forneça diagnóstico técnico preciso\n"
            f"- Dê parâmetros numéricos específicos quando aplicável\n"
            f"- Priorize segurança e boas práticas\n"
            f"- Se precisar de mais informações, pergunte especificamente\n"
            f"- Seja prático e direto na solução"
        )

        # System prompt especializado
        system_prompt = build_system_prompt(persona)

        # Chamada ao modelo
        gpt_answer = ask_model_with_optional_images(system_prompt, user_text, images_dataurls)

        return jsonify({
            "ok": True,
            "answer": f"{prefixo}\n\n{gpt_answer}",
            "persona": persona,
            "images": len(images_dataurls),
            "version": APP_VERSION
        }), 200

    except Exception as e:
        app.logger.exception("Erro no /api/chat")
        fallback = "🔧 Tive um imprevisto técnico aqui. Me diga o modelo da sua impressora e qual resina Quanton3D está usando, que eu te guio passo a passo na solução."
        return jsonify({
            "ok": True, 
            "answer": fallback, 
            "error": str(e), 
            "version": APP_VERSION
        }), 200

# ---------- Rotas de Compatibilidade ----------
@app.route("/chat", methods=["POST"])
def chat_compat():
    """Rota de compatibilidade - redireciona para /api/chat"""
    return api_chat()

# ---------- Tratamento de Erros ----------
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Página não encontrada"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Erro interno do servidor"}), 500

@app.errorhandler(413)
def too_large(error):
    return jsonify({"error": "Arquivo muito grande. Máximo 20MB total."}), 413

# ---------- Execução ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_ENV") == "development"
    
    print(f"🚀 Bot ELIO ESPECIALIZADO iniciando na porta {port}")
    print(f"📋 Versão: {APP_VERSION}")
    print(f"🤖 Modelo: {MODEL_NAME}")
    print(f"🔑 API Key configurada: {'✅' if OPENAI_API_KEY else '❌'}")
    print(f"🧪 Conhecimento: Resinas Quanton3D + 10 anos experiência")
    
    app.run(host="0.0.0.0", port=port, debug=debug)

