"""Gate de avaliacao: baseline aprova, regressoes reprovam com regra dura, revert volta.

Roda numa COPIA do projeto (tmp_path) para nunca sujar o repositorio.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = ("core", "corpus", "prompts", "config", "orders", "evals", "scripts")


@pytest.fixture
def copia(tmp_path):
    dest = tmp_path / "qa"
    for part in PARTS:
        shutil.copytree(os.path.join(ROOT, part), dest / part,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def run_eval(root, out_dir, *extra):
    out, junit = out_dir / "eval.json", out_dir / "junit.xml"
    proc = subprocess.run([sys.executable, str(root / "evals" / "run_eval.py"), "--set", "golden_v1",
                           "--target", "local", "--out", str(out), "--junit", str(junit), "--seed", "42", *extra],
                          capture_output=True, text=True)
    assert junit.exists(), "JUnit precisa existir mesmo em falha"
    return proc.returncode, json.loads(out.read_text(encoding="utf-8")), junit.read_text(encoding="utf-8")


def inject(root, kind, action):
    subprocess.run([sys.executable, str(root / "scripts" / "inject_regression.py"), "--kind", kind, action],
                   check=True, capture_output=True)


def test_golden_tem_campos_e_tamanho_minimo():
    required = {"case_id", "question", "intent", "expected_route", "expected_docs", "expected_doc_version",
                "expected_answer_fields", "must_abstain", "expected_order_status", "tenant", "user_role",
                "slice", "severity", "explanation"}
    with open(os.path.join(ROOT, "evals", "golden_v1.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert len(rows) >= 22
    assert len({r["case_id"] for r in rows}) == len(rows)
    for r in rows:
        assert required <= set(r), r["case_id"]
        if r["must_abstain"]:
            assert r.get("expected_abstain_reason"), r["case_id"]


def test_baseline_aprova(copia, tmp_path):
    code, report, junit = run_eval(copia, tmp_path)
    assert code == 0 and report["verdict"] == "PASS"
    assert report["metrics"]["hit_at_k"] >= 0.9 and report["hard_rule_violations"] == []
    assert 'failures="0"' in junit


@pytest.mark.parametrize("kind", ["obsolete-priority", "remove-active-filter"])
def test_regressao_reprova_e_revert_volta(copia, tmp_path, kind):
    inject(copia, kind, "--apply")
    code, report, junit = run_eval(copia, tmp_path)
    assert code == 1 and report["verdict"] == "FAIL"
    rules = {v["rule"] for v in report["hard_rule_violations"]}
    assert "fonte_obsoleta" in rules
    assert "troca-politica-obsoleta" in report["failed_cases"]
    assert "<failure" in junit and "REGRA DURA" in junit
    inject(copia, kind, "--revert")
    code, report, _ = run_eval(copia, tmp_path)
    assert code == 0 and report["verdict"] == "PASS"


def test_mesma_seed_mesmo_relatorio(copia, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, ra, _ = run_eval(copia, a)
    _, rb, _ = run_eval(copia, b)
    for r in (ra, rb):
        for case in r["cases"]:
            case.pop("latency_ms")
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)


def test_erro_de_execucao_grava_junit_e_reprova(copia, tmp_path):
    code, report, junit = run_eval(copia, tmp_path, "--target", "http", "--url", "http://127.0.0.1:9",
                                   "--timeout", "0.5", "--slice", "politicas")
    assert code == 1 and report["verdict"] == "FAIL"
    assert "<error" in junit
