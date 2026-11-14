"""Testes da camada web (web/app) SEM Neo4j: o Engine e um stub com a interface do contrato.

O que se prova aqui (o resto e teste real com Neo4j, fora do repositorio):
- validacao de entrada (pergunta, data simulada, tenant, papel, estrategia, corpo > 8 KB);
- /ready 503 enquanto o bootstrap nao terminou e 503 controlado quando o Neo4j cai;
- QC_PATH_PREFIX=/candidate: rotas com e sem prefixo, redirect de /candidate;
- headers de seguranca, sem CORS e sem cookie;
- HMAC da porta admin: sem assinatura, assinatura errada, janela de tempo, corpo trocado.
"""
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fastapi = pytest.importorskip("fastapi")  # runner sem requirements-web.txt: pula, nao quebra
from fastapi.testclient import TestClient  # noqa: E402

from web import admin_client  # noqa: E402
from web.app import runtime as rt  # noqa: E402
from web.app.admin import create_admin_app  # noqa: E402
from web.app.main import create_app  # noqa: E402
from web.app.security import canonical_string, verify_hmac  # noqa: E402

KEY = "chave-de-teste-nao-secreta"


class Neo4jDown(Exception):
    pass


Neo4jDown.__module__ = "neo4j.exceptions"


class StubEngine:
    def __init__(self):
        self.up = True
        self.calls = []
        self.mutations = []

    def bootstrap(self, timeout_s=180):
        if not self.up:
            raise Neo4jDown("sem neo4j")
        return {"graph_version": "g1", "graph_seed_version": "g1", "counts": {"nodes": 1, "edges": 1, "chunks": 1},
                "index": {"name": "chunk_embedding", "dim": 384, "state": "ONLINE"}}

    def ready(self):
        return {"ready": self.up, "checks": {"neo4j": self.up, "vector_index": "ONLINE" if self.up else "MISSING",
                                             "model_loaded": True, "graph_loaded": self.up}}

    def versions(self):
        return {"corpus_version": "c1", "graph_version": "g1", "graph_seed_version": "g1", "model_version": "none"}

    def meta(self):
        return {"counts": {"nodes": 1}}

    def _guard(self):
        if not self.up:
            raise Neo4jDown("neo4j fora")

    def compare(self, question, as_of_date, **kw):
        self._guard()
        self.calls.append(("compare", question, as_of_date, kw))
        return {"question_id": "q", "as_of_date": as_of_date, "route": "policy",
                "strategies": {s: {"strategy": s, "latency_ms": {"total": 1.0}} for s in kw["strategies"]}}

    def ask(self, question, strategy="graph", **kw):
        self._guard()
        self.calls.append(("ask", question, strategy, kw))
        return {"answer": "x", "route": "policy", "strategy": strategy}

    def graph_for_product(self, product_id, as_of_date, tenant):
        self._guard()
        return {"nodes": [], "edges": [], "policy_version": None, "reason": "ok"}

    def admin_state(self):
        return {"graph_version": "g1", "active_mutations": list(self.mutations)}

    def apply_scenario(self, name, action):
        self.mutations = [name] if action == "apply" else []
        return {"scenario": name, "action": action}

    def reindex(self):
        return {"graph_version": "g1"}


def make_runtime(engine, bootstrapped=True):
    r = rt.Runtime(engine_factory=lambda data_root=None: engine)
    if bootstrapped:
        r.engine, r.bootstrapped = engine, True
        r.check_ready()
    return r


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def client(engine):
    app = create_app(make_runtime(engine), path_prefix="", start_runtime=False)
    with TestClient(app) as c:
        yield c


def ask_body(**extra):
    return dict({"question": "Qual o prazo de troca do fone?", "as_of_date": "2026-11-14"}, **extra)


# ------------------------------------------------------------ prontidao
def test_health_responde_sem_engine_e_ready_503_antes_do_bootstrap(engine):
    r = make_runtime(engine, bootstrapped=False)
    with TestClient(create_app(r, path_prefix="", start_runtime=False)) as c:
        h = c.get("/health")
        assert h.status_code == 200 and h.json()["status"] == "alive"
        assert h.json()["versions"]["model_version"] == "none"
        ready = c.get("/ready")
        assert ready.status_code == 503 and ready.json()["ready"] is False
        assert c.post("/lab/compare", json=ask_body()).status_code == 503


