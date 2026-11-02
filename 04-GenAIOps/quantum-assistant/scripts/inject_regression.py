"""Falha planejada da Demo 5.1: mudanca pequena, reversivel e commitavel.

  python3 scripts/inject_regression.py --kind obsolete-priority --apply
  python3 scripts/inject_regression.py --kind obsolete-priority --revert
  python3 scripts/inject_regression.py --kind remove-active-filter --apply
  python3 scripts/inject_regression.py --kind remove-active-filter --revert
  python3 scripts/inject_regression.py --status

obsolete-priority    alguem republica a politica de trocas v2 (15 dias) como
                     'active'. Nenhum codigo muda: so CONTEUDO. O gate precisa
                     pegar mesmo assim.
remove-active-filter alguem desliga o filtro status=active no config "para
                     simplificar". O corpus nao muda: muda o INDICE efetivo.

Por que troca de texto exata e nao backup: o aluno commita a falha e depois o
revert; o arquivo precisa voltar byte a byte ao original, em qualquer maquina.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KINDS = {
    "obsolete-priority": {
        "file": os.path.join("corpus", "trocas-v2.md"),
        "original": "status: obsolete\n",
        "injected": "status: active\n",
        "explica": "politica de trocas v2 (15 dias) republicada como active",
    },
    "remove-active-filter": {
        "file": os.path.join("config", "settings.json"),
        "original": '"require_active": true',
        "injected": '"require_active": false',
        "explica": "filtro status=active desligado no retriever",
    },
}


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def state(kind):
    spec = KINDS[kind]
    with open(os.path.join(ROOT, spec["file"]), encoding="utf-8") as f:
        text = f.read()
    if spec["injected"] in text:
        return "applied", text
    if spec["original"] in text:
        return "clean", text
    return "unknown", text


def change(kind, apply):
    spec = KINDS[kind]
    current, text = state(kind)
    wanted = "applied" if apply else "clean"
    if current == wanted:
        log(f"{kind}: ja estava '{wanted}', nada a fazer")
        return 0
    if current == "unknown":
        log(f"{kind}: {spec['file']} nao tem o trecho esperado; edite manualmente")
        return 2
    old, new = (spec["original"], spec["injected"]) if apply else (spec["injected"], spec["original"])
    with open(os.path.join(ROOT, spec["file"]), "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    acao = "APLICADA" if apply else "REVERTIDA"
    log(f"{kind}: regressao {acao} em {spec['file']} ({spec['explica']})")
    print(f"{kind} {wanted} {spec['file']}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Aplica ou reverte a regressao planejada da Demo 5.1.")
    p.add_argument("--kind", choices=sorted(KINDS))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--revert", action="store_true")
    g.add_argument("--status", action="store_true")
    args = p.parse_args(argv)
    if args.status or not (args.apply or args.revert):
        for kind in ([args.kind] if args.kind else sorted(KINDS)):
            print(f"{kind} {state(kind)[0]} {KINDS[kind]['file']}")
        return 0
    if not args.kind:
        p.error("--apply/--revert exigem --kind")
    return change(args.kind, args.apply)


if __name__ == "__main__":
    sys.exit(main())
