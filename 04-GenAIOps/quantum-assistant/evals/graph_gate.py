"""graph-policy-gate: o grafo e as versoes de politica continuam certos?

Dois blocos, nessa ordem, e qualquer um reprova o job:

1. CONSISTENCIA DO SEED (sem Neo4j, segundos). Compara o grafo que o seed +
   front matter do corpus vao gerar com as regras de versao:
   - SUPERSEDES sempre da versao NOVA para a antiga do MESMO documento, sem ciclo;
   - versao substituida (alvo de SUPERSEDES) nunca fica com status active;
   - no maximo uma versao active por documento/tenant em cada data;
   - nenhuma referencia "<doc_id>@vN" no seed aponta para versao obsoleta;
   - nenhuma aresta de negocio (APPLIES_TO, PARTICIPATES_IN...) chega direto
     numa PolicyVersion que nao e a vigente.
   E isso que pega o Cenario B da Demo 5.2: alguem republica a troca v2
   (15 dias) como active ou inverte o SUPERSEDES. O pipeline reprova ANTES do
   build da imagem.

2. COMPORTAMENTO (golden_graph_v1 contra o motor real). Cada caso roda nas tres
   estrategias (bm25, vector, graph) via lab.engine com um Neo4j de verdade
   (no CI, um Neo4j efemero no runner) ou via HTTP contra o candidate no ALB.
   Mede Hit@k e MRR por estrategia, graph_path_correct_rate,
   source_is_current_rate, abstention_correct_rate e as CONTAGENS CRITICAS:
     tenant_acl_violation_count, stale_source_served_count,
     wrong_graph_edge_count, order_auth_violation_count.
   Contagem critica > 0 reprova, nao importa a media. Comparar as tres
   estrategias e honesto: uma pode ser pior em alguns casos; so as listadas em
   gate_strategies de cada caso decidem se o caso passa.

Uso:
  python3 evals/graph_gate.py --target seed                       # so o bloco 1
  python3 evals/graph_gate.py --target local --neo4j-uri bolt://127.0.0.1:17687 \
      --neo4j-password-file /tmp/x/neo4j.pw                       # blocos 1 e 2
  python3 evals/graph_gate.py --target http --url http://<alb>/candidate \
      --expect-release-id rel-...                                  # pos-deploy
Saidas: --out (JSON por slice e por estrategia) e --junit (sempre gravado).
Exit 0 aprovado, 1 reprovado. O JUnit e vitrine; quem reprova e o exit code.
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

from core.corpus import load_chunks, load_documents  # noqa: E402
from core.retrieval import check_access, strip_accents  # noqa: E402

EVALS = os.path.join(ROOT, "evals")
STRATEGIES = ("bm25", "vector", "graph")
CRITICAL_COUNTS = ("tenant_acl_violation_count", "stale_source_served_count", "wrong_graph_edge_count",
                   "order_auth_violation_count")
# Arestas de negocio: ligam catalogo a politica. Nenhuma pode apontar direto
# para uma versao; quem escolhe a versao e HAS_VERSION + vigencia + SUPERSEDES.
BUSINESS_EDGES = ("APPLIES_TO", "PARTICIPATES_IN", "IN_CATEGORY", "BELONGS_TO")
CURRENT_FILTERS = {"require_active": True, "respect_effective_date": True,
                   "enforce_tenant": False, "enforce_access_level": False}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def norm(text):
    return strip_accents(text or "")


def ratio(num, den):
    return round(num / den, 4) if den else None


# ================================================================ bloco 1: seed
def _walk_strings(obj, path="seed"):
    """Toda string do seed com o caminho dela (para achar "<doc>@vN" escondido)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _overlap(a, b):
    """As janelas de vigencia [effective, expires] de duas versoes se cruzam?"""
    a_end = a.get("expires_date") or "9999-12-31"
    b_end = b.get("expires_date") or "9999-12-31"
    return a["effective_date"] <= b_end and b["effective_date"] <= a_end


