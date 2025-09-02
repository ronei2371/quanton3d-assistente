# -*- coding: utf-8 -*-
import os, json, base64
from pathlib import Path
from datetime import datetime
from typing import List

from flask import Flask, render_template, request, jsonify

# ================= Config =================
APP_VERSION   = "1.0.0"
MODEL_NAME    = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
OPENAI_APIKEY = os.getenv("OPENAI_API_KEY", "").strip()
ADMIN_TOKEN   = os.getenv("ADMIN_TOKEN", "").strip()  # opcional

app = Flask(__name__, static_folder="static", template_folder="templates")

# ================= Health check (Render) =================
@app.get("/healthz")
def healthz():
    return "ok", 200

# ================= Bloqueio por telefone (admin) =================
BLOCK_FILE = Path(os.getcwd()) / "blocked.json"

def load_blocked() -> set[str]:
    try:
        return set(json.loads(BLOCK_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()

def save_blocked(s: set[str]) -> None:
    BLOCK_FILE.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=2), encoding="utf-8")

def is_admin(req) -> bool:
    if not ADMIN_TOKEN:
        return True  # sem token, livre
    tok = req.headers.get("x-admin-token") or req.args.get("token", "")
    return tok == ADMIN_TOKEN

@app.get("/admin/blocked")
def admin_list():
    if not is_admin(request): return ("forbidden", 403)
    return jsonify(sorted(load_blocked()))

@app.post("/admin/block")
def admin_block():
    if not is_admin(request): return ("forbidden", 403)
    phone = (request.args.get("phone") or request.form.get("phone") or "").strip()
    if not phone: return ("phone obrigatório", 400)
    s = load_blocked(); s.add(phone); save_blocked(s)
    return jsonify({"ok": True, "blocked": sorted(s)})

@app.post("/admin/unblock")
def admin_unblock():
    if not is_admin(request): return ("forbidden", 403)
    phone = (request.args.get("phone") or request.form.get("phone") or "").strip()
    if not phone: return ("phone obrigatório", 400)
    s = load_blocked(); s.discard(phone); save_blocked(s)
    return jsonify({"ok": True, "blocked": sorted(s)})

# ================= Páginas =================
@app.route("/")
def home():
    # index.html pode ter overlay de Termos (q3d_terms_v7)
    return render_template("index.html", app_version=APP_VERSION)

@app.route("/chat")  # interface do bot
def chat():
    return render_template("index.html", app_version=APP_VERSION)

@app.get("/apply")   # candidatura (template; se não existir, serve estático /static/apply.html)
def apply_page():
    try:
        return render_template("apply.html")
    except Exception:
        return app.send_static_file("apply.html")

# ================= Util =================
def _read_images_from_request() -> List[str]:
    """
    Lê imagens enviadas como base64 em 'images[]' (form-data) ou JSON 'images'.
    Retorna lista de dataURLs (ou base64 simples).
    """
    images: List[str] = []

    # JSON
    if request.is_json:
        data = request.get_json(silent=True) or {}
        images += [x for x in (data.get("images") or []) if isinstance(x, str)]

    # form-data (images[])
    for _, f in (request.files or {}).items():
        try:
            b = f.read()
            if b:
                import imghdr
                kind = imghdr.what(None, b) or "png"
                b64 = base64.b64encode(b).decode("utf-8")
                images.append(f"data:image/{kind};base64," + b64)
        except Exception:
            pass

    return images[:5]  # limite de segurança

# ================= POST /chat (API) =================
# Mantemos endpoint separado para não conflitar com GET /chat
@app.route("/chat", methods=["POST"], endpoint="chat_post")
def api_chat():
    # aceita form-data e JSON
    payload = request.get_json(silent=True) or {}
    phone   = (request.form.get("phone") or payload.get("phone") or "").strip()
    problem = (request.form.get("problem") or payload.get("problem") or "").strip()
    persona = (request.form.get("persona") or payload.get("persona") or "caio").strip().lower()

    # bloqueio por telefone
    if phone in load_blocked():
        return jsonify({"ok": False, "error": "Acesso bloqueado. Fale com o suporte."}), 403

    if not phone:
        return jsonify({"ok": False, "error": "Informe o telefone."}), 400
    if not problem:
        return jsonify({"ok": False, "error": "Descreva o problema."}), 400

    images_dataurls = _read_images_from_request()

    # --------- FAST-PATH: Amarelamento após cura (responde com números) ---------
    pl = problem.lower()
    if any(k in pl for k in ("amarel", "amarela", "amarelamento")):
        resposta = (
            "Para reduzir amarelamento na pós-cura:\n"
            "• Translúcidas (fora d’água): 5 s de um lado + 5 s do outro; espere 10–20 s; repita 2–3×.\n"
            "• Cura em água (raso): 10–15 s por lado; repetir 2×.\n"
            "• Ajuste em passos de ±15–30 s até estabilizar a transparência.\n"
            "Checklist: lavar bem; SECAR totalmente (sem IPA preso); evitar calor excessivo na câmara."
        )
        return jsonify({
            "ok": True, "answer": resposta, "persona": "caio",
            "images": len(images_dataurls), "version": APP_VERSION
        }), 200
    # ---------------------------------------------------------------------------

    # Sem chave? responda gracioso
    if not OPENAI_APIKEY:
        return jsonify({"ok": True, "answer":
            "Servidor sem OPENAI_API_KEY configurada. Descrição recebida, mas não posso consultar a IA agora.",
            "version": APP_VERSION}), 200

    # --------- Monta prompt (persona + política) ----------
    persona_name = "Caio — especialista em resinas e SLA, fala de forma direta."
    policy = (
        "REGRAS: Responda de forma objetiva, sem 'EPI' salvo se o usuário pedir. "
        "Se pedir tempo, traga FAIXAS NUMÉRICAS. Quando relevante, traga checklist curto."
    )
    system_msg = f"{persona_name}\n{policy}"

    user_msg = f"Telefone: {phone}\nProblema: {problem}"
    if images_dataurls:
        user_msg += f"\nImagens anexadas: {len(images_dataurls)} (interprete de forma geral se possível)."

    # --------- OpenAI call (chat.completions) ----------
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_APIKEY)

        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",    "content": user_msg},
            ],
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.2")),
        )
        content = (completion.choices[0].message.content or "").strip()
    except Exception as e:
        return jsonify({"ok": False, "error": f"OpenAI error: {e}"}), 500

    # (Opcional) filtro para remover EPI se o usuário não pediu explicitamente
    if "epi" not in pl and "luva" not in pl and "óculos" not in pl and "oculos" not in pl:
        lines = [ln for ln in content.splitlines()
                 if not any(k in ln.lower() for k in ("epi", "luva", "óculos", "oculos"))]
        content = "\n".join(lines).strip() or content

    return jsonify({
        "ok": True,
        "answer": content,
        "persona": persona,
        "images": len(images_dataurls),
        "version": APP_VERSION
    }), 200

# ================= Main (dev local) =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
