"""Observabilidade da camada web: span raiz http.request, metricas qc_* e log JSON.

Usa a fachada core/telemetry.py (mesmo tracer e mesmo provider do engine), entao
os spans do engine (graph.traverse, vector.query...) nascem como FILHOS do
http.request aberto aqui. Sem SDK ou sem endpoint, tudo vira no-op e a requisicao
nunca falha por causa de telemetria (se o Collector cair, so perdemos o sinal).

Metricas do contrato (nomes exatos; o Collector exporta sem sufixo de unidade):
  qc_requests_total{route,status_class,environment,release_id,strategy}
  qc_request_duration_seconds{route,environment,release_id}   (histograma)
  qc_stage_duration_seconds{stage,environment}                (histograma, spans do web)
X-QC-Scenario / X-QC-Case vao para span e log, NUNCA para label: sao texto livre
do cliente e cada valor novo criaria uma serie nova no Prometheus.
"""
import os
import re
import time
import uuid
from contextlib import contextmanager

from core import telemetry, versions

from .runtime import log

SERVICE_NAME = "qc-assistant"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# Rotas conhecidas viram label; qualquer outro caminho (scanner, typo) vira "other".
KNOWN_ROUTES = {"/health", "/ready", "/lab/meta", "/lab/compare", "/assistant/ask",
                "/lab/graph/product/{product_id}", "/admin/graph/state", "/admin/graph/scenario",
                "/admin/reindex", "static"}
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

_inst = {}