def test_bootstrap_em_background_tenta_de_novo_ate_o_neo4j_subir(engine):
    engine.up = False
    r = rt.Runtime(engine_factory=lambda data_root=None: engine, max_backoff_s=0.05, supervise_interval_s=0.05)
    r.start()
    try:
        time.sleep(0.2)
        assert r.check_ready()["ready"] is False and r.attempts >= 2
        engine.up = True
        deadline = time.time() + 3
        while time.time() < deadline and not r.check_ready()["ready"]:
            time.sleep(0.05)
        assert r.check_ready()["ready"] is True and r.ready_at_s is not None
    finally:
        r.stop()


def test_ready_200_com_versions_e_identidade_do_deploy(client, monkeypatch):
    monkeypatch.setenv("QC_IMAGE_DIGEST", "sha256:" + "a" * 64)
    monkeypatch.setenv("QC_RELEASE_ID", "R7")
    body = client.get("/ready").json()
    assert body["ready"] is True
    assert body["versions"]["image_digest"] == "sha256:" + "a" * 64
    assert body["versions"]["release_id"] == "R7"


def test_neo4j_fora_vira_503_controlado_e_nunca_resposta(client, engine):
    engine.up = False
    r = client.post("/lab/compare", json=ask_body())
    assert r.status_code == 503
    assert r.json()["error"] in ("dependency_unavailable", "not_ready")
    assert "answer" not in r.text
    assert client.get("/ready").status_code == 503
    engine.up = True
    assert client.get("/ready").status_code == 200
    assert client.post("/lab/compare", json=ask_body()).status_code == 200


# ------------------------------------------------------------ validacao
@pytest.mark.parametrize("patch,field", [
    ({"question": ""}, "question"),
    ({"question": "x" * 501}, "question"),
    ({"as_of_date": "14/11/2026"}, "as_of_date"),
    ({"as_of_date": "2019-12-31"}, "as_of_date"),
    ({"as_of_date": "2031-01-01"}, "as_of_date"),
    ({"tenant": "outro"}, "tenant"),
    ({"user_role": "admin"}, "user_role"),
    ({"strategies": ["cypher"]}, "strategies.0"),
    ({"order_id": "Q-1001; DROP"}, "order_id"),
    ({"campo_extra": 1}, "campo_extra"),
])
def test_compare_valida_entrada_com_422_sem_ecoar_o_valor(client, patch, field):
    r = client.post("/lab/compare", json=ask_body(**patch))
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_request"
    assert any(d["field"] == field for d in body["detail"])
    assert "DROP" not in r.text and "x" * 50 not in r.text


def test_compare_passa_parametros_validados_ao_engine(client, engine):
    r = client.post("/lab/compare", json=ask_body(tenant="parceiro-x", user_role="agent",
                                                  strategies=["graph", "bm25", "graph"]),
                    headers={"X-QC-Scenario": "baseline", "X-QC-Case": "c-01"})
    assert r.status_code == 200
    _, _, as_of, kw = engine.calls[-1]
    assert as_of == "2026-11-14" and kw["tenant"] == "parceiro-x" and kw["user_role"] == "agent"
    assert kw["strategies"] == ("graph", "bm25")
    assert kw["scenario_id"] == "baseline" and kw["case_id"] == "c-01"
    assert r.json()["request_id"] == r.headers["x-request-id"]


def test_ask_valida_strategy_e_usa_default(client, engine, monkeypatch):
    assert client.post("/assistant/ask", json={"question": "troca", "strategy": "sql"}).status_code == 422
    r = client.post("/assistant/ask", json={"question": "troca"})
    assert r.status_code == 200 and r.json()["strategy"] == "graph"
    r = client.post("/assistant/ask", json={"question": "troca", "strategy": "bm25"},
                    headers={"Authorization": "Bearer tok-1"})
    assert engine.calls[-1][2] == "bm25" and engine.calls[-1][3]["auth_token"] == "tok-1"


