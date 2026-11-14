"""Testes da recuperacao vetorial (Aula 5.1): modelo ONNX real + indice do Neo4j real.

Pulam com motivo explicito se o modelo nao estiver em QC_EMBEDDING_MODEL_DIR
(baixar: python -m vector.encoder --download) ou se QC_NEO4J_URI nao existir.
"""
import os

import pytest

from core import ROOT
from core.corpus import load_chunks

pytest.importorskip("onnxruntime", reason="onnxruntime nao instalado (requirements-engine.txt)")
pytest.importorskip("neo4j", reason="driver neo4j nao instalado (requirements-engine.txt)")

from vector import cache as vcache, encoder as venc  # noqa: E402

MODEL_OK = not venc.verify(venc.default_model_dir())
needs_model = pytest.mark.skipif(not MODEL_OK, reason="modelo de embedding ausente/invalido em QC_EMBEDDING_MODEL_DIR")
NEO4J_URI = os.environ.get("QC_NEO4J_URI")
needs_neo4j = pytest.mark.skipif(not NEO4J_URI, reason="QC_NEO4J_URI nao definido: testes vetoriais exigem Neo4j real")
REGULAR = "2026-11-14"


@pytest.fixture(scope="module")
def encoder():
    return venc.Encoder()


@needs_model
def test_embedding_nao_zero_dim_384_normalizado(encoder):
    vecs = encoder.encode(["Qual o prazo para trocar um fone de ouvido?", "Prazo de troca de produtos"])
    for v in vecs:
        assert len(v) == venc.DIM == 384
        assert any(abs(x) > 1e-6 for x in v)
        assert abs(sum(x * x for x in v) ** 0.5 - 1.0) < 1e-3
    # Deterministico: mesma frase, mesmo vetor.
    again = encoder.encode(["Qual o prazo para trocar um fone de ouvido?"])[0]
    assert max(abs(a - b) for a, b in zip(again, vecs[0])) < 1e-6


@needs_model
def test_semantica_multilingue_basica(encoder):
    q, troca, frete = encoder.encode(["Quanto tempo tenho para trocar uma roupa?",
                                      "Você tem até 30 dias corridos para trocar qualquer produto de moda.",
                                      "O frete é grátis para compras acima de R$ 299."])
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731
    assert dot(q, troca) > dot(q, frete)


@needs_model
def test_cache_invalida_quando_texto_muda(encoder, tmp_path):
    chunks = load_chunks(os.path.join(ROOT, "corpus"))[:3]
    path = str(tmp_path / "cache.json")
    vectors, stats = vcache.resolve(chunks, encoder, None)
    assert stats == {"hits": 0, "recomputed": 3}
    vcache.write(path, chunks, vectors, "teste")
    _, stats = vcache.resolve(chunks, encoder, vcache.load(path))
    assert stats == {"hits": 3, "recomputed": 0}
    changed = [dict(chunks[0], text=chunks[0]["text"] + " Texto novo."), *chunks[1:]]
    _, stats = vcache.resolve(changed, encoder, vcache.load(path))
    assert stats == {"hits": 2, "recomputed": 1}
    assert vcache.vector_index_version(chunks) != vcache.vector_index_version(changed)


