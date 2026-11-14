"""Monta o grafo a partir do seed + front matter do corpus e sincroniza no Neo4j.

Por que duas fontes: o corpus e a fonte unica de status, vigencia, tenant e
access_level de cada versao de politica; o seed so acrescenta o que o corpus
nao sabe (produtos, categorias, campanhas, a quem cada politica se aplica).
Assim ninguem precisa lembrar de atualizar o status em dois lugares.

build_graph() e Python puro (sem Neo4j): serve para o gate de CI validar o seed
e para calcular graph_seed_version, o hash do grafo ESPERADO. sync() grava no
Neo4j com MERGE (idempotente) e apaga o que sobrar: rodar duas vezes da o mesmo
grafo, e rodar depois de uma mutacao em runtime devolve o grafo da fonte.
"""
import hashlib
import json
import os
import re

from core.corpus import load_chunks, load_documents

LABELS = ("Tenant", "Category", "Product", "Campaign", "Policy", "PolicyVersion", "Document", "Chunk")
EDGE_TYPES = ("BELONGS_TO", "IN_CATEGORY", "PARTICIPATES_IN", "APPLIES_TO", "HAS_VERSION",
              "SUPERSEDES", "DOCUMENTED_BY", "CONTAINS")
# Campos que so podem vir do front matter: se aparecerem no seed, alguem esta
# duplicando a fonte de verdade e as duas copias vao divergir um dia.
FRONT_MATTER_ONLY = ("status", "effective_date", "expires_date", "access_level")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEED_FILE = "catalog.json"


def seed_path(root):
    return os.path.join(root, "graph", "seed", SEED_FILE)


def load_seed(root):
    with open(seed_path(root), encoding="utf-8") as f:
        return json.load(f)


def version_id(doc_id, version):
    return f"{doc_id}@v{version}"


def _clean(props):
    # O Neo4j nao guarda propriedade nula; tirar aqui deixa o hash do grafo
    # esperado igual ao hash do grafo lido do banco.
    return {k: v for k, v in props.items() if v is not None and v != "" and v != []}


