"""Testes do motor de grafo (Aula 5.1) contra um Neo4j REAL.

Rodam so com QC_NEO4J_URI definido (e NEO4J_PASSWORD), por exemplo:
  docker run -d --name qc18-neo4j-test -p 127.0.0.1:17687:7687 \\
    -e NEO4J_AUTH=neo4j/<senha-local> neo4j:5.26.33-community
  QC_NEO4J_URI=bolt://127.0.0.1:17687 NEO4J_PASSWORD=<senha-local> \\
    QC_EMBEDDING_MODEL_DIR=<dir do modelo> python -m pytest -m neo4j tests/test_graph_engine.py
Sem essas variaveis, o arquivo inteiro e pulado com motivo explicito: a suite
legada continua rodando sem Neo4j. Os testes OFFLINE do seed (sem banco) rodam sempre.
"""
import json
import os

import pytest

from core import ROOT
from core.orders import InMemoryOrders

pytest.importorskip("neo4j", reason="driver neo4j nao instalado (requirements-engine.txt)")

from graph import loader  # noqa: E402

NEO4J_URI = os.environ.get("QC_NEO4J_URI")
needs_neo4j = pytest.mark.skipif(not NEO4J_URI, reason="QC_NEO4J_URI nao definido: testes de grafo exigem Neo4j real")

REGULAR, BF, AFTER_BF = "2026-11-14", "2027-11-26", "2027-12-06"
FONE = "Comprei um fone de ouvido, qual o prazo para trocar?"
FONE_HEADPHONE = "Meu headphone deu problema de tamanho, qual o prazo para trocar?"
FONE_BF = "Comprei um fone de ouvido na Black Friday, até quando posso trocar?"
MOCHILA_BF = "Comprei uma mochila na Black Friday, até quando posso trocar?"
SEMANTIC = {"IN_CATEGORY", "PARTICIPATES_IN", "APPLIES_TO", "HAS_VERSION", "SUPERSEDES", "DOCUMENTED_BY", "CONTAINS"}


# ----------------------------------------------------------- offline (sem banco)
def test_seed_valido_e_sem_campos_do_front_matter():
    assert loader.validate(ROOT) == []
    with open(loader.seed_path(ROOT), encoding="utf-8") as f:
        raw = f.read()
    seed = json.loads(raw)
    for kind in ("products", "categories", "campaigns", "policies"):
        for item in seed[kind]:
            assert not set(item) & set(loader.FRONT_MATTER_ONLY), item["id"]
    assert 8 <= len(seed["products"]) <= 15 and 4 <= len(seed["categories"]) <= 6
    assert 2 <= len(seed["campaigns"]) <= 3


def test_status_da_versao_vem_do_front_matter():
    g = loader.build_graph(ROOT)
    pv = {n["id"]: n["props"] for n in g["nodes"] if n["label"] == "PolicyVersion"}
    assert pv["politica-trocas@v2"]["status"] == "obsolete"
    assert pv["politica-trocas@v3"]["status"] == "active"
    assert pv["campanha-black-friday@v1"]["effective_date"] == "2027-11-22"
    assert pv["parceiro-x-trocas@v1"]["tenant"] == "parceiro-x"
    assert pv["manual-interno-reembolso@v1"]["access_level"] == "internal"
    sup = [e for e in g["edges"] if e["type"] == "SUPERSEDES"]
    assert [(e["source"], e["target"]) for e in sup] == [("politica-trocas@v3", "politica-trocas@v2")]


def test_toda_aresta_tem_provenance():
    g = loader.build_graph(ROOT)
    assert g["edges"] and all(e["props"].get("provenance") for e in g["edges"])


def test_seed_quebrado_e_reprovado():
    seed = loader.load_seed(ROOT)
    seed["products"][0]["category"] = "categoria-que-nao-existe"
    seed["policies"][0]["status"] = "active"
    errors = loader.validate(ROOT, seed)
    assert any("categoria inexistente" in e for e in errors)
    assert any("front matter" in e for e in errors)


def test_graph_seed_version_deterministico():
    assert loader.graph_seed_version(ROOT) == loader.graph_seed_version(ROOT)


