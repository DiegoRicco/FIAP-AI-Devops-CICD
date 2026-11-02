"""Gera o pacote da Lambda (dist/bundle.zip) e o manifesto do release (dist/manifest.json).

Por que um pacote unico: a resposta do assistente depende de codigo, corpus,
templates e config JUNTOS. Se eles viajassem separados, um rollback poderia
voltar o codigo e deixar o corpus novo. Aqui os quatro viram um zip so, que
vira UMA versao imutavel da Lambda.

Por que reprodutivel (mesma entrada -> mesmo sha256): data fixa em todos os
arquivos, ordem alfabetica e sem compressao (zlibs diferentes comprimem
diferente). Assim o pipeline pode provar que o que foi avaliado e o que subiu.

O pacote NAO e o planfile: o Terraform so recebe o caminho do zip pronto.

Uso:  python3 release/build_bundle.py [--out dist]
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# O que entra no pacote. evals/ fica de fora: avaliacao roda no pipeline, nao
# no runtime; a versao do eval set vai no build_info para aparecer no /health.
INCLUDE_DIRS = ("core", "corpus", "prompts", "config")
INCLUDE_FILES = ("adapters/lambda_handler.py", "orders/fixtures.json")
FIXED_DATE = (1980, 1, 1, 0, 0, 0)  # menor data aceita pelo formato zip


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def source_files():
    files = []
    for top in INCLUDE_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, top)):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in filenames:
                if name.endswith((".pyc", ".DS_Store")) or name.startswith("."):
                    continue
                files.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    files.extend(INCLUDE_FILES)
    # Separador "/" sempre: o zip gerado no Windows/Mac/Linux fica identico.
    return sorted(f.replace(os.sep, "/") for f in files)


def git_sha():
    sha = os.environ.get("CI_COMMIT_SHA")
    if sha:
        return sha
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "sem-git"


def content_sha(files):
    digest = hashlib.sha256()
    for rel in files:
        with open(os.path.join(ROOT, rel), "rb") as f:
            digest.update(rel.encode("utf-8") + b"\0" + f.read())
    return digest.hexdigest()


def add(zf, name, data):
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16  # permissao fixa: umask diferente nao muda o hash
    info.create_system = 3  # "unix" fixo, independente do SO do build
    zf.writestr(info, data)


def build(out_dir):
    from core.assistant import Assistant
    files = source_files()
    sha = git_sha()
    versions = Assistant({"orders": {"backend": "memory"}}).versions
    # release_id = commit + conteudo: duas maquinas no mesmo commit geram o
    # mesmo id; uma edicao local nao commitada gera outro.
    release_id = f"rel-{sha[:8]}-{content_sha(files)[:6]}"
    build_info = {"release_id": release_id, "git_sha": sha, **versions}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel in files:
            with open(os.path.join(ROOT, rel), "rb") as f:
                add(zf, rel, f.read())
        add(zf, "release/build_info.json",
            json.dumps(build_info, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    data = buf.getvalue()

    os.makedirs(out_dir, exist_ok=True)
    bundle_path = os.path.join(out_dir, "bundle.zip")
    with open(bundle_path, "wb") as f:
        f.write(data)

    manifest = {
        "release_id": release_id,
        "git_sha": sha,
        **versions,
        "bundle_sha256": hashlib.sha256(data).hexdigest(),
        "bundle_files": len(files) + 1,
        # built_at e pipeline_id ficam FORA do zip: senao o hash mudaria a cada build.
        "built_at": os.environ.get("CI_PIPELINE_CREATED_AT")
        or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_id": os.environ.get("CI_PIPELINE_ID", "local"),
        # Preenchido de verdade no promote (job approve ou politica automatica).
        "approved_by": None,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest, bundle_path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = p.parse_args(argv)
    manifest, path = build(args.out)
    log(f"bundle: {path} ({manifest['bundle_files']} arquivos)")
    log(f"release_id={manifest['release_id']} corpus={manifest['corpus_version']} "
        f"index={manifest['index_version']} prompt={manifest['prompt_version']}")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
