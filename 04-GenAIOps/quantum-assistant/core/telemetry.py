"""Fachada de telemetria do assistente (contrato entre o core e o OpenTelemetry).

Por que uma fachada: o core precisa rodar sem dependencias (Lambda, runner com
Python 3.10, testes offline). O SDK do OpenTelemetry so e ativado quando as DUAS
condicoes valem:
  1. a variavel OTEL_EXPORTER_OTLP_ENDPOINT esta definida (ha um Collector para
     receber os dados);
  2. o pacote opentelemetry-sdk esta instalado (requirements-ops.txt).
Fora disso, cada funcao abaixo e no-op e o comportamento do assistente nao muda.

Sobre `gen_ai.*`: as convencoes semanticas de GenAI do OpenTelemetry descrevem
chamadas a um modelo (nome do modelo, tokens de entrada/saida, custo). Este
assistente NAO chama LLM: o composer e deterministico e extrativo. Emitir
`gen_ai.*` aqui seria fingir uma operacao que nao existe, entao nenhum atributo
`gen_ai.*` e usado; custo/token ficam como `not_applicable` nos relatorios.

Metricas: o SDK emite CONTADORES e HISTOGRAMAS. As taxas do contrato
(`synthetic_quality_pass_rate`, `source_is_current_rate`, `retrieval_hit_at_k`,
`retrieval_mrr`, `citation_presence_rate`, `abstention_rate`,
`transaction_lookup_failure_rate`) sao calculadas no Prometheus por regras de
gravacao (ops/alert-rules.yml) sobre uma janela. Por que nao emitir a taxa
pronta: uma taxa nao se soma entre processos nem informa quantas amostras a
sustentam; com contadores, a janela e o n minimo ficam explicitos na consulta.
"""
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager

# Labels permitidos nas metricas. Qualquer outro e descartado: e o que impede
# que order_id, trace_id ou texto livre virem label e explodam a cardinalidade
# do Prometheus (cada valor distinto de label cria uma serie nova).
_ALLOWED_LABELS = (
    "route", "environment", "release_id", "corpus_version", "scenario", "slice",
    "status_code", "outcome", "reason", "abstained", "abstain_reason", "service",
)

# Chaves que nunca saem do processo (logs e spans).
_DROP_KEYS = {"auth_token", "token", "authorization", "password", "secret", "api_key"}
# Identificadores de pessoa ou pedido: viram um apelido estavel (hash curto),
# que permite correlacionar sem expor o valor original.
_SURROGATE_KEYS = {"customer_id", "order_id", "email", "cpf", "phone"}
# Texto livre (pergunta, resposta, trecho do corpus) pode conter dado pessoal
# digitado pelo cliente: registramos so o tamanho.
_TEXT_KEYS = {"question", "answer", "text", "content", "chunk_text", "body"}

_state = {
    "enabled": False,
    "json_logs": os.environ.get("QC_LOG_JSON") == "1",
    "service_name": None,
    "defaults": {},
    "tracer": None,
    "instruments": {},
    "providers": [],
}


class _SpanHandle:
    """Alca devolvida por span(); sem SDK, nao faz nada."""

    trace_id = None

    def set(self, key, value):
        pass

    def event(self, name, **attributes):
        pass


class _OtelSpanHandle:
    def __init__(self, otel_span):
        self._span = otel_span
        ctx = otel_span.get_span_context()
        self.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else None

    def set(self, key, value):
        for k, v in _span_attributes({key: value}).items():
            self._span.set_attribute(k, v)

    def event(self, name, **attributes):
        self._span.add_event(name, _span_attributes(attributes))


def _surrogate(value):
    return "h:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]


def sanitize(fields):
    """Remove segredo, troca identificador por apelido e texto livre por tamanho."""
    clean = {}
    for key, value in fields.items():
        low = key.lower()
        if low in _DROP_KEYS:
            continue
        if value is None:
            clean[key] = None
        elif low in _SURROGATE_KEYS:
            clean[key + "_ref"] = _surrogate(value)
        elif low in _TEXT_KEYS:
            clean[key + "_chars"] = len(str(value))
        else:
            clean[key] = value
    return clean


