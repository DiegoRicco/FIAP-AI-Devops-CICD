"""Testes da operacao da 5.2: monitor (janelas, n minimo, niveis, falso verde),
servico de pedidos com faults deterministicos e o fluxo inject -> restore.

Tudo stdlib + pytest; os servidores HTTP sobem em portas livres de 127.0.0.1,
sem Docker e sem OpenTelemetry (a telemetria fica em no-op).
"""
import datetime
import importlib.util
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


monitor = _load("quality_monitor", "ops/quality_monitor.py")
orders_service = _load("orders_service", "adapters/orders_service.py")


def _row(i, outcome="pass", reason="ok", stale=False, scenario="baseline", latency=8.0, status=200):
    ts = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=i)
    return {"ts": ts.isoformat(), "scenario_id": scenario, "release_id": "r1", "corpus_version": "c1",
            "outcome": outcome, "passed": outcome == "pass", "reason": reason, "hit": True,
            "reciprocal_rank": 1.0, "citation_required": True, "citation_present": True,
            "source_checked": True, "source_is_current": not stale, "acl_violation": False,
            "latency_ms": latency, "http_status": status, "trace_id": f"t{i}"}


def _run_monitor(tmp_path, rows, *extra):
    path = tmp_path / "r.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "out.json"
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = monitor.main(["--results", str(path), "--alerts-dir", str(tmp_path / "alerts"), *extra])
    out.write_text(buf.getvalue(), encoding="utf-8")
    return rc, json.loads(buf.getvalue())


def test_falso_verde_http_200_e_p95_bom_mas_pass_rate_ruim(tmp_path):
    # Todo probe respondeu 200 em 8 ms (sinal tecnico perfeito), mas 30% falham
    # por conteudo. O monitor TEM de alertar: disponibilidade nao e qualidade.
    rows = [_row(i) for i in range(28)] + [_row(28 + i, "fail", "campos_esperados") for i in range(12)]
    rc, rep = _run_monitor(tmp_path, rows)
    w = rep["windows"][0]
    assert w["http_non_200"] == 0 and w["p95_latency_ms"] <= 10
    assert w["level"] == "warning" and w["pass_rate"] == 0.7
    assert rc == 1
    assert list((tmp_path / "alerts").glob("*.json")), "alerta local nao foi gravado"


def test_fonte_obsoleta_e_critical_mesmo_com_pass_rate_alto(tmp_path):
    rows = [_row(i) for i in range(39)] + [_row(39, "fail", "fonte_obsoleta", stale=True)]
    rc, rep = _run_monitor(tmp_path, rows)
    assert rep["windows"][0]["level"] == "critical"
    assert rep["windows"][0]["why"] == "stale_source_served"
    assert rc == 1


def test_amostra_pequena_e_insufficient_data_e_nao_sucesso(tmp_path):
    rows = [_row(i, "fail", "rota") for i in range(5)]
    rc, rep = _run_monitor(tmp_path, rows)
    assert rep["windows"][0]["level"] == "insufficient_data"
    assert rc == 3


def test_sem_probe_nenhum_e_insufficient_data(tmp_path):
    rc, rep = _run_monitor(tmp_path, [])
    assert rep["windows"][0]["level"] == "insufficient_data"
    assert rc == 3


def test_fallback_de_dependencia_fica_fora_do_pass_rate(tmp_path):
    rows = [_row(i) for i in range(25)] + [_row(25 + i, "fallback", "dependency_unavailable") for i in range(20)]
    rc, rep = _run_monitor(tmp_path, rows)
    w = rep["windows"][0]
    assert w["n"] == 25 and w["n_fallback"] == 20 and w["pass_rate"] == 1.0
    assert w["level"] == "ok" and rc == 0


def test_janelas_separam_corpus_diferentes(tmp_path):
    rows = [_row(i) for i in range(20)]
    rows += [dict(_row(20 + i, "fail", "fonte_obsoleta", stale=True), corpus_version="c2") for i in range(20)]
    _, rep = _run_monitor(tmp_path, rows)
    levels = {w["corpus_version"]: w["level"] for w in rep["windows"]}
    assert levels == {"c1": "ok", "c2": "critical"}


# ------------------------------------------------------------ servico de pedidos
@pytest.fixture()
def orders_server():
    with open(os.path.join(ROOT, "orders", "fixtures.json"), encoding="utf-8") as fh:
        fixtures = json.load(fh)
    faults = orders_service.FaultState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), orders_service.make_handler(fixtures, faults, 0.3))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as err:
        return err.code


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_pedidos_autorizacao_no_servidor(orders_server):
    assert _get(orders_server + "/orders/Q-1001", "lab-token-ana") == 200
    assert _get(orders_server + "/orders/Q-2001", "lab-token-ana") == 403
    assert _get(orders_server + "/orders/Q-1001") == 401
    assert _get(orders_server + "/orders/Q-9999", "lab-token-ana") == 404


def test_faults_com_mesma_seed_repetem_a_mesma_sequencia(orders_server):
    def sequence():
        _post(orders_server + "/admin/faults", {"error_rate": 0.5, "seed": 7})
        return [_get(orders_server + "/orders/Q-1001", "lab-token-ana") for _ in range(12)]
    first, second = sequence(), sequence()
    assert first == second
    assert 503 in first and 200 in first
    _post(orders_server + "/admin/faults/reset", {})
    assert _get(orders_server + "/orders/Q-1001", "lab-token-ana") == 200


