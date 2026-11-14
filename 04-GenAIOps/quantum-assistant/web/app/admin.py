"""API admin (porta 8001): estado do grafo, cenarios de incidente e reindex, com HMAC.

Fica numa porta SEPARADA e num app FastAPI separado porque a rede e a primeira
barreira: o SG da task so aceita a 8001 vindo do SG do runner, e o ALB nem tem
rota para ela. O HMAC e a segunda barreira (quem chega na porta ainda precisa da
chave). Toda rota exige assinatura, inclusive a 404, para nao revelar o que existe.

Assinatura (contrato): X-QC-Admin-Timestamp = epoch em segundos (janela +-120 s) e
X-QC-Admin-Signature = hex(HMAC_SHA256(chave, f"{ts}\\n{METHOD}\\n{path}\\n{sha256hex(corpo)}")).
O cliente de referencia e web/admin_client.py.
"""
import asyncio
import os
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import observability as obs
from .runtime import log
from .security import HmacMiddleware, SecurityHeadersMiddleware


class Scenario(str, Enum):
    stale_graph_policy = "stale_graph_policy"
    remove_campaign_edge = "remove_campaign_edge"


class Action(str, Enum):
    apply = "apply"
    restore = "restore"


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    action: Action


def _key():
    # Lida a cada requisicao (e nao guardada em atributo): nada de chave em repr/log.
    return os.environ.get("QC_ADMIN_HMAC_KEY") or None


def _reject(method, path, reason):
    log("admin.rejected", method=method, path=path, reason=reason)


def create_admin_app(runtime):
    app = FastAPI(title="qc-assistant admin", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc):
        detail = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "msg": e.get("msg")}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": detail})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request, exc):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": "not_found" if exc.status_code == 404 else "http_error",
                                     "detail": str(exc.detail)})

    async def run(request, op, fn, *args):
        if runtime.engine is None or not runtime.bootstrapped:
            return JSONResponse(status_code=503, content={
                "error": "not_ready", "detail": {"checks": runtime.check_ready().get("checks")}})
        try:
            with obs.stage("admin." + op):
                result = await asyncio.to_thread(fn, *args)
        except ValueError as err:
            return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": str(err)[:200]})
        except Exception as err:  # noqa: BLE001 - Neo4j fora: 503 controlado, sem stack
            log("admin.error", op=op, error=type(err).__name__)
            return JSONResponse(status_code=503, content={"error": "dependency_unavailable",
                                                          "detail": {"kind": type(err).__name__}})
        versions = runtime.versions()
        # Registro do incidente: quem quiser reconstruir a linha do tempo acha aqui
        # o antes/depois do graph_version, sem precisar do corpo da requisicao.
        log("admin.operation", op=op, args=[getattr(a, "value", a) for a in args],
            graph_version=versions.get("graph_version"), graph_seed_version=versions.get("graph_seed_version"))
        body = dict(result) if isinstance(result, dict) else {"result": result}
        body.setdefault("versions", versions)
        return body

    @app.get("/admin/graph/state")
    async def graph_state(request: Request):
        return await run(request, "graph.state", runtime.engine.admin_state if runtime.engine else None)

    @app.post("/admin/graph/scenario")
    async def graph_scenario(body: ScenarioRequest, request: Request):
        return await run(request, "graph.scenario",
                         runtime.engine.apply_scenario if runtime.engine else None,
                         body.scenario.value, body.action.value)

    @app.post("/admin/reindex")
    async def reindex(request: Request):
        return await run(request, "reindex", runtime.engine.reindex if runtime.engine else None)

    app.add_middleware(HmacMiddleware, key_provider=_key, on_reject=_reject)
    app.add_middleware(obs.ObservabilityMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app