def build_graph(root, seed=None):
    """Retorna {"nodes": [{label,id,props}], "edges": [{type,source,target,source_label,target_label,props}]}."""
    seed = seed or load_seed(root)
    corpus_dir = os.path.join(root, "corpus")
    docs = load_documents(corpus_dir)
    chunks = load_chunks(corpus_dir)
    seed_ref = f"seed:graph/seed/{SEED_FILE}"
    nodes, edges = [], []

    def node(label, nid, **props):
        nodes.append({"label": label, "id": nid, "props": _clean({"id": nid, **props})})

    def edge(etype, src, src_label, dst, dst_label, provenance, **props):
        edges.append({"type": etype, "source": src, "target": dst, "source_label": src_label,
                      "target_label": dst_label, "props": _clean({"provenance": provenance, **props})})

    for t in seed["tenants"]:
        node("Tenant", t["id"], name=t["name"])
    for c in seed["categories"]:
        node("Category", c["id"], name=c["name"], family=c.get("family"), synonyms=c.get("synonyms", []))
    for p in seed["products"]:
        node("Product", p["id"], name=p["name"], tenant=p["tenant"], synonyms=p.get("synonyms", []))
        edge("BELONGS_TO", p["id"], "Product", p["tenant"], "Tenant", f"{seed_ref}#products[{p['id']}].tenant")
        edge("IN_CATEGORY", p["id"], "Product", p["category"], "Category", f"{seed_ref}#products[{p['id']}].category")

    by_doc = {}
    for meta, _body in docs:
        by_doc.setdefault(meta["doc_id"], []).append(meta)

    for cmp in seed["campaigns"]:
        start, end, window = cmp.get("start_date"), cmp.get("end_date"), f"{seed_ref}#campaigns[{cmp['id']}]"
        if cmp.get("window_from_doc"):
            # A janela da campanha e a vigencia do documento dela: um lugar so.
            latest = max(by_doc[cmp["window_from_doc"]], key=lambda m: int(m["version"]))
            start, end = latest["effective_date"], latest.get("expires_date")
            window = f"corpus:{latest['file']}#front_matter.effective_date/expires_date"
        node("Campaign", cmp["id"], name=cmp["name"], tenant=cmp["tenant"], start_date=start, end_date=end,
             window_source=window, synonyms=cmp.get("synonyms", []))
        edge("BELONGS_TO", cmp["id"], "Campaign", cmp["tenant"], "Tenant", f"{seed_ref}#campaigns[{cmp['id']}].tenant")
        for cat in cmp["participants"].get("categories", []):
            edge("PARTICIPATES_IN", cat, "Category", cmp["id"], "Campaign",
                 f"{seed_ref}#campaigns[{cmp['id']}].participants.categories", evidence=cmp.get("evidence"))
        for prod in cmp["participants"].get("products", []):
            edge("PARTICIPATES_IN", prod, "Product", cmp["id"], "Campaign",
                 f"{seed_ref}#campaigns[{cmp['id']}].participants.products", evidence=cmp.get("evidence"))

    for pol in seed["policies"]:
        versions_meta = by_doc.get(pol["doc_id"], [])
        tenant = max(versions_meta, key=lambda m: int(m["version"]))["tenant"] if versions_meta else None
        node("Policy", pol["id"], doc_id=pol["doc_id"], tenant=tenant, topics=pol.get("topics", []))
        for cat in pol["applies_to"].get("categories", []):
            edge("APPLIES_TO", pol["id"], "Policy", cat, "Category",
                 f"{seed_ref}#policies[{pol['id']}].applies_to.categories", evidence=pol.get("evidence"))
        for cmp_id in pol["applies_to"].get("campaigns", []):
            edge("APPLIES_TO", pol["id"], "Policy", cmp_id, "Campaign",
                 f"{seed_ref}#policies[{pol['id']}].applies_to.campaigns", evidence=pol.get("evidence"))

    policy_ids = {p["doc_id"]: p["id"] for p in seed["policies"]}
    for meta, _body in docs:
        vid = version_id(meta["doc_id"], meta["version"])
        fm = f"corpus:{meta['file']}#front_matter"
        node("PolicyVersion", vid, doc_id=meta["doc_id"], version=meta["version"], title=meta["title"],
             status=meta["status"], effective_date=meta["effective_date"], expires_date=meta.get("expires_date"),
             tenant=meta["tenant"], access_level=meta["access_level"], category=meta.get("category", "policy"),
             file=meta["file"], status_source=fm)
        node("Document", meta["file"], doc_id=meta["doc_id"], version=meta["version"], title=meta["title"],
             file=meta["file"])
        if meta["doc_id"] in policy_ids:
            edge("HAS_VERSION", policy_ids[meta["doc_id"]], "Policy", vid, "PolicyVersion", f"{fm}.doc_id+version")
        edge("DOCUMENTED_BY", vid, "PolicyVersion", meta["file"], "Document", f"{fm}.version")
        if meta.get("supersedes"):
            # Direcao: a versao NOVA aponta para a que ela substitui (v3 -> v2).
            edge("SUPERSEDES", vid, "PolicyVersion", meta["supersedes"], "PolicyVersion", f"{fm}.supersedes")

    # Secao que escreve a REGRA de cada topico (ex.: "Prazo para troca"): vem
    # do seed e vira propriedade do trecho, para o grafo citar a regra e nao
    # um trecho vizinho que so repete palavras da pergunta.
    rule_topics = {}
    for pol in seed["policies"]:
        for topic, section in pol.get("rule_sections", {}).items():
            rule_topics.setdefault((pol["doc_id"], section), []).append(topic)
    for ch in chunks:
        node("Chunk", ch["chunk_id"], rule_topics=sorted(rule_topics.get((ch["doc_id"], ch["section"]), [])), doc_id=ch["doc_id"], version=ch["version"], title=ch["title"],
             section=ch["section"], text=ch["text"], category=ch["category"], file=ch["file"],
             # Copia dos metadados de acesso do front matter: a estrategia vetorial
             # aplica check_access direto no chunk que o indice devolveu.
             status=ch["status"], effective_date=ch["effective_date"], expires_date=ch["expires_date"],
             tenant=ch["tenant"], access_level=ch["access_level"])
        edge("CONTAINS", ch["file"], "Document", ch["chunk_id"], "Chunk",
             f"corpus:{ch['file']}#secao:{ch['section']}")
    return {"nodes": nodes, "edges": edges}