# --------------------------------------------------------------- Neo4j real
@pytest.fixture(scope="module")
def engine():
    if not NEO4J_URI:
        pytest.skip("QC_NEO4J_URI nao definido: testes de grafo exigem Neo4j real")
    from lab.engine import Engine
    with open(os.path.join(ROOT, "orders", "fixtures.json"), encoding="utf-8") as f:
        orders = InMemoryOrders(json.load(f))
    eng = Engine(NEO4J_URI, os.environ.get("QC_NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""),
                 orders=orders)
    eng.bootstrap(timeout_s=120)
    yield eng
    eng.apply_scenario("stale_graph_policy", "restore")
    eng.close()


def graph(engine, question, as_of, **kw):
    return engine.compare(question, as_of, strategies=("graph",), **kw)["strategies"]["graph"]


def selected_edges(entry):
    return [e for e in entry["path"]["edges"] if e["role"] == "selected"]


@pytest.mark.neo4j
@needs_neo4j
@pytest.mark.parametrize("question", [FONE, FONE_HEADPHONE])
def test_caso1_fone_regular_v3_30_dias(engine, question):
    g = graph(engine, question, REGULAR)
    assert not g["abstained"]
    assert "30 dias" in g["answer"] and "15 dias" not in g["answer"]
    assert g["selected_policy_version"] == "politica-trocas@v3"
    assert [c["chunk_id"] for c in g["citations"]] == ["politica-trocas@v3#prazo-para-troca"]
    semantic = [e for e in selected_edges(g) if e["type"] in SEMANTIC]
    assert len(semantic) >= 2
    assert {"doc_id": "campanha-black-friday", "version": "1", "reason": "future"} in g["blocked"]
    assert g["cypher_query_id"] == "graph.policy_for_product.v1"
    assert "10 de janeiro" not in g["answer"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso2_black_friday_so_para_produto_participante(engine):
    g = graph(engine, FONE_BF, BF)
    assert not g["abstained"] and "10 de janeiro" in g["answer"]
    assert g["selected_policy_version"] == "campanha-black-friday@v1"
    assert ("fone-bluetooth-qc", "PARTICIPATES_IN", "black-friday-2027") in {
        (e["source"], e["type"], e["target"]) for e in selected_edges(g)}
    # Mochila nao participa da BF2027: continua na regra geral (sem generalizar o prazo especial).
    m = graph(engine, MOCHILA_BF, BF)
    assert m["selected_policy_version"] == "politica-trocas@v3"
    assert "30 dias" in m["answer"] and "10 de janeiro" not in m["answer"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso2b_arrependimento_nao_e_troca(engine):
    g = graph(engine, "Me arrependi de comprar o fone, posso desistir?", BF)
    assert g["selected_policy_version"] == "politica-devolucoes@v4"
    assert "7 dias" in g["answer"] and "10 de janeiro" not in g["answer"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso3_black_friday_expirada_volta_v3(engine):
    g = graph(engine, FONE_BF, AFTER_BF)
    assert g["selected_policy_version"] == "politica-trocas@v3"
    assert "30 dias" in g["answer"] and "10 de janeiro" not in g["answer"]
    assert {"doc_id": "campanha-black-friday", "version": "1", "reason": "expired"} in g["blocked"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso4_v2_nunca_compoe(engine):
    for q, d in ((FONE, REGULAR), ("O prazo de troca do fone ainda é de 15 dias?", REGULAR), (FONE_BF, AFTER_BF)):
        r = engine.compare(q, d)
        for name, s in r["strategies"].items():
            assert all(c["version"] != "2" or c["doc_id"] != "politica-trocas" for c in s["citations"]), name
            assert "15 dias" not in (s["answer"] or ""), name
        g = r["strategies"]["graph"]
        assert any(b["doc_id"] == "politica-trocas" and b["version"] == "2" and b["reason"] in ("status", "superseded")
                   for b in g["blocked"])
        # blocked[] nunca carrega texto.
        assert all(set(b) == {"doc_id", "version", "reason"} for s in r["strategies"].values() for b in s["blocked"])


@pytest.mark.neo4j
@needs_neo4j
def test_caso5_parceiro_x_nunca_aparece_para_quantum(engine):
    for q in ("Comprei um relogio analogico no Parceiro X, o prazo de troca é de 60 dias?",
              "Comprei no Parceiro X, o prazo de troca é de 60 dias?", "Qual o prazo para trocar uma mochila?"):
        r = engine.compare(q, REGULAR, tenant="quantum")
        for name, s in r["strategies"].items():
            assert "60 dias" not in (s["answer"] or ""), name
            assert all(c["doc_id"] != "parceiro-x-trocas" for c in s["citations"]), name
            assert all(x["doc_id"] != "parceiro-x-trocas" for x in s["results"]), name
            if s.get("path"):
                assert all(n["id"] not in ("parceiro-x-trocas@v1", "parceiro-x-trocas.md", "relogio-parceiro-x")
                           or n["role"] == "blocked" for n in s["path"]["nodes"]), name
    # Produto de outro tenant: a travessia nem comeca (abstem, sem expor nada).
    g = graph(engine, "Comprei um relogio analogico, posso trocar?", REGULAR, tenant="quantum")
    assert g["abstained"] and g["abstain_reason"] == "entity_not_in_tenant" and g["path"]["nodes"] == []


@pytest.mark.neo4j
@needs_neo4j
def test_caso6_manual_interno_recusa_para_customer(engine):
    q = "Qual a alçada do atendente para aprovar estorno sem frete reverso do notebook?"
    r = engine.compare(q, REGULAR, user_role="customer")
    for name, s in r["strategies"].items():
        assert "R$ 200" not in (s["answer"] or ""), name
        assert all(c["doc_id"] != "manual-interno-reembolso" for c in s["citations"]), name
    g = r["strategies"]["graph"]
    assert g["abstained"] and {"doc_id": "manual-interno-reembolso", "version": "1", "reason": "access_level"} in g["blocked"]
    agent = graph(engine, q, REGULAR, user_role="agent")
    assert agent["selected_policy_version"] == "manual-interno-reembolso@v1" and "R$ 200" in agent["answer"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso7_multi_hop_real_e_remove_campaign_edge(engine):
    g = graph(engine, FONE_BF, BF)
    chain = [(e["source"], e["type"], e["target"]) for e in selected_edges(g)]
    # Produto -> Campanha -> Politica -> Versao -> Documento -> Trecho, devolvido pelo Neo4j.
    for edge in (("fone-bluetooth-qc", "PARTICIPATES_IN", "black-friday-2027"),
                 ("campanha-black-friday", "APPLIES_TO", "black-friday-2027"),
                 ("campanha-black-friday", "HAS_VERSION", "campanha-black-friday@v1"),
                 ("campanha-black-friday@v1", "DOCUMENTED_BY", "black-friday-2027.md"),
                 ("black-friday-2027.md", "CONTAINS", "campanha-black-friday@v1#trocas-na-black-friday")):
        assert edge in chain
    assert all(e["props"].get("provenance") for e in selected_edges(g))
    # Categoria inteira participando: notebook chega na campanha pela CATEGORIA (4 saltos semanticos).
    nb = engine.graph_for_product("notebook-qc14", BF)
    assert nb["policy_version"] == "campanha-black-friday@v1"
    assert ("informatica", "PARTICIPATES_IN", "black-friday-2027") in {
        (e["source"], e["type"], e["target"]) for e in nb["edges"] if e["role"] == "selected"}
    before = engine.admin_state()["graph_version"]
    try:
        st = engine.apply_scenario("remove_campaign_edge", "apply")
        assert st["active_mutations"] == ["remove_campaign_edge"] and st["graph_version"] != before
        g2 = graph(engine, FONE_BF, BF)
        assert g2["selected_policy_version"] == "politica-trocas@v3"
        assert ("fone-bluetooth-qc", "PARTICIPATES_IN", "black-friday-2027") not in {
            (e["source"], e["type"], e["target"]) for e in g2["path"]["edges"]}
        assert engine.apply_scenario("remove_campaign_edge", "apply")["graph_version"] == st["graph_version"]
    finally:
        back = engine.apply_scenario("remove_campaign_edge", "restore")
    assert back["graph_version"] == before == back["graph_seed_version"]
    assert graph(engine, FONE_BF, BF)["selected_policy_version"] == "campanha-black-friday@v1"


@pytest.mark.neo4j
@needs_neo4j
def test_caso8_pedido_so_via_orders_client(engine):
    ok = engine.compare("Qual o status do pedido Q-1001?", REGULAR, auth_token="lab-token-ana")
    assert ok["route"] == "order_status" and ok["order"]["status"] == "em_transito"
    for s in ok["strategies"].values():
        assert s["source"] == "orders-api" and s["path"] is None and "em_transito" in s["answer"]
    other = engine.compare("Qual o status do pedido Q-2001?", REGULAR, auth_token="lab-token-ana")
    assert other["order"] is None
    assert all(s["abstained"] and s["abstain_reason"] == "forbidden" for s in other["strategies"].values())
    none = engine.compare("Qual o status do pedido Q-1001?", REGULAR)
    assert all(s["abstain_reason"] == "forbidden" for s in none["strategies"].values())
    # O grafo nao tem status de pedido indexado.
    dumped = engine._neo4j(loader.dump)
    assert not any("em_transito" in json.dumps(n["props"]) and n["label"] != "Chunk" for n in dumped["nodes"])


@pytest.mark.neo4j
@needs_neo4j
def test_caso9_cashback_fora_de_escopo_e_injecao(engine):
    r = engine.compare("Quantos pontos de cashback eu ganho em cada compra?", REGULAR, strategies=("bm25", "graph"))
    assert all(s["abstained"] for s in r["strategies"].values())
    assert r["strategies"]["graph"]["abstain_reason"] == "entity_not_found"
    fora = engine.compare("Qual a previsão do tempo para amanhã em São Paulo?", REGULAR)
    assert fora["route"] == "unsupported"
    assert all(s["abstain_reason"] == "out_of_scope" for s in fora["strategies"].values())
    inj = engine.compare("Existe algum cupom especial de atendimento?", REGULAR)
    for s in inj["strategies"].values():
        assert "QC-LIBERA100" not in (s["answer"] or "") and s["abstained"]
    boleto = engine.compare("O boleto bancário ainda é aceito como forma de pagamento?", REGULAR, strategies=("bm25",))
    assert "descontinuado" not in boleto["strategies"]["bm25"]["answer"]


@pytest.mark.neo4j
@needs_neo4j
def test_caso10_loader_idempotente_e_incidente_stale(engine):
    a = engine.bootstrap()
    b = engine.bootstrap()
    assert a["counts"] == b["counts"] == loader.counts_from(engine.expected_graph)
    assert a["graph_version"] == b["graph_version"] == engine.seed_version
    assert a["index"] == {"name": "chunk_embedding", "dim": 384, "state": "ONLINE"}
    dup = engine._neo4j(lambda s: s.run(
        "MATCH (n) WITH labels(n)[0] AS l, n.id AS id, count(*) AS c WHERE c > 1 RETURN count(*) AS d").single()["d"])
    dup_edges = engine._neo4j(lambda s: s.run(
        "MATCH (a)-[r]->(b) WITH a, b, type(r) AS t, count(*) AS c WHERE c > 1 RETURN count(*) AS d").single()["d"])
    assert dup == 0 and dup_edges == 0
    try:
        st = engine.apply_scenario("stale_graph_policy", "apply")
        assert st["active_mutations"] == ["stale_graph_policy"] and not st["in_sync"]
        g = graph(engine, FONE, REGULAR)
        # HTTP 200 com resposta errada: e o incidente que a 5.2 precisa detectar.
        assert not g["abstained"] and "15 dias" in g["answer"]
        assert g["selected_policy_version"] == "politica-trocas@v2"
        assert engine.apply_scenario("stale_graph_policy", "apply")["graph_version"] == st["graph_version"]
    finally:
        back = engine.apply_scenario("stale_graph_policy", "restore")
    assert back["in_sync"] and back["graph_version"] == back["graph_seed_version"] == engine.seed_version
    g = graph(engine, FONE, REGULAR)
    assert "30 dias" in g["answer"] and g["selected_policy_version"] == "politica-trocas@v3"


@pytest.mark.neo4j
@needs_neo4j
def test_entrada_invalida_e_versions(engine):
    from lab.engine import InvalidInput
    for kwargs in ({"as_of_date": "14/11/2026"}, {"as_of_date": "2026-02-30"}, {"tenant": "x' OR 1=1"},
                   {"user_role": "root"}, {"strategies": ("cypher",)}):
        args = {"as_of_date": REGULAR, **kwargs}
        with pytest.raises(InvalidInput):
            engine.compare(FONE, **args)
    with pytest.raises(InvalidInput):
        engine.graph_for_product("fone') MATCH (n) DETACH DELETE n //", REGULAR)
    v = engine.versions()
    for key in ("application_version", "prompt_version", "corpus_version", "index_version", "vector_index_version",
                "graph_version", "graph_seed_version", "embedding_model_id", "embedding_model_revision",
                "embedding_dim", "eval_set_version", "model_version", "git_sha", "image_digest", "release_id"):
        assert key in v
    assert v["model_version"] == "none" and v["embedding_dim"] == 384


@pytest.mark.neo4j
@needs_neo4j
def test_legado_bm25_inalterado(engine):
    from core.assistant import Assistant
    legacy = Assistant({"as_of_date": REGULAR}).answer("Qual o prazo para trocar uma roupa que comprei?")
    new = engine.ask("Qual o prazo para trocar uma roupa que comprei?", strategy="bm25", as_of_date=REGULAR)
    assert new["answer"] == legacy["answer"] and new["citations"] == legacy["citations"]
    assert new["strategy"] == "bm25"
    cmp = engine.compare("Qual o prazo para trocar uma roupa que comprei?", REGULAR, strategies=("bm25",))
    assert cmp["strategies"]["bm25"]["answer"] == legacy["answer"]
    assert [r["chunk_id"] for r in cmp["strategies"]["bm25"]["results"]] == [r["chunk_id"] for r in legacy["retrieval"]]


# ------------------------------------------------- secao certa, nao so o doc
# A pergunta do lab e "a fonte se aplica?": citar politica-trocas@v3 com a
# secao "Troca de presente" ou citar a BF com a secao de ENTREGA e erro, mesmo
# com doc_id e versao certos. Por isso a assercao e pelo chunk_id exato.
REGULAR_PHRASES = [
    "Comprei um fone de ouvido, qual o prazo para trocar?",
    "Qual o prazo para trocar um fone de ouvido?",
    "Em quantos dias posso trocar meu fone?",
    "Até quando consigo trocar o fone bluetooth que comprei?",
    "Posso trocar o headphone? Qual o prazo?",
    "Quero trocar meu fone de ouvido por outra cor, tenho quanto tempo?",
]
BF_PHRASES = [
    "Comprei um fone de ouvido na Black Friday, até quando posso trocar?",
    "Qual o prazo para trocar um fone comprado na Black Friday?",
    "Fone de ouvido da Black Friday: até quando dá para trocar?",
    "Posso trocar o headphone que comprei na black friday?",
    "Comprei um fone bluetooth na BF 2027, qual o prazo de troca?",
    "Quero trocar o fone de ouvido comprado na Black Friday, tenho até quando?",
]


@pytest.mark.neo4j
@needs_neo4j
@pytest.mark.parametrize("question", REGULAR_PHRASES)
def test_secao_fone_regular_e_prazo_para_troca(engine, question):
    g = graph(engine, question, REGULAR)
    assert not g["abstained"], g.get("fallback")
    assert g["citations"][0]["chunk_id"] == "politica-trocas@v3#prazo-para-troca"
    assert g["citations"][0]["section"] == "Prazo para troca"
    assert "30 dias" in g["answer"]
    assert "presente" not in g["answer"].split("Fonte:")[0].lower()


@pytest.mark.neo4j
@needs_neo4j
@pytest.mark.parametrize("question", BF_PHRASES)
def test_secao_fone_black_friday_e_trocas_na_black_friday(engine, question):
    g = graph(engine, question, BF)
    assert not g["abstained"], g.get("fallback")
    assert g["citations"][0]["chunk_id"] == "campanha-black-friday@v1#trocas-na-black-friday"
    assert g["citations"][0]["section"] == "Trocas na Black Friday"
    assert "10 de janeiro" in g["answer"] and "dias úteis" not in g["answer"]


@pytest.mark.neo4j
@needs_neo4j
@pytest.mark.parametrize("question", BF_PHRASES)
def test_secao_fone_depois_da_black_friday_volta_prazo_para_troca(engine, question):
    g = graph(engine, question, AFTER_BF)
    assert g["citations"][0]["chunk_id"] == "politica-trocas@v3#prazo-para-troca"
    assert "10 de janeiro" not in g["answer"]


@pytest.mark.neo4j
@needs_neo4j
@pytest.mark.parametrize("question", REGULAR_PHRASES + BF_PHRASES)
def test_bm25_e_vector_nunca_citam_versao_bloqueada(engine, question):
    # BM25 e vetor podem citar outra politica (similaridade nao e aplicabilidade:
    # e o contraste da aula), mas nunca uma versao bloqueada pelo filtro.
    for as_of in (REGULAR, BF, AFTER_BF):
        r = engine.compare(question, as_of, strategies=("bm25", "vector"))
        for name, s in r["strategies"].items():
            for c in s["citations"]:
                assert c["chunk_id"] != "politica-trocas@v2#prazo-para-troca", name
                assert c["doc_id"] != "parceiro-x-trocas", name
                if as_of != BF:
                    assert c["doc_id"] != "campanha-black-friday", (name, as_of)
