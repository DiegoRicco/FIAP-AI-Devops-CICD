"""Avaliacao offline/pos-deploy do assistente: golden set -> JSON + JUnit + exit 0/1.

Uso (contrato estavel com o pipeline):
  python3 evals/run_eval.py --set golden_v1 --target local --out reports/eval.json \
      --junit reports/junit-eval.xml --seed 42
  python3 evals/run_eval.py --set golden_v1 --target http --url https://.../candidate \
      --expect-release-id <id> --slice politicas,pedidos --out ... --junit ...

O que se mede (modo sem LLM; nada aqui e "faithfulness" de modelo):
- Hit@k e MRR: RECUPERACAO. Dizem se a politica certa veio entre os k trechos,
  nao se a resposta final esta correta.
- citacao, abstencao, fonte vigente e campos esperados: CONTRATO da resposta.
- regras duras (ACL, fonte obsoleta, recusa obrigatoria, contrato de pedido,
  injection): qualquer violacao reprova, nao importa a media.
O JUnit e gravado SEMPRE (inclusive em erro), e o exit code continua sendo o
veredito: 0 aprovado, 1 reprovado. O GitLab so desenha o JUnit; quem reprova
o job e o exit code.
"""
import argparse
import datetime
import json
import os
import random
import sys
import traceback
import urllib.error
import urllib.request
from xml.sax.saxutils import escape, quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import versions  # noqa: E402
from core.corpus import load_chunks  # noqa: E402
from core.retrieval import check_access, strip_accents  # noqa: E402
from core.route import is_injection  # noqa: E402

EVALS = os.path.join(ROOT, "evals")
HARD_RULES = ("acl_tenant", "fonte_obsoleta", "recusa_obrigatoria", "contrato_pedido", "injection")
CURRENT_FILTERS = {"require_active": True, "respect_effective_date": True,
                   "enforce_tenant": False, "enforce_access_level": False}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_cases(name):
    with open(os.path.join(EVALS, f"{name}.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def norm(text):
    return strip_accents(text or "")


# ------------------------------------------------------------------ alvos
class LocalTarget:
    """Chama o core direto, no mesmo processo (gate offline, antes do deploy)."""

    def __init__(self, as_of_date):
        from core.assistant import Assistant
        self.assistant = Assistant({"as_of_date": as_of_date})
        self.as_of_date = as_of_date

    def ask(self, case, request_id):
        return self.assistant.answer(
            case["question"], customer_id=case.get("customer_id"), auth_token=case.get("auth_token"),
            order_id=case.get("order_id"), tenant=case.get("tenant", "quantum"),
            user_role=case.get("user_role", "customer"), request_id=request_id,
            as_of_date=self.as_of_date)


class HttpTarget:
    """POST {url}/assistant/ask no runtime publicado (pos-deploy)."""

    def __init__(self, url, timeout_s):
        self.url = url.rstrip("/") + "/assistant/ask"
        self.timeout_s = timeout_s

    def ask(self, case, request_id):
        body = {k: case.get(k) for k in ("question", "customer_id", "auth_token", "order_id",
                                         "tenant", "user_role")}
        body["tenant"] = body["tenant"] or "quantum"
        body["user_role"] = body["user_role"] or "customer"
        body["request_id"] = request_id
        body["scenario_id"] = "eval"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "X-Request-Id": request_id})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            # 4xx/5xx do runtime nao e resposta valida do assistente: o caso reprova.
            raise RuntimeError(f"HTTP {err.code} em {self.url}") from err


# ------------------------------------------------------------------ checagens
def forbidden_text_rule(case):
    """A qual regra dura pertence o texto proibido (must_not_contain) deste caso."""
    if case["expected_route"] == "order_status":
        return "contrato_pedido"
    if case["slice"] in ("seguranca", "faq"):
        return "injection"
    if case["slice"] == "autorizacao":
        return "acl_tenant"
    return "fonte_obsoleta"


