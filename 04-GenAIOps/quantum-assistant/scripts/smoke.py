"""Verificacao pos-deploy (postdeploy-synthetic-gate) contra o runtime PUBLICADO.

Duas perguntas, nessa ordem:
1. "O que esta no ar e o que o pipeline avaliou?" GET <url>/health tem que
   devolver o release_id e as versoes do dist/manifest.json. Se nao bater, a
   avaliacao seguinte mediria outro release e nao provaria nada.
2. "Ele responde certo na nuvem?" Roda um subset do golden set via HTTP real
   (evals/run_eval.py --target http), com o release_id conferido em cada caso.

Uso:
  python3 scripts/smoke.py --url https://<api>/candidate --manifest dist/manifest.json
Saidas: reports/junit-smoke.xml, reports/junit-postdeploy.xml, reports/postdeploy.json.
Exit 0 aprovado, 1 reprovado. O JUnit e gravado mesmo na reprovacao.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import telemetry  # noqa: E402  (no-op sem OTel; span release.verify do contrato)
VERSION_KEYS = ("application_version", "prompt_version", "corpus_version", "index_version",
                "eval_set_version", "model_version")
# Subset escolhido para cobrir o caminho inteiro na nuvem: politica (retriever),
# pedido real (Lambda -> API -> Lambda de pedidos -> DynamoDB), autorizacao,
# vigencia e API de pedidos com falha planejada.
DEFAULT_SLICES = "politicas,pedidos,autorizacao,vigencia,dependencia"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def get_health(url, attempts, wait_s):
    """Algumas tentativas: logo apos o apply o alias pode levar segundos para responder."""
    last = None
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as err:
            last = err
            log(f"  /health tentativa {i}/{attempts} falhou: {err}")
            time.sleep(wait_s)
    raise RuntimeError(f"/health nao respondeu em {attempts} tentativas: {last}")


def write_junit(path, checks):
    failures = sum(1 for c in checks if not c["ok"])
    cases = []
    for c in checks:
        body = "" if c["ok"] else f"<failure message={quoteattr(c['detail'])}/>"
        cases.append(f'<testcase classname="smoke" name={quoteattr(c["name"])}>{body}</testcase>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite name="postdeploy-smoke" '
           f'tests="{len(checks)}" failures="{failures}">{"".join(cases)}</testsuite></testsuites>\n')
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def main(argv=None):
    p = argparse.ArgumentParser(description="smoke + golden subset contra o runtime publicado")
    p.add_argument("--url", required=True, help="base do runtime, ex.: https://<api>/candidate")
    p.add_argument("--manifest", default=os.path.join(ROOT, "dist", "manifest.json"))
    p.add_argument("--slice", default=DEFAULT_SLICES)
    p.add_argument("--reports", default=os.path.join(ROOT, "reports"))
    p.add_argument("--attempts", type=int, default=5)
    args = p.parse_args(argv)
    url = args.url.rstrip("/")

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    expected = manifest["release_id"]
    log(f"smoke: alvo {url} | release esperado {expected}")

    checks = []
    try:
        with telemetry.span("release.verify", release_id=expected):
            health = get_health(url, args.attempts, 3)
        checks.append({"name": "health_ok", "ok": health.get("status") == "ok", "detail": str(health.get("status"))})
        checks.append({"name": "release_id", "ok": health.get("release_id") == expected,
                       "detail": f"esperado={expected} publicado={health.get('release_id')}"})
        for key in VERSION_KEYS:
            got = (health.get("versions") or {}).get(key)
            checks.append({"name": f"versao.{key}", "ok": got == manifest.get(key),
                           "detail": f"manifesto={manifest.get(key)} publicado={got}"})
        log(f"  publicado: release_id={health.get('release_id')} alias={health.get('environment')} "
            f"versao_lambda={health.get('function_version')}")
    except RuntimeError as err:
        checks.append({"name": "health_ok", "ok": False, "detail": str(err)})

    write_junit(os.path.join(args.reports, "junit-smoke.xml"), checks)
    for c in checks:
        log(f"  {'OK  ' if c['ok'] else 'FALHA'} {c['name']}: {c['detail']}")
    if not all(c["ok"] for c in checks):
        # Release errado no ar: nem avalia, porque o resultado seria de outro conjunto.
        log("smoke REPROVADO: o runtime publicado nao e o release avaliado pelo pipeline")
        return 1

    cmd = [sys.executable, os.path.join(ROOT, "evals", "run_eval.py"), "--set", "golden_v1",
           "--target", "http", "--url", url, "--expect-release-id", expected, "--slice", args.slice,
           "--out", os.path.join(args.reports, "postdeploy.json"),
           "--junit", os.path.join(args.reports, "junit-postdeploy.xml")]
    log("smoke: " + " ".join(cmd[1:]))
    rc = subprocess.call(cmd)
    log(f"smoke: golden subset via HTTP terminou com exit {rc}")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
