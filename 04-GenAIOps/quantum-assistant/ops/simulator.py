"""Simulador de qualidade ON-GOING da Demo 5.2: probes rotulados via HTTP real.

Diferenca para o eval da 5.1: o eval roda uma vez, antes do deploy, sobre o
codigo empacotado. O simulador roda contra o runtime QUE ESTA ATENDENDO, em
ritmo constante, com uma distribuicao de perguntas por cenario, e emite metricas
e traces. E assim que uma politica obsoleta republicada sem novo release aparece.

Cada amostra:
  - sorteia um caso ROTULADO (golden set da 5.1, importado de evals/) com a seed;
  - abre o span `quality.evaluate` e faz POST /assistant/ask (o traceparent vai
    junto, entao probe -> assistente -> pedidos formam UM trace);
  - julga a resposta com as MESMAS regras do eval (evals/run_eval.check_case),
    usando o corpus do repositorio como referencia (fonte da verdade);
  - grava uma linha JSONL com test_sample=true, trace_id, versoes e reason code;
  - emite as metricas quality_* (proxy sintetico, nao trafego humano).

Uso (o wrapper ops/run escolhe o venv certo e liga a telemetria):
  ops/run simulator.py --seed 42 --scenario baseline --rate 2 --duration 25
Saida (stdout): o caminho do JSONL e o resumo. Progresso vai para o stderr.
"""
import argparse
import datetime
import importlib.util
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import telemetry  # noqa: E402
from core.corpus import load_chunks  # noqa: E402

SERVICE_NAME = "quantum-quality-probe"
SCENARIOS_FILE = os.path.join(HERE, "scenarios.json")
RESULTS_DIR = os.path.join(HERE, "results")
# Ordem de prioridade do reason code: regra dura primeiro, porque e a causa que
# o runbook trata; um caso com fonte obsoleta tambem costuma errar o texto.
REASON_ORDER = ("acl_tenant", "fonte_obsoleta", "injection", "recusa_obrigatoria", "contrato_pedido",
                "rota", "citacao", "nao_recusou", "campos_esperados", "texto_proibido", "min_citacoes",
                "contrato_resposta", "release_id")
