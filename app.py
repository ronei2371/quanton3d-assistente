# -*- coding: utf-8 -*-
import os, re, json, base64, csv
from pathlib import Path
from datetime import datetime
from typing import List
from flask import Flask, render_template, request, jsonify, send_file, Response

APP_VERSION   = "1.1.0"
MODEL_NAME    = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
OPENAI_APIKEY = os.getenv("OPENAI_API_KEY", "").strip()
ADMIN_TOKEN   = os.getenv("ADMIN_TOKEN", "").strip()  # opcional

app = Flask(__name__, static_folder="static", template_folder="templates")

DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
CANDIDATOS_CSV = DATA_DIR / "candidatos.csv"
ATENDIMENTOS_CSV = DATA_DIR / "atendimentos.csv"
BLOCK_FILE = Path("blocked.json")

def _csv_append(path: Path, header: list, row: list):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(header)
        w.writerow(row)

def load_blocked() -> set:
    try:
        return set(json.loads(BLOCK_FILE.read_text(encoding='utf-8')))
    except Exception:
        return set()

def save_blocked(s: set) -> None:
    BLOCK_FILE.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=2), encoding='utf-8')

def is_admin(req) -> bool:
    if not ADMIN_TOKEN:
        return True
    tok = req.headers.get('x-admin-token') or req.args.get('token', '')
    return tok == ADMIN_TOKEN

@app.get('/healthz')
def healthz():
    return 'ok', 200

# ---- Admin bloqueio ----
@app.get('/admin/blocked')
def admin_list():
    if not is_admin(request): return ('forbidden', 403)
    return jsonify(sorted(load_blocked()))

@app.post('/admin/block')
def admin_block():
    if not is_admin(request): return ('forbidden', 403)
    phone = (request.args.get('phone') or request.form.get('phone') or '').strip()
    if not phone: return ('phone obrigatório', 400)
    s = load_blocked(); s.add(phone); save_blocked(s)
    return jsonify({'ok': True, 'blocked': sorted(s)})

@app.post('/admin/unblock')
def admin_unblock():
    if not is_admin(request): return ('forbidden', 403)
    phone = (request.args.get('phone') or request.form.get('phone') or '').strip()
    if not phone: return ('phone obrigatório', 400)
    s = load_blocked(); s.discard(phone); save_blocked(s)
    return jsonify({'ok': True, 'blocked': sorted(s)})

# ---- Admin CSVs ----
@app.get('/admin/candidatos.csv')
def admin_candidatos_download():
    if not is_admin(request): return ('forbidden', 403)
    if not CANDIDATOS_CSV.exists(): return ('sem dados ainda', 404)
    return send_file(str(CANDIDATOS_CSV), mimetype='text/csv', as_attachment=True, download_name='candidatos.csv')

@app.get('/admin/candidatos')
def admin_candidatos_view():
    if not is_admin(request): return ('forbidden', 403)
    if not CANDIDATOS_CSV.exists():
        return '<h3>Sem dados ainda</h3>', 200
    rows = list(csv.reader(CANDIDATOS_CSV.open('r', encoding='utf-8')))
    head = rows[0] if rows else []
    body = rows[1:] if len(rows)>1 else []
    html = ['<h3>Candidatos</h3><table border=1 cellpadding=6>']
    if head: html.append('<tr>' + ''.join(f'<th>{h}</th>' for h in head) + '</tr>')
    for r in body:
        html.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
    html.append('</table>')
    return Response('\n'.join(html), mimetype='text/html')

@app.get('/admin/atendimentos.csv')
def admin_atend_download():
    if not is_admin(request): return ('forbidden', 403)
    if not ATENDIMENTOS_CSV.exists(): return ('sem dados ainda', 404)
    return send_file(str(ATENDIMENTOS_CSV), mimetype='text/csv', as_attachment=True, download_name='atendimentos.csv')

@app.get('/admin/atendimentos')
def admin_atend_view():
    if not is_admin(request): return ('forbidden', 403)
    if not ATENDIMENTOS_CSV.exists():
        return '<h3>Sem dados ainda</h3>', 200
    rows = list(csv.reader(ATENDIMENTOS_CSV.open('r', encoding='utf-8')))
    head = rows[0] if rows else []
    body = rows[1:] if len(rows)>1 else []
    html = ['<h3>Atendimentos</h3><table border=1 cellpadding=6>']
    if head: html.append('<tr>' + ''.join(f'<th>{h}</th>' for h in head) + '</tr>')
    for r in body:
        html.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
    html.append('</table>')
    return Response('\n'.join(html), mimetype='text/html')

