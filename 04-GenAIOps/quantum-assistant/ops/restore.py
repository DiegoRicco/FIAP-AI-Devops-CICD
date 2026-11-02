"""Desfaz TODAS as falhas do inject.py e prova o estado de volta.

  ops/run restore.py

1. Apaga ops/.runtime/overlay.json e o overlay -> o runtime volta a ler o corpus
   do repositorio e REINDEXA (POST /admin/reload). Note: nao reiniciamos nada;
   reiniciar com o mesmo corpus ruim nao resolveria o incidente.
2. Zera as falhas da API de pedidos (POST /admin/faults/reset).
3. Confere que o corpus_version do runtime e o do repositorio sao iguais.
Saida (stdout): JSON com as versoes depois do restore. Sai com 1 se nao bater.
"""
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import versions  # noqa: E402

RUNTIME_DIR = os.environ.get("QC_RUNTIME_DIR", os.path.join(HERE, ".runtime"))


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def warn_env():
    """inject/restore so mexem no runtime, mas quem roda isto sem a telemetria
    provavelmente vai rodar o simulador do mesmo jeito: avisamos ja aqui."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        log("AVISO: telemetria DESLIGADA neste terminal (OTEL_EXPORTER_OTLP_ENDPOINT nao definido). "
            "Use 'ops/run inject.py|restore.py|simulator.py ...': sem isso os probes NAO aparecem no Grafana.")


def http_json(method, url, body=None, timeout=10.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main():
    warn_env()
    a_url = os.environ.get("QC_ASSISTANT_URL", "http://127.0.0.1:8080").rstrip("/")
    o_url = os.environ.get("QC_ORDERS_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
    for name in ("overlay.json", "faults.json"):
        path = os.path.join(RUNTIME_DIR, name)
        if os.path.exists(path):
            os.remove(path)
            log(f"removido {os.path.relpath(path, ROOT)}")
    overlay_dir = os.path.join(RUNTIME_DIR, "overlay")
    if os.path.isdir(overlay_dir):
        shutil.rmtree(overlay_dir)
    try:
        reloaded = http_json("POST", a_url + "/admin/reload")
        faults = http_json("POST", o_url + "/admin/faults/reset")
    except urllib.error.URLError as err:
        log(f"runtime ou pedidos fora do ar ({err}); rode ops/bootstrap-demo52.sh")
        return 2
    expected = versions.corpus_version(os.path.join(ROOT, "corpus"))
    got = reloaded["versions"]["corpus_version"]
    ok = got == expected and reloaded.get("corpus_source") == "repositorio"
    log(f"reindexado a partir do repositorio: corpus_version={got} (esperado {expected}) "
        f"index_version={reloaded['versions']['index_version']}; falhas de pedidos zeradas")
    print(json.dumps({"restored": ok, "versions": reloaded["versions"], "changed": reloaded.get("changed"),
                      "corpus_source": reloaded.get("corpus_source"), "faults": faults}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