def check_case(case, resp, meta_by_chunk, as_of_date, release_id=None):
    """Retorna (checks, violacoes). checks: lista de {name, ok, detail, hard_rule}."""
    checks = []

    def add(name, ok, detail="", hard_rule=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "hard_rule": hard_rule})

    citations = resp.get("citations") or []
    retrieved = resp.get("retrieval") or []
    answer = resp.get("answer") or ""
    cited_keys = [f"{c['doc_id']}@v{c['version']}" for c in citations]

    add("contrato_resposta", resp.get("mode") == "deterministic" and "versions" in resp,
        f"mode={resp.get('mode')}")
    if release_id:
        add("release_id", resp.get("release_id") == release_id,
            f"esperado={release_id} recebido={resp.get('release_id')}")
    add("rota", resp.get("route") == case["expected_route"],
        f"esperada={case['expected_route']} recebida={resp.get('route')}")

    # Regra dura: ACL / tenant. Vale para o que foi RECUPERADO, nao so citado:
    # trecho de outro cliente no contexto ja e vazamento.
    leaked = []
    for item in list(retrieved) + list(citations):
        meta = meta_by_chunk.get(item["chunk_id"])
        if meta and (meta["tenant"] != case.get("tenant", "quantum") or
                     (meta["access_level"] != "public" and case.get("user_role", "customer") == "customer")):
            leaked.append(item["chunk_id"])
    add("acl_tenant", not leaked, f"vazou: {sorted(set(leaked))}" if leaked else "", "acl_tenant")

    # Regra dura: fonte vigente. Toda citacao precisa estar ativa e dentro da
    # vigencia, e na versao que o golden set declara como a correta.
    stale = []
    for c in citations:
        meta = meta_by_chunk.get(c["chunk_id"])
        if meta is None or check_access(meta, as_of_date, None, None, CURRENT_FILTERS):
            stale.append(c["chunk_id"])
        expected_version = case.get("expected_doc_version", {}).get(c["doc_id"])
        if expected_version and str(c["version"]) != str(expected_version):
            stale.append(c["chunk_id"])
    bad_cites = [k for k in case.get("must_not_cite", []) if k in cited_keys]
    add("fonte_obsoleta", not stale and not bad_cites,
        f"citou fora de vigencia/versao: {sorted(set(stale + bad_cites))}" if stale or bad_cites else "",
        "fonte_obsoleta")

    if case["must_abstain"]:
        expected = case.get("expected_abstain_reason")
        add("recusa_obrigatoria", resp.get("abstained") is True and resp.get("abstain_reason") == expected,
            f"esperado={expected} recebido={resp.get('abstain_reason')}", "recusa_obrigatoria")
    else:
        add("nao_recusou", resp.get("abstained") is False, f"abstain_reason={resp.get('abstain_reason')}")

    if case["expected_route"] == "order_status":
        got = (resp.get("order") or {}).get("status")
        add("contrato_pedido", resp.get("route") == "order_status" and got == case.get("expected_order_status"),
            f"status esperado={case.get('expected_order_status')} recebido={got}", "contrato_pedido")

    forbidden = [t for t in case.get("must_not_contain", []) if norm(t) in norm(answer)]
    if case.get("must_not_contain"):
        rule = forbidden_text_rule(case)
        add(f"texto_proibido({rule})", not forbidden, f"apareceu: {forbidden}" if forbidden else "", rule)
    # Regra dura global: frase com cara de instrucao nunca chega a resposta.
    add("injection", not is_injection(answer), "resposta contem instrucao embutida" if is_injection(answer) else "",
        "injection")

    missing = [f for f in case.get("expected_answer_fields", []) if norm(f) not in norm(answer)]
    add("campos_esperados", not missing, f"faltou: {missing}" if missing else "")

    if case["expected_docs"] and not case["must_abstain"]:
        ok = any(c["doc_id"] in case["expected_docs"] for c in citations)
        add("citacao", ok, f"citou={cited_keys}")
    if case.get("min_citations"):
        add("min_citacoes", len(citations) >= case["min_citations"], f"citacoes={len(citations)}")
    return checks


def retrieval_rank(case, resp):
    """Posicao (1..k) do primeiro trecho relevante: doc esperado NA versao esperada."""
    for position, item in enumerate(resp.get("retrieval") or [], start=1):
        expected_version = case.get("expected_doc_version", {}).get(item["doc_id"])
        if item["doc_id"] in case["expected_docs"] and (
                expected_version is None or str(item.get("version")) == str(expected_version)):
            return position
    return None


