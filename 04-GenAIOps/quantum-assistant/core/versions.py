"""Versoes dos artefatos que, juntos, definem o comportamento do assistente.

Por que separar: uma resposta muda quando muda o codigo, o template, o corpus,
o indice ou o conjunto de avaliacao. Rastrear so a versao do codigo esconderia
uma troca de politica feita sem commit de codigo.
"""
import hashlib
import json
import os

from . import ROOT

APPLICATION_VERSION = "1.0.0"
MODEL_VERSION = "none"  # modo deterministico: nenhum modelo de linguagem e chamado

EVAL_FILES = ("golden_v1.jsonl", "adversarial.jsonl", "thresholds.json")


def short_sha(data):
    return hashlib.sha256(data).hexdigest()[:12]


def corpus_version(corpus_dir=None):
    """sha256[:12] de nome + conteudo de cada .md, em ordem alfabetica."""
    corpus_dir = corpus_dir or os.path.join(ROOT, "corpus")
    digest = hashlib.sha256()
    for name in sorted(os.listdir(corpus_dir)):
        if name.endswith(".md"):
            with open(os.path.join(corpus_dir, name), "rb") as f:
                digest.update(name.encode("utf-8") + f.read())
    return digest.hexdigest()[:12]


def index_version(chunks, params):
    """sha256[:12] dos chunks + parametros de busca e filtros.

    Mudar k, BM25 ou um filtro muda o indice efetivo mesmo com o corpus igual.
    """
    payload = {
        "chunks": [[c["chunk_id"], c["version"], c["status"], c["text"]] for c in chunks],
        "params": params,
    }
    return short_sha(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def prompt_version(templates):
    return str(templates.get("version", "unknown"))


def eval_set_version(evals_dir=None, fallback=None):
    """sha256[:12] dos arquivos de avaliacao.

    No bundle da Lambda a pasta evals/ pode nao existir; nesse caso vale a versao
    informada pelo build (settings/env), para nao inventar um hash.
    """
    evals_dir = evals_dir or os.path.join(ROOT, "evals")
    digest = hashlib.sha256()
    found = False
    for name in EVAL_FILES:
        path = os.path.join(evals_dir, name)
        if os.path.exists(path):
            found = True
            with open(path, "rb") as f:
                digest.update(name.encode("utf-8") + f.read())
    if found:
        return digest.hexdigest()[:12]
    return os.environ.get("QC_EVAL_SET_VERSION") or fallback or "not_bundled"