def _span_attributes(fields):
    # O OpenTelemetry aceita so str/bool/int/float (ou listas deles) e rejeita None.
    attrs = {}
    for key, value in sanitize(fields).items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            attrs[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, bool, int, float)) for v in value):
            attrs[key] = list(value)
        else:
            attrs[key] = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return attrs


def _labels(extra):
    merged = dict(_state["defaults"])
    merged.update({k: v for k, v in extra.items() if v is not None})
    return {k: str(merged[k]) for k in _ALLOWED_LABELS if k in merged}


def configure(service_name, service_version, environment, release_id):
    """Liga o SDK (se houver endpoint e pacote) e fixa a identidade do servico."""
    _state["service_name"] = service_name
    _state["defaults"].update({"environment": environment, "release_id": release_id})
    if _state["enabled"] or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # Endpoint definido mas pacote ausente: seguimos em no-op, avisando no stderr.
        print("telemetry: OTEL_EXPORTER_OTLP_ENDPOINT definido, mas opentelemetry-sdk "
              "nao esta instalado; telemetria desligada", file=sys.stderr)
        return

    resource = Resource.create({
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment.name": environment,
        "quantum.release_id": release_id,
        # Um id por processo: cada execucao do simulador vira uma serie propria
        # no Prometheus, em vez de "resetar" a serie da execucao anterior.
        "service.instance.id": uuid.uuid4().hex[:12],
    })
    tracer_provider = TracerProvider(resource=resource)
    # Lote curto: na aula queremos ver o trace no Jaeger em ~1 s, nao em 5 s.
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=1000))
    trace.set_tracer_provider(tracer_provider)

    interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "5000"))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    meter = metrics.get_meter("quantum-assistant")
    _state["tracer"] = trace.get_tracer("quantum-assistant")
    _state["instruments"] = {
        "requests_total": meter.create_counter(
            "requests_total", description="Requisicoes HTTP atendidas"),
        "request_latency_ms": meter.create_histogram(
            "request_latency_ms", unit="ms", description="Latencia da requisicao HTTP"),
        "answers_total": meter.create_counter(
            "answers_total", description="Respostas do assistente por rota e motivo de recusa"),
        "order_lookups_total": meter.create_counter(
            "order_lookups_total", description="Consultas a API de pedidos por resultado"),
        "quality_probes_total": meter.create_counter(
            "quality_probes_total", description="Probes rotulados (proxy sintetico) por resultado"),
        "quality_retrieval_probes_total": meter.create_counter(
            "quality_retrieval_probes_total", description="Probes com documento relevante esperado"),
        "quality_retrieval_hits_total": meter.create_counter(
            "quality_retrieval_hits_total", description="Probes com documento esperado no top-k"),
        "quality_retrieval_rr_sum_total": meter.create_counter(
            "quality_retrieval_rr_sum_total", description="Soma de 1/posicao do primeiro relevante"),
        "quality_citation_required_total": meter.create_counter(
            "quality_citation_required_total", description="Probes que exigem citacao"),
        "quality_citation_present_total": meter.create_counter(
            "quality_citation_present_total", description="Probes com citacao presente"),
        "quality_source_checked_total": meter.create_counter(
            "quality_source_checked_total", description="Probes com fonte verificavel"),
        "quality_source_current_total": meter.create_counter(
            "quality_source_current_total", description="Probes cuja fonte citada esta vigente"),
        "quality_source_stale_total": meter.create_counter(
            "quality_source_stale_total", description="Probes que citaram fonte obsoleta"),
        "quality_acl_violations_total": meter.create_counter(
            "quality_acl_violations_total", description="Probes com violacao de ACL ou tenant"),
        "quality_probe_latency_ms": meter.create_histogram(
            "quality_probe_latency_ms", unit="ms", description="Latencia vista pelo probe"),
    }
    _state["providers"] = [tracer_provider, meter_provider]
    _state["enabled"] = True
    import atexit
    atexit.register(shutdown)


def enabled():
    return _state["enabled"]


