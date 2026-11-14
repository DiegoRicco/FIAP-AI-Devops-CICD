"""Entrada do conteiner: `python -m web.app.serve` sobe a 8000 (publica) e a 8001 (admin).

Dois servidores uvicorn no MESMO processo e no mesmo event loop porque os dois
precisam do MESMO Runtime (o mesmo Engine e a mesma conexao com o Neo4j): o
cenario aplicado pela 8001 tem que aparecer na resposta da 8000. Dois processos
teriam dois engines e o incidente nao seria visivel.

Sinais: o ECS para a task com SIGTERM. Cada uvicorn.Server instalaria o proprio
handler e o segundo sobrescreveria o primeiro; aqui um handler unico manda os
dois servidores encerrarem juntos.
"""
import asyncio
import os
import signal
import sys

import uvicorn

from . import observability as obs
from .admin import create_admin_app
from .main import create_app
from .runtime import Runtime, log


class _Server(uvicorn.Server):
    def install_signal_handlers(self):  # uvicorn < 0.29
        pass

    def capture_signals(self):  # uvicorn >= 0.29: sinais tratados em main()
        import contextlib
        return contextlib.nullcontext()


def build_servers(runtime=None, host=None, port=None, admin_port=None):
    obs.configure()
    runtime = runtime or Runtime()
    host = host or os.environ.get("QC_BIND_HOST", "0.0.0.0")
    port = int(port or os.environ.get("QC_PORT", "8000"))
    admin_port = int(admin_port or os.environ.get("QC_ADMIN_PORT", "8001"))
    public = create_app(runtime)
    admin = create_admin_app(runtime)
    common = {"host": host, "log_level": "warning", "access_log": False, "server_header": False,
              "date_header": True, "proxy_headers": True, "forwarded_allow_ips": "*",
              # Keep-alive do ALB e 60 s: o servidor precisa segurar a conexao por mais
              # tempo, senao fecha no meio e o ALB devolve 502 esporadico.
              "timeout_keep_alive": 65}
    servers = [_Server(uvicorn.Config(public, port=port, lifespan="on", **common)),
               _Server(uvicorn.Config(admin, port=admin_port, lifespan="off", **common))]
    return runtime, servers


async def _serve(servers):
    loop = asyncio.get_running_loop()

    def stop(*_):
        log("server.stopping")
        for s in servers:
            s.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    runtime, servers = build_servers()
    log("server.starting", ports=[s.config.port for s in servers],
        path_prefix=os.environ.get("QC_PATH_PREFIX", ""), admin_key_configured=bool(os.environ.get("QC_ADMIN_HMAC_KEY")))
    try:
        asyncio.run(_serve(servers))
    finally:
        runtime.stop()
        from core import telemetry
        telemetry.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