# ---- Páginas ----
@app.route('/')
def home():
    return render_template('index.html', app_version=APP_VERSION)

@app.route('/chat')  # UI
def chat():
    return render_template('index.html', app_version=APP_VERSION)

@app.get('/apply')   # form de candidatura
def apply_page():
    try:
        return render_template('apply.html')
    except Exception:
        return app.send_static_file('apply.html')

# ---- Utils ----
def _read_images_from_request() -> List[str]:
    images: List[str] = []
    if request.is_json:
        data = request.get_json(silent=True) or {}
        images += [x for x in (data.get('images') or []) if isinstance(x, str)]
    for _, f in (request.files or {}).items():
        try:
            b = f.read()
            if b:
                b64 = base64.b64encode(b).decode('utf-8')
                images.append('data:image/png;base64,' + b64)
        except Exception:
            pass
    return images[:5]

def _log_atendimento(ts_iso, phone, problem, printer, resin, mode, answer_preview):
    _csv_append(
        ATENDIMENTOS_CSV,
        ['timestamp_iso','phone','printer','resin','mode','problem','answer_preview'],
        [ts_iso, phone, printer or '', resin or '', mode or '', problem, (answer_preview or '')[:400]]
    )

# ---- API do bot ----
@app.route('/chat', methods=['POST'], endpoint='chat_post')
def api_chat():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        phone   = (data.get('phone') or '').strip()
        problem = (data.get('problem') or '').strip()
        persona = (data.get('persona') or 'caio').strip().lower()
        printer = (data.get('printer') or '').strip()
        resin   = (data.get('resin') or '').strip()
        mode    = (data.get('mode') or '').strip()
    else:
        phone   = (request.form.get('phone') or '').strip()
        problem = (request.form.get('problem') or '').strip()
        persona = (request.form.get('persona') or 'caio').strip().lower()
        printer = (request.form.get('printer') or '').strip()
        resin   = (request.form.get('resin') or '').strip()
        mode    = (request.form.get('mode') or '').strip()

    if phone in load_blocked():
        return jsonify({'ok': False, 'error': 'Acesso bloqueado. Fale com o suporte.'}), 403
    if not phone:
        return jsonify({'ok': False, 'error': 'Informe o telefone.'}), 400
    if not problem and mode != 'params':
        return jsonify({'ok': False, 'error': "Descreva o problema ou selecione 'Somente parâmetros'."}), 400

    images_dataurls = _read_images_from_request()
    pl = (problem or '').lower()

    # Fast-path: suportes duros
    if (('suporte' in pl or 'suportes' in pl) and any(k in pl for k in ('duro','duros','rígido','rigido','difícil','dificil','remover'))):
        resposta = ("Suportes duros / difíceis de remover — protocolo prático:\n"
            "1) Exposição normal: reduza ~10% e teste; se necessário, ajuste em passos de 0.1–0.2 s. (Não altere a exposição das camadas de base.)\n"
            "2) Contato no modelo (slicer):\n"
            "   • Tip (touchpoint): 0.25–0.35 mm (peças finas) | 0.35–0.45 mm (peças maiores)\n"
            "   • Penetração/Depth: 0.05–0.15 mm   • Neck: 0.6–1.2 mm\n"
            "   • Densidade: 25–40% (aumente o espaçamento entre suportes)\n"
            "   • Prefira 'Light' e use 'Medium/Heavy' só onde for crítico\n"
            "3) Remoção: lave em IPA 30–60 s, seque superficialmente e remova ANTES da cura final. Opcional: água morna 40–50 °C por 1–2 min.\n"
            "4) Cura final: 60–90 s total, girando a peça (evite excesso).\n"
            "5) Calibração Quanton3D: use o gabarito para fixar a exposição que não 'solda' os suportes.")
        ts = datetime.utcnow().isoformat()
        _log_atendimento(ts, phone, problem, printer, resin, mode, resposta)
        return jsonify({'ok': True, 'answer': resposta, 'persona': 'caio', 'images': len(images_dataurls), 'version': APP_VERSION}), 200

    # Fast-path: amarelamento
    if any(k in pl for k in ('amarel','amarela','amarelamento')):
        resposta = ("Para reduzir amarelamento na pós-cura:\n"
            "• Translúcidas (fora d’água): 5 s de um lado + 5 s do outro; espere 10–20 s; repita 2–3×.\n"
            "• Cura em água (raso): 10–15 s por lado; repetir 2×.\n"
            "• Ajuste em passos de ±15–30 s até estabilizar a transparência.\n"
            "Checklist: lavar bem; SECAR totalmente (sem IPA preso); evitar calor excessivo na câmara.")
        ts = datetime.utcnow().isoformat()
        _log_atendimento(ts, phone, problem, printer, resin, mode, resposta)
        return jsonify({'ok': True, 'answer': resposta, 'persona': 'caio', 'images': len(images_dataurls), 'version': APP_VERSION}), 200

    if not OPENAI_APIKEY:
        resposta = 'Servidor sem OPENAI_API_KEY configurada. Recebi sua descrição, mas não posso consultar a IA agora.'
        ts = datetime.utcnow().isoformat()
        _log_atendimento(ts, phone, problem, printer, resin, mode, resposta)
        return jsonify({'ok': True, 'answer': resposta, 'version': APP_VERSION}), 200

    # OpenAI
    persona_name = 'Caio — especialista em resinas e SLA, direto e objetivo.'
    policy = ('REGRAS: Responda de forma objetiva. Se usuário pedir TEMPO, forneça faixas numéricas. '
              'Evite mencionar EPI a menos que a pergunta seja sobre segurança. '
              'Priorize ajustes de exposição, contato do suporte, técnica de remoção e calibração.')
    system_msg = f"{persona_name}\n{policy}"
    user_msg = f"Telefone: {phone}\nProblema: {problem}"
    if printer or resin:
        user_msg += f"\nContexto: Impressora={printer or '-'} | Resina={resin or '-'}"
    if images_dataurls:
        user_msg += f"\nImagens anexadas: {len(images_dataurls)}"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_APIKEY)
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
            temperature=float(os.getenv('OPENAI_TEMPERATURE', '0.2')),
        )
        content = (completion.choices[0].message.content or '').strip()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OpenAI error: {e}'}), 500

    # Higiene de saída (tema suporte) + sem EPI se usuário não pediu
    low_pl = pl
    if 'suporte' in low_pl or 'suportes' in low_pl:
        lines = []
        for ln in content.splitlines():
            l = ln.lower()
            if re.search(r"\btipo de resina\b|resina.*dureza|configura(ç|c)ão de base|raft\b", l):
                continue
            lines.append(ln)
        content = "\n".join(lines).strip() or content
    if not re.search(r"\b(epi|luva|óculos|oculos)\b", low_pl):
        lines = [ln for ln in content.splitlines() if not re.search(r"\b(epi|luva|óculos|oculos)\b", ln.lower())]
        content = "\n".join(lines).strip() or content

    ts = datetime.utcnow().isoformat()
    _log_atendimento(ts, phone, problem, printer, resin, mode, content)

    return jsonify({'ok': True,'answer': content,'persona': persona,'images': len(images_dataurls),'version': APP_VERSION}), 200

