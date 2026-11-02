"""Fachada do assistente: Assistant(settings).answer(...) -> dict do contrato.

Fluxo: route_intent -> (order_api.lookup | rag.retrieve > rag.filter_documents)
-> rag.compose_deterministic. Nenhuma etapa chama modelo de linguagem.
"""
import copy
import datetime
import json
import os
import uuid

from . import ROOT, telemetry, versions
from .composer import compose, compose_order
from .corpus import load_chunks
from .orders import HttpOrders, InMemoryOrders, OrderLookupError
from .retrieval import BM25Index, filter_chunks, rank
from .route import is_injection, route_intent

ROUTES = ("policy", "faq", "order_status", "unsupported")
ABSTAIN_REASONS = ("insufficient_evidence", "out_of_scope", "forbidden", "order_not_found",
                   "order_api_unavailable", "injection_blocked")
STATUS_DOC_ID = "status-do-pedido"

# Variaveis de ambiente vencem o arquivo: a Lambda e o runtime local definem
# release e ambiente no deploy, sem editar config/settings.json.
ENV_OVERRIDES = {
    "QC_ENVIRONMENT": ("environment",),
    "QC_RELEASE_ID": ("release_id",),
    "QC_AS_OF_DATE": ("as_of_date",),
    "QC_ORDERS_BASE_URL": ("orders", "base_url"),
}


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(base, extra):
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_settings(settings=None, root=ROOT):
    """settings: None (arquivo padrao), caminho de JSON ou dict de sobrescritas."""
    data = _read_json(os.path.join(root, "config", "settings.json"))
    if isinstance(settings, str):
        data = _read_json(settings)
    elif isinstance(settings, dict):
        data = _merge(copy.deepcopy(data), settings)
    for env, path in ENV_OVERRIDES.items():
        if os.environ.get(env):
            target = data
            for key in path[:-1]:
                target = target.setdefault(key, {})
            target[path[-1]] = os.environ[env]
    if os.environ.get("QC_ORDERS_BASE_URL"):
        data.setdefault("orders", {})["backend"] = "http"
    return data


