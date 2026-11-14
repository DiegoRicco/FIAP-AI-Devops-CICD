"""API publica do laboratorio 5.1 (porta 8000): FastAPI sobre o lab.engine.Engine.

Rotas (todas tambem sob QC_PATH_PREFIX, ex.: /candidate/ready no service candidate):
  GET  /health                       processo vivo + versoes (nunca depende do Neo4j)
  GET  /ready                        200 so com Neo4j + indice ONLINE + modelo + grafo; senao 503
  GET  /lab/meta                     versoes, contagens, indice, datas simuladas, tenants, produtos
  POST /lab/compare                  mesma pergunta nas 3 estrategias (bm25, vector, graph)
  GET  /lab/graph/product/{id}       caminho do produto no grafo (id validado; sem Cypher do usuario)
  POST /assistant/ask                contrato legado do core + strategy opcional
  GET  /                             UI estatica (web/static), caminhos relativos

Nenhuma regra de negocio mora aqui: este arquivo valida a entrada, chama o engine,
traduz erro de dependencia em 503 controlado e registra telemetria sem PII.
"""
import asyncio
import contextlib
import datetime
import os
import re
from enum import Enum
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import ROOT, telemetry

from . import observability as obs
from .runtime import PRESET_DATES, STRATEGIES, TENANTS, USER_ROLES, Runtime, log
from .security import BodyLimitMiddleware, PathPrefixMiddleware, SecurityHeadersMiddleware

STATIC_DIR = os.path.join(ROOT, "web", "static")
MAX_QUESTION = 500
PRODUCT_ID_RE = re.compile(r"^[a-z0-9-]{2,40}$")
ORDER_ID_RE = r"^[A-Za-z0-9-]{1,40}$"
# Faixa de datas simuladas aceitas: cobre as datas do roteiro com folga e impede
# "viagens" absurdas no tempo (ano 1900, 9999) que so serviriam para fuzzing.
MIN_DATE, MAX_DATE = datetime.date(2020, 1, 1), datetime.date(2030, 12, 31)


class Strategy(str, Enum):
    bm25 = "bm25"
    vector = "vector"
    graph = "graph"


class Tenant(str, Enum):
    quantum = "quantum"
    parceiro_x = "parceiro-x"


class UserRole(str, Enum):
    customer = "customer"
    agent = "agent"


def _check_date(value):
    if value is None:
        return value
    try:
        parsed = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("use o formato AAAA-MM-DD") from None
    if not (MIN_DATE <= parsed <= MAX_DATE) or len(value) != 10:
        raise ValueError(f"data simulada fora da faixa {MIN_DATE}..{MAX_DATE}")
    return value


class _Base(BaseModel):
    # extra=forbid: campo desconhecido e erro, nao e ignorado em silencio
    # (um "user_rol" digitado errado viraria customer sem ninguem perceber).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("question", check_fields=False)
    @classmethod
    def _question_not_blank(cls, v):
        if not v or not v.strip():
            raise ValueError("pergunta vazia")
        return v

    @field_validator("as_of_date", check_fields=False)
    @classmethod
    def _date(cls, v):
        return _check_date(v)


class CompareRequest(_Base):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    as_of_date: str = Field(default=PRESET_DATES[0], description="data SIMULADA da pergunta (AAAA-MM-DD)")
    tenant: Tenant = Tenant.quantum
    user_role: UserRole = UserRole.customer
    order_id: Optional[str] = Field(default=None, pattern=ORDER_ID_RE)
    auth_token: Optional[str] = Field(default=None, max_length=200)
    strategies: Optional[List[Strategy]] = Field(default=None, min_length=1, max_length=3)


class AskRequest(_Base):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    customer_id: Optional[str] = Field(default=None, max_length=64)
    auth_token: Optional[str] = Field(default=None, max_length=200)
    order_id: Optional[str] = Field(default=None, pattern=ORDER_ID_RE)
    tenant: Tenant = Tenant.quantum
    user_role: UserRole = UserRole.customer
    scenario_id: Optional[str] = Field(default=None, max_length=64)
    request_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")
    as_of_date: Optional[str] = None
    strategy: Optional[Strategy] = None


def _err(status, error, detail, request_id=None):
    body = {"error": error, "detail": detail}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=status, content=body)


def _is_dependency_error(err):
    """Erros de dependencia (Neo4j, rede) viram 503; bug nosso continua 500."""
    module = type(err).__module__ or ""
    return (module.startswith("neo4j") or isinstance(err, (ConnectionError, TimeoutError, OSError))
            or type(err).__name__ in ("ServiceUnavailable", "SessionExpired", "DependencyUnavailable"))