def check_seed(data_root):
    """Lista de achados {rule, check, severity, detail}. Lista vazia = seed coerente.

    rule e a contagem critica que o achado alimenta: um seed que liga a versao
    errada e a causa raiz de "fonte obsoleta servida" e de "aresta errada".
    """
    import re
    from graph import loader

    findings = []

    def add(rule, check, detail, severity="critical"):
        findings.append({"rule": rule, "check": check, "severity": severity, "detail": detail})

    for err in loader.validate(data_root):
        add("seed_structure", "estrutura", err)
    if findings:
        # Estrutura quebrada: o grafo nem monta; as checagens abaixo nao se aplicam.
        return findings

    docs = [m for m, _ in load_documents(os.path.join(data_root, "corpus"))]
    versions = {loader.version_id(m["doc_id"], m["version"]): m for m in docs}
    graph = loader.build_graph(data_root)

    sup = [e for e in graph["edges"] if e["type"] == "SUPERSEDES"]
    superseded = {}
    for e in sup:
        src, dst = versions.get(e["source"]), versions.get(e["target"])
        prov = e["props"].get("provenance")
        if dst is None:
            add("wrong_graph_edge_count", "supersedes_alvo_inexistente",
                f"{e['source']} SUPERSEDES {e['target']}, que nao existe no corpus ({prov})")
            continue
        if src["doc_id"] != dst["doc_id"]:
            add("wrong_graph_edge_count", "supersedes_entre_documentos",
                f"{e['source']} SUPERSEDES {e['target']}: documentos diferentes ({prov})")
        elif int(src["version"]) <= int(dst["version"]):
            add("wrong_graph_edge_count", "supersedes_invertido",
                f"{e['source']} SUPERSEDES {e['target']}: a versao antiga substitui a nova ({prov})")
        superseded.setdefault(e["target"], []).append(e["source"])

    # Ciclo de SUPERSEDES (v2 -> v3 -> v2): nenhuma versao seria a vigente.
    nxt = {}
    for e in sup:
        nxt.setdefault(e["source"], []).append(e["target"])
    for start in nxt:
        seen, stack = set(), list(nxt[start])
        while stack:
            cur = stack.pop()
            if cur == start:
                add("wrong_graph_edge_count", "supersedes_ciclo", f"ciclo de SUPERSEDES passando por {start}")
                break
            if cur not in seen:
                seen.add(cur)
                stack.extend(nxt.get(cur, []))

    for vid, by in sorted(superseded.items()):
        meta = versions.get(vid)
        if meta and meta["status"] == "active":
            add("stale_source_served_count", "substituida_ativa",
                f"{vid} esta como status: active em corpus/{meta['file']}, mas e substituida por {sorted(by)}")

    by_doc = {}
    for vid, m in versions.items():
        by_doc.setdefault((m["doc_id"], m["tenant"]), []).append((vid, m))
    for (doc_id, tenant), items in sorted(by_doc.items()):
        active = sorted([(vid, m) for vid, m in items if m["status"] == "active"], key=lambda x: int(x[1]["version"]))
        for i, (va, ma) in enumerate(active):
            for vb, mb in active[i + 1:]:
                if _overlap(ma, mb):
                    add("stale_source_served_count", "duas_versoes_ativas",
                        f"{doc_id} ({tenant}): {va} e {vb} active com vigencia sobreposta")
        newest = max(items, key=lambda x: int(x[1]["version"]))
        if active and newest[1]["status"] != "active":
            add("stale_source_served_count", "versao_nova_inativa",
                f"{doc_id}: a versao mais nova {newest[0]} esta {newest[1]['status']} e uma anterior segue active")

    current = {vid for vid, m in versions.items() if m["status"] == "active" and vid not in superseded}
    ref = re.compile(r"\b([a-z0-9][a-z0-9-]*)@v(\d+)\b")
    for path, text in _walk_strings(loader.load_seed(data_root)):
        for doc_id, ver in ref.findall(text):
            vid = f"{doc_id}@v{ver}"
            if vid in versions and vid not in current:
                add("stale_source_served_count", "seed_referencia_obsoleta",
                    f"{path} cita {vid}, que nao e a versao vigente")

    for e in graph["edges"]:
        if e["type"] in BUSINESS_EDGES and e["target_label"] == "PolicyVersion" and e["target"] not in current:
            add("wrong_graph_edge_count", "aresta_negocio_em_versao_obsoleta",
                f"{e['source']} {e['type']} {e['target']} ({e['props'].get('provenance')})")
    return findings