def test_corpo_maior_que_8kb_recebe_413(client):
    big = json.dumps(ask_body(question="a" * 9000))
    r = client.post("/lab/compare", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "payload_too_large"


def test_produto_validado_por_regex_e_lista(client):
    assert client.get("/lab/graph/product/fone-bluetooth-qc").status_code == 200
    assert client.get("/lab/graph/product/nao-existe").status_code == 404
    assert client.get("/lab/graph/product/MATCH%20(n)").status_code == 404
    assert client.get("/lab/graph/product/fone-bluetooth-qc?as_of_date=2040-01-01").status_code == 422


# ------------------------------------------------------------ prefixo e headers
def test_prefixo_candidate_aceita_rotas_com_e_sem_prefixo(engine):
    app = create_app(make_runtime(engine), path_prefix="/candidate", start_runtime=False)
    with TestClient(app) as c:
        assert c.get("/candidate/health").status_code == 200
        assert c.get("/candidate/ready").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.post("/candidate/lab/compare", json=ask_body()).status_code == 200
        r = c.get("/candidate", follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == "/candidate/"


def test_ui_estatica_em_raiz_e_sob_prefixo(engine):
    if not os.path.exists(os.path.join(ROOT, "web", "static", "index.html")):
        pytest.skip("web/static ainda nao existe")
    app = create_app(make_runtime(engine), path_prefix="/candidate", start_runtime=False)
    with TestClient(app) as c:
        assert "text/html" in c.get("/").headers["content-type"]
        assert "text/html" in c.get("/candidate/").headers["content-type"]
        assert c.get("/candidate/app.js").status_code == 200


def test_headers_de_seguranca_sem_cors_e_sem_cookie(client):
    for r in (client.get("/health"), client.post("/lab/compare", json={}),
              client.get("/nao-existe"), client.options("/lab/compare", headers={"Origin": "https://evil.example"})):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert "access-control-allow-origin" not in r.headers
        assert "set-cookie" not in r.headers
        assert "server" not in r.headers


def test_coop_nao_vai_em_http(client):
    # ALB do lab e HTTP:80: COOP ali so geraria erro no console do navegador.
    for r in (client.get("/health"), client.get("/nao-existe")):
        assert "cross-origin-opener-policy" not in r.headers
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_coop_vai_quando_o_proxy_diz_https(client):
    r = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_cypher_do_catalogo_e_constante_e_sem_valores():
    # O painel "Cypher parametrizado (somente leitura)" mostra o texto EXATO que o
    # driver executa e so os NOMES dos parametros: nada de valor nem da pergunta.
    pytest.importorskip("neo4j")
    from graph import queries
    from lab.engine import Engine
    from vector import index as vindex
    cat = Engine._cypher_catalog()
    assert cat["graph.policy_for_product.v1"] == queries.POLICY_FOR_PRODUCT["cypher"].strip()
    assert cat[vindex.QUERY_ID] == vindex.KNN.strip()
    assert all("{_TAIL}" not in txt and "{{" not in txt for txt in cat.values())
    assert queries.param_names(cat["graph.policy_for_product.v1"]) == ["product_id", "tenant", "topic"]
    assert queries.param_names(cat[vindex.QUERY_ID]) == ["index", "k", "embedding"]

    entry = Engine._with_cypher(Engine.__new__(Engine), {"cypher_query_id": "graph.policy_for_product.v1"})
    assert entry["cypher"].startswith("MATCH tp = (p:Product {id: $product_id})")
    assert entry["cypher_params"] == ["product_id", "tenant", "topic"]
    bm25 = Engine._with_cypher(Engine.__new__(Engine), {"cypher_query_id": None})
    assert "cypher" not in bm25 and "cypher_params" not in bm25


def test_compare_repassa_cypher_e_nomes_de_parametros(client, engine):
    original = engine.compare

    def compare(question, as_of_date, **kw):
        out = original(question, as_of_date, **kw)
        out["strategies"]["graph"].update({"cypher_query_id": "graph.policy_for_product.v1",
                                           "cypher": "MATCH (p:Product {id: $product_id}) RETURN p",
                                           "cypher_params": ["product_id"]})
        return out
    engine.compare = compare
    r = client.post("/lab/compare", json={"question": "Qual o prazo para trocar um fone?", "as_of_date": "2026-11-14"})
    g = r.json()["strategies"]["graph"]
    assert r.status_code == 200 and g["cypher_params"] == ["product_id"]
    assert "fone" not in json.dumps(g["cypher"]) and "fone" not in json.dumps(g["cypher_params"])


# ------------------------------------------------------------ admin HMAC
@pytest.fixture
def admin(engine, monkeypatch):
    monkeypatch.setenv("QC_ADMIN_HMAC_KEY", KEY)
    with TestClient(create_admin_app(make_runtime(engine))) as c:
        yield c


def signed(method, path, body=b"", ts=None, key=KEY):
    return admin_client.signed_headers(key, method, path, body, ts=ts)


def test_admin_sem_assinatura_401_inclusive_rota_inexistente(admin):
    assert admin.get("/admin/graph/state").status_code == 401
    assert admin.get("/admin/nada").status_code == 401
    assert admin.post("/admin/reindex").json()["detail"] == "missing_signature"


def test_admin_com_hmac_valido_200_e_cenario_idempotente(admin, engine):
    assert admin.get("/admin/graph/state", headers=signed("GET", "/admin/graph/state")).status_code == 200
    body = json.dumps({"scenario": "stale_graph_policy", "action": "apply"}).encode()
    for _ in range(2):
        r = admin.post("/admin/graph/scenario", content=body,
                       headers=dict(signed("POST", "/admin/graph/scenario", body), **{"Content-Type": "application/json"}))
        assert r.status_code == 200
    assert engine.mutations == ["stale_graph_policy"]


def test_admin_recusa_chave_errada_fora_da_janela_e_corpo_trocado(admin):
    path = "/admin/graph/scenario"
    body = json.dumps({"scenario": "stale_graph_policy", "action": "apply"}).encode()
    other = json.dumps({"scenario": "remove_campaign_edge", "action": "apply"}).encode()
    h = {"Content-Type": "application/json"}
    assert admin.post(path, content=body, headers=dict(signed("POST", path, body, key="errada"), **h)).json()["detail"] \
        == "bad_signature"
    old = int(time.time()) - 121
    assert admin.post(path, content=body, headers=dict(signed("POST", path, body, ts=old), **h)).json()["detail"] \
        == "timestamp_out_of_window"
    assert admin.post(path, content=other, headers=dict(signed("POST", path, body), **h)).status_code == 401


def test_admin_sem_chave_configurada_fica_desligado(engine, monkeypatch):
    monkeypatch.delenv("QC_ADMIN_HMAC_KEY", raising=False)
    with TestClient(create_admin_app(make_runtime(engine))) as c:
        assert c.get("/admin/graph/state", headers=signed("GET", "/admin/graph/state")).status_code == 503


def test_assinatura_segue_o_contrato_literal():
    ts, body = "1700000000", b'{"a":1}'
    expected = hmac.new(KEY.encode(), f"{ts}\nPOST\n/admin/reindex\n{hashlib.sha256(body).hexdigest()}".encode(),
                        hashlib.sha256).hexdigest()
    assert canonical_string(ts, "post", "/admin/reindex", body) == \
        f"{ts}\nPOST\n/admin/reindex\n{hashlib.sha256(body).hexdigest()}"
    assert admin_client.signature(KEY, ts, "POST", "/admin/reindex", body) == expected
    assert verify_hmac(KEY, ts, expected, "POST", "/admin/reindex", body, now=1700000000 + 119) is None
    assert verify_hmac(KEY, ts, expected, "POST", "/admin/reindex", body, now=1700000000 + 121) == \
        "timestamp_out_of_window"


# ------------------------------------------------------------ bundle e telemetria
def test_bundle_com_sha_divergente_deixa_ready_503(engine, monkeypatch):
    r = make_runtime(engine, bootstrapped=False)
    r.fatal = {"reason": "data_bundle_sha_mismatch", "detail": "x"}
    with TestClient(create_app(r, path_prefix="", start_runtime=False)) as c:
        resp = c.get("/ready")
        assert resp.status_code == 503 and resp.json()["reason"] == "data_bundle_sha_mismatch"


def test_extracao_do_bundle_recusa_path_traversal(tmp_path):
    import io
    import tarfile
    tgz = tmp_path / "b.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        info = tarfile.TarInfo("../fora.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"oi"))
    with pytest.raises(rt.BundleError) as err:
        rt._safe_extract(str(tgz), str(tmp_path / "dest"))
    assert err.value.reason == "data_bundle_unsafe_member"
    assert not (tmp_path / "fora.txt").exists()


def test_otel_disabled_nao_liga_o_sdk(monkeypatch):
    from core import telemetry
    from web.app import observability
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "disabled")
    observability.configure()
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
    assert telemetry.enabled() is False


def test_log_de_requisicao_sem_pergunta_nem_token(client, capsys):
    client.post("/assistant/ask", json={"question": "meu cpf 123.456.789-00 troca", "auth_token": "tok-secreto"})
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert any(line.get("event") == "http.request" and line.get("route") == "/assistant/ask" for line in lines)
    assert "123.456" not in out and "tok-secreto" not in out
