"""Testes da fachada de telemetria (core/telemetry.py).

Sem o SDK (runner, Lambda): tudo e no-op e a API publica nao quebra.
Com o SDK (venv da 5.2): spans, propagacao W3C, labels de baixa cardinalidade e
logs sem PII, verificados com exportadores EM MEMORIA (sem Collector, sem rede).
"""
import importlib
import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import telemetry  # noqa: E402

PUBLIC_API = ("configure", "span", "current_trace_id", "inject_headers", "log", "record_request",
              "record_quality")


def test_api_publica_do_stub_continua_existindo():
    for name in PUBLIC_API:
        assert callable(getattr(telemetry, name)), name


def test_sem_endpoint_tudo_e_noop(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    mod = importlib.reload(telemetry)
    mod.configure("svc", "1.0.0", "test", "rel-1")
    assert not mod.enabled()
    with mod.span("rag.retrieve", k=3) as sp:
        sp.set("results", 2)
        sp.event("x", a=1)
        assert sp.trace_id is None
    assert mod.current_trace_id() is None
    assert mod.inject_headers({"a": "b"}) == {"a": "b"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.log("evento", question="segredo")
        mod.record_request("policy", 200, 1.0)
        mod.record_quality({"passed": True}, scenario="baseline")
    assert buf.getvalue() == ""


def test_telemetria_desligada_avisa_no_stderr(monkeypatch, capsys):
    # Silencio esconderia um Grafana vazio: sem endpoint, o aviso precisa sair.
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    mod = importlib.reload(telemetry)
    why = mod.warn_if_disabled()
    err = capsys.readouterr().err
    assert why and "telemetria DESLIGADA" in err and "ops/run" in err


def test_sanitize_remove_token_e_pii():
    out = telemetry.sanitize({"auth_token": "lab-token-ana", "order_id": "Q-1001", "customer_id": "c1",
                              "question": "meu cpf e 123", "route": "policy"})
    assert "auth_token" not in out
    assert "order_id" not in out and out["order_id_ref"].startswith("h:")
    assert "Q-1001" not in json.dumps(out)
    assert out["question_chars"] == len("meu cpf e 123")
    assert out["route"] == "policy"


def test_labels_descartam_alta_cardinalidade():
    telemetry._state["defaults"] = {"environment": "test", "release_id": "r1"}
    labels = telemetry._labels({"route": "policy", "order_id": "Q-1", "trace_id": "abc", "question": "x"})
    assert labels == {"environment": "test", "release_id": "r1", "route": "policy"}


def test_nenhum_atributo_gen_ai():
    # Sem LLM, gen_ai.* seria fingir uma operacao que nao existe.
    with open(os.path.join(ROOT, "core", "telemetry.py"), encoding="utf-8") as fh:
        code = fh.read()
    assert '"gen_ai.' not in code


# ---------------------------------------------------------------- com o SDK
otel = pytest.importorskip("opentelemetry.sdk", reason="opentelemetry-sdk ausente (venv sem requirements-ops)")


@pytest.fixture()
def sdk(monkeypatch):
    """Liga a fachada com exportadores em memoria no lugar do OTLP."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    meter = mp.get_meter("test")
    mod = telemetry
    names = ("requests_total", "answers_total", "order_lookups_total", "quality_probes_total",
             "quality_retrieval_probes_total", "quality_retrieval_hits_total", "quality_retrieval_rr_sum_total",
             "quality_citation_required_total", "quality_citation_present_total", "quality_source_checked_total",
             "quality_source_current_total", "quality_source_stale_total", "quality_acl_violations_total")
    instruments = {n: meter.create_counter(n) for n in names}
    instruments["request_latency_ms"] = meter.create_histogram("request_latency_ms")
    instruments["quality_probe_latency_ms"] = meter.create_histogram("quality_probe_latency_ms")
    monkeypatch.setitem(mod._state, "enabled", True)
    monkeypatch.setitem(mod._state, "tracer", tp.get_tracer("test"))
    monkeypatch.setitem(mod._state, "instruments", instruments)
    monkeypatch.setitem(mod._state, "defaults", {"environment": "test", "release_id": "rel-1"})
    monkeypatch.setitem(mod._state, "providers", [])
    monkeypatch.setitem(mod._state, "service_name", "quantum-assistant")
    yield mod, spans, reader, tp


def _points(reader, name):
    data = reader.get_metrics_data()
    out = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    out.extend(m.data.data_points)
    return out


def test_span_com_atributos_e_trace_id(sdk):
    mod, spans, _, _ = sdk
    with mod.span("rag.retrieve", k=3, order_id="Q-1001") as sp:
        sp.set("results", 2)
        assert sp.trace_id and len(sp.trace_id) == 32
    finished = spans.get_finished_spans()
    assert [s.name for s in finished] == ["rag.retrieve"]
    attrs = dict(finished[0].attributes)
    assert attrs["k"] == 3 and attrs["results"] == 2
    assert "order_id" not in attrs and attrs["order_id_ref"].startswith("h:")


def test_propagacao_w3c_cliente_para_servidor(sdk):
    mod, spans, _, _ = sdk
    with mod.span("order_api.lookup") as client:
        headers = mod.inject_headers({})
        assert headers["traceparent"].split("-")[1] == client.trace_id
    with mod.server_span("orders.get", headers) as server:
        assert server.trace_id == client.trace_id
    by_name = {s.name: s for s in spans.get_finished_spans()}
    assert by_name["orders.get"].parent.span_id == by_name["order_api.lookup"].context.span_id


def test_log_json_tem_trace_id_e_nao_tem_pii(sdk):
    mod, _, _, _ = sdk
    buf = io.StringIO()
    with redirect_stdout(buf):
        with mod.span("http.request") as sp:
            mod.log("assistant.answer", auth_token="lab-token-ana", question="Q-1001 cpf 123", route="policy")
    line = json.loads(buf.getvalue().strip())
    assert line["trace_id"] == sp.trace_id
    assert "lab-token-ana" not in buf.getvalue() and "cpf" not in buf.getvalue()
    assert line["route"] == "policy"


def test_metricas_de_qualidade_com_labels_permitidos(sdk):
    mod, _, reader, _ = sdk
    mod.record_quality({"passed": False, "reason": "fonte_obsoleta", "hit": True, "reciprocal_rank": 0.5,
                        "citation_required": True, "citation_present": True, "source_checked": True,
                        "source_is_current": False, "latency_ms": 12},
                       scenario="stale_policy", corpus_version="abc", slice="politicas", case_id="x")
    probes = _points(reader, "quality_probes_total")
    assert len(probes) == 1
    attrs = dict(probes[0].attributes)
    assert attrs["outcome"] == "fail" and attrs["reason"] == "fonte_obsoleta"
    assert "case_id" not in attrs and attrs["scenario"] == "stale_policy"
    assert _points(reader, "quality_source_stale_total")[0].value == 1
    assert _points(reader, "quality_retrieval_rr_sum_total")[0].value == 0.5


def test_record_request_separa_abstencao_e_pedidos(sdk):
    mod, _, reader, _ = sdk
    mod.record_request("order_status", 200, 30.0, abstained=True, abstain_reason="order_api_unavailable",
                       order_lookup="unavailable", scenario="order_api_degradation")
    assert dict(_points(reader, "requests_total")[0].attributes)["status_code"] == "200"
    assert dict(_points(reader, "answers_total")[0].attributes)["abstain_reason"] == "order_api_unavailable"
    assert dict(_points(reader, "order_lookups_total")[0].attributes)["outcome"] == "unavailable"
