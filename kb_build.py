# kb_build.py — Gera índice kb/kb_index.json com embeddings por trecho
import os, re, json, uuid
from typing import List, Dict
from openai import OpenAI

# OPCIONAL: suporte a PDF (se não quiser PDF, pode remover pypdf do requirements)
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# onde estão os arquivos .txt/.md/.pdf
KB_DIR = "kb"
OUT = "kb/kb_index.json"
MODEL_EMB = "text-embedding-3-small"

def read_text_from_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ext == ".pdf" and PdfReader:
        try:
            r = PdfReader(path)
            return "\n".join(page.extract_text() or "" for page in r.pages)
        except Exception:
            return ""
    return ""

def chunk(text: str, size: int = 900, overlap: int = 200) -> List[str]:
    text = re.sub(r"\s+\n", "\n", text).strip()
    if not text:
        return []
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        j = min(n, i + size)
        chunks.append(text[i:j].strip())
        i = j - overlap
        if i < 0:
            i = 0
        if i >= n:
            break
    return [c for c in chunks if len(c) > 30]

def main():
    os.makedirs(KB_DIR, exist_ok=True)
    files = [os.path.join(KB_DIR, f) for f in os.listdir(KB_DIR)
             if os.path.splitext(f)[1].lower() in (".md",".txt",".pdf")]
    if not files:
        print("Nenhum arquivo em kb/. Coloque .txt/.md/.pdf e rode novamente.")
        return

    client = OpenAI()

    chunks, metas = [], []
    for path in files:
        txt = read_text_from_file(path)
        if not txt:
            continue
        for c in chunk(txt):
            chunks.append(c)
            metas.append({"source": os.path.basename(path)})

    if not chunks:
        print("Sem trechos válidos.")
        return

    # embeddings em lote (limita para segurança)
    print(f"Gerando embeddings para {len(chunks)} trechos…")
    # Quebra em lotes para não estourar limites de tokens
    vectors = []
    batch = 80
    for i in range(0, len(chunks), batch):
        part = chunks[i:i+batch]
        emb = client.embeddings.create(model=MODEL_EMB, input=part)
        vectors.extend([d.embedding for d in emb.data])

    out = {
        "model": MODEL_EMB,
        "chunks": [
            {"id": uuid.uuid4().hex, "text": t, "source": metas[i]["source"]}
            for i, t in enumerate(chunks)
        ],
        "vectors": vectors
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("OK:", OUT, "trechos:", len(out["chunks"]))

if __name__ == "__main__":
    main()
