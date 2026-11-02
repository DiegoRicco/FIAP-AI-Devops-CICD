"""Monitor de qualidade da Demo 5.2: janelas, n minimo e niveis de alerta.

Le os JSONL do simulador (ops/results/*.jsonl) e, se pedido, o Prometheus.
Agrupa os probes em janelas por cenario/release/corpus e aplica as regras:

  insufficient_data  n < --min-n na janela          (ausencia de dado NAO e sucesso)
  critical           qualquer violacao de ACL/tenant ou fonte obsoleta citada
  warning            pass_rate < --warn-below (0,90) com n >= --min-n
  ok                 nenhum dos anteriores

Probes com outcome "fallback" (API de pedidos fora e recusa honesta) ficam fora
do pass_rate: medem a dependencia, nao a qualidade do conteudo. Sao contados a
parte e aparecem como sinal tecnico.

Para cada janela que nao esta "ok", grava ops/alerts/<ts>.json (alerta LOCAL:
nada e enviado para pager, e-mail ou chat) e imprime no console.

Uso:
  ops/run quality_monitor.py                         # ultimo JSONL de cada cenario
  ops/run quality_monitor.py --results ops/results/stale_policy-*.jsonl
  ops/run quality_monitor.py --prometheus http://127.0.0.1:9090
Saida (stdout): JSON com as janelas e alertas. Sai com 0 (ok), 1 (warning/critical)
ou 3 (so insufficient_data).
"""
import argparse
import datetime
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")
ALERTS_DIR = os.path.join(HERE, "alerts")
LEVEL_RANK = {"ok": 0, "insufficient_data": 1, "warning": 2, "critical": 3}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def read_jsonl(paths):
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    row["_file"] = os.path.basename(path)
                    rows.append(row)
    return rows


def latest_per_scenario():
    latest = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.jsonl")), key=os.path.getmtime):
        latest[os.path.basename(path).split("-seed")[0]] = path
    return list(latest.values())


def ratio(num, den):
    return round(num / den, 4) if den else None


def summarize(rows):
    judged = [r for r in rows if r["outcome"] in ("pass", "fail")]
    ret = [r for r in judged if r.get("hit") is not None]
    cite = [r for r in judged if r.get("citation_required")]
    src = [r for r in judged if r.get("source_checked")]
    reasons = {}
    for r in judged:
        if r["outcome"] == "fail":
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {
        "n": len(judged),
        "n_fallback": sum(1 for r in rows if r["outcome"] == "fallback"),
        "pass_rate": ratio(sum(r["passed"] for r in judged), len(judged)),
        "source_is_current_rate": ratio(sum(r["source_is_current"] for r in src), len(src)),
        "hit_at_k": ratio(sum(bool(r["hit"]) for r in ret), len(ret)),
        "mrr": ratio(sum(r["reciprocal_rank"] or 0 for r in ret), len(ret)),
        "citation_presence_rate": ratio(sum(bool(r["citation_present"]) for r in cite), len(cite)),
        "stale_sources": sum(1 for r in src if not r["source_is_current"]),
        "acl_violations": sum(1 for r in judged if r.get("acl_violation")),
        "fail_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "p95_latency_ms": percentile([r["latency_ms"] for r in rows], 0.95),
        "http_non_200": sum(1 for r in rows if r.get("http_status") != 200),
    }


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def classify(summary, min_n, warn_below):
    if summary["acl_violations"] > 0:
        return "critical", "acl_violation"
    if summary["stale_sources"] > 0:
        return "critical", "stale_source_served"
    if summary["n"] < min_n:
        return "insufficient_data", f"n={summary['n']} < {min_n}"
    if summary["pass_rate"] is not None and summary["pass_rate"] < warn_below:
        return "warning", f"pass_rate={summary['pass_rate']} < {warn_below}"
    return "ok", "dentro do limiar"