# ================================================================ bloco 2: alvos
class LocalTarget:
    """lab.engine no mesmo processo, contra um Neo4j de verdade (CI: efemero)."""

    def __init__(self, args):
        from lab.engine import Engine
        password = os.environ.get("NEO4J_PASSWORD")
        if args.neo4j_password_file:
            with open(args.neo4j_password_file, encoding="utf-8") as f:
                password = f.read().strip()
        if not password:
            raise ValueError("senha do Neo4j ausente: --neo4j-password-file ou NEO4J_PASSWORD")
        self.engine = Engine(args.neo4j_uri, args.neo4j_user, password, root=ROOT, data_root=args.data_root)
        started = datetime.datetime.now()
        boot = self.engine.bootstrap(timeout_s=args.bootstrap_timeout)
        log(f"graph-gate: bootstrap em {(datetime.datetime.now() - started).total_seconds():.1f} s "
            f"graph_version={boot.get('graph_version')} index={boot.get('index')}")
        self.boot = boot

    def compare(self, case, request_id):
        return self.engine.compare(case["question"], case["as_of_date"], tenant=case["tenant"],
                                   user_role=case["user_role"], strategies=STRATEGIES,
                                   order_id=case.get("order_id"), auth_token=case.get("auth_token"),
                                   request_id=request_id, scenario_id="ci-graph-gate", case_id=case["case_id"])

    def ask(self, case, request_id):
        return self.engine.ask(case["question"], strategy="graph", customer_id=case.get("customer_id"),
                               auth_token=case.get("auth_token"), order_id=case.get("order_id"),
                               tenant=case["tenant"], user_role=case["user_role"], scenario_id="ci-graph-gate",
                               request_id=request_id, as_of_date=case["as_of_date"], case_id=case["case_id"])

    def close(self):
        self.engine.close()


class HttpTarget:
    """Mesmos casos via HTTP no runtime publicado (candidate no ALB)."""

    def __init__(self, args):
        self.url = args.url.rstrip("/")
        self.timeout = args.timeout
        self.boot = None

    def _post(self, path, body, case_id):
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "X-QC-Scenario": "ci-graph-gate",
                                              "X-QC-Case": case_id})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"HTTP {err.code} em {path}") from err

    def compare(self, case, request_id):
        body = {k: case.get(k) for k in ("question", "as_of_date", "tenant", "user_role", "order_id", "auth_token")}
        body["strategies"] = list(STRATEGIES)
        return self._post("/lab/compare", body, case["case_id"])

    def ask(self, case, request_id):
        body = {k: case.get(k) for k in ("question", "as_of_date", "tenant", "user_role", "order_id", "auth_token",
                                         "customer_id")}
        body.update({"strategy": "graph", "request_id": request_id, "scenario_id": "ci-graph-gate"})
        return self._post("/assistant/ask", body, case["case_id"])

    def close(self):
        pass


# ================================================================ checagens
def _current_for(meta, as_of):
    return meta is not None and check_access(meta, as_of, None, None, CURRENT_FILTERS) is None