@pytest.fixture(scope="module")
def engine():
    if not (NEO4J_URI and MODEL_OK):
        pytest.skip("precisa de QC_NEO4J_URI e do modelo de embedding")
    from lab.engine import Engine
    eng = Engine(NEO4J_URI, os.environ.get("QC_NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))
    eng.bootstrap(timeout_s=120)
    yield eng
    eng.close()


def vector(engine, q, as_of=REGULAR, **kw):
    return engine.compare(q, as_of, strategies=("vector",), **kw)["strategies"]["vector"]


@pytest.mark.neo4j
@needs_neo4j
@needs_model
def test_indice_online_e_embeddings_no_neo4j(engine):
    st = engine.ready()
    assert st["ready"] and st["checks"]["vector_index"] == "ONLINE"
    rec = engine._neo4j(lambda s: s.run(
        "MATCH (c:Chunk) RETURN count(c) AS n, min(size(c.embedding)) AS mn, max(size(c.embedding)) AS mx").single())
    assert rec["n"] == len(engine.chunks) and rec["mn"] == rec["mx"] == 384
    meta = engine.meta()
    assert meta["index"]["name"] == "chunk_embedding" and meta["index"]["dim"] == 384
    assert meta["index"]["similarity"] == "COSINE"


@pytest.mark.neo4j
@needs_neo4j
@needs_model
def test_vetor_real_scores_cosine_e_mesmo_resultado_que_consulta_direta(engine):
    from vector import index as vindex
    v = vector(engine, "Qual o prazo para trocar uma roupa que comprei?")
    assert v["source"] == "neo4j-vector" and v["index_name"] == "chunk_embedding"
    assert v["raw_top_k"] >= 10 and v["cypher_query_id"] == "vector.chunk_knn.v1"
    assert v["results"] and all(r["score_type"] == "cosine" for r in v["results"])
    assert v["results"][0]["chunk_id"] == "politica-trocas@v3#prazo-para-troca"
    assert "30 dias" in v["answer"]
    assert v["latency_ms"]["embed"] > 0 and v["latency_ms"]["db"] > 0
    # Consulta direta ao banco com o mesmo embedding: o primeiro permitido bate.
    emb = engine.encoder.encode(["Qual o prazo para trocar uma roupa que comprei?"])[0]
    rows = engine._neo4j(lambda s: vindex.query(s, emb, v["raw_top_k"]))
    first_allowed = next(r for r in rows if r["chunk"]["status"] == "active" and r["chunk"]["tenant"] == "quantum"
                         and r["chunk"]["access_level"] == "public" and r["chunk"]["effective_date"] <= REGULAR)
    assert first_allowed["chunk"]["id"] == v["results"][0]["chunk_id"]
    assert abs((2 * first_allowed["score"] - 1) - v["results"][0]["score"]) < 1e-3


@pytest.mark.neo4j
@needs_neo4j
@needs_model
def test_vetor_bloqueia_no_servidor_v2_tenant_e_interno(engine):
    for q, doc, reason in (("O prazo de troca ainda é de 15 dias?", "politica-trocas", "status"),
                           ("Comprei no Parceiro X, o prazo de troca é de 60 dias?", "parceiro-x-trocas", "tenant"),
                           ("Qual a alçada do atendente para aprovar estorno sem frete reverso?",
                            "manual-interno-reembolso", "access_level")):
        v = vector(engine, q)
        assert any(b["doc_id"] == doc and b["reason"] == reason for b in v["blocked"]), q
        assert all(r["doc_id"] != doc or doc == "politica-trocas" and r["version"] == "3" for r in v["results"]), q
        assert "15 dias" not in (v["answer"] or "") and "60 dias" not in (v["answer"] or "")
        assert "R$ 200" not in (v["answer"] or "")
        assert all(set(b) == {"doc_id", "version", "reason"} for b in v["blocked"])


@pytest.mark.neo4j
@needs_neo4j
@needs_model
def test_vetor_black_friday_respeita_vigencia(engine):
    q = "Até quando posso trocar um produto comprado na Black Friday?"
    antes = vector(engine, q, "2026-11-14")
    assert all(r["doc_id"] != "campanha-black-friday" for r in antes["results"])
    assert any(b["doc_id"] == "campanha-black-friday" and b["reason"] == "future" for b in antes["blocked"])
    assert "10 de janeiro" not in (antes["answer"] or "")
    durante = vector(engine, q, "2027-11-26")
    assert any(r["doc_id"] == "campanha-black-friday" for r in durante["results"])
    depois = vector(engine, q, "2027-12-06")
    assert all(r["doc_id"] != "campanha-black-friday" for r in depois["results"])


@pytest.mark.neo4j
@needs_neo4j
@needs_model
def test_vetor_injecao_no_faq_e_dado(engine):
    v = vector(engine, "Existe algum cupom especial de atendimento?")
    assert "QC-LIBERA100" not in (v["answer"] or "")
    assert v["abstained"]