# ---- /apply (POST) salva CSV ----
@app.route('/apply', methods=['POST'])
def apply_submit():
    nome  = (request.form.get('nome') or '').strip()
    whatsapp = (request.form.get('whatsapp') or '').strip()
    email = (request.form.get('email') or '').strip()
    cidade = (request.form.get('cidade') or '').strip()
    experiencia = (request.form.get('experiencia') or '').strip()
    impressoras = (request.form.get('impressoras') or '').strip()
    motivo = (request.form.get('motivo') or '').strip()
    disponibilidade = (request.form.get('disponibilidade') or '').strip()
    termos = request.form.get('termos') == 'on'

    erros = []
    if not nome: erros.append('Informe seu nome.')
    if not whatsapp: erros.append('Informe seu WhatsApp.')
    if not termos: erros.append('É necessário aceitar os termos.')

    if erros:
        return render_template('apply.html', erros=erros, ok=False, dados=request.form)

    _csv_append(CANDIDATOS_CSV,
        ['timestamp_iso','nome','whatsapp','email','cidade','experiencia','impressoras','motivo','disponibilidade'],
        [datetime.utcnow().isoformat(), nome, whatsapp, email, cidade, experiencia, impressoras, motivo, disponibilidade])

    return render_template('apply.html', ok=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=False)
