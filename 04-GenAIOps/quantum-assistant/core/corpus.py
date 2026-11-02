"""Leitura do corpus: front matter (metadados) + secoes '##' viram chunks.

Por que o chunk e a secao: cada secao trata de uma regra so (prazo, condicoes,
como solicitar). Citar a secao deixa a fonte verificavel por qualquer pessoa.
"""
import os
import re

REQUIRED_FIELDS = ("doc_id", "title", "version", "effective_date", "status", "tenant", "access_level")
VALID_STATUS = ("active", "obsolete", "draft")
VALID_ACCESS = ("public", "internal")


def parse_front_matter(text):
    """Front matter simples ('chave: valor' entre linhas '---'), sem PyYAML."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("documento sem front matter (primeira linha deve ser ---)")
    meta, end = {}, None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    if end is None:
        raise ValueError("front matter sem linha --- de fechamento")
    return meta, "\n".join(lines[end + 1:])


def slug(text):
    import unicodedata
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def load_documents(corpus_dir):
    docs = []
    for name in sorted(os.listdir(corpus_dir)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(corpus_dir, name), encoding="utf-8") as f:
            meta, body = parse_front_matter(f.read())
        missing = [k for k in REQUIRED_FIELDS if not meta.get(k)]
        if missing:
            raise ValueError(f"{name}: faltam campos no front matter: {missing}")
        if meta["status"] not in VALID_STATUS or meta["access_level"] not in VALID_ACCESS:
            raise ValueError(f"{name}: status ou access_level invalido")
        meta["file"] = name
        docs.append((meta, body))
    return docs


def chunk_document(meta, body):
    """Cada '## Titulo' vira um chunk. O texto antes da primeira secao (titulo e
    ressalva de politica ficticia) nao vira evidencia."""
    chunks, section, lines = [], None, []

    def flush():
        text = " ".join(l.strip() for l in lines if l.strip())
        if section and text:
            chunks.append({
                "chunk_id": f"{meta['doc_id']}@v{meta['version']}#{slug(section)}",
                "doc_id": meta["doc_id"],
                "title": meta["title"],
                "section": section,
                "version": meta["version"],
                "effective_date": meta["effective_date"],
                "expires_date": meta.get("expires_date") or None,
                "status": meta["status"],
                "tenant": meta["tenant"],
                "access_level": meta["access_level"],
                "category": meta.get("category", "policy"),
                "supersedes": meta.get("supersedes") or None,
                "file": meta["file"],
                "text": text,
            })

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            section, lines = line[3:].strip(), []
        elif section is not None:
            lines.append(line)
    flush()
    return chunks


def load_chunks(corpus_dir):
    chunks = []
    for meta, body in load_documents(corpus_dir):
        chunks.extend(chunk_document(meta, body))
    return chunks
