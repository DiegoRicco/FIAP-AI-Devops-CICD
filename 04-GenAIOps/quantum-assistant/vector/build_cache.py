"""Gera o cache de embeddings dos chunks (rodado no docker build).

    python -m vector.build_cache [--data-root DIR] [--out ARQUIVO]

stdout = caminho do cache gerado; progresso no stderr.
"""
import argparse
import os
import sys
import time

from core import ROOT, versions
from core.corpus import load_chunks

from . import cache, encoder as enc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=os.environ.get("QC_DATA_ROOT") or ROOT)
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    model_dir = args.model_dir or enc.default_model_dir()
    out = args.out or cache.default_cache_path(model_dir)
    corpus_dir = os.path.join(args.data_root, "corpus")
    chunks = load_chunks(corpus_dir)
    started = time.perf_counter()
    encoder = enc.Encoder(model_dir)
    vectors, stats = cache.resolve(chunks, encoder, cache.load(out))
    cache.write(out, chunks, vectors, versions.corpus_version(corpus_dir))
    enc.log(f"cache de embeddings: {len(chunks)} chunks ({stats['hits']} reaproveitados, "
            f"{stats['recomputed']} calculados) em {time.perf_counter() - started:.1f}s")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