def windows(rows, window_s):
    """Janelas de window_s segundos por cenario+release+corpus, ancoradas no
    primeiro probe de cada grupo. Por que ancorar: com janelas fixas no relogio,
    uma rodada de 25 s podia cair metade em cada janela e virar dois
    insufficient_data falsos."""
    first = {}
    for r in rows:
        key = (r["scenario_id"], r["release_id"], r["corpus_version"])
        ts = datetime.datetime.fromisoformat(r["ts"]).timestamp()
        first[key] = min(first.get(key, ts), ts)
    groups = {}
    for r in rows:
        key = (r["scenario_id"], r["release_id"], r["corpus_version"])
        ts = datetime.datetime.fromisoformat(r["ts"]).timestamp()
        bucket = first[key] + int((ts - first[key]) // window_s) * window_s
        groups.setdefault(key + (bucket,), []).append(r)
    return groups


def query_prometheus(base):
    """Le do Prometheus os mesmos sinais (regras de gravacao) e os alertas FIRING."""
    def q(expr):
        url = base.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))["data"]["result"]
    out = {}
    for name in ("qc:synthetic_quality_pass_rate:2m", "qc:quality_probes:n_2m", "qc:source_is_current_rate:2m",
                 "qc:transaction_lookup_failure_rate:2m"):
        out[name] = [{"labels": {k: v for k, v in s["metric"].items() if k != "__name__"},
                      "value": round(float(s["value"][1]), 4)} for s in q(name)]
    out["alerts_firing"] = sorted({s["metric"]["alertname"] + "/" + s["metric"].get("severity", "")
                                   for s in q('ALERTS{alertstate="firing"}')})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Monitor de qualidade (janelas + n minimo + niveis)")
    p.add_argument("--results", nargs="*", help="JSONL do simulador (padrao: o ultimo de cada cenario)")
    p.add_argument("--window-s", type=int, default=120, help="tamanho da janela em segundos")
    p.add_argument("--min-n", type=int, default=20, help="amostra minima por janela")
    p.add_argument("--warn-below", type=float, default=0.90)
    p.add_argument("--prometheus", help="URL do Prometheus para conferir os mesmos sinais e os alertas")
    p.add_argument("--no-write", action="store_true", help="nao grava ops/alerts/ (usado nos testes)")
    p.add_argument("--alerts-dir", default=ALERTS_DIR)
    args = p.parse_args(argv)

    paths = args.results or latest_per_scenario()
    rows = read_jsonl(paths) if paths else []
    report = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "source": "local alert (arquivo + console); nenhuma notificacao externa",
              "signal": "proxy sintetico: probes rotulados, nao trafego humano",
              "rules": {"window_s": args.window_s, "min_n": args.min_n, "warn_below": args.warn_below},
              "files": [os.path.basename(x) for x in paths], "windows": [], "alerts": []}
    if not rows:
        report["windows"].append({"level": "insufficient_data", "why": "nenhum probe encontrado"})
    for (scenario, release, corpus, bucket), group in sorted(windows(rows, args.window_s).items(),
                                                              key=lambda kv: kv[0][3]):
        summary = summarize(group)
        level, why = classify(summary, args.min_n, args.warn_below)
        start = datetime.datetime.fromtimestamp(bucket, datetime.timezone.utc)
        report["windows"].append({
            "scenario": scenario, "release_id": release, "corpus_version": corpus,
            "window_start": start.isoformat(timespec="seconds"),
            "window_end": (start + datetime.timedelta(seconds=args.window_s)).isoformat(timespec="seconds"),
            "level": level, "why": why, **summary,
            "sample_trace_ids": [r["trace_id"] for r in group if r["outcome"] == "fail" and r.get("trace_id")][:3],
        })
    if args.prometheus:
        try:
            report["prometheus"] = query_prometheus(args.prometheus)
        except OSError as err:
            report["prometheus"] = {"error": str(err)}

    for w in report["windows"]:
        tag = w["level"].upper()
        if "scenario" in w:
            log(f"[{tag:17}] {w['scenario']:22} release={w['release_id']} corpus={w['corpus_version']} "
                f"n={w['n']} pass_rate={w['pass_rate']} fonte_vigente={w['source_is_current_rate']} "
                f"hit@k={w['hit_at_k']} mrr={w['mrr']} obsoletas={w['stale_sources']} "
                f"fallback={w['n_fallback']} p95={w['p95_latency_ms']}ms ({w['why']})")
        else:
            log(f"[{tag:17}] {w['why']}")
        if w["level"] != "ok":
            report["alerts"].append({k: w.get(k) for k in ("level", "why", "scenario", "release_id",
                                                           "corpus_version", "window_start", "n", "pass_rate",
                                                           "stale_sources", "acl_violations", "fail_reasons",
                                                           "sample_trace_ids")})
    if args.prometheus and "alerts_firing" in report.get("prometheus", {}):
        log(f"Prometheus FIRING: {report['prometheus']['alerts_firing'] or 'nenhum'}")

    worst = max((LEVEL_RANK[w["level"]] for w in report["windows"]), default=1)
    if report["alerts"] and not args.no_write:
        os.makedirs(args.alerts_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        path = os.path.join(args.alerts_dir, f"{stamp}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        report["alert_file"] = path
        log(f"LOCAL ALERT gravado em {path}")
    print(json.dumps(report, ensure_ascii=False))
    if worst >= LEVEL_RANK["warning"]:
        return 1
    return 3 if worst == LEVEL_RANK["insufficient_data"] else 0


if __name__ == "__main__":
    sys.exit(main())
