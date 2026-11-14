"""Indice vetorial nativo do Neo4j (Community 5.26) sobre :Chunk(embedding).

O embedding fica como LIST<FLOAT> na propriedade do no; o Neo4j aceita indice
vetorial nessa forma na edicao Community, sem plugin. Similaridade cosine: os
vetores ja chegam com norma 1, entao cosine e produto interno dao o mesmo
ranking. O score devolvido pelo Neo4j para cosine fica em [0, 1]
(score = (1 + cos) / 2); a resposta mostra os dois para nao confundir.
"""
import time

from neo4j import Query

from . import encoder as enc

INDEX_NAME = "chunk_embedding"
SIMILARITY = "cosine"
QUERY_ID = "vector.chunk_knn.v1"
QUERY_TIMEOUT_S = 3.0

CREATE = (
    f"CREATE VECTOR INDEX {INDEX_NAME} IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) "
    f"OPTIONS {{indexConfig: {{`vector.dimensions`: {enc.DIM}, `vector.similarity_function`: '{SIMILARITY}'}}}}"
)

KNN = (
    "CALL db.index.vector.queryNodes($index, $k, $embedding) YIELD node, score "
    "RETURN node {.*, embedding: null} AS chunk, score ORDER BY score DESC, chunk.id"
)


def store_embeddings(session, vectors):
    rows = [{"id": cid, "embedding": vec} for cid, vec in sorted(vectors.items())]
    session.run("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) SET c.embedding = row.embedding",
                rows=rows).consume()


def ensure(session):
    session.run(CREATE).consume()


def state(session):
    rec = session.run(
        "SHOW INDEXES YIELD name, type, state, populationPercent, options WHERE name = $name "
        "RETURN type, state, populationPercent, options", name=INDEX_NAME).single()
    if rec is None:
        return {"name": INDEX_NAME, "state": "MISSING", "dim": None, "similarity": None, "population_percent": None}
    cfg = (rec["options"] or {}).get("indexConfig", {})
    return {"name": INDEX_NAME, "type": rec["type"], "state": rec["state"],
            "population_percent": rec["populationPercent"],
            "dim": cfg.get("vector.dimensions"), "similarity": cfg.get("vector.similarity_function")}


def wait_online(session, timeout_s=60.0):
    deadline = time.monotonic() + timeout_s
    while True:
        st = state(session)
        if st["state"] == "ONLINE" or time.monotonic() > deadline:
            return st
        time.sleep(0.5)


def query(session, embedding, k, timeout=QUERY_TIMEOUT_S):
    result = session.run(Query(KNN, timeout=timeout), index=INDEX_NAME, k=int(k), embedding=embedding)
    return [r.data() for r in result]
