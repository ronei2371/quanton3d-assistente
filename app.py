# kb_build.py — RAG index builder (memory-friendly)
# v2025-08-30b
import os, re, json, uuid, sys
from typing import List, Dict, Iterable, Tuple
from openai import OpenAI

# PDF reader (opcional)
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

# ---- Configs (ajustáveis por ENV)
KB_DIR = os.getenv("KB_DIR", "kb")
OUT    = os.getenv("KB_OUT", "kb/kb_index.json")
EMB_MODEL = os.getenv("KB_EMB_MODEL", "text-embedding-3-small")

# limites para não estourar memória
CHUNK_SIZE     = int(os.getenv("KB_CHUNK_SIZE", "900"))
CHUNK_OVERLAP  = int(os.getenv("KB_CHUNK_OVERLAP", "150"))
BATCH_SIZE     = int(os.getenv("KB_BATCH_SIZE", "16"))
MAX_CHARS_FILE = int(os.getenv("KB_MAX_CHARS_FILE", "400000"))  # ~400k chars por arquivo
MIN_CHUNK_LEN  = int(os.getenv("KB_MIN_CHUNK_LEN", "40"))       # ignora trechos muito curtos

def norm_ws(s: str) -> str:
    # normaliza espaços e quebras
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def chunk_stream(text: str, size: int, overlap: int) -> Iterable[str]:
    """Gera trechos sem guardar todos em memória de uma vez."""
    text = norm_ws(text)
    if not text:
        return
    i, n = 0, len(text)
    while i < n:
        j = min(n, i + size)
        piece = text[i:j].strip()
        if len(piece) >= MIN_CHUNK_LEN:
            yield piece
        if j == n:
            break
        i = max(0, j - overlap)

def read_text_txt(path: str) -> Iterable[str]:
    """Lê .txt/.md sem carregar tudo: agrega em buffers de ~64k e faz chunk."""
    buf = []
    total = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            buf.append(line)
            total += len(line)
            # a cada 64k chars, faz chunk e esvazia
            if total >= 65536:
                block = "".join(buf)
                for c in chunk_stream(block[:MAX_CHARS_FILE], CHUNK_SIZE, CHUNK_OVERLAP):
                    yield c
                buf, total = [], 0
        if buf:
            block = "".join(buf)
            for c in chunk_stream(block[:MAX_CHARS_FILE], CHUNK_SIZE, CHUNK_OVERLAP):
                yield c

def read_text_pdf(path: str) -> Iterable[str]:
    """Lê PDF por página (se pypdf disponível). Faz chunk por página."""
    if not PdfReader:
        return
    try:
        r = PdfReader(path)
    except Exception:
        return
    acc = []
    acc_len = 0
    for page in r.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if not t.strip():
            continue
        acc.append(t)
        acc_len += len(t)
        # a cada ~50k chars agregados, chunk e limpa
        if acc_len >= 50000:
            block = norm_ws("\n".join(acc))[:MAX_CHARS_FILE]
            for c in chunk_stream(block, CHUNK_SIZE, CHUNK_OVERLAP):
                yield c
            acc, acc_len = [], 0
    # resto
    if acc:
        block = norm_ws("\n".join(acc))[:MAX_CHARS_FILE]
        for c in chunk_stream(block, CHUNK_SIZE, CHUNK_OVERLAP):
            yield c

def iterate_chunks(path: str) -> Iterable[Tuple[str, str]]:
    """Retorna (source, chunk_text) de cada arquivo suportado."""
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    if ext in (".txt", ".md"):
        for c in read_text_txt(path):
            yield (base, c)
    elif ext == ".pdf":
        for c in read_text_pdf(path):
            yield (base, c)
    else:
        return

def main():
    if not os.path.isdir(KB_DIR):
        print(f"Pasta '{KB_DIR}' não existe. Crie e coloque .txt/.md/.pdf.")
        sys.exit(1)

    files = [os.path.join(KB_DIR, f) for f in os.listdir(KB_DIR)
             if os.path.splitext(f)[1].lower() in (".txt",".md",".pdf")]

    if not files:
        print("Nenhum arquivo em kb/.")
        sys.exit(1)

    client = OpenAI()

    chunks_meta: List[Dict] = []
    vectors: List[List[float]] = []

    buffer_texts: List[str] = []
    buffer_meta:  List[Dict] = []

    def flush():
        nonlocal buffer_texts, buffer_meta, vectors, chunks_meta
        if not buffer_texts:
            return
        emb = client.embeddings.create(model=EMB_MODEL, input=buffer_texts)
        for i, d in enumerate(emb.data):
            vectors.append(d.embedding)
            chunks_meta.append({
                "id": uuid.uuid4().hex,
                "text": buffer_texts[i],
                "source": buffer_meta[i]["source"]
            })
        buffer_texts, buffer_meta = [], []

    for path in files:
        try:
            total_chars = 0
            for source, c in iterate_chunks(path):
                if total_chars >= MAX_CHARS_FILE:
                    break
                total_chars += len(c)

                buffer_texts.append(c)
                buffer_meta.append({"source": source})

                if len(buffer_texts) >= BATCH_SIZE:
                    flush()
        except MemoryError:
            # mesmo que um arquivo dispare MemoryError, salvamos o que já deu
            flush()
            print(f"[aviso] MemoryError em {path}. Pulei o restante.")
            continue

    flush()  # final

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = {
        "model": EMB_MODEL,
        "chunks": chunks_meta,
        "vectors": vectors
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"OK: {OUT}  trechos: {len(chunks_meta)}")

if __name__ == "__main__":
    main()
