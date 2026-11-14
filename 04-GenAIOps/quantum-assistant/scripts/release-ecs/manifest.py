"""Registro de release do runtime ECS: monta, sela e publica o release-record.json.

Por que um registro e nao "a revisao atual - 1": a revisao anterior da task
definition pode ser um candidate que reprovou no smoke e nunca atendeu cliente.
O historico em s3://<bucket>/quantum-assistant/releases-ecs/ so recebe o que
foi PROMOVIDO, entao o rollback sempre volta para um release aprovado.

O que o registro amarra (o release e o CONJUNTO, nao so o codigo):
  image_digest (sha256 do ECR) + git_sha + CI_PIPELINE_ID + versoes de corpus,
  grafo (graph_seed_version), indice vetorial, modelo de embedding (id e
  revisao), eval set, prompt e aplicacao; model_version=none (sem LLM);
  resultados dos gates; quem aprovou; timestamps; e o sha256 do proprio
  registro (manifest_sha256), para provar que ninguem editou depois.

Subcomandos (stdout = JSON resultante; stderr = progresso):
  build    --digest sha256:... --reports reports --out dist/release-record.json
  seal     --record FILE                 recalcula manifest_sha256
  verify   --record FILE                 confere manifest_sha256 (exit 1 se divergir)
  publish  --record FILE --kind candidate|live   grava <release_id>.json + candidate.json|live.json
                                                 (+ history/ no live) no S3
  history  [--bucket B]                  lista as promocoes em ordem cronologica
  get      --key live|candidate          baixa o ponteiro atual
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

PREFIX = "quantum-assistant/releases-ecs"
# Chaves que precisam bater 1:1 entre o registro e o /ready do ambiente.
VERSION_KEYS = ("image_digest", "corpus_version", "graph_seed_version", "vector_index_version",
                "embedding_model_id", "embedding_model_revision", "embedding_dim", "index_version",
                "eval_set_version", "prompt_version", "application_version", "model_version")
GATE_REPORTS = {
    "security-gate": ("junit-tflint.xml", "junit-checkov.xml"),
    "api-contract-gate": ("junit-pytest.xml",),
    "retrieval-quality-gate": ("eval-retrieval.json",),
    "business-policy-gate": ("eval-policy.json", "eval-adversarial.json"),
    "graph-policy-gate": ("graph-gate.json",),
}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aws(*args, capture=True):
    out = subprocess.run(["aws", *args], check=True, text=True,
                         stdout=subprocess.PIPE if capture else None)
    return (out.stdout or "").strip()


def bucket(explicit=None):
    b = explicit or os.environ.get("TF_STATE_BUCKET")
    if b:
        return b
    names = aws("s3api", "list-buckets", "--query", "Buckets[].Name", "--output", "text").split()
    found = sorted(n for n in names if n.startswith("base-config"))
    if not found:
        raise SystemExit("nenhum bucket base-config-* na conta: informe --bucket ou TF_STATE_BUCKET")
    return found[0]


def canonical(record):
    body = {k: v for k, v in record.items() if k != "manifest_sha256"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def seal(record):
    record["manifest_sha256"] = hashlib.sha256(canonical(record)).hexdigest()
    return record


def verify(record):
    return record.get("manifest_sha256") == hashlib.sha256(canonical(record)).hexdigest()


def static_versions():
    """Versoes ESTATICAS do conjunto que vai na imagem, calculadas sem Neo4j.

    O mesmo calculo que o app faz no boot (core.versions + graph.loader +
    vector.cache): se o /ready do candidate divergir daqui, o que esta no ar
    nao e o que o pipeline avaliou.
    """
    from core.assistant import Assistant
    from graph import loader
    v = dict(Assistant({"orders": {"backend": "memory"}}).versions)
    out = {k: v.get(k) for k in ("application_version", "prompt_version", "corpus_version", "index_version",
                                 "eval_set_version", "model_version")}
    out["graph_seed_version"] = loader.graph_seed_version(ROOT)
    try:
        from vector import cache as vcache, encoder as venc
        from core.corpus import load_chunks
        out["vector_index_version"] = vcache.vector_index_version(load_chunks(os.path.join(ROOT, "corpus")))
        info = venc.model_info()
        out.update({k: info[k] for k in ("embedding_model_id", "embedding_model_revision", "embedding_dim")})
    except ImportError as err:
        # O python do sistema no runner nao tem numpy/onnx; o /ready confere o resto.
        log(f"manifest: versoes vetoriais virao do candidate (/ready): {err}")
    return out


def _source_commit_file():
    path = os.path.join(ROOT, "release", "SOURCE_COMMIT")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    return None


def gate_results(reports):
    out = {}
    for gate, files in GATE_REPORTS.items():
        found = [f for f in files if os.path.exists(os.path.join(reports, f))]
        if not found:
            out[gate] = {"status": "sem_relatorio"}
            continue
        entry = {"status": "passed", "reports": found}
        for f in found:
            if f.endswith(".json"):
                with open(os.path.join(reports, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                entry.setdefault("verdicts", {})[f] = data.get("verdict")
                if data.get("metrics") and gate == "graph-policy-gate":
                    m = data["metrics"]
                    entry["critical_counts"] = {k: m.get(k) for k in ("tenant_acl_violation_count",
                                                                     "stale_source_served_count",
                                                                     "wrong_graph_edge_count",
                                                                     "order_auth_violation_count")}
                    entry["per_strategy"] = {s: {k: v for k, v in sm.items() if k in ("hit_at_k", "mrr",
                                             "graph_path_correct_rate", "source_is_current_rate")}
                                             for s, sm in m["strategies"].items()}
                if data.get("verdict") not in (None, "PASS"):
                    entry["status"] = "failed"
        out[gate] = entry
    return out


def cmd_build(args):
    if not args.digest.startswith("sha256:") or len(args.digest) != 71:
        raise SystemExit(f"digest invalido: {args.digest!r} (esperado sha256: + 64 hex)")
    git_sha = os.environ.get("CI_COMMIT_SHA") or subprocess.run(
        ["git", "-C", ROOT, "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL).stdout.strip() or "sem-git"
    pipeline = os.environ.get("CI_PIPELINE_ID", "local")
    versions = static_versions()
    source_file = os.path.join(args.reports, "image-source.txt")
    source = open(source_file).read().strip() if os.path.exists(source_file) else "build"
    # release_id = commit + digest: dois builds do mesmo commit geram imagens
    # diferentes (camadas com data) e, portanto, releases diferentes.
    release_id = f"rel-{git_sha[:8]}-{args.digest[7:15]}"
    record = {
        "record_format": 1,
        "release_id": release_id,
        "runtime": "ecs_fargate",
        "git_sha": git_sha,
        "git_ref": os.environ.get("CI_COMMIT_REF_NAME"),
        # Commit da disciplina de onde o projeto do aluno foi copiado (opcional):
        # liga o release ao material publicado, alem do commit do projeto GitLab.
        "source_commit": os.environ.get("QC_SOURCE_COMMIT") or _source_commit_file(),
        "ci_pipeline_id": pipeline,
        "ci_pipeline_url": os.environ.get("CI_PIPELINE_URL"),
        "ci_project_path": os.environ.get("CI_PROJECT_PATH"),
        "image_repository": f"{os.environ.get('PROJECT', 'qc-assistant')}-app",
        "image_digest": args.digest,
        "image_source": source,
        **versions,
        "model_version": "none",
        "gates": gate_results(args.reports),
        "approval": {"required": os.environ.get("REQUIRE_MANUAL_APPROVAL") == "true", "approved_by": None,
                     "approved_at": None},
        "timestamps": {"built_at": now()},
        "deploy": {},
        "promotion": None,
    }
    failed = [g for g, r in record["gates"].items() if r["status"] == "failed"]
    if failed:
        raise SystemExit(f"gates reprovados no relatorio: {failed}; package nao registra release")
    seal(record)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    log(f"manifest: {release_id} digest={args.digest[:19]}... corpus={versions['corpus_version']} "
        f"grafo={versions['graph_seed_version']} sha256={record['manifest_sha256'][:12]}")
    return record


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, record):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def s3_put(b, key, record):
    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"qc-rec-{os.getpid()}.json")
    save(tmp, record)
    try:
        aws("s3", "cp", "--quiet", tmp, f"s3://{b}/{PREFIX}/{key}", "--content-type", "application/json",
            capture=False)
    finally:
        os.remove(tmp)


def s3_get(b, key):
    try:
        raw = aws("s3", "cp", "--quiet", f"s3://{b}/{PREFIX}/{key}", "-")
    except subprocess.CalledProcessError:
        return None
    return json.loads(raw) if raw else None


def cmd_publish(args):
    record = load(args.record)
    if not verify(record):
        raise SystemExit("manifest_sha256 nao confere: o registro foi alterado sem selar de novo")
    b = bucket(args.bucket)
    s3_put(b, f"{record['release_id']}.json", record)
    if args.kind == "candidate":
        s3_put(b, "candidate.json", record)
    else:
        # Historico imutavel de PROMOCOES: o nome comeca pela data, entao listar
        # o prefixo ja devolve em ordem cronologica. E daqui que o rollback le.
        stamp = record["promotion"]["promoted_at"].replace(":", "").replace("-", "")
        s3_put(b, f"history/{stamp}-{record['release_id']}.json", record)
        s3_put(b, "live.json", record)
    log(f"manifest: publicado s3://{b}/{PREFIX}/{record['release_id']}.json ({args.kind})")
    return record


def history(b):
    try:
        listing = aws("s3", "ls", f"s3://{b}/{PREFIX}/history/")
    except subprocess.CalledProcessError:
        return []
    names = sorted(line.split()[-1] for line in listing.splitlines() if line.strip().endswith(".json"))
    return [{"key": f"history/{n}", **(s3_get(b, f"history/{n}") or {})} for n in names]


def cmd_history(args):
    items = history(bucket(args.bucket))
    return [{"key": i["key"], "release_id": i.get("release_id"), "image_digest": i.get("image_digest"),
             "event": (i.get("promotion") or {}).get("event"),
             "task_definition": ((i.get("deploy") or {}).get("live") or {}).get("task_definition_revision"),
             "at": (i.get("promotion") or {}).get("promoted_at")} for i in items]


def main(argv=None):
    p = argparse.ArgumentParser(description="registro de release do runtime ECS")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--digest", required=True)
    b.add_argument("--reports", default=os.path.join(ROOT, "reports"))
    b.add_argument("--out", default=os.path.join(ROOT, "dist", "release-record.json"))
    for name in ("seal", "verify"):
        s = sub.add_parser(name)
        s.add_argument("--record", required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("--record", required=True)
    pub.add_argument("--kind", choices=("candidate", "live"), required=True)
    pub.add_argument("--bucket")
    h = sub.add_parser("history")
    h.add_argument("--bucket")
    g = sub.add_parser("get")
    g.add_argument("--key", choices=("live", "candidate"), required=True)
    g.add_argument("--bucket")
    args = p.parse_args(argv)

    if args.cmd == "build":
        out = cmd_build(args)
    elif args.cmd == "seal":
        out = seal(load(args.record))
        save(args.record, out)
    elif args.cmd == "verify":
        out = load(args.record)
        if not verify(out):
            log("manifest: manifest_sha256 NAO confere")
            print(json.dumps({"ok": False}))
            return 1
        out = {"ok": True, "manifest_sha256": out["manifest_sha256"]}
    elif args.cmd == "publish":
        out = cmd_publish(args)
    elif args.cmd == "history":
        out = cmd_history(args)
    else:
        out = s3_get(bucket(args.bucket), f"{args.key}.json")
        if out is None:
            log(f"manifest: {args.key}.json ainda nao existe em releases-ecs/")
            print("null")
            return 1
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