def evaluate_strategy(case, entry, meta_by_chunk, k):
    """Avalia UMA estrategia de UM caso. Retorna dict com checks e contribuicoes."""
    checks = []
    # safety=True: regra de seguranca, vale para QUALQUER estrategia (citar a v2,
    # vazar tenant ou manual interno e erro mesmo na estrategia que nao decide o
    # caso). As demais so contam nas estrategias de gate_strategies.
    safety_names = ("tenant_acl", "fonte_vigente", "texto_proibido")

    def add(name, ok, detail="", rule=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "rule": rule, "safety": name in safety_names})

    answer = entry.get("answer") or ""
    citations = entry.get("citations") or []
    results = entry.get("results") or []
    cited = [f"{c['doc_id']}@v{c['version']}" for c in citations]

    # Regra dura: tenant/ACL vale para o que foi RECUPERADO ou citado, nao so citado.
    leaked = []
    for item in list(results) + list(citations):
        meta = meta_by_chunk.get(item.get("chunk_id"))
        if meta and (meta["tenant"] != case["tenant"] or
                     (meta["access_level"] != "public" and case["user_role"] == "customer")):
            leaked.append(item["chunk_id"])
    add("tenant_acl", not leaked, f"vazou: {sorted(set(leaked))}" if leaked else "", "tenant_acl_violation_count")

    # Regra dura: fonte obsoleta. Citar versao fora de vigencia (pela data do
    # caso), versao proibida pelo gabarito ou versao diferente da esperada.
    stale = [c["chunk_id"] for c in citations if not _current_for(meta_by_chunk.get(c["chunk_id"]), case["as_of_date"])]
    stale += [v for v in cited if v in case.get("must_not_cite", [])]
    if case.get("expected_doc_id") and case.get("expected_doc_version"):
        stale += [v for c, v in zip(citations, cited)
                  if c["doc_id"] == case["expected_doc_id"] and str(c["version"]) != str(case["expected_doc_version"])]
    forbidden = [t for t in case.get("must_not_contain", []) if norm(t) in norm(answer)]
    add("fonte_vigente", not stale, f"citou: {sorted(set(stale))}" if stale else "", "stale_source_served_count")
    if case.get("must_not_contain"):
        # Texto proibido e o sintoma que o cliente ve (ex.: "15 dias"). Em caso de
        # tenant/autorizacao conta como vazamento; nos demais, como fonte obsoleta.
        rule = "tenant_acl_violation_count" if case["slice"] in ("tenant", "authorization") else \
            "stale_source_served_count" if case["slice"] in ("graph_precedence", "temporal", "graph_multi_hop") else None
        add("texto_proibido", not forbidden, f"apareceu: {forbidden}" if forbidden else "", rule)

    abstained = entry.get("abstained") is True
    if case.get("must_abstain") is True:
        reason = case.get("expected_abstain_reason")
        add("recusa_obrigatoria", abstained and (reason is None or entry.get("abstain_reason") == reason),
            f"esperado recusa ({reason or 'qualquer motivo'}), recebido abstained={abstained} "
            f"reason={entry.get('abstain_reason')}")
    elif case.get("must_abstain") is False:
        add("nao_recusou", not abstained, f"abstain_reason={entry.get('abstain_reason')}")
        missing = [f for f in case.get("expected_answer_fields", []) if norm(f) not in norm(answer)]
        add("campos_esperados", not missing, f"faltou: {missing}" if missing else "")
        if case.get("expected_doc_id"):
            want = f"{case['expected_doc_id']}@v{case['expected_doc_version']}"
            add("citacao_esperada", want in cited, f"esperado {want}, citou {cited}")
        if case.get("expected_chunk_id") and entry.get("strategy") == "graph":
            # A versao certa com a secao errada ("Troca de presente" no lugar de
            # "Prazo para troca") tambem informa errado: confere o trecho citado.
            first = citations[0]["chunk_id"] if citations else None
            add("secao_esperada", first == case["expected_chunk_id"],
                f"esperado {case['expected_chunk_id']}, primeira citacao {first}")

    rank = None
    if case.get("expected_doc_id") and case.get("must_abstain") is False:
        for pos, r in enumerate(results, start=1):
            if r.get("doc_id") == case["expected_doc_id"] and str(r.get("version")) == str(case["expected_doc_version"]):
                rank = pos
                break

    path_ok = None
    if entry.get("strategy") == "graph" and case.get("expected_path_edges"):
        path = entry.get("path") or {}
        edges = path.get("edges") or []
        selected = [e for e in edges if e.get("role", "selected") == "selected"]
        types = {e.get("type") for e in selected}
        missing = [t for t in case["expected_path_edges"] if t not in types]
        want = f"{case['expected_doc_id']}@v{case['expected_doc_version']}"
        got = entry.get("selected_policy_version")
        bad_targets = [e for e in selected if e.get("target") in case.get("must_not_cite", [])]
        path_ok = not missing and got == want and not bad_targets
        detail = (f"arestas faltando={missing} versao_escolhida={got} (esperada {want})"
                  + (f" aresta para versao proibida={[e.get('target') for e in bad_targets]}" if bad_targets else ""))
        # Caminho errado em caso critico e regra dura; nos demais, entra na taxa.
        add("caminho_grafo", path_ok, "" if path_ok else detail,
            "wrong_graph_edge_count" if case["severity"] == "critical" else None)
    return {"checks": checks, "rank": rank, "path_ok": path_ok,
            "summary": {"answer": answer[:240], "abstained": abstained, "abstain_reason": entry.get("abstain_reason"),
                        "citations": cited, "selected_policy_version": entry.get("selected_policy_version"),
                        "results": [f"{r.get('doc_id')}@v{r.get('version')} ({r.get('score')})" for r in results[:k]],
                        "blocked": [f"{b.get('doc_id')}@v{b.get('version')}:{b.get('reason')}"
                                    for b in entry.get("blocked") or []][:8]}}