REASONS = REASON_ORDER + ("http_error", "extra_must_not_contain", "fonte_fora_do_corpus_aprovado")


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_run_eval():
    """Importa evals/run_eval.py como modulo: reaproveita as regras do gate da 5.1."""
    spec = importlib.util.spec_from_file_location("run_eval", os.path.join(ROOT, "evals", "run_eval.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_scenarios():
    with open(SCENARIOS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def build_pool(run_eval, scenario):
    """Casos por slice, nos conjuntos que o cenario usa, com os pesos do cenario."""
    by_slice = {}
    for set_name in scenario["sets"]:
        for case in run_eval.load_cases(set_name):
            by_slice.setdefault(case["slice"], []).append(case)
    # Casos rotulados que so fazem sentido nesta operacao (ex.: pergunta que
    # recupera o documento injetado). Ficam em scenarios.json, fora do golden set.
    for case in scenario.get("extra_cases", []):
        by_slice.setdefault(case["slice"], []).append(case)
    weights = {s: w for s, w in scenario["slice_weights"].items() if w > 0 and s in by_slice}
    return {s: sorted(by_slice[s], key=lambda c: c["case_id"]) for s in weights}, weights


def draw_cases(pool, weights, n, seed):
    """Mesma seed + mesmo pool = mesma sequencia de casos (replay do incidente)."""
    rng = random.Random(seed)
    slices = sorted(weights)
    w = [weights[s] for s in slices]
    picked = []
    for _ in range(n):
        s = rng.choices(slices, weights=w, k=1)[0]
        picked.append(rng.choice(pool[s]))
    return picked


def http_json(method, url, body=None, timeout=10.0, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        return err.code, {}


def classify(run_eval, case, resp, meta_by_chunk, as_of_date, extra_forbidden):
    """Julga uma resposta. Retorna o dict de resultado do probe."""
    checks = run_eval.check_case(case, resp, meta_by_chunk, as_of_date)
    failed = [c["name"].split("(")[0] for c in checks if not c["ok"]]
    answer = run_eval.norm(resp.get("answer") or "")
    leaked = [t for t in extra_forbidden if run_eval.norm(t) in answer]
    if leaked:
        failed.append("extra_must_not_contain")
    by_name = {c["name"].split("(")[0]: c["ok"] for c in checks}
    rank = run_eval.retrieval_rank(case, resp)
    retrieval_case = run_eval.is_retrieval_case(case)
    citation_required = bool(case["expected_docs"]) and not case["must_abstain"]

    # API de pedidos fora + recusa honesta (sem status inventado) = fallback.
    # Nao e erro de qualidade; e o sinal tecnico que OrderApiDegraded mede.
    # Vale tambem para pedido inexistente ou alheio: com a API fora, recusar com
    # order_api_unavailable e honesto (nao revela nem inventa nada).
    order_case = (case["expected_route"] == "order_status"
                  and case.get("expected_abstain_reason") != "order_api_unavailable")
    honest_fallback = (order_case and resp.get("abstain_reason") == "order_api_unavailable"
                       and resp.get("order") is None and not leaked
                       and by_name.get("acl_tenant", True) and by_name.get("injection", True))
    # Citacao de trecho que nao existe no corpus versionado do repositorio:
    # conteudo nao aprovado entrou no indice (ex.: documento injetado).
    unapproved = [c for c in resp.get("citations") or [] if c["chunk_id"] not in meta_by_chunk]
    if honest_fallback:
        outcome, reason = "fallback", "dependency_unavailable"
    elif not failed:
        outcome, reason = "pass", "ok"
    elif unapproved:
        outcome, reason = "fail", "fonte_fora_do_corpus_aprovado"
    else:
        outcome = "fail"
        reason = next((r for r in REASON_ORDER if r in failed), failed[0])
    return {
        "passed": outcome == "pass", "outcome": outcome, "reason": reason,
        "failed_checks": failed,
        "hit": (rank is not None) if retrieval_case else None,
        "reciprocal_rank": (1.0 / rank if rank else 0.0) if retrieval_case else None,
        "rank": rank,
        "citation_required": citation_required,
        "citation_present": by_name.get("citacao", False) if citation_required else None,
        "source_checked": bool(resp.get("citations")),
        "source_is_current": by_name.get("fonte_obsoleta", True),
        "acl_violation": not by_name.get("acl_tenant", True),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Probes rotulados contra o runtime HTTP (Demo 5.2)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--scenario", required=True,
                   choices=("baseline", "stale_policy", "order_api_degradation", "document_injection"))
    p.add_argument("--rate", type=float, default=2.0, help="probes por segundo")
    p.add_argument("--duration", type=float, default=25.0, help="segundos de execucao")
    p.add_argument("--url", default=os.environ.get("QC_ASSISTANT_URL", "http://127.0.0.1:8080"))
    p.add_argument("--out", help="JSONL de saida (padrao: ops/results/<cenario>-seed<seed>-<hora>.jsonl)")
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)

    cfg = load_scenarios()
    scenario = cfg["scenarios"][args.scenario]
    as_of_date = datetime.date.today().isoformat()
    run_eval = load_run_eval()
    # Referencia = corpus do REPOSITORIO, nao o que o runtime carregou: se alguem
    # reativar a v2 no indice, o probe ainda sabe que a v2 e obsoleta.
    meta_by_chunk = {c["chunk_id"]: c for c in load_chunks(os.path.join(ROOT, "corpus"))}

    code, health = http_json("GET", args.url.rstrip("/") + "/health", timeout=args.timeout)
    if code != 200:
        log(f"runtime fora do ar em {args.url} (GET /health -> {code}); rode ops/bootstrap-demo52.sh")
        return 2
    release_id = health["release_id"]
    corpus_version = health["versions"]["corpus_version"]
    log(f"alvo {args.url} release={release_id} corpus={corpus_version} fonte={health.get('corpus_source')}")

    telemetry.configure(SERVICE_NAME, health["versions"]["application_version"],
                        health.get("environment", "local-demo"), release_id)
    telemetry.warn_if_disabled()
    pool, weights = build_pool(run_eval, scenario)
    base = {"scenario": args.scenario, "release_id": release_id, "corpus_version": corpus_version}
    telemetry.prime_quality_series([dict(base, slice=s) for s in pool], REASONS)
    if telemetry.enabled():
        # O Prometheus precisa ver o zero ANTES do primeiro probe (increase()).
        telemetry.flush()
        time.sleep(6)

    n = max(1, int(round(args.rate * args.duration)))
    cases = draw_cases(pool, weights, n, args.seed)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = args.out or os.path.join(RESULTS_DIR, f"{args.scenario}-seed{args.seed}-{stamp}.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    extra_forbidden = scenario.get("extra_must_not_contain", [])
    interval = 1.0 / args.rate if args.rate > 0 else 0
    counts = {"pass": 0, "fail": 0, "fallback": 0}
    log(f"{n} probes do cenario {args.scenario} (seed {args.seed}, {args.rate}/s) -> {out}")

    with open(out, "w", encoding="utf-8") as fh:
        t0 = time.monotonic()
        for i, case in enumerate(cases):
            request_id = f"sim-{args.seed}-{i:04d}"
            # So o que um cliente real mandaria: identidade e data quem decide e o servidor.
            body = {k: case.get(k) for k in ("question", "customer_id", "auth_token", "order_id")}
            body.update({"request_id": request_id, "scenario_id": args.scenario})
            body = {k: v for k, v in body.items() if v is not None}
            labels = dict(base, slice=case["slice"])
            with telemetry.span("quality.evaluate", scenario=args.scenario, case_id=case["case_id"],
                                slice=case["slice"], request_id=request_id, test_sample=True) as sp:
                started = time.monotonic()
                try:
                    status, resp = http_json("POST", args.url.rstrip("/") + "/assistant/ask", body,
                                             timeout=args.timeout,
                                             headers=telemetry.inject_headers({"X-Request-Id": request_id}))
                except (urllib.error.URLError, TimeoutError, OSError) as err:
                    status, resp = 0, {"error": type(err).__name__}
                latency_ms = (time.monotonic() - started) * 1000
                if status == 200:
                    # Vigencia julgada na data em que o servidor respondeu (as_of_date da resposta).
                    judged_on = resp.get("as_of_date") or as_of_date
                    result = classify(run_eval, case, resp, meta_by_chunk, judged_on, extra_forbidden)
                else:
                    result = {"passed": False, "outcome": "fail", "reason": "http_error",
                              "failed_checks": [f"http_{status}"], "hit": None, "reciprocal_rank": None,
                              "rank": None, "citation_required": False, "citation_present": None,
                              "source_checked": False, "source_is_current": True, "acl_violation": False}
                result["latency_ms"] = latency_ms
                sp.set("outcome", result["outcome"])
                sp.set("reason", result["reason"])
                sp.set("http.response.status_code", status)
                trace_id = sp.trace_id or resp.get("trace_id")
            telemetry.record_quality(result, **labels)
            counts[result["outcome"]] += 1
            versions = resp.get("versions") or {}
            fh.write(json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
                "test_sample": True, "sample_type": "test_sample", "seed": args.seed, "sample_index": i,
                "scenario_id": args.scenario, "case_id": case["case_id"], "slice": case["slice"],
                "severity": case["severity"], "request_id": request_id, "trace_id": trace_id,
                "release_id": resp.get("release_id", release_id),
                "corpus_version": versions.get("corpus_version", corpus_version),
                "index_version": versions.get("index_version"), "http_status": status,
                "latency_ms": round(latency_ms, 1), "outcome": result["outcome"], "passed": result["passed"],
                "reason": result["reason"], "failed_checks": result["failed_checks"],
                "rank": result["rank"], "hit": result["hit"], "reciprocal_rank": result["reciprocal_rank"],
                "citation_required": result["citation_required"], "citation_present": result["citation_present"],
                "source_checked": result["source_checked"], "source_is_current": result["source_is_current"],
                "acl_violation": result["acl_violation"],
                "abstain_reason": resp.get("abstain_reason"),
                "cited": [f"{c['doc_id']}@v{c['version']}" for c in resp.get("citations") or []],
            }, ensure_ascii=False) + "\n")
            if (i + 1) % 10 == 0 or i + 1 == n:
                log(f"  {i + 1}/{n} pass={counts['pass']} fail={counts['fail']} fallback={counts['fallback']}")
            # Ritmo fixo: o proximo probe sai no instante planejado, nao "o quanto antes".
            wait = t0 + (i + 1) * interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
    telemetry.flush()
    judged = counts["pass"] + counts["fail"]
    rate = round(counts["pass"] / judged, 4) if judged else None
    print(json.dumps({"results": out, "scenario": args.scenario, "seed": args.seed, "n": n,
                      "release_id": release_id, "corpus_version": corpus_version,
                      "pass": counts["pass"], "fail": counts["fail"], "fallback": counts["fallback"],
                      "pass_rate": rate}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