def _bearer(request):
    value = request.headers.get("authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else None


def create_app(runtime=None, path_prefix=None, start_runtime=True):
    runtime = runtime or Runtime()
    prefix = os.environ.get("QC_PATH_PREFIX", "") if path_prefix is None else path_prefix
    default_strategy = os.environ.get("QC_DEFAULT_STRATEGY", "graph")
    if default_strategy not in STRATEGIES:
        default_strategy = "graph"

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # O bootstrap vai para uma thread: a porta abre na hora e /ready conta a verdade.
        if start_runtime:
            runtime.start()
        yield
        runtime.stop()

    app = FastAPI(title="Quantum Commerce AI Customer Assistant - Central de Evidencias",
                  version="1.1.0", docs_url="/docs", redoc_url=None, openapi_url="/openapi.json",
                  lifespan=lifespan)
    app.state.runtime = runtime

    # ------------------------------------------------------------ erros
    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc):
        # Sem ecoar o valor enviado (a pergunta pode ter dado pessoal): so campo e motivo.
        detail = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "msg": e.get("msg")}
                  for e in exc.errors()]
        return _err(422, "invalid_request", detail, request.state.request_id)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc):
        error = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _err(exc.status_code, error, exc.detail if isinstance(exc.detail, str) else error,
                    getattr(request.state, "request_id", None))

    async def call_engine(request, fn, *args, **kwargs):
        """Executa o engine fora do event loop e traduz falhas em erro JSON controlado."""
        rid = request.state.request_id
        if not runtime.is_ready() and not runtime.check_ready().get("ready"):
            state = runtime._ready_cache
            return None, _err(503, "not_ready", {"reason": state.get("reason") or "bootstrap_or_dependency",
                                                 "checks": state.get("checks")}, rid)
        try:
            return await asyncio.to_thread(fn, *args, **kwargs), None
        except ValueError as err:
            return None, _err(422, "invalid_request", str(err)[:200], rid)
        except Exception as err:  # noqa: BLE001
            if _is_dependency_error(err):
                # Neo4j caiu no meio: recusa honesta, nunca resposta inventada.
                runtime.check_ready()
                log("dependency.unavailable", request_id=rid, route=request.url.path, error=type(err).__name__)
                return None, _err(503, "dependency_unavailable",
                                  {"dependency": "neo4j", "kind": type(err).__name__}, rid)
            log("engine.error", request_id=rid, route=request.url.path, error=type(err).__name__)
            return None, _err(500, "internal_error", type(err).__name__, rid)

    def _route_attrs(request, strategy):
        # Atributos do contrato; nada da pergunta, do token ou do cliente.
        v = runtime.versions()
        attrs = {"qc.release_id": v.get("release_id"), "qc.environment": os.environ.get("QC_ENVIRONMENT", "local"),
                 "qc.graph_version": v.get("graph_version"), "qc.graph_seed_version": v.get("graph_seed_version"),
                 "qc.corpus_version": v.get("corpus_version"), "qc.vector_index_version": v.get("vector_index_version"),
                 "qc.embedding_model_id": v.get("embedding_model_id"), "qc.strategy": strategy,
                 "qc.scenario_id": getattr(request.state, "scenario_id", None),
                 "qc.case_id": getattr(request.state, "case_id", None)}
        return {k: val for k, val in attrs.items() if val is not None}

    # ------------------------------------------------------------ rotas
    @app.get("/health")
    async def health():
        v = runtime.versions()
        return {"status": "alive", "release_id": v.get("release_id"),
                "environment": os.environ.get("QC_ENVIRONMENT", "local"),
                "image_digest": v.get("image_digest"), "path_prefix": prefix or "",
                "mode": "deterministic", "telemetry": "otlp" if telemetry.enabled() else "off",
                "versions": v}

    @app.get("/ready")
    async def ready():
        state = await asyncio.to_thread(runtime.check_ready)
        body = {"ready": bool(state.get("ready")), "checks": state.get("checks", {}),
                "environment": os.environ.get("QC_ENVIRONMENT", "local"),
                "cold_start_s": runtime.ready_at_s, "versions": runtime.versions()}
        if state.get("reason"):
            body["reason"] = state["reason"]
        return JSONResponse(status_code=200 if body["ready"] else 503, content=body)

    @app.get("/lab/meta")
    async def meta(request: Request):
        out, err = await call_engine(request, runtime.engine.meta if runtime.engine else None)
        if err:
            return err
        out = dict(out or {})
        out.setdefault("versions", runtime.versions())
        out.setdefault("dates", PRESET_DATES)
        out.setdefault("tenants", TENANTS)
        out.setdefault("user_roles", USER_ROLES)
        out.setdefault("strategies", STRATEGIES)
        out.setdefault("default_strategy", default_strategy)
        out["environment"] = os.environ.get("QC_ENVIRONMENT", "local")
        if isinstance(out.get("versions"), dict):
            out["versions"] = {**out["versions"], "environment": out["environment"]}
        if "products" not in out:
            out["products"] = sorted(runtime.known_products())
        return out

    @app.post("/lab/compare")
    async def compare(body: CompareRequest, request: Request):
        rid = request.state.request_id
        strategies = tuple(dict.fromkeys(s.value for s in (body.strategies or [Strategy.bm25, Strategy.vector,
                                                                               Strategy.graph])))
        with obs.stage("auth.tenant", **{"qc.tenant": body.tenant.value, "qc.user_role": body.user_role.value}):
            pass
        request.state.strategy = ",".join(strategies)
        with obs.intent_route(**_route_attrs(request, ",".join(strategies))) as sp:
            out, err = await call_engine(
                request, runtime.engine.compare if runtime.engine else None, body.question, body.as_of_date,
                tenant=body.tenant.value, user_role=body.user_role.value, strategies=strategies,
                order_id=body.order_id, auth_token=body.auth_token or _bearer(request), request_id=rid,
                scenario_id=request.state.scenario_id, case_id=request.state.case_id)
            sp.set("qc.route", (out or {}).get("route") or "error")
        if err:
            return err
        out = dict(out)
        out.setdefault("request_id", rid)
        out["trace_id"] = out.get("trace_id") or request.state.trace_id
        out.setdefault("versions", runtime.versions())
        env = os.environ.get("QC_ENVIRONMENT", "local")
        if not out.get("environment"):
            out["environment"] = env
        if isinstance(out.get("versions"), dict):
            out["versions"] = {**out["versions"], "environment": env}
        obs.note_route(request, out.get("route"))
        obs.record_stage_latencies(out)
        return out

    @app.get("/lab/graph/product/{product_id}")
    async def graph_product(product_id: str, request: Request, as_of_date: str = PRESET_DATES[0],
                            tenant: Tenant = Tenant.quantum):
        rid = request.state.request_id
        try:
            _check_date(as_of_date)
        except ValueError as err:
            return _err(422, "invalid_request", [{"field": "as_of_date", "msg": str(err)}], rid)
        # Regex E lista fechada: o id so vira parametro de Cypher se existir no seed.
        if not PRODUCT_ID_RE.match(product_id) or product_id not in runtime.known_products():
            return _err(404, "unknown_product", "produto fora da fixture do laboratorio", rid)
        request.state.strategy = "graph"
        out, err = await call_engine(request, runtime.engine.graph_for_product if runtime.engine else None,
                                     product_id, as_of_date, tenant.value)
        if err:
            return err
        out = dict(out)
        out.setdefault("product_id", product_id)
        out.setdefault("as_of_date", as_of_date)
        out.setdefault("tenant", tenant.value)
        out.setdefault("request_id", rid)
        out.setdefault("trace_id", request.state.trace_id)
        out.setdefault("versions", runtime.versions())
        return out

    @app.post("/assistant/ask")
    async def ask(body: AskRequest, request: Request):
        rid = body.request_id or request.state.request_id
        strategy = (body.strategy.value if body.strategy else default_strategy)
        request.state.strategy = strategy
        with obs.stage("auth.tenant", **{"qc.tenant": body.tenant.value, "qc.user_role": body.user_role.value}):
            pass
        kwargs = {"customer_id": body.customer_id, "auth_token": body.auth_token or _bearer(request),
                  "order_id": body.order_id, "tenant": body.tenant.value, "user_role": body.user_role.value,
                  "scenario_id": body.scenario_id or request.state.scenario_id, "request_id": rid,
                  "as_of_date": body.as_of_date}
        with obs.intent_route(**_route_attrs(request, strategy)) as sp:
            out, err = await call_engine(request, runtime.engine.ask if runtime.engine else None,
                                         body.question, strategy=strategy, **kwargs)
            sp.set("qc.route", (out or {}).get("route") or "error")
        if err:
            return err
        out = dict(out)
        out.setdefault("strategy", strategy)
        out["trace_id"] = out.get("trace_id") or request.state.trace_id
        out.setdefault("request_id", rid)
        out.setdefault("release_id", runtime.versions().get("release_id"))
        # O engine devolve environment=None (o settings.json nao tem o env do
        # deploy); a fonte da verdade e o QC_ENVIRONMENT da task definition.
        if not out.get("environment"):
            out["environment"] = os.environ.get("QC_ENVIRONMENT", "local")
        if isinstance(out.get("versions"), dict):
            out["versions"] = {**out["versions"], "environment": out["environment"]}
        obs.note_route(request, out.get("route"))
        return out

    # UI estatica montada DEPOIS das rotas da API: "/" serve index.html e
    # "/candidate/" (prefixo removido pelo middleware) serve o mesmo arquivo.
    if os.path.isdir(STATIC_DIR):
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    # Ordem de execucao (de fora para dentro): headers -> prefixo -> observabilidade -> limite de corpo.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(obs.ObservabilityMiddleware, runtime=runtime)
    app.add_middleware(PathPrefixMiddleware, prefix=prefix)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