def is_retrieval_case(case):
    """Denominador de Hit@k/MRR (05_MATRIZ s2): perguntas documentais com relevante esperado.
    Pedido nao entra: status vem da API, nao do ranking."""
    return case["expected_route"] in ("policy", "faq") and bool(case["expected_docs"])


def ratio(num, den):
    return round(num / den, 4) if den else None


def metrics_for(rows):
    ret = [r for r in rows if r["retrieval_case"]]
    hits = [r for r in ret if r["rank"] is not None]
    cite_cases = [r for r in rows if r["case"]["expected_docs"] and not r["case"]["must_abstain"]]
    abst = [r for r in rows if r["case"]["must_abstain"]]
    with_cites = [r for r in rows if r["response"].get("citations")]
    check = lambda r, name: next((c["ok"] for c in r["checks"] if c["name"] == name), False)  # noqa: E731
    return {
        "n_cases": len(rows),
        "n_retrieval_cases": len(ret),
        "hit_at_k": ratio(len(hits), len(ret)),
        "mrr": ratio(sum(1 / r["rank"] for r in hits), len(ret)),
        "citation_rate": ratio(sum(check(r, "citacao") for r in cite_cases), len(cite_cases)),
        "n_citation_cases": len(cite_cases),
        "abstention_correct_rate": ratio(sum(check(r, "recusa_obrigatoria") for r in abst), len(abst)),
        "n_abstention_cases": len(abst),
        "source_is_current_rate": ratio(sum(check(r, "fonte_obsoleta") for r in with_cites), len(with_cites)),
        "n_cases_with_citations": len(with_cites),
        "case_pass_rate": ratio(sum(r["passed"] for r in rows), len(rows)),
    }