def validate(root, seed=None):
    """Erros estruturais do seed (lista vazia = ok). Nao precisa de Neo4j.

    So reprova o que impede montar o grafo (referencia quebrada, id invalido,
    campo do front matter duplicado no seed). Inconsistencia semantica entre
    versoes (SUPERSEDES invertido, duas versoes ativas) e papel do gate de CI.
    """
    errors = []
    try:
        seed = seed or load_seed(root)
    except (OSError, ValueError) as err:
        return [f"seed ilegivel: {err}"]
    try:
        docs = load_documents(os.path.join(root, "corpus"))
    except (OSError, ValueError) as err:
        return [f"corpus ilegivel: {err}"]
    doc_ids = {m["doc_id"] for m, _ in docs}
    ids = {}
    for kind in ("tenants", "categories", "products", "campaigns", "policies"):
        ids[kind] = set()
        for item in seed.get(kind, []):
            iid = item.get("id", "")
            if not ID_RE.match(iid):
                errors.append(f"{kind}: id invalido {iid!r}")
            if iid in ids[kind]:
                errors.append(f"{kind}: id duplicado {iid!r}")
            ids[kind].add(iid)
            dup = [k for k in FRONT_MATTER_ONLY if k in item]
            if dup:
                errors.append(f"{kind}[{iid}]: {dup} pertencem ao front matter do corpus, nao ao seed")
    for p in seed.get("products", []):
        if p.get("tenant") not in ids["tenants"]:
            errors.append(f"products[{p['id']}]: tenant inexistente {p.get('tenant')!r}")
        if p.get("category") not in ids["categories"]:
            errors.append(f"products[{p['id']}]: categoria inexistente {p.get('category')!r}")
    for c in seed.get("campaigns", []):
        if c.get("tenant") not in ids["tenants"]:
            errors.append(f"campaigns[{c['id']}]: tenant inexistente")
        if c.get("window_from_doc") and c["window_from_doc"] not in doc_ids:
            errors.append(f"campaigns[{c['id']}]: window_from_doc sem documento no corpus")
        if not c.get("window_from_doc") and not (c.get("start_date") and c.get("end_date")):
            errors.append(f"campaigns[{c['id']}]: sem janela (start_date/end_date ou window_from_doc)")
        parts = c.get("participants", {})
        for cat in parts.get("categories", []):
            if cat not in ids["categories"]:
                errors.append(f"campaigns[{c['id']}]: categoria participante inexistente {cat!r}")
        for prod in parts.get("products", []):
            if prod not in ids["products"]:
                errors.append(f"campaigns[{c['id']}]: produto participante inexistente {prod!r}")
    topic_ids = {t["id"] for t in seed.get("topics", [])}
    sections = {}
    for meta, body in docs:
        sections.setdefault(meta["doc_id"], set()).update(
            line[3:].strip() for line in body.splitlines() if line.startswith("## "))
    for pol in seed.get("policies", []):
        if pol.get("doc_id") not in doc_ids:
            errors.append(f"policies[{pol['id']}]: doc_id sem documento no corpus")
        for topic, section in pol.get("rule_sections", {}).items():
            if topic not in pol.get("topics", []):
                errors.append(f"policies[{pol['id']}]: rule_sections usa topico fora de topics {topic!r}")
            if section not in sections.get(pol.get("doc_id"), set()):
                errors.append(f"policies[{pol['id']}]: secao {section!r} nao existe no documento")
        for t in pol.get("topics", []):
            if t not in topic_ids:
                errors.append(f"policies[{pol['id']}]: topico inexistente {t!r}")
        for cat in pol.get("applies_to", {}).get("categories", []):
            if cat not in ids["categories"]:
                errors.append(f"policies[{pol['id']}]: applies_to categoria inexistente {cat!r}")
        for cmp in pol.get("applies_to", {}).get("campaigns", []):
            if cmp not in ids["campaigns"]:
                errors.append(f"policies[{pol['id']}]: applies_to campanha inexistente {cmp!r}")
    if not errors:
        graph = build_graph(root, seed)
        node_ids = {(n["label"], n["id"]) for n in graph["nodes"]}
        for e in graph["edges"]:
            for side, label in (("source", "source_label"), ("target", "target_label")):
                if (e[label], e[side]) not in node_ids:
                    errors.append(f"aresta {e['type']} aponta para no inexistente {e[label]}:{e[side]}")
    return errors