def evaluate_order(case, resp):
    """Pedido: status so vem da API autorizada; nunca do grafo nem de outro cliente."""
    checks, counts = [], {c: 0 for c in CRITICAL_COUNTS}

    def add(name, ok, detail="", rule=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "rule": rule})
        if rule and not ok:
            counts[rule] += 1

    answer = resp.get("answer") or ""
    status = (resp.get("order") or {}).get("status")
    add("rota", resp.get("route") == "order_status", f"rota={resp.get('route')}")
    if case.get("must_abstain"):
        add("pedido_negado", resp.get("abstained") is True and status is None
            and resp.get("abstain_reason") == case.get("expected_abstain_reason"),
            f"esperado {case.get('expected_abstain_reason')}, recebido abstain={resp.get('abstain_reason')} status={status}",
            "order_auth_violation_count")
    else:
        add("status_pedido", status == case.get("expected_order_status"),
            f"esperado={case.get('expected_order_status')} recebido={status}", "order_auth_violation_count")
    forbidden = [t for t in case.get("must_not_contain", []) if norm(t) in norm(answer)]
    if case.get("must_not_contain"):
        add("texto_proibido", not forbidden, f"apareceu: {forbidden}" if forbidden else "", "order_auth_violation_count")
    return {"checks": checks, "counts": counts, "passed": all(c["ok"] for c in checks),
            "summary": {"answer": answer[:240], "abstain_reason": resp.get("abstain_reason"), "order_status": status}}