# ------------------------------------------------------------------ saida
def write_junit(path, report):
    """Um testcase por caso (classname = slice) + um por gate de metrica."""
    cases = []
    for r in report.get("cases", []):
        failed = [c for c in r["checks"] if not c["ok"]]
        body = ""
        if r.get("error"):
            body = f"<error message={quoteattr(r['error'])}/>"
        elif failed:
            msg = "; ".join(f"{c['name']}: {c['detail']}" + (" [REGRA DURA]" if c["hard_rule"] else "") for c in failed)
            body = f"<failure message={quoteattr(msg)}>{escape(msg)}</failure>"
        cases.append(f'<testcase classname="eval.{r["slice"]}" name={quoteattr(r["case_id"])} time="{r["latency_ms"] / 1000:.3f}">{body}</testcase>')
    for g in report.get("gates", []):
        body = "" if g["ok"] else f"<failure message={quoteattr(g['detail'])}>{escape(g['detail'])}</failure>"
        cases.append(f'<testcase classname="eval.gates" name={quoteattr(g["name"])} time="0">{body}</testcase>')
    if report.get("fatal_error"):
        cases.append(f'<testcase classname="eval.runner" name="execucao"><error message={quoteattr(report["fatal_error"])}/></testcase>')
    failures = sum(1 for c in cases if "<failure" in c)
    errors = sum(1 for c in cases if "<error" in c)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<testsuites><testsuite name="quantum-assistant-eval-{escape(report.get("set", "?"))}" '
           f'tests="{len(cases)}" failures="{failures}" errors="{errors}">\n' + "\n".join(cases) +
           "\n</testsuite></testsuites>\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def write_json(path, report):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def run(args):
    with open(os.path.join(EVALS, "thresholds.json"), encoding="utf-8") as f:
        thresholds = json.load(f)
    as_of_date = args.as_of_date or thresholds["as_of_date"]
    cases = load_cases(args.set)
    if args.slice:
        wanted = {s.strip() for s in args.slice.split(",") if s.strip()}
        cases = [c for c in cases if c["slice"] in wanted]
    if not cases:
        raise SystemExit(f"nenhum caso selecionado (set={args.set}, slice={args.slice})")
    # A seed so embaralha a ORDEM de execucao e fixa os request_id: o veredito
    # nao pode depender da ordem. Mesma seed + mesma entrada = mesmo relatorio.
    order = list(cases)
    random.Random(args.seed).shuffle(order)

    meta_by_chunk = {c["chunk_id"]: c for c in load_chunks(os.path.join(ROOT, "corpus"))}
    target = LocalTarget(as_of_date) if args.target == "local" else HttpTarget(args.url, args.timeout)

    rows = []
    for case in order:
        request_id = f"eval-{args.seed}-{case['case_id']}"
        start = datetime.datetime.now()
        error, resp = None, {}
        try:
            resp = target.ask(case, request_id)
        except Exception as err:  # noqa: BLE001 - qualquer falha de chamada reprova o caso
            error = f"{type(err).__name__}: {err}"
        latency_ms = round((datetime.datetime.now() - start).total_seconds() * 1000, 1)
        # No alvo http vale a data que o runtime publicado usou (ele responde com
        # as_of_date): e a vigencia REAL de producao que precisa ser conferida.
        case_date = resp.get("as_of_date") or as_of_date
        checks = [] if error else check_case(case, resp, meta_by_chunk, case_date, args.expect_release_id)
        rows.append({
            "case": case, "case_id": case["case_id"], "slice": case["slice"], "severity": case["severity"],
            "response": resp, "error": error, "checks": checks, "latency_ms": latency_ms,
            "retrieval_case": is_retrieval_case(case),
            "rank": None if error else retrieval_rank(case, resp),
            "passed": error is None and all(c["ok"] for c in checks),
        })
    rows.sort(key=lambda r: r["case_id"])

    overall = metrics_for(rows)
    slices = {s: metrics_for([r for r in rows if r["slice"] == s]) for s in sorted({r["slice"] for r in rows})}

    gates = []
    for name, rule in thresholds["metrics"].items():
        if name == "slice_pass_rate":
            continue
        value = overall.get(name)
        if value is None:
            # Sem denominador (ex.: --slice pedidos nao tem caso de retrieval): nao se aplica.
            gates.append({"name": f"metrica.{name}", "ok": True, "value": None, "min": rule["min"],
                          "detail": "nao se aplica a este subconjunto"})
            continue
        gates.append({"name": f"metrica.{name}", "ok": value >= rule["min"], "value": value, "min": rule["min"],
                      "detail": f"{name}={value} (minimo {rule['min']}): {rule['por_que']}"})
    slice_min = thresholds["metrics"]["slice_pass_rate"]["min"]
    for s, m in slices.items():
        gates.append({"name": f"slice.{s}", "ok": m["case_pass_rate"] >= slice_min, "value": m["case_pass_rate"],
                      "min": slice_min, "detail": f"slice {s}: case_pass_rate={m['case_pass_rate']} (minimo {slice_min})"})

    violations = [
        {"case_id": r["case_id"], "slice": r["slice"], "rule": c["hard_rule"], "check": c["name"], "detail": c["detail"]}
        for r in rows for c in r["checks"] if c["hard_rule"] and not c["ok"]
    ] + [{"case_id": r["case_id"], "slice": r["slice"], "rule": "execucao", "check": "chamada", "detail": r["error"]}
         for r in rows if r["error"]]
    if args.expect_release_id:
        # Pos-deploy: avaliar o release errado (cache, alias nao movido) e falso verde.
        wrong = sorted({r["case_id"] for r in rows for c in r["checks"] if c["name"] == "release_id" and not c["ok"]})
        gates.append({"name": "release_id_conferido", "ok": not wrong, "value": len(wrong), "min": 0,
                      "detail": f"release_id diferente de {args.expect_release_id} em {len(wrong)} casos" if wrong
                      else f"todas as respostas vieram de {args.expect_release_id}"})
    gates.append({"name": "regras_duras", "ok": not violations, "value": len(violations), "min": 0,
                  "detail": "; ".join(f"{v['case_id']} -> {v['rule']} ({v['detail']})" for v in violations)
                  or "nenhuma violacao"})

    first = next((r["response"] for r in rows if r["response"].get("versions")), {})
    report = {
        "set": args.set,
        "target": args.target,
        "url": args.url if args.target == "http" else None,
        "seed": args.seed,
        "as_of_date": as_of_date,
        "slice_filter": args.slice,
        "eval_set_version": versions.eval_set_version(EVALS),
        "system_versions": first.get("versions"),
        "release_id": first.get("release_id"),
        "environment": first.get("environment"),
        "k": thresholds.get("k"),
        "metrics": overall,
        "slices": slices,
        "gates": gates,
        "hard_rule_violations": violations,
        "failed_cases": [r["case_id"] for r in rows if not r["passed"]],
        "verdict": "PASS" if all(g["ok"] for g in gates) else "FAIL",
        "cases": [{
            "case_id": r["case_id"], "slice": r["slice"], "severity": r["severity"], "passed": r["passed"],
            "error": r["error"], "rank": r["rank"], "latency_ms": r["latency_ms"],
            "route": r["response"].get("route"), "abstain_reason": r["response"].get("abstain_reason"),
            "citations": [f"{c['doc_id']}@v{c['version']}#{c['section']}" for c in r["response"].get("citations") or []],
            "retrieval": [f"{x['chunk_id']} ({x['score']})" for x in r["response"].get("retrieval") or []],
            "answer": r["response"].get("answer"),
            "checks": r["checks"],
        } for r in rows],
    }
    # Latencia muda a cada execucao: fica fora da comparacao de determinismo.
    report["nondeterministic_fields"] = ["cases[].latency_ms"]
    return report