def canonical_hash(nodes, edges):
    """sha256[:12] do conteudo do grafo (nos + arestas + propriedades, ordenados).

    O mesmo calculo serve para o grafo esperado (graph_seed_version) e para o
    grafo lido do Neo4j em runtime (graph_version): se forem iguais, o banco
    esta exatamente como a fonte manda. O embedding fica de fora (tem versao
    propria, vector_index_version).
    """
    norm_nodes = sorted(
        [n["label"], n["id"], sorted((k, v) for k, v in n["props"].items() if k != "embedding")]
        for n in nodes)
    norm_edges = sorted(
        [e["type"], e["source"], e["target"], sorted(e["props"].items())] for e in edges)
    payload = json.dumps({"nodes": norm_nodes, "edges": norm_edges}, sort_keys=True, ensure_ascii=False,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def graph_seed_version(root, graph=None):
    graph = graph or build_graph(root)
    return canonical_hash(graph["nodes"], graph["edges"])


# ------------------------------------------------------------------ Neo4j
def apply_schema(session, root):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.cypher"), encoding="utf-8") as f:
        statements = [s.strip() for s in re.sub(r"//[^\n]*", "", f.read()).split(";") if s.strip()]
    for stmt in statements:
        session.run(stmt).consume()


def sync(session, graph):
    """Grava o grafo esperado: MERGE do que falta e DELETE do que sobra.

    Rotulos e tipos de aresta entram no texto do Cypher porque o Cypher nao
    aceita rotulo como parametro; eles vem da lista fixa LABELS/EDGE_TYPES,
    nunca de entrada do usuario. Valores sempre vao como parametro.
    """
    by_label = {}
    for n in graph["nodes"]:
        by_label.setdefault(n["label"], []).append({"id": n["id"], "props": n["props"]})
    for label in LABELS:
        rows = by_label.get(label, [])
        # SET n = props troca TODAS as propriedades (desfaz mutacao em runtime);
        # o embedding do chunk e preservado porque nao vem do seed.
        session.run(
            f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
            "WITH n, row, n.embedding AS emb SET n = row.props SET n.embedding = emb",
            rows=rows).consume()
        session.run(f"MATCH (n:{label}) WHERE NOT n.id IN $ids DETACH DELETE n",
                    ids=[r["id"] for r in rows]).consume()
    by_type = {}
    for e in graph["edges"]:
        by_type.setdefault((e["type"], e["source_label"], e["target_label"]), []).append(
            {"source": e["source"], "target": e["target"], "props": e["props"]})
    expected = {}
    for (etype, sl, tl), rows in by_type.items():
        assert etype in EDGE_TYPES and sl in LABELS and tl in LABELS
        session.run(
            f"UNWIND $rows AS row MATCH (a:{sl} {{id: row.source}}) MATCH (b:{tl} {{id: row.target}}) "
            f"MERGE (a)-[r:{etype}]->(b) SET r = row.props",
            rows=rows).consume()
        expected.setdefault(etype, set()).update((r["source"], r["target"]) for r in rows)
    for etype in EDGE_TYPES:
        pairs = [[s, t] for s, t in sorted(expected.get(etype, set()))]
        session.run(
            f"MATCH (a)-[r:{etype}]->(b) WHERE NOT [a.id, b.id] IN $pairs DELETE r", pairs=pairs).consume()


def dump(session):
    """Le o grafo do banco no mesmo formato de build_graph (sem embedding)."""
    nodes = [
        {"label": rec["label"], "id": rec["id"], "props": rec["props"]}
        for rec in session.run(
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
            "RETURN labels(n)[0] AS label, n.id AS id, properties(n) AS props", labels=list(LABELS))
    ]
    for n in nodes:
        n["props"].pop("embedding", None)
    edges = [
        {"type": rec["type"], "source": rec["source"], "target": rec["target"], "props": rec["props"]}
        for rec in session.run(
            "MATCH (a)-[r]->(b) WHERE type(r) IN $types "
            "RETURN type(r) AS type, a.id AS source, b.id AS target, properties(r) AS props",
            types=list(EDGE_TYPES))
    ]
    return {"nodes": nodes, "edges": edges}


def counts(session):
    rec = session.run(
        "CALL () { MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN count(n) AS nodes } "
        "CALL () { MATCH ()-[r]->() WHERE type(r) IN $types RETURN count(r) AS edges } "
        "CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks } "
        "RETURN nodes, edges, chunks", labels=list(LABELS), types=list(EDGE_TYPES)).single()
    return {"nodes": rec["nodes"], "edges": rec["edges"], "chunks": rec["chunks"]}


def counts_from(graph):
    return {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
            "chunks": sum(1 for n in graph["nodes"] if n["label"] == "Chunk")}
