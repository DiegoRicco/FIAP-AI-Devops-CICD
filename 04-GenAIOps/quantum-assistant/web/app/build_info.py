"""Versoes ESTATICAS do release, sem Neo4j: `python -m web.app.build_info` (stdout = JSON).

O Dockerfile grava a saida em /app/release/build_info.json e o pipeline le o
arquivo direto da imagem pelo digest, para o manifesto do release bater 1:1 com
o /ready. graph_version fica de fora de proposito: e o hash do grafo EM RUNTIME
(so existe com o banco de pe); no boot limpo ele tem de ser igual a graph_seed_version.
"""
import json
import os
import sys


def collect(root=None, data_root=None):
    from core import ROOT
    from core.assistant import Assistant
    root = root or ROOT
    out = dict(Assistant(root=root).versions)
    try:
        from graph import loader
        from vector import cache as vcache, encoder as venc
        seed_root = data_root or root
        expected = loader.build_graph(seed_root, loader.load_seed(seed_root))
        out.update({
            "graph_seed_version": loader.canonical_hash(expected["nodes"], expected["edges"]),
            "vector_index_version": vcache.vector_index_version(Assistant(root=root).chunks),
            "embedding_model_id": venc.MODEL_ID,
            "embedding_model_revision": venc.MODEL_REVISION,
            "embedding_dim": venc.DIM,
            "embedding_model_license": getattr(venc, "MODEL_LICENSE", None),
        })
    except ImportError as err:
        # Sem os modulos do engine (bundle Lambda legado), so as versoes do core.
        print(f"build_info: modulos de grafo/vetor ausentes ({err}); so versoes do core", file=sys.stderr)
    out["model_version"] = out.get("model_version") or "none"
    out["git_sha"] = os.environ.get("QC_GIT_SHA") or "unknown"
    return out


def main():
    data_root = sys.argv[1] if len(sys.argv) > 1 else None
    sys.stdout.write(json.dumps(collect(data_root=data_root), sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