TELEMETRY_OFF_WARNING = (
    "AVISO: telemetria DESLIGADA ({why}). Rode via 'ops/run <script>' (depois de "
    "'bash ops/bootstrap-demo52.sh'): sem isso os probes NAO aparecem no Prometheus/Grafana "
    "e os alertas QualityProbeDegraded/StaleSourceServed nao acendem."
)


def disabled_reason():
    """Por que a telemetria esta desligada (None se esta ligada)."""
    if _state["enabled"]:
        return None
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return "OTEL_EXPORTER_OTLP_ENDPOINT nao definido"
    try:
        import opentelemetry.sdk  # noqa: F401
    except ImportError:
        return "pacote opentelemetry-sdk nao instalado neste python: " + sys.executable
    return "configure() ainda nao foi chamado"


def warn_if_disabled():
    """Silencio aqui esconderia um painel vazio: avisa no stderr, alto e claro."""
    why = disabled_reason()
    if why:
        print(TELEMETRY_OFF_WARNING.format(why=why), file=sys.stderr, flush=True)
    return why


def set_default_labels(**labels):
    """Atualiza labels padrao (ex.: corpus_version apos um /admin/reload)."""
    _state["defaults"].update({k: v for k, v in labels.items() if v is not None})


def flush():
    """Empurra spans e metricas pendentes (fim de um lote do simulador)."""
    for provider in _state["providers"]:
        provider.force_flush()


def shutdown():
    if not _state["enabled"]:
        return
    _state["enabled"] = False
    for provider in _state["providers"]:
        try:
            provider.shutdown()
        except Exception:
            pass


_SPAN_KINDS = {"order_api.lookup": "CLIENT"}


@contextmanager
def span(name, **attributes):
    if not _state["enabled"]:
        yield _SpanHandle()
        return
    from opentelemetry.trace import SpanKind, Status, StatusCode
    kind = getattr(SpanKind, _SPAN_KINDS.get(name, "INTERNAL"))
    with _state["tracer"].start_as_current_span(
        name, kind=kind, attributes=_span_attributes(attributes),
        record_exception=False, set_status_on_exception=False,
    ) as otel_span:
        try:
            yield _OtelSpanHandle(otel_span)
        except Exception as exc:
            otel_span.record_exception(exc)
            otel_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


@contextmanager
def server_span(name, headers, **attributes):
    """Span de entrada HTTP: continua o trace do chamador lendo o `traceparent` W3C."""
    if not _state["enabled"]:
        yield _SpanHandle()
        return
    from opentelemetry import context, propagate
    from opentelemetry.trace import SpanKind
    carrier = {k.lower(): v for k, v in dict(headers or {}).items()}
    token = context.attach(propagate.extract(carrier))
    try:
        with _state["tracer"].start_as_current_span(
            name, kind=SpanKind.SERVER, attributes=_span_attributes(attributes),
        ) as otel_span:
            yield _OtelSpanHandle(otel_span)
    finally:
        context.detach(token)


def current_trace_id():
    if not _state["enabled"]:
        return None
    from opentelemetry import trace
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _current_span_id():
    if not _state["enabled"]:
        return None
    from opentelemetry import trace
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.span_id, "016x") if ctx.is_valid else None


def inject_headers(headers):
    """Escreve o `traceparent` W3C nos headers de uma chamada de saida."""
    if _state["enabled"]:
        from opentelemetry import propagate
        propagate.inject(headers)
    return headers


def log(event, **fields):
    """Uma linha JSON no stdout, com trace_id e sem PII (ver sanitize)."""
    if not (_state["enabled"] or _state["json_logs"]):
        return
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "service": _state["service_name"],
        "event": event,
        "trace_id": current_trace_id(),
        "span_id": _current_span_id(),
        "environment": _state["defaults"].get("environment"),
        "release_id": _state["defaults"].get("release_id"),
    }
    line.update(sanitize(fields))
    sys.stdout.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def record_request(route, status_code, latency_ms, **labels):
    """Sinal tecnico de uma requisicao. Labels extras aceitos:
    abstained, abstain_reason (viram answers_total) e order_lookup
    (ok|not_found|forbidden|unavailable, vira order_lookups_total)."""
    if not _state["enabled"]:
        return
    ins = _state["instruments"]
    order_lookup = labels.pop("order_lookup", None)
    abstained = labels.pop("abstained", None)
    abstain_reason = labels.pop("abstain_reason", None)
    base = _labels(dict(labels, route=route, status_code=status_code))
    ins["requests_total"].add(1, base)
    ins["request_latency_ms"].record(float(latency_ms), _labels(dict(labels, route=route)))
    if abstained is not None:
        ins["answers_total"].add(1, _labels(dict(
            labels, route=route, abstained=str(bool(abstained)).lower(),
            abstain_reason=abstain_reason or "none")))
    if order_lookup is not None:
        ins["order_lookups_total"].add(1, _labels(dict(labels, outcome=order_lookup)))