class Assistant:
    def __init__(self, settings=None, orders=None, root=ROOT):
        self.root = root
        self.settings = load_settings(settings, root)
        r = self.settings["retrieval"]
        self.templates = _read_json(os.path.join(root, "prompts", "templates.json"))
        self.chunks = load_chunks(os.path.join(root, "corpus"))
        self.index = BM25Index(self.chunks, k1=r["bm25_k1"], b=r["bm25_b"], title_boost=r["title_boost"])
        params = {"retrieval": r, "filters": self.settings["filters"], "composer": self.settings["composer"]}
        self.versions = {
            "application_version": versions.APPLICATION_VERSION,
            "prompt_version": versions.prompt_version(self.templates),
            "corpus_version": versions.corpus_version(os.path.join(root, "corpus")),
            "index_version": versions.index_version(self.chunks, params),
            "eval_set_version": versions.eval_set_version(os.path.join(root, "evals"),
                                                          self.settings.get("eval_set_version")),
            "model_version": versions.MODEL_VERSION,
        }
        self.orders = orders or self._build_orders()

    def _build_orders(self):
        o = self.settings["orders"]
        if o.get("backend") == "http":
            return HttpOrders(o["base_url"], timeout_s=float(o["timeout_s"]), retries=int(o["retries"]),
                              backoff_s=float(o.get("backoff_s", 0.2)))
        return InMemoryOrders(_read_json(os.path.join(self.root, "orders", "fixtures.json")))

    def as_of_date(self, override=None):
        value = override or self.settings.get("as_of_date")
        return value or datetime.date.today().isoformat()

    # ---------------------------------------------------------------- etapas
    def _retrieve(self, question, tenant, user_role, as_of):
        r = self.settings["retrieval"]
        with telemetry.span("rag.retrieve", k=r["k"]) as sp:
            with telemetry.span("rag.filter_documents", as_of_date=as_of, tenant=tenant) as fsp:
                allowed, blocked = filter_chunks(self.index, as_of_date=as_of, tenant=tenant,
                                                 user_role=user_role, filters=self.settings["filters"])
                fsp.set("chunks_allowed", len(allowed))
                for reason, count in blocked.items():
                    fsp.set(f"blocked_{reason}", count)
            results = rank(self.index, question, allowed, k=int(r["k"]),
                           corpus_version=self.versions["corpus_version"])
            sp.set("results", len(results))
            if results:
                sp.set("top_doc_id", results[0]["doc_id"])
                sp.set("top_score", results[0]["score"])
        return results

    def _order(self, order_id, auth_token, request_id, tenant, user_role, as_of):
        t = self.templates
        with telemetry.span("order_api.lookup") as sp:
            if not auth_token:
                # Sem token nao ha como o servidor saber de quem e o pedido.
                sp.set("outcome", "forbidden")
                return {"answer": t["forbidden"], "citations": [], "abstained": True,
                        "abstain_reason": "forbidden", "order": None}
            if not order_id:
                sp.set("outcome", "missing_order_id")
                return {"answer": t["order_missing_id"], "citations": [], "abstained": True,
                        "abstain_reason": "insufficient_evidence", "order": None}
            try:
                order = self.orders.lookup(order_id, auth_token, request_id=request_id)
            except OrderLookupError as err:
                sp.set("outcome", err.kind)
                answer = t[err.kind].format(order_id=order_id) if err.kind == "order_not_found" else t[err.kind]
                return {"answer": answer, "citations": [], "abstained": True,
                        "abstain_reason": err.kind, "order": None}
            sp.set("outcome", "ok")
        # O documento so explica o significado do status que a API devolveu.
        allowed, _ = filter_chunks(self.index, as_of_date=as_of, tenant=tenant,
                                   user_role=user_role, filters=self.settings["filters"])
        status_chunk = next((self.index.chunks[p] for p in allowed
                             if self.index.chunks[p]["doc_id"] == STATUS_DOC_ID
                             and self.index.chunks[p]["section"] == order["status"]), None)
        with telemetry.span("rag.compose_deterministic", route="order_status"):
            out = compose_order(order, status_chunk, t)
        out["order"] = order
        return out

    # ------------------------------------------------------------------ API
    def answer(self, question, customer_id=None, auth_token=None, order_id=None, tenant="quantum",
               user_role="customer", scenario_id=None, request_id=None, as_of_date=None):
        """customer_id e informativo: quem autoriza o pedido e o token, no servidor."""
        request_id = request_id or uuid.uuid4().hex
        as_of = self.as_of_date(as_of_date)
        t = self.templates
        retrieval = []
        with telemetry.span("route_intent") as sp:
            intent, found_order = route_intent(question, order_id)
            sp.set("intent", intent)
            # Capturado aqui, dentro de um span aberto: depois que todos fecham,
            # pode nao haver trace corrente (ex.: eval local, sem adapter HTTP).
            trace_id = getattr(sp, "trace_id", None) or telemetry.current_trace_id()

        if intent == "order_status":
            route = "order_status"
            out = self._order(found_order, auth_token, request_id, tenant, user_role, as_of)
        elif intent == "unsupported":
            route = "unsupported"
            reason = "injection_blocked" if is_injection(question) else "out_of_scope"
            out = {"answer": t[reason], "citations": [], "abstained": True, "abstain_reason": reason}
        else:
            retrieval = self._retrieve(question, tenant, user_role, as_of)
            with telemetry.span("rag.compose_deterministic") as sp:
                out = compose(question, retrieval, self.index.idf, t, self.settings)
                sp.set("abstain_reason", out["abstain_reason"] or "none")
                sp.set("injection_dropped", out.pop("injection_dropped", 0))
            route = "faq" if out.pop("evidence_category", None) == "faq" else "policy"

        response = {
            "answer": out["answer"],
            "mode": "deterministic",
            "route": route,
            "citations": out["citations"],
            "abstained": out["abstained"],
            "abstain_reason": out["abstain_reason"],
            "order": out.get("order"),
            # Extra ao contrato (aditivo): o que o retriever devolveu, para medir Hit@k/MRR.
            "retrieval": [{k: r[k] for k in ("doc_id", "chunk_id", "corpus_version", "score")}
                          | {"version": r["chunk"]["version"]} for r in retrieval],
            "versions": dict(self.versions),
            "request_id": request_id,
            "trace_id": trace_id or telemetry.current_trace_id(),
            "release_id": self.settings.get("release_id"),
            "environment": self.settings.get("environment"),
            "as_of_date": as_of,
        }
        if scenario_id:
            response["scenario_id"] = scenario_id
        # Log sem PII: nada da pergunta, do token ou do cliente.
        telemetry.log("assistant.answer", route=route, abstained=response["abstained"],
                      abstain_reason=response["abstain_reason"], citations=len(response["citations"]),
                      request_id=request_id, scenario_id=scenario_id,
                      release_id=response["release_id"], corpus_version=self.versions["corpus_version"])
        return response
