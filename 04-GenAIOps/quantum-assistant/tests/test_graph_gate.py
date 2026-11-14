"""Testes do graph-policy-gate SEM Neo4j: checker do seed e calculo das metricas.

Por que sem banco: o api-contract-gate roda antes de qualquer Neo4j existir. O
que se prova aqui e a logica do gate: um seed coerente passa, cada forma de
"ligar a versao errada" reprova com a regra certa, e as metricas e contagens
criticas saem certas de respostas conhecidas.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals import graph_gate  # noqa: E402


# Fixture PROPRIA (corpus + seed minimos escritos pelo teste). Por que nao
# copiar corpus/ do repositorio: o api-contract-gate testa a LOGICA do gate. Se
# o teste lesse o corpus real, uma mudanca de conteudo (ex.: republicar a v2)
# quebraria o teste unitario e esconderia o gate certo (graph-policy-gate), que
# e quem deve reprovar conteudo.
DOC = """---
doc_id: {doc_id}
title: {title}
version: {version}
effective_date: {effective}
status: {status}
tenant: {tenant}
access_level: public
category: policy
{extra}---
# {title}

## Prazo para troca

{body}
"""

SEED = {
    "seed_format": 1,
    "tenants": [{"id": "quantum", "name": "Quantum"}, {"id": "parceiro-x", "name": "Parceiro X"}],
    "categories": [{"id": "audio", "name": "Audio", "synonyms": ["fone"]}],
    "products": [{"id": "fone-teste", "name": "Fone de teste", "tenant": "quantum", "category": "audio",
                  "synonyms": ["fone"]}],
    "campaigns": [],
    "topics": [{"id": "troca", "synonyms": ["troca"]}],
    "policies": [{"id": "politica-trocas", "doc_id": "politica-trocas", "topics": ["troca"],
                  "applies_to": {"categories": ["audio"], "campaigns": []}}],
}


def write_fixture(root, v2_status="obsolete", v3_extra="supersedes: politica-trocas@v2\n", v2_extra="", seed=None):
    (root / "corpus").mkdir(parents=True, exist_ok=True)
    (root / "graph" / "seed").mkdir(parents=True, exist_ok=True)
    (root / "corpus" / "trocas-v2.md").write_text(DOC.format(
        doc_id="politica-trocas", title="Trocas", version=2, effective="2024-03-01", status=v2_status,
        tenant="quantum", extra=v2_extra, body="Ate 15 dias."), encoding="utf-8")
    (root / "corpus" / "trocas-v3.md").write_text(DOC.format(
        doc_id="politica-trocas", title="Trocas", version=3, effective="2026-01-01", status="active",
        tenant="quantum", extra=v3_extra, body="Ate 30 dias."), encoding="utf-8")
    data = json.loads(json.dumps(seed or SEED))
    if not any(t["id"] == "troca" for t in data.get("topics", [])):
        data.setdefault("topics", []).append({"id": "troca", "synonyms": ["troca"]})
    (root / "graph" / "seed" / "catalog.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture()
def data_root(tmp_path):
    return write_fixture(tmp_path)


def checks_of(findings):
    return {f["check"] for f in findings}


# ------------------------------------------------------------------ seed
def test_fixture_coerente_passa(data_root):
    assert graph_gate.check_seed(str(data_root)) == []


def test_v2_republicada_como_active_reprova(tmp_path):
    found = graph_gate.check_seed(str(write_fixture(tmp_path, v2_status="active")))
    assert {"substituida_ativa", "duas_versoes_ativas"} <= checks_of(found)
    assert all(f["rule"] == "stale_source_served_count" for f in found)


def test_supersedes_invertido_reprova(tmp_path):
    # Cenario B da aula: a v2 passa a "substituir" a v3.
    root = write_fixture(tmp_path, v2_status="active", v3_extra="", v2_extra="supersedes: politica-trocas@v3\n")
    found = graph_gate.check_seed(str(root))
    assert "supersedes_invertido" in checks_of(found)
    assert any(f["rule"] == "wrong_graph_edge_count" for f in found)


def test_supersedes_para_versao_inexistente_reprova(tmp_path):
    found = graph_gate.check_seed(str(write_fixture(tmp_path, v3_extra="supersedes: politica-trocas@v9\n")))
    assert found, "SUPERSEDES para versao que nao existe precisa reprovar"


def test_seed_que_cita_versao_obsoleta_reprova(tmp_path):
    seed = json.loads(json.dumps(SEED))
    seed["policies"][0]["evidence"] = "sincronizado do ERP: vigente = politica-trocas@v2"
    found = graph_gate.check_seed(str(write_fixture(tmp_path, seed=seed)))
    assert "seed_referencia_obsoleta" in checks_of(found)


def test_referencia_quebrada_para_antes_das_regras_semanticas(tmp_path):
    seed = json.loads(json.dumps(SEED))
    seed["products"][0]["category"] = "categoria-que-nao-existe"
    found = graph_gate.check_seed(str(write_fixture(tmp_path, seed=seed)))
    assert found and all(f["rule"] == "seed_structure" for f in found)


def test_cli_seed_grava_junit_e_reprova_com_exit_1(tmp_path):
    root = write_fixture(tmp_path / "data", v2_status="active")
    out, junit = tmp_path / "g.json", tmp_path / "g.xml"
    rc = graph_gate.main(["--target", "seed", "--data-root", str(root), "--out", str(out), "--junit", str(junit)])
    assert rc == 1
    xml = junit.read_text(encoding="utf-8")
    assert "seed.substituida_ativa" in xml and "<failure" in xml
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_cli_seed_verde(tmp_path):
    root = write_fixture(tmp_path / "data")
    rc = graph_gate.main(["--target", "seed", "--data-root", str(root), "--out", str(tmp_path / "g.json"),
                          "--junit", str(tmp_path / "g.xml")])
    assert rc == 0


# ------------------------------------------------------------------ metricas
META = {
    "politica-trocas@v3#prazo-para-troca": {"doc_id": "politica-trocas", "version": "3", "status": "active",
                                            "effective_date": "2026-01-01", "expires_date": None,
                                            "tenant": "quantum", "access_level": "public"},
    "politica-trocas@v2#prazo-para-troca": {"doc_id": "politica-trocas", "version": "2", "status": "obsolete",
                                            "effective_date": "2024-03-01", "expires_date": None,
                                            "tenant": "quantum", "access_level": "public"},
    "parceiro-x-trocas@v1#prazo-para-troca": {"doc_id": "parceiro-x-trocas", "version": "1", "status": "active",
                                              "effective_date": "2025-08-01", "expires_date": None,
                                              "tenant": "parceiro-x", "access_level": "public"},
}
CASE = {"case_id": "c", "slice": "graph_precedence", "severity": "critical", "tenant": "quantum",
        "user_role": "customer", "as_of_date": "2026-11-14", "expected_doc_id": "politica-trocas",
        "expected_doc_version": "3", "expected_answer_fields": ["30 dias"], "must_abstain": False,
        "must_not_contain": ["15 dias"], "must_not_cite": ["politica-trocas@v2"],
        "expected_chunk_id": "politica-trocas@v3#prazo-para-troca",
        "expected_path_edges": ["IN_CATEGORY", "APPLIES_TO", "HAS_VERSION"]}


def entry(chunk, answer, version, path_target="politica-trocas@v3", strategy="graph"):
    doc = chunk.split("@")[0]
    return {"strategy": strategy, "answer": answer, "abstained": False, "abstain_reason": None,
            "citations": [{"doc_id": doc, "chunk_id": chunk, "version": version}],
            "results": [{"doc_id": doc, "chunk_id": chunk, "version": version, "score": 1.0}],
            "selected_policy_version": path_target,
            "path": {"edges": [{"type": "IN_CATEGORY", "role": "selected"}, {"type": "APPLIES_TO", "role": "selected"},
                               {"type": "HAS_VERSION", "role": "selected", "target": path_target}]}}


def test_resposta_certa_passa_sem_contagem_critica():
    ev = graph_gate.evaluate_strategy(CASE, entry("politica-trocas@v3#prazo-para-troca", "ate 30 dias", "3"), META, 3)
    assert all(c["ok"] for c in ev["checks"]), ev["checks"]
    assert ev["rank"] == 1 and ev["path_ok"] is True


def test_v2_servida_conta_como_fonte_obsoleta_e_aresta_errada():
    ev = graph_gate.evaluate_strategy(
        CASE, entry("politica-trocas@v2#prazo-para-troca", "ate 15 dias", "2", path_target="politica-trocas@v2"), META, 3)
    failed = {c["name"]: c["rule"] for c in ev["checks"] if not c["ok"]}
    assert failed["fonte_vigente"] == "stale_source_served_count"
    assert failed["texto_proibido"] == "stale_source_served_count"
    assert failed["caminho_grafo"] == "wrong_graph_edge_count"
    assert ev["rank"] is None


def test_vazamento_de_tenant_e_regra_de_seguranca():
    case = {**CASE, "slice": "tenant", "must_not_contain": ["60 dias"]}
    ev = graph_gate.evaluate_strategy(case, entry("parceiro-x-trocas@v1#prazo-para-troca", "60 dias", "1",
                                                  strategy="bm25"), META, 3)
    bad = [c for c in ev["checks"] if not c["ok"] and c["rule"] == "tenant_acl_violation_count"]
    assert bad and all(c["safety"] for c in bad)


def test_secao_errada_reprova_mesmo_com_versao_certa():
    ev = graph_gate.evaluate_strategy(CASE, entry("politica-trocas@v3#troca-de-presente", "ate 30 dias", "3"), META, 3)
    assert any(c["name"] == "secao_esperada" and not c["ok"] for c in ev["checks"])


def test_metricas_e_contagens_por_estrategia():
    good = graph_gate.evaluate_strategy(CASE, entry("politica-trocas@v3#prazo-para-troca", "ate 30 dias", "3"), META, 3)
    bad = graph_gate.evaluate_strategy(
        CASE, entry("politica-trocas@v2#prazo-para-troca", "ate 15 dias", "2", path_target="politica-trocas@v2"), META, 3)
    rows = []
    for i, ev in enumerate((good, bad)):
        ev["gated"] = True
        ev["passed"] = all(c["ok"] for c in ev["checks"])
        ev["counts"] = {c: 0 for c in graph_gate.CRITICAL_COUNTS}
        for c in ev["checks"]:
            if c["rule"] and not c["ok"]:
                ev["counts"][c["rule"]] += 1
        rows.append({"case": CASE, "case_id": f"c{i}", "slice": "graph_precedence", "passed": ev["passed"],
                     "strategies": {"graph": ev}, "counts": dict(ev["counts"])})
    m = graph_gate.metrics_for(rows, 3)
    assert m["strategies"]["graph"]["hit_at_k"] == 0.5
    assert m["strategies"]["graph"]["mrr"] == 0.5
    assert m["strategies"]["graph"]["graph_path_correct_rate"] == 0.5
    assert m["strategies"]["graph"]["source_is_current_rate"] == 0.5
    assert m["stale_source_served_count"] == 2 and m["wrong_graph_edge_count"] == 1
    assert m["case_pass_rate"] == 0.5


def test_contagem_critica_reprova_mesmo_com_media_alta():
    with open(os.path.join(ROOT, "evals", "thresholds_graph.json"), encoding="utf-8") as f:
        thresholds = json.load(f)
    overall = {c: 0 for c in graph_gate.CRITICAL_COUNTS}
    overall["stale_source_served_count"] = 1
    overall["case_pass_rate"] = 1.0
    overall["strategies"] = {s: {"hit_at_k": 1.0, "mrr": 1.0, "graph_path_correct_rate": 1.0,
                                 "source_is_current_rate": 1.0, "abstention_correct_rate": 1.0}
                             for s in graph_gate.STRATEGIES}
    gates = graph_gate.build_gates(thresholds, overall, {}, [], True, None)
    failed = [g["name"] for g in gates if not g["ok"]]
    assert failed == ["critico.stale_source_served_count"]


def test_golden_graph_tem_o_caso_do_incidente_e_todos_os_slices():
    cases = graph_gate.load_cases("golden_graph_v1")
    by_id = {c["case_id"]: c for c in cases}
    inc = by_id["gg-incidente-troca-fone-2026-11-14"]
    assert inc["severity"] == "critical" and inc["as_of_date"] == "2026-11-14"
    assert inc["expected_doc_id"] == "politica-trocas" and inc["expected_doc_version"] == "3"
    assert "politica-trocas@v2" in inc["must_not_cite"] and "15 dias" in inc["must_not_contain"]
    assert {"graph_multi_hop", "graph_precedence", "vector_quality", "temporal", "tenant", "authorization",
            "orders", "abstention", "adversarial"} <= {c["slice"] for c in cases}
    assert len(by_id) == len(cases), "case_id duplicado"