def configure():
    """Liga o SDK so com endpoint http(s) de verdade.

    O ECS injeta OTEL_EXPORTER_OTLP_ENDPOINT=disabled enquanto o Collector nao existe;
    core.telemetry ligaria o SDK com qualquer valor nao vazio e tentaria exportar
    para "disabled". Por isso o valor invalido e removido do ambiente antes.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint.lower().startswith("http"):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    env = os.environ.get("QC_ENVIRONMENT", "local")
    release = os.environ.get("QC_RELEASE_ID") or "local-dev"
    telemetry.configure(SERVICE_NAME, versions.APPLICATION_VERSION, env, release)
    if telemetry.enabled() and not _inst:
        from opentelemetry import metrics
        meter = metrics.get_meter("qc-assistant-web")
        _inst["requests"] = meter.create_counter(
            "qc_requests_total", description="Requisicoes HTTP por rota, classe de status e estrategia")
        _inst["duration"] = meter.create_histogram(
            "qc_request_duration_seconds", unit="s", description="Duracao da requisicao HTTP",
            explicit_bucket_boundaries_advisory=_BUCKETS)
        _inst["stage"] = meter.create_histogram(
            "qc_stage_duration_seconds", unit="s", description="Duracao por etapa do pipeline",
            explicit_bucket_boundaries_advisory=_BUCKETS)
    log("telemetry.configured", telemetry="otlp" if telemetry.enabled() else "off",
        reason=telemetry.disabled_reason())


def _base_labels():
    return {"environment": os.environ.get("QC_ENVIRONMENT", "local"),
            "release_id": os.environ.get("QC_RELEASE_ID") or "local-dev"}


def record_stage(stage, seconds):
    if "stage" in _inst:
        _inst["stage"].record(float(seconds), {"stage": stage, "environment": _base_labels()["environment"]})


def record_stage_latencies(body):
    """O engine devolve latency_ms por estrategia; viram qc_stage_duration_seconds."""
    if "stage" not in _inst or not isinstance(body, dict):
        return
    stage_names = {"link": "entity.link", "traverse": "graph.traverse", "embed": "vector.embed",
                   "query": "vector.query", "filter": "policy.version.filter",
                   "compose": "compose.deterministic", "db": "db.query"}
    orders_seen = False
    for strat in (body.get("strategies") or {}).values():
        lat = (strat or {}).get("latency_ms") or {}
        for key, stage in stage_names.items():
            if isinstance(lat.get(key), (int, float)):
                record_stage(stage, lat[key] / 1000.0)
        # A chamada a API de pedidos acontece UMA vez por requisicao, mesmo que
        # a resposta seja repetida em cada estrategia: registra uma vez so.
        if not orders_seen and isinstance(lat.get("orders"), (int, float)):
            record_stage("orders.lookup", lat["orders"] / 1000.0)
            orders_seen = True


@contextmanager
def stage(name, **attributes):
    started = time.monotonic()
    with telemetry.span(name, **attributes) as sp:
        yield sp
    record_stage(name, time.monotonic() - started)


def note_route(request, route):
    """Guarda a rota decidida para o log da requisicao. O span intent.route e
    aberto aqui no web (intent_route abaixo), em volta da chamada ao engine;
    o engine so aplica a regra, sem abrir outro span."""
    if route:
        request.state.intent_route = route


@contextmanager
def intent_route(**attributes):
    """Span intent.route + latencia da etapa. Envolve a chamada ao engine, que e
    onde a rota (knowledge, order_status, unsupported) e decidida; o atributo
    qc.route e preenchido depois, com a rota que o engine devolveu."""
    started = time.monotonic()
    with telemetry.span("intent.route", **attributes) as sp:
        yield sp
    record_stage("intent.route", time.monotonic() - started)


def _safe(value):
    return value if value and _SAFE_ID.match(value) else None


def _route_template(scope):
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path in KNOWN_ROUTES:
        return path
    if path == "" or (route is None and scope.get("path", "").startswith(("/app.", "/vendor/", "/index"))):
        return "static"
    if route is not None and type(route).__name__ == "Mount":
        return "static"
    return "other"


class ObservabilityMiddleware:
    """Span raiz, request_id, metricas e uma linha de log por requisicao."""

    def __init__(self, app, runtime=None):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        request = Request(scope)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id", "")
        rid = rid if re.match(r"^[A-Za-z0-9-]{1,64}$", rid) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state.update(request_id=rid, scenario_id=_safe(headers.get("x-qc-scenario")),
                     case_id=_safe(headers.get("x-qc-case")), strategy=None, intent_route=None, trace_id=None)
        started = time.monotonic()
        status = {"code": 500}

        async def wrapped(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message = dict(message, headers=list(message.get("headers", [])) +
                               [(b"x-request-id", rid.encode("ascii"))])
            await send(message)

        attrs = {"http.request.method": scope["method"], "url.path": scope["path"], "request_id": rid,
                 "qc.scenario_id": state["scenario_id"], "qc.case_id": state["case_id"],
                 "qc.environment": os.environ.get("QC_ENVIRONMENT", "local"),
                 "qc.release_id": os.environ.get("QC_RELEASE_ID") or "local-dev"}
        with telemetry.server_span("http.request", headers, **attrs) as sp:
            state["trace_id"] = getattr(sp, "trace_id", None)
            try:
                await self.app(scope, receive, wrapped)
            finally:
                route = _route_template(scope)
                latency = time.monotonic() - started
                strategy = state.get("strategy") or "none"
                # Label de metrica so com valores fechados: o /lab/compare aceita
                # combinacoes de estrategias, que viram "compare" (o detalhe fica no span/log).
                strategy_label = strategy if strategy in ("bm25", "vector", "graph", "none") else "compare"
                sp.set("http.route", route)
                sp.set("http.response.status_code", status["code"])
                sp.set("qc.strategy", strategy)
                if self.runtime is not None and route not in ("/health", "static"):
                    v = self.runtime.versions()
                    for key in ("graph_version", "graph_seed_version", "corpus_version",
                                "vector_index_version", "embedding_model_id"):
                        if v.get(key):
                            sp.set("qc." + key, v[key])
                if _inst:
                    labels = dict(_base_labels(), route=route)
                    _inst["requests"].add(1, dict(labels, status_class=f"{status['code'] // 100}xx",
                                                  strategy=strategy_label))
                    _inst["duration"].record(latency, labels)
                if route not in ("static",):
                    log("http.request", request_id=rid, trace_id=state["trace_id"], method=scope["method"],
                        route=route, status_code=status["code"], strategy=strategy,
                        intent_route=state.get("intent_route"), latency_ms=round(latency * 1000, 1),
                        scenario_id=state["scenario_id"], case_id=state["case_id"],
                        release_id=attrs["qc.release_id"],
                        image_digest=os.environ.get("QC_IMAGE_DIGEST"), git_sha=os.environ.get("QC_GIT_SHA"))
