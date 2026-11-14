"""Diagnostico do motor: python -m lab.selftest  (stdout = JSON com os checks).

Usado pelo /ready e pelo professor para saber, sem abrir codigo, se o modelo
esta integro, o seed e valido e o Neo4j tem o grafo e o indice esperados.
Sem QC_NEO4J_URI, roda so os checks offline (modelo + seed) e diz isso.
Exit 0 = todos os checks executados passaram; 1 = algum falhou.
"""
import json
import os
import sys
import time

from core import ROOT
from graph import loader
from vector import encoder as venc


def main():
    data_root = os.environ.get("QC_DATA_ROOT") or ROOT
    checks, started = {}, time.perf_counter()
    errors = loader.validate(data_root)
    checks["seed_valid"] = {"ok": not errors, "errors": errors[:10]}
    checks["graph_seed_version"] = {"ok": True, "value": loader.graph_seed_version(data_root)}
    problems = venc.verify(venc.default_model_dir())
    checks["model_files"] = {"ok": not problems, "problems": problems, "model_dir": venc.default_model_dir(),
                             **venc.model_info()}
    if not problems:
        t = time.perf_counter()
        vec = venc.Encoder().encode(["Qual o prazo para trocar um fone de ouvido?"])[0]
        norm = sum(v * v for v in vec) ** 0.5
        checks["embedding"] = {"ok": len(vec) == venc.DIM and abs(norm - 1) < 1e-3 and any(vec),
                               "dim": len(vec), "l2_norm": round(norm, 6), "ms": round((time.perf_counter() - t) * 1000, 1)}
    uri = os.environ.get("QC_NEO4J_URI")
    if not uri:
        checks["neo4j"] = {"ok": None, "skipped": "QC_NEO4J_URI nao definido: checks do banco nao rodaram"}
    else:
        from lab.engine import DependencyUnavailable, Engine
        eng = Engine(uri, os.environ.get("QC_NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""),
                     data_root=data_root)
        try:
            boot = eng.bootstrap(timeout_s=float(os.environ.get("QC_SELFTEST_TIMEOUT_S", "60")))
            ready = eng.ready()
            checks["neo4j"] = {"ok": ready["ready"], **ready["checks"]}
            checks["graph_in_sync"] = {"ok": boot["graph_version"] == boot["graph_seed_version"],
                                       "graph_version": boot["graph_version"], "counts": boot["counts"]}
            checks["vector_index"] = {"ok": boot["index"]["state"] == "ONLINE" and boot["index"]["dim"] == venc.DIM,
                                      **boot["index"]}
            r = eng.compare("Comprei um fone de ouvido, qual o prazo para trocar?", "2026-11-14")
            g = r["strategies"]["graph"]
            checks["graph_query"] = {"ok": not g["abstained"] and g.get("path_hops", 0) >= 2,
                                     "selected": g.get("selected_policy_version"), "path_hops": g.get("path_hops"),
                                     "cypher_query_id": g.get("cypher_query_id")}
        except DependencyUnavailable as err:
            checks["neo4j"] = {"ok": False, "error": str(err)}
        finally:
            eng.close()
    ok = all(c.get("ok") is not False for c in checks.values())
    print(json.dumps({"ok": ok, "checks": checks, "duration_ms": round((time.perf_counter() - started) * 1000, 1)},
                     ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
