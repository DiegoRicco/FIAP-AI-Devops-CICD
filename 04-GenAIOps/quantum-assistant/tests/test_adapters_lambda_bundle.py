"""Pacote do release: reprodutivel, completo e com manifesto do contrato."""
import hashlib
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from release import build_bundle  # noqa: E402

MANIFEST_FIELDS = {"release_id", "git_sha", "application_version", "prompt_version", "corpus_version",
                   "index_version", "eval_set_version", "model_version", "bundle_sha256", "built_at",
                   "pipeline_id", "approved_by"}


def test_bundle_reprodutivel_e_manifesto(tmp_path):
    m1, p1 = build_bundle.build(str(tmp_path / "a"))
    m2, p2 = build_bundle.build(str(tmp_path / "b"))
    sha = hashlib.sha256(open(p1, "rb").read()).hexdigest()
    assert sha == hashlib.sha256(open(p2, "rb").read()).hexdigest() == m1["bundle_sha256"]
    assert MANIFEST_FIELDS <= set(m1)
    assert m1["release_id"] == m2["release_id"] and m1["model_version"] == "none"


def test_bundle_leva_o_conjunto_coerente(tmp_path):
    manifest, path = build_bundle.build(str(tmp_path))
    names = zipfile.ZipFile(path).namelist()
    assert names == sorted(names[:-1]) + ["release/build_info.json"]
    for required in ("adapters/lambda_handler.py", "core/assistant.py", "config/settings.json",
                     "prompts/templates.json", "orders/fixtures.json"):
        assert required in names
    assert any(n.startswith("corpus/") and n.endswith(".md") for n in names)
    assert not any("__pycache__" in n or n.startswith("evals/") for n in names)
    info = json.loads(zipfile.ZipFile(path).read("release/build_info.json"))
    assert info["release_id"] == manifest["release_id"]
    assert info["corpus_version"] == manifest["corpus_version"]
