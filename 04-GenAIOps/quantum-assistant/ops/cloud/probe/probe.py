"""Probe de qualidade da Demo 5.2 NA AWS: perguntas rotuladas contra o live, pelo ALB.

Roda dentro do conteiner qc-probe, na EC2 qc-assistant-obs. Reaproveita o
simulador local (ops/simulator.py: sorteio por seed e julgamento do golden_v1
com as regras do gate, evals/run_eval.py) e acrescenta o que a 5.1 trouxe:
casos do golden_graph_v1 julgados contra o caminho que o Neo4j devolveu
(/lab/compare), e o grafo de REFERENCIA (graph/seed + front matter do corpus
sincronizados do repositorio) para saber se uma aresta ou um status no grafo
em runtime diverge da fonte.

  python ops/cloud/probe/probe.py loop                     # vigilancia continua (live_watch)
  python ops/cloud/probe/probe.py run --scenario stale_graph_policy --seed 42 --n 40
  python ops/cloud/probe/probe.py monitor --scenario stale_graph_policy [--at ISO] [--since ISO]
  python ops/cloud/probe/probe.py check                    # cada caso uma vez (conferencia)

Monitor (mesma semantica do ops/quality_monitor.py, agora em janela deslizante real):
  critical           qualquer stale_source_served, tenant_acl, order_auth ou wrong_graph_edge
                     na janela, COM QUALQUER n (nao ha media que compense)
  insufficient_data  n < min_n na janela (ausencia de dado NAO e verde)
  warning            pass_rate < warn_below com n >= min_n
  ok                 nenhum dos anteriores
Metricas so com labels de baixa cardinalidade (scenario_id, slice, result,
reason, environment, release_id); case_id, request_id e trace_id vao para o
span e para o JSONL, nunca para label.
Saida: stdout = resultado (JSON); stderr = progresso.
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.corpus import load_chunks  # noqa: E402
from core.route import is_injection  # noqa: E402

SERVICE_NAME = "qc-probe"
SCENARIOS_FILE = os.path.join(os.path.dirname(HERE), "scenarios.json")
# Na EC2 o compose aponta para /data (volume do host). Fora dela, um diretorio
# temporario: resultado de probe nunca vai parar dentro do repositorio.
DATA_DIR = os.environ.get("QC_PROBE_DATA_DIR", os.path.join(tempfile.gettempdir(), "qc-probe"))
KINDS = ("stale_source_served", "tenant_acl", "order_auth", "wrong_graph_edge")
LEVELS = {"insufficient_data": -1, "ok": 0, "warning": 1, "critical": 2}
REASONS = KINDS + ("http_error", "recusa_obrigatoria", "nao_recusou", "campos_esperados", "citacao",
                   "texto_proibido", "injection", "contrato_pedido", "rota", "fonte_obsoleta", "acl_tenant",
                   "min_citacoes", "contrato_resposta", "extra_must_not_contain", "fonte_fora_do_corpus_aprovado",
                   "caminho_grafo")


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def parse_ts(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ telemetria
class Telemetry:
    """OpenTelemetry proprio do probe. Sem endpoint ou sem pacote: no-op.

    Por que nao core/telemetry.py: la os labels permitidos sao os do runtime
    local (scenario, corpus_version...). O contrato da 5.2 usa scenario_id e as
    metricas qc_probe_*/qc_quality_*; um modulo separado nao muda o legado.
    """

    def __init__(self, instance_id, environment):
        self.enabled = False
        self.tracer = None
        self.providers = []
        self.c = {}
        self.gauge_sources = []
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            log("telemetria DESLIGADA: OTEL_EXPORTER_OTLP_ENDPOINT nao definido (JSONL continua)")
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
            log("telemetria DESLIGADA: opentelemetry-sdk nao instalado neste python")
            return
        # service.instance.id FIXO por modo/cenario: o replay de restore substitui
        # a serie do replay do incidente, em vez de criar uma serie paralela que
        # manteria o "critical" antigo vivo por 5 min no Collector.
        resource = Resource.create({"service.name": SERVICE_NAME, "service.version": "5.2",
                                    "deployment.environment.name": environment,
                                    "service.instance.id": instance_id})
        tp = TracerProvider(resource=resource)
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=1000))
        trace.set_tracer_provider(tp)
        interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "5000"))
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)
        mp = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(mp)
        meter = metrics.get_meter("qc-probe")
        self.tracer = trace.get_tracer("qc-probe")
        for name, desc in (
                ("qc_probe_total", "Probes rotulados por resultado (proxy sintetico, nao trafego humano)"),
                ("qc_probe_stale_source_served_total", "Probes que receberam fonte obsoleta/fora de vigencia"),
                ("qc_probe_tenant_acl_violation_total", "Probes com documento de outro tenant ou interno"),
                ("qc_probe_wrong_graph_edge_total", "Probes cujo caminho tem aresta que nao existe na fonte"),
                ("qc_probe_order_auth_violation_total", "Probes com status de pedido sem autorizacao"),
                ("qc_probe_source_checked_total", "Probes com fonte citada verificavel"),
                ("qc_probe_source_current_total", "Probes cuja fonte citada esta vigente"),
                ("qc_probe_retrieval_cases_total", "Probes com documento relevante esperado"),
                ("qc_probe_retrieval_hits_total", "Probes com o documento esperado no top-k"),
                ("qc_probe_retrieval_rr_sum_total", "Soma de 1/posicao do primeiro relevante"),
                ("qc_probe_citation_required_total", "Probes que exigem citacao"),
                ("qc_probe_citation_present_total", "Probes com a citacao esperada"),
                ("qc_probe_graph_path_checked_total", "Probes com caminho de grafo esperado"),
                ("qc_probe_graph_path_correct_total", "Probes com o caminho de grafo correto")):
            self.c[name] = meter.create_counter(name, description=desc)
        self.c["latency"] = meter.create_histogram("qc_probe_latency_seconds", unit="s",
                                                   description="Latencia vista pelo probe")
        from opentelemetry.metrics import Observation

        def gauge(name, desc):
            def cb(_options):
                out = []
                for source in self.gauge_sources:
                    for value, attrs in source(name):
                        out.append(Observation(value, attrs))
                return out
            meter.create_observable_gauge(name, callbacks=[cb], description=desc)
        for name, desc in (
                ("qc_quality_status", "-1 insufficient_data, 0 ok, 1 warning, 2 critical (janela deslizante)"),
                ("qc_quality_pass_rate", "Taxa de aprovacao na janela (so existe com n > 0)"),
                ("qc_quality_samples", "n julgado na janela"),
                ("qc_quality_min_samples", "n minimo configurado para a janela"),
                ("qc_quality_window_violations", "Violacoes criticas na janela por tipo"),
                ("qc_probe_observed_version", "Ultima combinacao de versoes vista pelo probe (=1)")):
            gauge(name, desc)
        self.providers = [tp, mp]
        self.enabled = True

    def span(self, name, attrs):
        if not self.enabled:
            return _NoSpan()
        return self.tracer.start_as_current_span(name, attributes={k: v for k, v in attrs.items() if v is not None})

    def inject(self, headers):
        if self.enabled:
            from opentelemetry import propagate
            propagate.inject(headers)
        return headers

    def trace_id(self):
        if not self.enabled:
            return None
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None

    def add(self, name, value, attrs):
        if self.enabled:
            self.c[name].add(value, attrs)

    def flush(self):
        for p in self.providers:
            p.force_flush()

    def shutdown(self):
        for p in self.providers:
            try:
                p.shutdown()
            except Exception:  # noqa: BLE001 - desligar nunca derruba o probe
                pass


class _NoSpan:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_attribute(self, *_a):
        pass


# ------------------------------------------------------------------ monitor
class WindowMonitor:
    """Janela deslizante por cenario: o que o qc_quality_status mostra AGORA."""

    def __init__(self, cfg, environment, events_path=None):
        self.window_s = int(cfg.get("window_s", 120))
        self.min_n = int(cfg.get("min_n", 20))
        self.warn_below = float(cfg.get("warn_below", 0.90))
        self.environment = environment
        self.events_path = events_path
        self.rows = collections.defaultdict(collections.deque)
        self.last_level = {}
        self.versions = {}
        self.lock = threading.Lock()

    def add(self, row):
        with self.lock:
            self.rows[row["scenario_id"]].append(row)
            v = row.get("versions") or {}
            self.versions = {"release_id": row.get("release_id") or "unknown",
                             "graph_version": v.get("graph_version") or "unknown",
                             "graph_seed_version": v.get("graph_seed_version") or "unknown",
                             "corpus_version": v.get("corpus_version") or "unknown"}
        self.evaluate(row["scenario_id"])

    def summary(self, scenario, at=None):
        at = at or time.time()
        with self.lock:
            q = self.rows[scenario]
            while q and q[0]["_t"] < at - 3 * self.window_s:
                q.popleft()
            win = [r for r in q if at - self.window_s < r["_t"] <= at]
        return summarize(win, self.window_s, self.min_n, self.warn_below)

    def evaluate(self, scenario):
        s = self.summary(scenario)
        prev = self.last_level.get(scenario)
        if s["level"] != prev:
            self.last_level[scenario] = s["level"]
            event = {"ts": now_iso(), "event": "quality.status_changed", "scenario_id": scenario,
                     "environment": self.environment, "level": s["level"], "previous_level": prev,
                     "window": f"{self.window_s}s", **{k: v for k, v in s.items() if k != "level"},
                     **self.versions}
            log(f"[monitor] {scenario}: {prev} -> {s['level']} ({s['why']}) n={s['n']}")
            if self.events_path:
                with open(self.events_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return s

    def observe(self, name):
        """Callback dos gauges: calcula na hora da coleta (janela de verdade)."""
        out = []
        base_window = f"{self.window_s // 60}m" if self.window_s % 60 == 0 else f"{self.window_s}s"
        for scenario in list(self.rows):
            s = self.summary(scenario)
            attrs = {"scenario_id": scenario, "environment": self.environment, "window": base_window}
            if name == "qc_quality_status":
                out.append((LEVELS[s["level"]], attrs))
            elif name == "qc_quality_pass_rate" and s["n"] > 0:
                out.append((s["pass_rate"], attrs))
            elif name == "qc_quality_samples":
                out.append((s["n"], attrs))
            elif name == "qc_quality_min_samples":
                out.append((self.min_n, attrs))
            elif name == "qc_quality_window_violations":
                for kind in KINDS:
                    out.append((s["violations"][kind], dict(attrs, kind=kind)))
        if name == "qc_probe_observed_version" and self.versions:
            out.append((1, dict(self.versions, environment=self.environment)))
        return out


def summarize(rows, window_s, min_n, warn_below):
    judged = [r for r in rows if r["result"] in ("pass", "fail")]
    violations = {k: sum(1 for r in rows if k in (r.get("kinds") or [])) for k in KINDS}
    n = len(judged)
    passed = sum(1 for r in judged if r["result"] == "pass")
    pass_rate = round(passed / n, 4) if n else None
    worst = next((k for k in KINDS if violations[k] > 0), None)
    if worst:
        level, why = "critical", worst
    elif n < min_n:
        level, why = "insufficient_data", f"n={n} < {min_n}"
    elif pass_rate < warn_below:
        level, why = "warning", f"pass_rate={pass_rate} < {warn_below}"
    else:
        level, why = "ok", "dentro do limiar"
    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    return {"level": level, "why": why, "n": n, "n_fallback": sum(1 for r in rows if r["result"] == "fallback"),
            "pass_rate": pass_rate, "violations": violations, "window_s": window_s, "min_n": min_n,
            "warn_below": warn_below,
            "p95_latency_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "http_non_200": sum(1 for r in rows if r.get("http_status") != 200),
            "sample_trace_ids": [r["trace_id"] for r in rows if r["result"] == "fail" and r.get("trace_id")][:3],
            "fail_reasons": dict(collections.Counter(r["reason"] for r in judged if r["result"] == "fail")),
            "first_ts": rows[0]["ts"] if rows else None, "last_ts": rows[-1]["ts"] if rows else None}


# ------------------------------------------------------------------ julgamento
class Judge:
    def __init__(self):
        self.sim = load_module("qc_simulator", os.path.join(ROOT, "ops", "simulator.py"))
        self.run_eval = self.sim.load_run_eval()
        # Referencia = corpus e seed do REPOSITORIO sincronizados nesta EC2, nao
        # o que o runtime carregou: se o grafo em runtime reativar a v2, o probe
        # ainda sabe que a v2 e obsoleta.
        self.meta_by_chunk = {c["chunk_id"]: c for c in load_chunks(os.path.join(ROOT, "corpus"))}
        self.meta_by_doc = {}
        for c in self.meta_by_chunk.values():
            self.meta_by_doc.setdefault(f"{c['doc_id']}@v{c['version']}", c)
        from graph.loader import build_graph
        ref = build_graph(ROOT)
        self.ref_edges = {(e["type"], e["source"], e["target"]) for e in ref["edges"]}

    def is_graph_case(self, case):
        return "gate_strategies" in case

    def _acl_bad(self, meta, case):
        return meta["tenant"] != case.get("tenant", "quantum") or (
            meta["access_level"] != "public" and case.get("user_role", "customer") == "customer")

    def judge_legacy(self, case, resp, as_of):
        """golden_v1: as MESMAS regras do gate (simulator.classify -> run_eval.check_case)."""
        r = self.sim.classify(self.run_eval, case, resp, self.meta_by_chunk, as_of, [])
        kinds = []
        if not r["source_is_current"]:
            kinds.append("stale_source_served")
        if r["acl_violation"]:
            kinds.append("tenant_acl")
        if (case["expected_route"] == "order_status" and case.get("expected_order_status") is None
                and (resp.get("order") or {}).get("status")):
            kinds.append("order_auth")
        r["kinds"] = kinds
        r["graph_path_checked"] = False
        r["graph_path_correct"] = None
        if kinds:
            r["outcome"], r["passed"], r["reason"] = "fail", False, kinds[0]
        return r

    def judge_graph(self, case, entry, order, as_of):
        """golden_graph_v1: resposta + caminho do Neo4j contra a fonte de referencia."""
        failed, kinds = [], []
        cites = entry.get("citations") or []
        cited_keys = [f"{c['doc_id']}@v{c['version']}" for c in cites]
        answer = self.run_eval.norm(entry.get("answer") or "")
        for item in cites + [x for x in entry.get("results") or [] if x.get("chunk_id")]:
            meta = self.meta_by_chunk.get(item["chunk_id"])
            if meta and self._acl_bad(meta, case) and "tenant_acl" not in kinds:
                kinds.append("tenant_acl")
        stale = False
        for c in cites:
            meta = self.meta_by_chunk.get(c["chunk_id"])
            if meta is None or self.run_eval.check_access(meta, as_of, None, None, self.run_eval.CURRENT_FILTERS):
                stale = True
            if case.get("expected_doc_id") == c["doc_id"] and str(c["version"]) != str(case.get("expected_doc_version")):
                stale = True
        for key in case.get("must_not_cite") or []:
            if key in cited_keys:
                meta = self.meta_by_doc.get(key)
                if meta and self._acl_bad(meta, case):
                    kinds.append("tenant_acl") if "tenant_acl" not in kinds else None
                else:
                    stale = True
        if stale:
            kinds.insert(0, "stale_source_served")
        edges = (entry.get("path") or {}).get("edges") or []
        wrong = [e for e in edges if (e["type"], e["source"], e["target"]) not in self.ref_edges]
        if wrong:
            kinds.append("wrong_graph_edge")
        exp_edges = case.get("expected_path_edges") or []
        path_checked = bool(exp_edges) and entry.get("strategy") == "graph"
        path_ok = None
        if path_checked:
            selected = {e["type"] for e in edges if e.get("role") == "selected"}
            path_ok = set(exp_edges) <= selected and not wrong
            if not path_ok:
                failed.append("caminho_grafo")
        if case.get("must_abstain") is True:
            exp = case.get("expected_abstain_reason")
            if not entry.get("abstained") or (exp and entry.get("abstain_reason") != exp):
                failed.append("recusa_obrigatoria")
        elif case.get("must_abstain") is False and entry.get("abstained"):
            failed.append("nao_recusou")
        if [f for f in case.get("expected_answer_fields") or [] if self.run_eval.norm(f) not in answer]:
            failed.append("campos_esperados")
        if [t for t in case.get("must_not_contain") or [] if self.run_eval.norm(t) in answer]:
            failed.append("texto_proibido")
        if is_injection(entry.get("answer") or ""):
            failed.append("injection")
        exp_doc = case.get("expected_doc_id")
        citation_required = bool(exp_doc) and case.get("must_abstain") is not True
        citation_present = any(c["doc_id"] == exp_doc and str(c["version"]) == str(case.get("expected_doc_version"))
                               for c in cites) if citation_required else None
        if citation_required and not citation_present:
            failed.append("citacao")
        got_status = (order or {}).get("status")
        if case.get("expected_order_status") is not None and got_status != case["expected_order_status"]:
            failed.append("contrato_pedido")
        if case.get("mode") == "ask" and case.get("expected_order_status") is None and got_status:
            kinds.append("order_auth")
        rank = None
        if citation_required:
            for pos, item in enumerate(entry.get("results") or [], start=1):
                if item.get("doc_id") == exp_doc and str(item.get("version")) == str(case.get("expected_doc_version")):
                    rank = pos
                    break
        honest_fallback = entry.get("abstain_reason") == "order_api_unavailable" and not got_status
        if honest_fallback and case.get("mode") == "ask":
            outcome, reason = "fallback", "dependency_unavailable"
        elif kinds:
            outcome, reason = "fail", kinds[0]
        elif failed:
            outcome, reason = "fail", failed[0]
        else:
            outcome, reason = "pass", "ok"
        return {"passed": outcome == "pass", "outcome": outcome, "reason": reason, "failed_checks": failed,
                "kinds": kinds, "hit": (rank is not None) if citation_required else None,
                "reciprocal_rank": (1.0 / rank if rank else 0.0) if citation_required else None, "rank": rank,
                "citation_required": citation_required, "citation_present": citation_present,
                "source_checked": bool(cites), "source_is_current": not stale,
                "acl_violation": "tenant_acl" in kinds, "graph_path_checked": path_checked,
                "graph_path_correct": path_ok, "wrong_edges": [f"{e['source']}-[{e['type']}]->{e['target']}"
                                                                for e in wrong][:3]}


# ------------------------------------------------------------------ probe
class Prober:
    def __init__(self, target, scenario_id, cfg, environment, telemetry, monitor, timeout=10.0):
        self.target = target.rstrip("/")
        self.scenario_id = scenario_id
        self.scenario = cfg["scenarios"][scenario_id]
        self.environment = environment
        self.tel = telemetry
        self.monitor = monitor
        self.timeout = timeout
        self.judge = Judge()
        sets = [s for s in self.scenario["sets"] if os.path.exists(os.path.join(ROOT, "evals", f"{s}.jsonl"))]
        self.pool, self.weights = self.judge.sim.build_pool(self.judge.run_eval, dict(self.scenario, sets=sets))
        self.legacy_strategy = self.scenario.get("legacy_strategy", "graph")
        self.release_id = "unknown"
        self.primed = set()

    def http(self, method, path, body, headers):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.target + path, data=data, method=method,
                                     headers=dict({"Content-Type": "application/json"}, **headers))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as err:
            return err.code, {}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
            return 0, {"error": type(err).__name__}

    def health(self):
        code, h = self.http("GET", "/health", None, {})
        if code == 200:
            self.release_id = h.get("release_id") or "unknown"
        return code, h

    def labels(self):
        return {"scenario_id": self.scenario_id, "environment": self.environment, "release_id": self.release_id}

    def prime(self):
        """Zero em cada serie antes do primeiro probe: increase() precisa de 2 amostras."""
        key = self.release_id
        if key in self.primed or not self.tel.enabled:
            return
        self.primed.add(key)
        base = self.labels()
        for name in self.tel.c:
            if name.endswith("_total") and name != "qc_probe_total":
                self.tel.add(name, 0, base)
        for slc in self.pool:
            self.tel.add("qc_probe_total", 0, dict(base, slice=slc, result="pass", reason="ok"))
            for reason in KINDS + ("http_error",):
                self.tel.add("qc_probe_total", 0, dict(base, slice=slc, result="fail", reason=reason))

    def request_for(self, case, request_id):
        hdr = {"X-Request-Id": request_id, "X-QC-Scenario": self.scenario_id, "X-QC-Case": case["case_id"]}
        if self.judge.is_graph_case(case):
            strategies = case.get("gate_strategies") or ["graph"]
            strategy = "graph" if "graph" in strategies else strategies[0]
            body = {k: case.get(k) for k in ("question", "tenant", "user_role", "order_id", "auth_token",
                                             "as_of_date")}
            if case.get("mode") == "ask":
                body.update({"strategy": strategy, "customer_id": case.get("customer_id"),
                             "request_id": request_id, "scenario_id": self.scenario_id})
                return "/assistant/ask", strategy, {k: v for k, v in body.items() if v is not None}, hdr
            body["strategies"] = [strategy]
            return "/lab/compare", strategy, {k: v for k, v in body.items() if v is not None}, hdr
        body = {k: case.get(k) for k in ("question", "customer_id", "auth_token", "order_id", "tenant", "user_role")}
        body.update({"request_id": request_id, "scenario_id": self.scenario_id, "strategy": self.legacy_strategy})
        return "/assistant/ask", self.legacy_strategy, {k: v for k, v in body.items() if v is not None}, hdr

    def probe(self, case, index, seed):
        # O app so aceita [A-Za-z0-9-] no request_id (contrato): sem "_" do scenario_id.
        tag = self.scenario_id.replace("_", "-")[:18]
        request_id = f"probe-{tag}-{seed}-{index:05d}-{uuid.uuid4().hex[:6]}"
        path, strategy, body, hdr = self.request_for(case, request_id)
        attrs = {"qc.scenario_id": self.scenario_id, "qc.case_id": case["case_id"], "qc.slice": case["slice"],
                 "qc.strategy": strategy, "request_id": request_id, "test_sample": True}
        with self.tel.span("quality.evaluate", attrs) as sp:
            started = time.monotonic()
            status, resp = self.http("POST", path, body, self.tel.inject(hdr))
            latency_ms = (time.monotonic() - started) * 1000
            trace_id = self.tel.trace_id() or resp.get("trace_id")
            if status == 200:
                if path == "/lab/compare":
                    entry = dict((resp.get("strategies") or {}).get(strategy) or {}, strategy=strategy)
                    as_of = resp.get("as_of_date") or case.get("as_of_date")
                    result = self.judge.judge_graph(case, entry, resp.get("order"), as_of)
                elif self.judge.is_graph_case(case):
                    entry = dict(resp, strategy=strategy)
                    result = self.judge.judge_graph(case, entry, resp.get("order"),
                                                    resp.get("as_of_date") or case.get("as_of_date"))
                else:
                    entry = resp
                    result = self.judge.judge_legacy(case, resp, resp.get("as_of_date")
                                                     or datetime.date.today().isoformat())
            else:
                entry = {}
                result = {"passed": False, "outcome": "fail", "reason": "http_error", "failed_checks": [f"http_{status}"],
                          "kinds": [], "hit": None, "reciprocal_rank": None, "rank": None, "citation_required": False,
                          "citation_present": None, "source_checked": False, "source_is_current": True,
                          "acl_violation": False, "graph_path_checked": False, "graph_path_correct": None}
            sp.set_attribute("qc.result", result["outcome"])
            sp.set_attribute("qc.reason", result["reason"])
            sp.set_attribute("http.response.status_code", status)
        versions = resp.get("versions") or {}
        cites = entry.get("citations") or []
        self.record(case, result, latency_ms)
        row = {"ts": now_iso(), "test_sample": True, "seed": seed, "sample_index": index,
               "scenario_id": self.scenario_id, "case_id": case["case_id"], "slice": case["slice"],
               "severity": case.get("severity"), "endpoint": path, "strategy": strategy,
               "request_id": request_id, "trace_id": trace_id, "http_status": status,
               "latency_ms": round(latency_ms, 1), "result": result["outcome"], "reason": result["reason"],
               "failed_checks": result["failed_checks"], "kinds": result["kinds"],
               "cited": [f"{c['doc_id']}@v{c['version']}" for c in cites],
               "selected_policy_version": entry.get("selected_policy_version"),
               "wrong_edges": result.get("wrong_edges") or [],
               "abstain_reason": entry.get("abstain_reason"), "environment": resp.get("environment") or self.environment,
               "release_id": resp.get("release_id") or versions.get("release_id") or self.release_id,
               "graph_version": versions.get("graph_version"), "graph_seed_version": versions.get("graph_seed_version"),
               "corpus_version": versions.get("corpus_version"), "image_digest": versions.get("image_digest"),
               "versions": {k: versions.get(k) for k in ("graph_version", "graph_seed_version", "corpus_version")},
               "_t": time.time()}
        if self.monitor:
            self.monitor.add(row)
        return row

    def record(self, case, r, latency_ms):
        if not self.tel.enabled:
            return
        base = self.labels()
        self.tel.add("qc_probe_total", 1, dict(base, slice=case["slice"], result=r["outcome"], reason=r["reason"]))
        self.tel.c["latency"].record(latency_ms / 1000.0, {"scenario_id": self.scenario_id,
                                                           "environment": self.environment})
        for kind, name in (("stale_source_served", "qc_probe_stale_source_served_total"),
                           ("tenant_acl", "qc_probe_tenant_acl_violation_total"),
                           ("wrong_graph_edge", "qc_probe_wrong_graph_edge_total"),
                           ("order_auth", "qc_probe_order_auth_violation_total")):
            self.tel.add(name, int(kind in r["kinds"]), base)
        if r["source_checked"]:
            self.tel.add("qc_probe_source_checked_total", 1, base)
            self.tel.add("qc_probe_source_current_total", int(bool(r["source_is_current"])), base)
        if r["hit"] is not None:
            self.tel.add("qc_probe_retrieval_cases_total", 1, base)
            self.tel.add("qc_probe_retrieval_hits_total", int(bool(r["hit"])), base)
            self.tel.add("qc_probe_retrieval_rr_sum_total", float(r["reciprocal_rank"] or 0.0), base)
        if r["citation_required"]:
            self.tel.add("qc_probe_citation_required_total", 1, base)
            self.tel.add("qc_probe_citation_present_total", int(bool(r["citation_present"])), base)
        if r.get("graph_path_checked"):
            self.tel.add("qc_probe_graph_path_checked_total", 1, base)
            self.tel.add("qc_probe_graph_path_correct_total", int(bool(r["graph_path_correct"])), base)


def write_row(fh, row):
    fh.write(json.dumps({k: v for k, v in row.items() if not k.startswith("_") and k != "versions"},
                        ensure_ascii=False) + "\n")
    fh.flush()


def load_cfg():
    with open(SCENARIOS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def target_url(args):
    url = args.url or os.environ.get("QC_TARGET_URL")
    if not url:
        log("sem alvo: defina QC_TARGET_URL (http://<dns-do-alb>) ou --url")
        sys.exit(2)
    return url


def cmd_loop(args):
    cfg = load_cfg()
    env = os.environ.get("QC_ENVIRONMENT", "live")
    os.makedirs(os.path.join(DATA_DIR, "results"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "monitor"), exist_ok=True)
    tel = Telemetry("qc-probe-loop", env)
    mon = WindowMonitor(cfg["monitor"], env, os.path.join(DATA_DIR, "monitor", "events.jsonl"))
    tel.gauge_sources.append(mon.observe)
    prober = Prober(target_url(args), args.scenario, cfg, env, tel, mon)
    rate = args.rate or prober.scenario.get("rate_per_s", 0.5)
    interval = 1.0 / rate
    rng = random.Random(args.seed)
    slices = sorted(prober.weights)
    weights = [prober.weights[s] for s in slices]
    index = 0
    log(f"loop {args.scenario} seed={args.seed} {rate}/s contra {prober.target}")
    # Um arquivo por dia: o Collector le o JSONL, e o disco nao cresce sem fim.
    t_next = time.monotonic()
    last_health = 0.0
    while True:
        if time.monotonic() - last_health > 30:
            code, _ = prober.health()
            last_health = time.monotonic()
            if code != 200:
                log(f"GET /health -> {code}: alvo fora do ar, probe espera")
            prober.prime()
        slc = rng.choices(slices, weights=weights, k=1)[0]
        case = rng.choice(prober.pool[slc])
        path = os.path.join(DATA_DIR, "results", f"{args.scenario}-{datetime.date.today().isoformat()}.jsonl")
        row = prober.probe(case, index, args.seed)
        with open(path, "a", encoding="utf-8") as fh:
            write_row(fh, row)
        index += 1
        if index % 30 == 0:
            s = mon.summary(args.scenario)
            log(f"  {index} probes; janela: {s['level']} n={s['n']} pass_rate={s['pass_rate']}")
        # Ritmo fixo e reavaliacao da janela mesmo sem probe novo (janela esvazia).
        t_next += interval
        while True:
            wait = t_next - time.monotonic()
            if wait <= 0:
                break
            time.sleep(min(wait, 1.0))
            mon.evaluate(args.scenario)


def cmd_run(args):
    """Rodada finita com a MESMA seed: replay comparavel antes e depois do restore."""
    cfg = load_cfg()
    env = os.environ.get("QC_ENVIRONMENT", "live")
    os.makedirs(os.path.join(DATA_DIR, "results"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "monitor"), exist_ok=True)
    tel = Telemetry(f"qc-probe-run-{args.scenario}", env)
    mon = WindowMonitor(cfg["monitor"], env, os.path.join(DATA_DIR, "monitor", "events.jsonl"))
    tel.gauge_sources.append(mon.observe)
    prober = Prober(target_url(args), args.scenario, cfg, env, tel, mon)
    code, health = prober.health()
    if code != 200:
        log(f"GET /health -> {code}")
        return 2
    prober.prime()
    if tel.enabled:
        tel.flush()
        time.sleep(6)
    rate = args.rate or prober.scenario.get("rate_per_s", 2.0)
    cases = prober.judge.sim.draw_cases(prober.pool, prober.weights, args.n, args.seed)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(DATA_DIR, "results", f"{args.scenario}-seed{args.seed}-{stamp}.jsonl")
    started_at = now_iso()
    rows = []
    t0 = time.monotonic()
    with open(out, "w", encoding="utf-8") as fh:
        for i, case in enumerate(cases):
            row = prober.probe(case, i, args.seed)
            write_row(fh, row)
            rows.append(row)
            wait = t0 + (i + 1) / rate - time.monotonic()
            if wait > 0:
                time.sleep(wait)
    s = mon.summary(args.scenario)
    first_bad = next((r for r in rows if r["kinds"]), None)
    if tel.enabled:
        tel.flush()
        # Mantem o processo vivo por 2 exportacoes: o Prometheus raspa o gauge
        # final antes de o processo sair (o Collector guarda o ultimo valor).
        time.sleep(args.linger)
        tel.flush()
    tel.shutdown()
    result = {"results": out, "scenario_id": args.scenario, "seed": args.seed, "n_requested": args.n,
              "started_at": started_at, "finished_at": now_iso(), "release_id": prober.release_id,
              "graph_version": rows[-1]["graph_version"] if rows else None,
              "graph_seed_version": rows[-1]["graph_seed_version"] if rows else None,
              "http_status": dict(collections.Counter(str(r["http_status"]) for r in rows)),
              "case_sequence_sha": __import__("hashlib").sha256(
                  "|".join(c["case_id"] for c in cases).encode()).hexdigest()[:12],
              "monitor": s,
              "first_violation": ({k: first_bad[k] for k in ("ts", "case_id", "kinds", "cited", "trace_id",
                                                              "selected_policy_version", "wrong_edges")}
                                  if first_bad else None),
              "answers_by_case": _answers_by_case(rows)}
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _answers_by_case(rows):
    out = {}
    for r in rows:
        if r["case_id"].startswith(("gg-incidente", "gg-troca", "troca-prazo", "gg-camiseta", "gg-tenis")):
            out.setdefault(r["case_id"], {"cited": r["cited"], "result": r["result"], "reason": r["reason"],
                                          "trace_id": r["trace_id"]})
    return out


def cmd_monitor(args):
    """Janela sobre os JSONL do disco: auditavel depois do fato (e testa janela vazia)."""
    cfg = load_cfg()["monitor"]
    window_s = args.window_s or cfg["window_s"]
    at = parse_ts(args.at) if args.at else time.time()
    since = parse_ts(args.since) if args.since else at - window_s
    rows = []
    rdir = os.path.join(DATA_DIR, "results")
    for name in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
        if not name.startswith(args.scenario) or not name.endswith(".jsonl"):
            continue
        with open(os.path.join(rdir, name), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    t = parse_ts(r["ts"])
                    if r.get("scenario_id") == args.scenario and since < t <= at:
                        rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    s = summarize(rows, int(at - since), args.min_n or cfg["min_n"], cfg["warn_below"])
    s.update({"scenario_id": args.scenario, "window_start": datetime.datetime.fromtimestamp(
        since, datetime.timezone.utc).isoformat(timespec="seconds"),
        "window_end": datetime.datetime.fromtimestamp(at, datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "JSONL do probe na EC2 obs (alerta local; nenhuma notificacao externa)",
        "signal": "proxy sintetico: probes rotulados, nao trafego humano"})
    print(json.dumps(s, ensure_ascii=False))
    return {"ok": 0, "insufficient_data": 3}.get(s["level"], 1)


def cmd_check(args):
    """Cada caso do pool uma vez, sem telemetria: confere julgamento e alvo."""
    cfg = load_cfg()
    tel = Telemetry.__new__(Telemetry)
    tel.enabled, tel.c, tel.gauge_sources, tel.providers = False, {}, [], []
    prober = Prober(target_url(args), args.scenario, cfg, "live", tel, None)
    prober.health()
    summary = collections.Counter()
    fails = []
    for slc in sorted(prober.pool):
        for case in prober.pool[slc]:
            row = prober.probe(case, 0, 0)
            summary[row["result"]] += 1
            log(f"{row['result']:8} {slc:18} {case['case_id']:40} {row['strategy']:6} {row['reason']} {row['cited']}")
            if row["result"] != "pass":
                fails.append({k: row[k] for k in ("case_id", "slice", "strategy", "reason", "failed_checks",
                                                  "kinds", "cited", "abstain_reason")})
    print(json.dumps({"counts": dict(summary), "release_id": prober.release_id, "not_pass": fails},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Probe de qualidade da Demo 5.2 na AWS")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("loop", "run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--scenario", default="live_watch" if name != "run" else "stale_graph_policy")
        sp.add_argument("--seed", type=int, default=42)
        sp.add_argument("--rate", type=float)
        sp.add_argument("--url")
        if name == "run":
            sp.add_argument("--n", type=int, default=40)
            sp.add_argument("--linger", type=float, default=12.0)
    sm = sub.add_parser("monitor")
    sm.add_argument("--scenario", default="stale_graph_policy")
    sm.add_argument("--at", help="fim da janela (ISO 8601 UTC); padrao: agora")
    sm.add_argument("--since", help="inicio da janela (ISO); padrao: at - window_s")
    sm.add_argument("--window-s", type=int)
    sm.add_argument("--min-n", type=int)
    args = p.parse_args(argv)
    return {"loop": cmd_loop, "run": cmd_run, "monitor": cmd_monitor, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