def load_cases(name):
    with open(os.path.join(EVALS, f"{name}.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def metrics_for(rows, k):
    """Metricas por estrategia (comparacao honesta) + taxas do caso."""
    out = {"n_cases": len(rows), "case_pass_rate": ratio(sum(r["passed"] for r in rows), len(rows))}
    per = {}
    for s in STRATEGIES:
        evs = [r["strategies"][s] for r in rows if s in r.get("strategies", {})]
        # Denominador de Hit@k/MRR: casos documentais em que ESTA estrategia e
        # criterio (gate_strategies). O grafo recusa de proposito pergunta sem
        # produto/campanha; cobrar Hit@k dele ali mediria o desenho, nao a busca.
        ret = [r["strategies"][s] for r in rows if s in r.get("strategies", {}) and r["case"].get("expected_doc_id")
               and r["case"].get("must_abstain") is False and r["strategies"][s].get("gated")]
        ret_all = [r["strategies"][s] for r in rows if s in r.get("strategies", {}) and r["case"].get("expected_doc_id")
                   and r["case"].get("must_abstain") is False]
        hits = [e for e in ret if e["rank"] is not None and e["rank"] <= k]
        cite = [e for e in evs if e["summary"]["citations"]]
        cur_ok = [e for e in cite if next((c["ok"] for c in e["checks"] if c["name"] == "fonte_vigente"), True)]
        abst_cases = [r["strategies"][s] for r in rows if s in r.get("strategies", {}) and r["case"].get("must_abstain") is True]
        abst_ok = [e for e in abst_cases if next((c["ok"] for c in e["checks"] if c["name"] == "recusa_obrigatoria"), False)]
        paths = [e for e in evs if e["path_ok"] is not None]
        per[s] = {
            "n_retrieval_cases": len(ret),
            "hit_at_k": ratio(len(hits), len(ret)),
            "mrr": ratio(sum(1 / e["rank"] for e in ret if e["rank"]), len(ret)),
            # Mesma medida sobre TODOS os casos documentais: so para comparar as
            # estrategias com honestidade (nao e gate).
            "hit_at_k_all_cases": ratio(len([e for e in ret_all if e["rank"] and e["rank"] <= k]), len(ret_all)),
            "n_retrieval_cases_all": len(ret_all),
            "source_is_current_rate": ratio(len(cur_ok), len(cite)),
            "n_cases_with_citations": len(cite),
            "abstention_correct_rate": ratio(len(abst_ok), len(abst_cases)),
            "n_abstention_cases": len(abst_cases),
            "graph_path_correct_rate": ratio(sum(e["path_ok"] for e in paths), len(paths)) if paths else None,
            "n_path_cases": len(paths),
            "case_pass_rate": ratio(sum(e["passed"] for e in evs), len(evs)),
        }
    out["strategies"] = per
    for c in CRITICAL_COUNTS:
        out[c] = sum(r["counts"][c] for r in rows)
    return out


def run_behavior(args, thresholds, target):
    k = thresholds.get("k", 3)
    cases = load_cases(args.set)
    if args.slice:
        wanted = {s.strip() for s in args.slice.split(",") if s.strip()}
        cases = [c for c in cases if c["slice"] in wanted]
    if not cases:
        raise SystemExit(f"nenhum caso selecionado (set={args.set}, slice={args.slice})")
    order = list(cases)
    # A seed so embaralha a ordem: o veredito nao pode depender dela.
    random.Random(args.seed).shuffle(order)
    meta_by_chunk = {c["chunk_id"]: c for c in load_chunks(os.path.join(args.data_root or ROOT, "corpus"))}

    rows, first_versions, release_ids = [], None, set()
    for case in order:
        request_id = f"graph-gate-{args.seed}-{case['case_id']}"
        started = datetime.datetime.now()
        row = {"case": case, "case_id": case["case_id"], "slice": case["slice"], "severity": case["severity"],
               "strategies": {}, "counts": {c: 0 for c in CRITICAL_COUNTS}, "error": None, "checks": []}
        try:
            if case.get("mode") == "ask":
                resp = target.ask(case, request_id)
                ev = evaluate_order(case, resp)
                row["checks"], row["passed"], row["summary"] = ev["checks"], ev["passed"], ev["summary"]
                for c in CRITICAL_COUNTS:
                    row["counts"][c] += ev["counts"][c]
            else:
                resp = target.compare(case, request_id)
                gate = set(case.get("gate_strategies") or ["graph"])
                passed = True
                for s in STRATEGIES:
                    entry = (resp.get("strategies") or {}).get(s)
                    if entry is None:
                        raise RuntimeError(f"estrategia {s} ausente na resposta")
                    ev = evaluate_strategy(case, entry, meta_by_chunk, k)
                    ev["gated"] = s in gate
                    relevant = [c for c in ev["checks"] if ev["gated"] or c["safety"]]
                    ev["passed"] = all(c["ok"] for c in relevant)
                    ev["counts"] = {c: 0 for c in CRITICAL_COUNTS}
                    for c in relevant:
                        if c["rule"] and not c["ok"]:
                            ev["counts"][c["rule"]] += 1
                            row["counts"][c["rule"]] += 1
                    row["strategies"][s] = ev
                    passed = passed and ev["passed"]
                row["passed"] = passed
            versions = resp.get("versions") or {}
            first_versions = first_versions or versions
            release_ids.add(resp.get("release_id") or versions.get("release_id"))
        except Exception as err:  # noqa: BLE001 - qualquer falha de chamada reprova o caso
            row["error"] = f"{type(err).__name__}: {err}"
            row["passed"] = False
        row["latency_ms"] = round((datetime.datetime.now() - started).total_seconds() * 1000, 1)
        rows.append(row)
    rows.sort(key=lambda r: r["case_id"])
    return rows, first_versions, release_ids


def build_gates(thresholds, overall, slices, seed_findings, behavior_ran, release_check):
    gates = []
    for f in seed_findings:
        gates.append({"name": f"seed.{f['check']}", "ok": False, "detail": f["detail"], "group": "seed",
                      "rule": f["rule"]})
    gates.append({"name": "seed.consistencia", "ok": not seed_findings, "group": "seed",
                  "detail": f"{len(seed_findings)} achado(s) no seed/front matter" if seed_findings
                  else "seed e front matter coerentes (SUPERSEDES, status, vigencia, referencias)"})
    if not behavior_ran:
        return gates
    for c in CRITICAL_COUNTS:
        v = overall[c]
        gates.append({"name": f"critico.{c}", "ok": v == 0, "value": v, "max": 0, "group": "critico",
                      "detail": f"{c}={v} (maximo 0): {thresholds['hard_counts'][c]}"})
    for s, rules in thresholds["strategies"].items():
        m = overall["strategies"][s]
        for name, rule in rules.items():
            if not isinstance(rule, dict) or rule.get("min") is None:
                continue
            v = m.get(name)
            if v is None:
                gates.append({"name": f"{s}.{name}", "ok": True, "value": None, "min": rule["min"], "group": "metrica",
                              "detail": "sem denominador neste subconjunto"})
                continue
            gates.append({"name": f"{s}.{name}", "ok": v >= rule["min"], "value": v, "min": rule["min"],
                          "group": "metrica", "detail": f"{s}.{name}={v} (minimo {rule['min']}): {rule['por_que']}"})
    cp = thresholds["case_pass_rate"]
    gates.append({"name": "case_pass_rate", "ok": overall["case_pass_rate"] >= cp["min"], "value": overall["case_pass_rate"],
                  "min": cp["min"], "group": "metrica", "detail": f"case_pass_rate={overall['case_pass_rate']} (minimo {cp['min']})"})
    smin = thresholds["slice_pass_rate"]["min"]
    for name, m in slices.items():
        gates.append({"name": f"slice.{name}", "ok": m["case_pass_rate"] >= smin, "value": m["case_pass_rate"],
                      "min": smin, "group": "slice", "detail": f"slice {name}: case_pass_rate={m['case_pass_rate']} "
                      f"(minimo {smin}, n={m['n_cases']})"})
    if release_check:
        gates.append(release_check)
    return gates


def write_junit(path, report):
    cases = []
    for r in report.get("cases", []):
        failed = []
        if r.get("error"):
            failed.append(f"erro: {r['error']}")
        for s, ev in (r.get("strategies") or {}).items():
            for c in ev["checks"]:
                if not c["ok"] and (ev["gated"] or c["safety"]):
                    failed.append(f"[{s}] {c['name']}: {c['detail']}" + (f" [CRITICO {c['rule']}]" if c["rule"] else ""))
        for c in r.get("checks") or []:
            if not c["ok"]:
                failed.append(f"{c['name']}: {c['detail']}" + (f" [CRITICO {c['rule']}]" if c["rule"] else ""))
        body = ""
        if failed:
            msg = "; ".join(failed)
            body = f"<failure message={quoteattr(msg[:900])}>{escape(msg)}</failure>"
        cases.append(f'<testcase classname="graph.{escape(r["slice"])}" name={quoteattr(r["case_id"])} '
                     f'time="{r.get("latency_ms", 0) / 1000:.3f}">{body}</testcase>')
    for g in report.get("gates", []):
        body = "" if g["ok"] else f"<failure message={quoteattr(g['detail'][:900])}>{escape(g['detail'])}</failure>"
        cases.append(f'<testcase classname="graph.gates.{g.get("group", "gate")}" name={quoteattr(g["name"])} '
                     f'time="0">{body}</testcase>')
    if report.get("fatal_error"):
        cases.append(f'<testcase classname="graph.runner" name="execucao"><error message='
                     f'{quoteattr(report["fatal_error"][:900])}/></testcase>')
    failures = sum(1 for c in cases if "<failure" in c)
    errors = sum(1 for c in cases if "<error" in c)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<testsuites><testsuite name="graph-policy-gate" tests="{len(cases)}" failures="{failures}" '
           f'errors="{errors}">\n' + "\n".join(cases) + "\n</testsuite></testsuites>\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def write_json(path, report):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        f.write("\n")


def print_summary(report):
    """Resumo no stdout: e o que o aluno le no log do job."""
    print(f"== graph-policy-gate | alvo={report['target']} | set={report['set']} | seed={report['seed']}")
    seed = report["seed_findings"]
    print(f"   seed/front matter: {'OK' if not seed else f'{len(seed)} achado(s)'}")
    for f in seed:
        print(f"   SEED [{f['rule']}] {f['check']}: {f['detail']}")
    m = report.get("metrics")
    if m:
        print(f"   versoes: {report.get('system_versions')}")
        print("   estrategia  Hit@k  MRR     fonte_vigente  recusa  caminho_grafo  casos")
        for s in STRATEGIES:
            sm = m["strategies"][s]
            print(f"   {s:<10}  {sm['hit_at_k']!s:<5}  {sm['mrr']!s:<6}  {sm['source_is_current_rate']!s:<13}  "
                  f"{sm['abstention_correct_rate']!s:<6}  {sm['graph_path_correct_rate']!s:<13}  {sm['case_pass_rate']}")
        print("   criticos: " + " | ".join(f"{c}={m[c]}" for c in CRITICAL_COUNTS))
        print("   slice              n  pass")
        for name, sm in report["slices"].items():
            print(f"   {name:<17} {sm['n_cases']:>2}  {sm['case_pass_rate']}")
    for g in report["gates"]:
        if not g["ok"]:
            print(f"   REPROVADO {g['name']}: {g['detail']}")
    print(f"== VEREDITO: {report['verdict']}")


def run(args):
    with open(os.path.join(EVALS, args.thresholds), encoding="utf-8") as f:
        thresholds = json.load(f)
    data_root = args.data_root or ROOT
    report = {"set": args.set, "target": args.target, "seed": args.seed, "data_root": data_root,
              "url": args.url if args.target == "http" else None, "k": thresholds.get("k", 3)}
    findings = check_seed(data_root) if args.target != "http" else []
    report["seed_findings"] = findings
    structural = any(f["rule"] == "seed_structure" for f in findings)
    behavior_ran, overall, slices, release_check = False, None, {}, None
    if args.target in ("local", "http") and not structural:
        target = LocalTarget(args) if args.target == "local" else HttpTarget(args)
        try:
            rows, versions, release_ids = run_behavior(args, thresholds, target)
        finally:
            target.close()
        behavior_ran = True
        k = thresholds.get("k", 3)
        overall = metrics_for(rows, k)
        slices = {s: metrics_for([r for r in rows if r["slice"] == s], k) for s in sorted({r["slice"] for r in rows})}
        if args.expect_release_id:
            wrong = sorted(str(x) for x in release_ids if x != args.expect_release_id)
            release_check = {"name": "release_id_conferido", "ok": not wrong, "group": "critico",
                             "detail": f"respostas de outro release: {wrong}" if wrong
                             else f"todas as respostas vieram de {args.expect_release_id}"}
        report.update({"system_versions": versions, "metrics": overall, "slices": slices,
                       "bootstrap": getattr(target, "boot", None),
                       "failed_cases": [r["case_id"] for r in rows if not r["passed"]],
                       "cases": [{"case_id": r["case_id"], "slice": r["slice"], "severity": r["severity"],
                                  "passed": r["passed"], "error": r["error"], "latency_ms": r["latency_ms"],
                                  "counts": r["counts"], "checks": r.get("checks"), "summary": r.get("summary"),
                                  "strategies": r["strategies"]} for r in rows]})
    elif structural:
        report["behavior_skipped"] = "seed com erro estrutural: o grafo nem monta"
    report["gates"] = build_gates(thresholds, overall, slices, findings, behavior_ran, release_check)
    report["verdict"] = "PASS" if all(g["ok"] for g in report["gates"]) else "FAIL"
    report["nondeterministic_fields"] = ["cases[].latency_ms", "bootstrap"]
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="graph-policy-gate: seed coerente + golden de grafo/vetor")
    p.add_argument("--target", choices=("seed", "local", "http"), default="seed")
    p.add_argument("--set", default="golden_graph_v1")
    p.add_argument("--thresholds", default="thresholds_graph.json")
    p.add_argument("--slice", help="filtra por slice (lista separada por virgula)")
    p.add_argument("--data-root", help="raiz com corpus/ e graph/seed/ (padrao: a do repositorio)")
    p.add_argument("--neo4j-uri", default=os.environ.get("QC_NEO4J_URI", "bolt://127.0.0.1:17687"))
    p.add_argument("--neo4j-user", default=os.environ.get("QC_NEO4J_USER", "neo4j"))
    p.add_argument("--neo4j-password-file", help="arquivo com a senha (chmod 600); alternativa: NEO4J_PASSWORD")
    p.add_argument("--bootstrap-timeout", type=int, default=240)
    p.add_argument("--url", help="base do runtime publicado, ex.: http://<alb>/candidate")
    p.add_argument("--expect-release-id")
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="reports/graph-gate.json")
    p.add_argument("--junit", default="reports/junit-graph.xml")
    args = p.parse_args(argv)

    report = {"set": args.set, "target": args.target, "seed": args.seed, "verdict": "FAIL", "seed_findings": [],
              "gates": []}
    try:
        if args.target == "http" and not args.url:
            raise ValueError("--target http exige --url")
        report = run(args)
        print_summary(report)
    except BaseException as err:  # noqa: BLE001 - qualquer erro reprova, mas o JUnit sai
        report["fatal_error"] = f"{type(err).__name__}: {err}"
        report["verdict"] = "FAIL"
        log(traceback.format_exc())
        print(f"== VEREDITO: FAIL (erro de execucao: {report['fatal_error']})")
    finally:
        write_json(args.out, report)
        write_junit(args.junit, report)
        log(f"relatorio: {args.out} | junit: {args.junit}")
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
