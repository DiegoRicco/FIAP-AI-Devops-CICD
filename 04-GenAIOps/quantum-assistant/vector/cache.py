"""Cache dos embeddings dos chunks, gerado no build da imagem.

Por que cachear: calcular os 31 embeddings no boot da task atrasaria o /ready;
com o cache embutido na imagem, o boot so le um JSON. A chave de cada entrada
e o sha256 do texto que vai para o encoder + a revisao do modelo: se o corpus
mudar (ex.: data bundle novo) so os chunks alterados sao recalculados, e o
boot registra quantos foram.
"""
import hashlib
import json
import os

from . import encoder as enc


def embedding_input(chunk):
    # Titulo + secao + texto: a secao resume a regra e ajuda a busca semantica.
    return f"{chunk['title']}. {chunk['section']}. {chunk['text']}"


def text_sha(chunk):
    return hashlib.sha256(embedding_input(chunk).encode("utf-8")).hexdigest()


def default_cache_path(model_dir=None):
    return os.environ.get("QC_EMBEDDING_CACHE") or os.path.join(
        model_dir or enc.default_model_dir(), "chunk-embeddings.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("embedding_model_revision") != enc.MODEL_REVISION or data.get("embedding_dim") != enc.DIM:
        return None
    return data


def resolve(chunks, encoder, cache_data):
    """Retorna ({chunk_id: vetor}, {"hits": n, "recomputed": n})."""
    entries = (cache_data or {}).get("entries", {})
    vectors, missing = {}, []
    for ch in chunks:
        entry = entries.get(ch["chunk_id"])
        if entry and entry.get("text_sha") == text_sha(ch) and len(entry.get("embedding", [])) == enc.DIM:
            vectors[ch["chunk_id"]] = entry["embedding"]
        else:
            missing.append(ch)
    if missing:
        for ch, vec in zip(missing, encoder.encode([embedding_input(c) for c in missing])):
            vectors[ch["chunk_id"]] = vec
    return vectors, {"hits": len(chunks) - len(missing), "recomputed": len(missing)}


def write(path, chunks, vectors, corpus_version):
    data = {
        "corpus_version": corpus_version,
        "embedding_model_id": enc.MODEL_ID,
        "embedding_model_revision": enc.MODEL_REVISION,
        "embedding_dim": enc.DIM,
        "entries": {ch["chunk_id"]: {"text_sha": text_sha(ch), "embedding": [round(v, 7) for v in vectors[ch["chunk_id"]]]}
                    for ch in chunks},
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
    os.replace(tmp, path)
    return data


def vector_index_version(chunks, similarity="cosine"):
    """sha256[:12] de modelo + revisao + dimensao + similaridade + texto de cada chunk.

    Muda quando muda o modelo OU o texto indexado; nao depende de ordem.
    """
    payload = {
        "model": enc.MODEL_ID, "revision": enc.MODEL_REVISION, "dim": enc.DIM, "similarity": similarity,
        "chunks": sorted([ch["chunk_id"], text_sha(ch)] for ch in chunks),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