def record_quality(result, **labels):
    """Resultado de UM probe rotulado (proxy sintetico, nao trafego humano).

    `result` aceita: passed (bool), reason (str), latency_ms, hit (0/1 ou None),
    reciprocal_rank (float ou None), citation_required, citation_present,
    source_checked, source_is_current, acl_violation.
    """
    if not _state["enabled"]:
        return
    ins = _state["instruments"]
    common = _labels(labels)
    # outcome "fallback": a dependencia caiu e o assistente recusou com honestidade.
    # Nao e erro de qualidade (nao mentiu), e sim sinal tecnico; fica fora do pass_rate.
    outcome = result.get("outcome") or ("pass" if result.get("passed") else "fail")
    ins["quality_probes_total"].add(1, _labels(dict(
        labels, outcome=outcome, reason=result.get("reason") or "ok")))
    if result.get("latency_ms") is not None:
        ins["quality_probe_latency_ms"].record(float(result["latency_ms"]), common)
    if result.get("hit") is not None:
        ins["quality_retrieval_probes_total"].add(1, common)
        ins["quality_retrieval_hits_total"].add(int(bool(result["hit"])), common)
        ins["quality_retrieval_rr_sum_total"].add(float(result.get("reciprocal_rank") or 0.0), common)
    if result.get("citation_required"):
        ins["quality_citation_required_total"].add(1, common)
        ins["quality_citation_present_total"].add(int(bool(result.get("citation_present"))), common)
    if result.get("source_checked"):
        ins["quality_source_checked_total"].add(1, common)
        current = bool(result.get("source_is_current"))
        ins["quality_source_current_total"].add(int(current), common)
        ins["quality_source_stale_total"].add(int(not current), common)
    ins["quality_acl_violations_total"].add(int(bool(result.get("acl_violation"))), common)


def prime_quality_series(label_sets, reasons):
    """Cria as series com valor 0 antes do primeiro probe.

    Por que: o Prometheus calcula increase() a partir da SEGUNDA amostra de uma
    serie. Sem este zero inicial, os primeiros probes de cada cenario novo
    sumiriam da janela e o n do alerta ficaria subestimado.
    """
    if not _state["enabled"]:
        return
    ins = _state["instruments"]
    for labels in label_sets:
        common = _labels(labels)
        for name in ("quality_retrieval_probes_total", "quality_retrieval_hits_total",
                     "quality_retrieval_rr_sum_total", "quality_citation_required_total",
                     "quality_citation_present_total", "quality_source_checked_total",
                     "quality_source_current_total", "quality_source_stale_total",
                     "quality_acl_violations_total"):
            ins[name].add(0, common)
        ins["quality_probes_total"].add(0, _labels(dict(labels, outcome="pass", reason="ok")))
        ins["quality_probes_total"].add(0, _labels(dict(labels, outcome="fallback", reason="dependency_unavailable")))
        for reason in reasons:
            ins["quality_probes_total"].add(0, _labels(dict(labels, outcome="fail", reason=reason)))


def prime_request_series(routes, status_codes, order_outcomes=()):
    """Mesmo motivo de prime_quality_series, para o sinal tecnico do runtime."""
    if not _state["enabled"]:
        return
    ins = _state["instruments"]
    for route in routes:
        for code in status_codes:
            ins["requests_total"].add(0, _labels({"route": route, "status_code": code}))
    for outcome in order_outcomes:
        ins["order_lookups_total"].add(0, _labels({"outcome": outcome}))
