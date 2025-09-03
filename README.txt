QUANTON3D - Patch: Fast-path 'Suportes Duros' + Higiene de Resposta
==================================================================

O que este patch faz:
- Adiciona fast-path para 'suportes duros / difíceis de remover' com valores numéricos e protocolo prático.
- Mantém fast-path de 'amarelamento'.
- Mantém /healthz, GET / e GET /chat (UI), POST /chat (endpoint separado), /apply (com fallback), admin block.
- Filtra 'tipo de resina' e 'configuração de base/raft' quando o tema é suporte.

Como aplicar:
1) Copie este app.py para a raiz do projeto (C:\projeto\app.py), substituindo o atual.
2) Commit & push:
   cd C:\projeto
   git add app.py
   git commit -m "patch: fast-path suportes duros + filtros + v1.0.1"
   git push origin main
3) Render: Manual Deploy → Deploy latest commit (ou Save, rebuild, and deploy se mudou Settings).

Testes:
- /healthz → ok
- /chat (UI) → envie "meus suportes estão duros" → deve responder o protocolo numérico imediatamente.
- Caso use overlay de termos, a UI deve aparecer após aceitar.