def print_summary(report):
    """Resumo legivel no stdout: e o que o aluno le no log do job."""
    m = report["metrics"]
    print(f"== Avaliacao {report['set']} | alvo={report['target']} | seed={report['seed']} | "
          f"as_of_date={report['as_of_date']} | release_id={report['release_id']}")
    print(f"   versoes: {report['system_versions']}")
    print(f"   Hit@k={m['hit_at_k']} MRR={m['mrr']} (n={m['n_retrieval_cases']}) | citacao={m['citation_rate']} "
          f"| abstencao={m['abstention_correct_rate']} | fonte_vigente={m['source_is_current_rate']} "
          f"| casos={m['case_pass_rate']} (n={m['n_cases']})")
    print("   slice            n  pass   hit@k  mrr")
    for s, sm in report["slices"].items():
        print(f"   {s:<15} {sm['n_cases']:>2}  {sm['case_pass_rate']!s:<6} {sm['hit_at_k']!s:<6} {sm['mrr']!s}")
    for g in report["gates"]:
        if not g["ok"]:
            print(f"   REPROVADO {g['name']}: {g['detail']}")
    for v in report["hard_rule_violations"]:
        print(f"   REGRA DURA {v['rule']} violada no caso {v['case_id']} ({v['slice']}): {v['detail']}")
    print(f"== VEREDITO: {report['verdict']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Avalia o assistente contra o golden set.")
    p.add_argument("--set", default="golden_v1", help="golden_v1 ou adversarial (arquivo evals/<set>.jsonl)")
    p.add_argument("--target", choices=("local", "http"), default="local")
    p.add_argument("--url", help="base do runtime publicado, ex.: https://.../candidate")
    p.add_argument("--out", default="reports/eval.json")
    p.add_argument("--junit", default="reports/junit-eval.xml")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--slice", help="filtra por slice (lista separada por virgula)")
    p.add_argument("--as-of-date", help="data de referencia da vigencia (padrao: thresholds.json)")
    p.add_argument("--expect-release-id", help="pos-deploy: confere se o runtime e o release esperado")
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)

    report = {"set": args.set, "verdict": "FAIL"}
    try:
        if args.target == "http" and not args.url:
            raise ValueError("--target http exige --url")
        report = run(args)
        print_summary(report)
    except BaseException as err:  # noqa: BLE001 - qualquer erro reprova, mas o JUnit sai
        report["fatal_error"] = f"{type(err).__name__}: {err}"
        log(traceback.format_exc())
        print(f"== VEREDITO: FAIL (erro de execucao: {report['fatal_error']})")
    finally:
        write_json(args.out, report)
        write_junit(args.junit, report)
        log(f"relatorio: {args.out} | junit: {args.junit}")
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