def test_fault_aceita_nome_curto_5xx_rate(orders_server):
    cfg = _post(orders_server + "/admin/faults", {"5xx_rate": 1.0, "latency_ms": 5, "seed": 1})
    assert cfg["error_rate"] == 1.0 and cfg["latency_ms"] == 5
    assert _get(orders_server + "/orders/Q-1001", "lab-token-ana") == 503


# ------------------------------------------------------- inject -> restore (HTTP)
def test_inject_stale_policy_e_restore_sao_reversiveis(tmp_path, monkeypatch, orders_server):
    from core import versions
    monkeypatch.setenv("QC_RUNTIME_DIR", str(tmp_path / "rt"))
    http_server = _load("http_server", "adapters/http_server.py")
    inject = _load("inject", "ops/inject.py")
    restore = _load("restore", "ops/restore.py")
    runtime = http_server.Runtime(orders_server, "rel-test", "test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), http_server.make_handler(runtime))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("QC_ASSISTANT_URL", url)
    monkeypatch.setenv("QC_ORDERS_BASE_URL", orders_server)
    try:
        repo_corpus = versions.corpus_version(os.path.join(ROOT, "corpus"))
        with open(os.path.join(ROOT, "corpus", "trocas-v2.md"), encoding="utf-8") as fh:
            original = fh.read()
        assert inject.main(["stale_policy", "--assistant-url", url, "--orders-url", orders_server]) == 0
        health = json.loads(urllib.request.urlopen(url + "/health").read())
        assert health["corpus_source"] == "overlay"
        assert health["versions"]["corpus_version"] != repo_corpus
        ask = _post(url + "/assistant/ask", {"question": "Como faço para solicitar uma troca?"})
        assert ask["citations"][0]["version"] == "2", "a v2 obsoleta deveria vencer no indice injetado"
        # O repositorio nao foi tocado: a falha vive so no overlay.
        with open(os.path.join(ROOT, "corpus", "trocas-v2.md"), encoding="utf-8") as fh:
            assert fh.read() == original
        assert restore.main() == 0
        health = json.loads(urllib.request.urlopen(url + "/health").read())
        assert health["corpus_source"] == "repositorio"
        assert health["versions"]["corpus_version"] == repo_corpus
        ask = _post(url + "/assistant/ask", {"question": "Como faço para solicitar uma troca?"})
        assert ask["citations"][0]["version"] == "3"
    finally:
        httpd.shutdown()


def test_http_server_health_expoe_versoes_e_400_sem_pergunta(orders_server, tmp_path, monkeypatch):
    monkeypatch.setenv("QC_RUNTIME_DIR", str(tmp_path / "rt"))
    http_server = _load("http_server", "adapters/http_server.py")
    runtime = http_server.Runtime(orders_server, "rel-test", "test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), http_server.make_handler(runtime))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        health = json.loads(urllib.request.urlopen(url + "/health").read())
        assert set(health["versions"]) == {"application_version", "prompt_version", "corpus_version",
                                           "index_version", "eval_set_version", "model_version"}
        assert health["versions"]["model_version"] == "none" and health["release_id"] == "rel-test"
        req = urllib.request.Request(url + "/assistant/ask", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        assert err.value.code == 400
        order = _post(url + "/assistant/ask", {"question": "Qual o status do pedido Q-1001?",
                                              "auth_token": "lab-token-ana"})
        assert order["route"] == "order_status" and order["order"]["status"] == "em_transito"
    finally:
        httpd.shutdown()


def test_http_server_ignora_identidade_e_data_vindas_do_corpo(orders_server, tmp_path, monkeypatch):
    # Seguranca: no canal publico o servidor fixa tenant=quantum, user_role=customer
    # e a data. Mandar user_role=agent no corpo NAO pode abrir o documento interno.
    monkeypatch.setenv("QC_RUNTIME_DIR", str(tmp_path / "rt"))
    http_server = _load("http_server", "adapters/http_server.py")
    runtime = http_server.Runtime(orders_server, "rel-test", "test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), http_server.make_handler(runtime))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    question = "Qual a alçada do atendente para aprovar estorno sem frete reverso?"
    try:
        # Controle: o core, chamado direto como agent, de fato le o manual interno.
        direct = runtime.assistant.answer(question, user_role="agent")
        assert any(c["doc_id"] == "manual-interno-reembolso" for c in direct["citations"])

        # Cada tentativa isolada: misturar tenant e user_role na mesma chamada
        # esconderia o vazamento (o filtro de tenant barraria o manual interno).
        as_agent = _post(url + "/assistant/ask", {"question": question, "user_role": "agent"})
        seen = {c["doc_id"] for c in as_agent["citations"]} | {r["doc_id"] for r in as_agent["retrieval"]}
        assert "manual-interno-reembolso" not in seen

        other_tenant = _post(url + "/assistant/ask", {"question": "Em quantos dias posso trocar um produto?",
                                                     "tenant": "parceiro-x"})
        seen = {c["doc_id"] for c in other_tenant["citations"]} | {r["doc_id"] for r in other_tenant["retrieval"]}
        assert "parceiro-x-trocas" not in seen
        assert other_tenant["as_of_date"] == datetime.date.today().isoformat()

        future = _post(url + "/assistant/ask", {"question": "Qual o prazo de troca na Black Friday?",
                                               "as_of_date": "2030-01-01"})
        assert all(c["doc_id"] != "campanha-black-friday" for c in future["citations"])
    finally:
        httpd.shutdown()
