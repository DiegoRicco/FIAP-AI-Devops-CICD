"""postdeploy-synthetic-gate (runtime ECS) contra o ALB REAL.

Tres perguntas, nessa ordem, e qualquer uma reprova (exit 1):
1. "O que esta no ar e o que o pipeline avaliou?" GET <alb>/candidate/ready
   precisa estar pronto e trazer o MESMO image_digest, release_id, corpus,
   graph_seed, indice vetorial e modelo de embedding do release-record.json, e
   graph_version == graph_seed_version (grafo da task = seed da imagem).
   Se nao bater, a avaliacao seguinte mediria outro release.
2. "Ele se comporta na nuvem?" Smoke direto: multi-hop do grafo (notebook na
   BF 2027), vetor, pedido Q-1001 (da cliente) e Q-2001 (de outro cliente),
   documento interno (ACL) e tenant parceiro-x.
3. "O golden de grafo passa pelo HTTP?" evals/graph_gate.py --target http no
   /candidate, com o release_id conferido em cada resposta.
E o live? Conferido de passagem: o candidate nunca pode estar servindo na URL
do cliente (live com o digest do candidate antes do promote = alerta).

Uso: python3 scripts/release-ecs/smoke_ecs.py --env candidate --record dist/release-record.json
Saidas: reports/junit-smoke-ecs.xml, reports/junit-postdeploy-graph.xml,
        reports/postdeploy-graph.json; o resultado vai para o registro (postdeploy).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERSION_KEYS = ("image_digest", "release_id", "corpus_version", "graph_seed_version", "vector_index_version",
                "embedding_model_id", "embedding_model_revision", "embedding_dim", "index_version",
                "eval_set_version", "prompt_version", "application_version", "model_version")


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def alb_url():
    out = subprocess.run(["aws", "ssm", "get-parameter", "--name", "/qc-assistant/ecs/alb-url",
                          "--query", "Parameter.Value", "--output", "text"], check=True, text=True,
                         stdout=subprocess.PIPE)
    return out.stdout.strip().rstrip("/")


def http(method, url, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "X-QC-Scenario": "postdeploy-smoke"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read().decode("utf-8"))
        except ValueError:
            return err.code, {}


def get_ready(base, attempts, wait_s):
    """Logo apos o deploy o ALB pode levar segundos para trocar o target."""
    last = None
    for i in range(1, attempts + 1):
        try:
            status, body = http("GET", f"{base}/ready")
            if status == 200:
                return body
            last = f"HTTP {status} {body.get('checks')}"
        except (urllib.error.URLError, OSError, ValueError) as err:
            last = str(err)
        log(f"  /ready tentativa {i}/{attempts}: {last}")
        time.sleep(wait_s)
    raise RuntimeError(f"/ready nao ficou 200 em {attempts} tentativas: {last}")


def graph_entry(resp):
    return (resp.get("strategies") or {}).get("graph") or {}


def cites(entry):
    return [f"{c['doc_id']}@v{c['version']}" for c in entry.get("citations") or []]


def stable_ready(base, record, reads=3, wait_s=3):
    """Tres leituras seguidas de /ready com o digest e o release do registro.

    Durante o rollout o ALB pode alternar entre a task velha e a nova; uma
    leitura so poderia pegar a nova por sorte e o smoke mediria a velha.
    """
    seen = []
    for _ in range(reads):
        status, body = http("GET", f"{base}/ready")
        v = body.get("versions") or {}
        seen.append((status, v.get("release_id"), v.get("image_digest")))
        time.sleep(wait_s)
    ok = all(s == 200 and r == record["release_id"] and d == record["image_digest"] for s, r, d in seen)
    return ok, f"{reads} leituras: {[(s, r) for s, r, _ in seen]}"


def behavior_checks(base, attempts=3, wait_s=5):
    """Smoke de comportamento: poucos casos, um por caminho critico do produto.

    Cada caso tem ate `attempts` tentativas: logo apos o boot a primeira consulta
    ao Neo4j pode voltar vazia (pool aquecendo). Toda tentativa falha fica no
    detalhe do JUnit, entao instabilidade nao some; o que reprova e falhar em
    TODAS as tentativas.
    """
    checks = []
    pending = {}

    def add(name, ok, detail):
        pending.setdefault(name, []).append((bool(ok), detail))

    def run_once(only=None):
        q = {"tenant": "quantum", "user_role": "customer"}
        _cases(base, q, add, only)

    run_once()
    for attempt in range(2, attempts + 1):
        failing = {n for n, tries in pending.items() if not tries[-1][0]}
        if not failing:
            break
        log(f"  smoke: {sorted(failing)} falhou; tentativa {attempt}/{attempts} em {wait_s} s")
        time.sleep(wait_s)
        run_once(failing)
    for name, tries in pending.items():
        ok = tries[-1][0]
        detail = tries[-1][1]
        if len(tries) > 1:
            detail += f" | tentativas={len(tries)}, falhas anteriores: " + " || ".join(d for o, d in tries[:-1] if not o)
        checks.append({"name": name, "ok": ok, "detail": detail, "attempts": len(tries)})
    return checks


def _cases(base, q, add, only=None):
    """Os casos do smoke. only = conjunto de nomes a repetir (None = todos)."""
    def want(name):
        return only is None or name in only

    if want("grafo.troca_fone_v3_30_dias"):
        _fone(base, q, add)
    if want("grafo.multi_hop_notebook_bf2027"):
        _multi_hop(base, q, add)
    if want("vetor.neo4j_arrependimento"):
        _vector(base, q, add)
    if want("pedidos.q1001_da_cliente"):
        _q1001(base, q, add)
    if want("pedidos.q2001_outro_cliente_negado"):
        _q2001(base, q, add)
    if want("acl.manual_interno_bloqueado"):
        _acl(base, q, add)
    if want("tenant.parceiro_x_isolado"):
        _tenant(base, q, add)


def _fone(base, q, add):
    s, r = http("POST", f"{base}/lab/compare", {**q, "question": "Comprei um fone de ouvido, qual o prazo para trocar?",
                                                "as_of_date": "2026-11-14", "strategies": ["bm25", "vector", "graph"]})
    g = graph_entry(r)
    add("grafo.troca_fone_v3_30_dias", s == 200 and "politica-trocas@v3" in cites(g) and "30 dias" in (g.get("answer") or "")
        and "politica-trocas@v2" not in sum((cites(e) for e in (r.get("strategies") or {}).values()), []),
        f"HTTP {s} graph cites={cites(g)} selected={g.get('selected_policy_version')} "
        f"abstain={g.get('abstain_reason')} error={g.get('error')}")


def _multi_hop(base, q, add):
    s, r = http("POST", f"{base}/lab/compare", {**q, "question": "Comprei um notebook na Black Friday, ate quando posso trocar?",
                                                "as_of_date": "2027-11-26", "strategies": ["graph"]})
    g = graph_entry(r)
    types = {e.get("type") for e in ((g.get("path") or {}).get("edges") or []) if e.get("role", "selected") == "selected"}
    add("grafo.multi_hop_notebook_bf2027", s == 200 and "campanha-black-friday@v1" in cites(g)
        and {"IN_CATEGORY", "PARTICIPATES_IN", "APPLIES_TO"} <= types,
        f"HTTP {s} cites={cites(g)} arestas={sorted(t for t in types if t)} abstain={g.get('abstain_reason')}")


def _vector(base, q, add):
    s, r = http("POST", f"{base}/lab/compare", {**q, "question": "Desisti da compra, consigo devolver e receber meu dinheiro de volta?",
                                                "as_of_date": "2026-11-14", "strategies": ["vector"]})
    v = (r.get("strategies") or {}).get("vector") or {}
    add("vetor.neo4j_arrependimento", s == 200 and v.get("source") == "neo4j-vector"
        and any(x.get("doc_id") == "politica-devolucoes" for x in (v.get("results") or [])[:3]),
        f"HTTP {s} source={v.get('source')} top={[x.get('chunk_id') for x in (v.get('results') or [])[:3]]}")


def _q1001(base, q, add):
    s, r = http("POST", f"{base}/assistant/ask", {**q, "question": "Qual o status do pedido Q-1001?", "order_id": "Q-1001",
                                                  "auth_token": "lab-token-ana", "customer_id": "cliente-ana"})
    add("pedidos.q1001_da_cliente", s == 200 and (r.get("order") or {}).get("status") == "em_transito",
        f"HTTP {s} status={(r.get('order') or {}).get('status')} abstain={r.get('abstain_reason')}")


def _q2001(base, q, add):
    s, r = http("POST", f"{base}/assistant/ask", {**q, "question": "Qual o status do pedido Q-2001?", "order_id": "Q-2001",
                                                  "auth_token": "lab-token-ana", "customer_id": "cliente-ana"})
    add("pedidos.q2001_outro_cliente_negado", s == 200 and r.get("abstain_reason") == "forbidden"
        and "confirmado" not in (r.get("answer") or ""),
        f"HTTP {s} abstain={r.get('abstain_reason')} order={r.get('order')}")


def _acl(base, q, add):
    s, r = http("POST", f"{base}/lab/compare", {**q, "question": "Qual a alcada do atendente para aprovar estorno sem frete reverso?",
                                                "as_of_date": "2026-11-14", "strategies": ["bm25", "vector", "graph"]})
    leaked = [c for e in (r.get("strategies") or {}).values() for c in cites(e) if c.startswith("manual-interno")]
    texts = " ".join((e.get("answer") or "") for e in (r.get("strategies") or {}).values())
    add("acl.manual_interno_bloqueado", s == 200 and not leaked and "R$ 200" not in texts,
        f"HTTP {s} citou_interno={leaked}")


def _tenant(base, q, add):
    s, r = http("POST", f"{base}/lab/compare", {**q, "question": "Em quantos dias posso trocar um produto?",
                                                "as_of_date": "2026-11-14", "strategies": ["bm25", "vector", "graph"]})
    leaked = [c for e in (r.get("strategies") or {}).values() for c in cites(e) if c.startswith("parceiro-x")]
    texts = " ".join((e.get("answer") or "") for e in (r.get("strategies") or {}).values())
    add("tenant.parceiro_x_isolado", s == 200 and not leaked and "60 dias" not in texts,
        f"HTTP {s} citou_parceiro={leaked}")


def write_junit(path, suite, checks):
    failures = sum(1 for c in checks if not c["ok"])
    cases = []
    for c in checks:
        body = "" if c["ok"] else f"<failure message={quoteattr(c['detail'][:900])}/>"
        cases.append(f'<testcase classname="{suite}" name={quoteattr(c["name"])}>{body}</testcase>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite name="{suite}" tests="{len(checks)}" '
           f'failures="{failures}">{"".join(cases)}</testsuite></testsuites>\n')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def main(argv=None):
    p = argparse.ArgumentParser(description="smoke + golden de grafo contra o ALB real")
    p.add_argument("--env", choices=("candidate", "live"), default="candidate")
    p.add_argument("--record", default=os.path.join(ROOT, "dist", "release-record.json"))
    p.add_argument("--reports", default=os.path.join(ROOT, "reports"))
    p.add_argument("--url", help="base do ALB (padrao: SSM /qc-assistant/ecs/alb-url)")
    p.add_argument("--attempts", type=int, default=10)
    p.add_argument("--slice", default="graph_precedence,graph_multi_hop,temporal,tenant,authorization,orders,adversarial")
    args = p.parse_args(argv)

    with open(args.record, encoding="utf-8") as f:
        record = json.load(f)
    alb = (args.url or alb_url()).rstrip("/")
    base = alb + ("/candidate" if args.env == "candidate" else "")
    log(f"smoke-ecs: alvo {base} | release esperado {record['release_id']} ({record['image_digest'][:19]}...)")

    checks = []
    started = time.time()
    try:
        ready = get_ready(base, args.attempts, 6)
        v = ready.get("versions") or {}
        checks.append({"name": "ready", "ok": ready.get("ready") is True, "detail": json.dumps(ready.get("checks"))})
        for k in VERSION_KEYS:
            want = record.get(k)
            if want is None:
                # Versao que o package nao conseguiu calcular sem o motor: o
                # candidate a fornece e ela entra no registro daqui em diante.
                record[k] = v.get(k)
                continue
            checks.append({"name": f"versao.{k}", "ok": str(v.get(k)) == str(want),
                           "detail": f"registro={want} no_ar={v.get(k)}"})
        checks.append({"name": "versao.graph_version_igual_seed", "ok": v.get("graph_version") == v.get("graph_seed_version"),
                       "detail": f"graph_version={v.get('graph_version')} graph_seed_version={v.get('graph_seed_version')}"})
        ok, detail = stable_ready(base, record)
        checks.append({"name": "ready_estavel_3_leituras", "ok": ok, "detail": detail})
        if args.env == "candidate":
            # Isolamento: o candidate ainda nao promovido nao pode estar na URL do cliente.
            ls, live = http("GET", f"{alb}/ready")
            lv = (live.get("versions") or {})
            checks.append({"name": "isolamento.live_nao_serve_candidate",
                           "ok": lv.get("release_id") != record["release_id"] or lv.get("image_digest") != record["image_digest"],
                           "detail": f"live release={lv.get('release_id')} digest={str(lv.get('image_digest'))[:19]}"})
        record.setdefault("readiness", {})[args.env] = {"seconds_until_ready": round(time.time() - started, 1),
                                                        "graph_version": v.get("graph_version")}
    except RuntimeError as err:
        checks.append({"name": "ready", "ok": False, "detail": str(err)})

    identity_ok = all(c["ok"] for c in checks)
    if identity_ok:
        checks += behavior_checks(base)
    write_junit(os.path.join(args.reports, "junit-smoke-ecs.xml"), "postdeploy-smoke-ecs", checks)
    for c in checks:
        log(f"  {'OK   ' if c['ok'] else 'FALHA'} {c['name']}: {c['detail']}")

    rc_graph = None
    if all(c["ok"] for c in checks):
        cmd = [sys.executable, os.path.join(ROOT, "evals", "graph_gate.py"), "--target", "http", "--url", base,
               "--expect-release-id", record["release_id"], "--slice", args.slice,
               "--out", os.path.join(args.reports, "postdeploy-graph.json"),
               "--junit", os.path.join(args.reports, "junit-postdeploy-graph.xml")]
        log("smoke-ecs: " + " ".join(cmd[1:]))
        rc_graph = subprocess.call(cmd)
    else:
        log("smoke-ecs REPROVADO antes do golden: o que esta no ar nao e o release avaliado, ou falhou o basico")

    verdict = "PASS" if all(c["ok"] for c in checks) and rc_graph == 0 else "FAIL"
    record["postdeploy"] = {"environment": args.env, "verdict": verdict, "url": base,
                            "checks_failed": [c["name"] for c in checks if not c["ok"]],
                            "graph_gate_exit": rc_graph,
                            "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(args.record, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    subprocess.check_call([sys.executable, os.path.join(ROOT, "scripts", "release-ecs", "manifest.py"), "seal",
                           "--record", args.record], stdout=subprocess.DEVNULL)
    log(f"smoke-ecs: VEREDITO {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
