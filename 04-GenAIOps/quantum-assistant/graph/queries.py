"""Consultas Cypher da recuperacao guiada por grafo (todas parametrizadas).

Regras de seguranca:
- Nenhum texto do usuario entra no Cypher: produto, categoria, tenant e topico
  chegam como PARAMETRO, depois de passar pelo entity linking (lista fechada).
- Profundidade fixa (padroes explicitos, no maximo 7 arestas), LIMIT em toda
  consulta e timeout de transacao: uma consulta nao pode varrer o banco.
- Cada consulta tem um id versionado (ex.: graph.policy_for_product.v1) que
  aparece na resposta e no trace: da para saber exatamente qual Cypher rodou.
- A consulta NAO decide autorizacao: ela traz os candidatos com os metadados
  da PolicyVersion lidos do grafo, e a regra unica core.retrieval.check_access
  decide em Python, antes de qualquer texto ir para o composer.
"""
import re

from neo4j import Query

QUERY_TIMEOUT_S = 3.0
MAX_ROWS = 50

# Propriedades que nunca saem no caminho: o texto so e lido para a versao
# escolhida (depois do filtro) e o embedding nao interessa ao aluno.
_NODE_MAP = "{id: n.id, label: labels(n)[0], props: n {.*, text: null, embedding: null, synonyms: null}}"
_EDGE_MAP = "{type: type(r), source: startNode(r).id, target: endNode(r).id, props: properties(r)}"

_TAIL = (
    "(pol:Policy)-[:HAS_VERSION]->(pv:PolicyVersion)-[:DOCUMENTED_BY]->(d:Document) "
    "WHERE $topic IN pol.topics"
)

POLICY_FOR_PRODUCT = {
    "id": "graph.policy_for_product.v1",
    "cypher": f"""
MATCH tp = (p:Product {{id: $product_id}})-[:BELONGS_TO]->(:Tenant {{id: $tenant}})
CALL (p) {{
  MATCH path = (p)-[:IN_CATEGORY]->(:Category)<-[:APPLIES_TO]-{_TAIL}
  RETURN path, pv, d, 'category' AS via, null AS cmp
  UNION
  MATCH path = (p)-[:PARTICIPATES_IN]->(cmp:Campaign)<-[:APPLIES_TO]-{_TAIL}
  RETURN path, pv, d, 'campaign' AS via, cmp
  UNION
  MATCH path = (p)-[:IN_CATEGORY]->(:Category)-[:PARTICIPATES_IN]->(cmp:Campaign)<-[:APPLIES_TO]-{_TAIL}
  RETURN path, pv, d, 'category-campaign' AS via, cmp
}}
OPTIONAL MATCH (newer:PolicyVersion)-[s:SUPERSEDES]->(pv)
WITH tp, path, pv, d, via, cmp, collect(newer {{.id, .status}}) AS superseded_by,
     collect(CASE WHEN s IS NULL THEN null ELSE {{type: type(s), source: newer.id, target: pv.id,
             props: properties(s), newer: newer {{.*}}}} END) AS supersedes_edges
RETURN via, properties(pv) AS version, d.id AS document, cmp {{.*, synonyms: null}} AS campaign,
       superseded_by, supersedes_edges, length(path) + 1 AS hops,
       [n IN nodes(tp) + nodes(path) | {_NODE_MAP}] AS nodes,
       [r IN relationships(tp) + relationships(path) | {_EDGE_MAP}] AS edges
ORDER BY via, version.id
LIMIT {MAX_ROWS}
""",
}

POLICY_FOR_CATEGORY = {
    "id": "graph.policy_for_category.v1",
    "cypher": f"""
MATCH (c:Category {{id: $category_id}})
CALL (c) {{
  MATCH path = (c)<-[:APPLIES_TO]-{_TAIL}
  RETURN path, pv, d, 'category' AS via, null AS cmp
  UNION
  MATCH path = (c)-[:PARTICIPATES_IN]->(cmp:Campaign)<-[:APPLIES_TO]-{_TAIL}
  RETURN path, pv, d, 'category-campaign' AS via, cmp
}}
OPTIONAL MATCH (newer:PolicyVersion)-[s:SUPERSEDES]->(pv)
WITH path, pv, d, via, cmp, collect(newer {{.id, .status}}) AS superseded_by,
     collect(CASE WHEN s IS NULL THEN null ELSE {{type: type(s), source: newer.id, target: pv.id,
             props: properties(s), newer: newer {{.*}}}} END) AS supersedes_edges
RETURN via, properties(pv) AS version, d.id AS document, cmp {{.*, synonyms: null}} AS campaign,
       superseded_by, supersedes_edges, length(path) AS hops,
       [n IN nodes(path) | {_NODE_MAP}] AS nodes,
       [r IN relationships(path) | {_EDGE_MAP}] AS edges
ORDER BY via, version.id
LIMIT {MAX_ROWS}
""",
}

CHUNKS_FOR_VERSION = {
    "id": "graph.chunks_for_version.v1",
    "cypher": f"""
MATCH (pv:PolicyVersion {{id: $version_id}})-[db:DOCUMENTED_BY]->(d:Document)-[r:CONTAINS]->(n:Chunk)
RETURN n {{.*, embedding: null}} AS chunk,
       {{type: type(r), source: d.id, target: n.id, props: properties(r)}} AS edge
ORDER BY n.id
LIMIT {MAX_ROWS}
""",
}

PRODUCT_NEIGHBORHOOD = {
    "id": "graph.product_neighborhood.v1",
    "cypher": f"""
MATCH (p:Product {{id: $product_id}})-[:BELONGS_TO]->(:Tenant {{id: $tenant}})
MATCH path = (p)-[*1..2]-(m)
WHERE NOT m:Chunk
WITH path LIMIT {MAX_ROWS}
UNWIND relationships(path) AS r
WITH DISTINCT r
RETURN {_EDGE_MAP} AS edge, startNode(r) {{.*, text: null, embedding: null, synonyms: null}} AS a,
       labels(startNode(r))[0] AS al, endNode(r) {{.*, text: null, embedding: null, synonyms: null}} AS b,
       labels(endNode(r))[0] AS bl
""",
}


def param_names(cypher):
    """Nomes dos parametros ($nome) de um Cypher, em ordem de aparicao. Vai para
    a interface sem os VALORES: o aluno ve a forma da consulta, nunca dados."""
    seen = []
    for name in re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher):
        if name not in seen:
            seen.append(name)
    return seen


def run(session, query, timeout=QUERY_TIMEOUT_S, **params):
    """Executa uma consulta do catalogo acima com timeout de transacao."""
    result = session.run(Query(query["cypher"], timeout=timeout), **params)
    return [r.data() for r in result]
