"""Cliente da porta admin (8001) com assinatura HMAC. So stdlib, Python >= 3.10.

Roda DENTRO do runner (via SSM) nos scripts de incidente da 5.2: o runner esta
na VPC e e o unico SG autorizado na 8001. Nunca passa pelo ALB.

Uso:
  QC_ADMIN_HMAC_KEY=... python3 web/admin_client.py --host 10.0.1.23 GET /admin/graph/state
  QC_ADMIN_HMAC_KEY=... python3 web/admin_client.py --host 10.0.1.23 \\
      POST /admin/graph/scenario '{"scenario":"stale_graph_policy","action":"apply"}'

stdout = JSON da resposta; stderr = linha de status. Codigo de saida 0 so com 2xx.

Por que HMAC com timestamp e hash do corpo: a chave nunca trafega; a assinatura
vale para UM metodo, UM caminho e UM corpo, e so por +-120 s. Quem capturar uma
requisicao nao consegue trocar o cenario nem reaproveita-la depois.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

KEY_ENV = "QC_ADMIN_HMAC_KEY"
WINDOW_S = 120


def canonical(ts, method, path, body):
    """Texto assinado: f"{ts}\\n{METHOD}\\n{path}\\n{sha256hex(body)}" (contrato)."""
    return f"{ts}\n{method.upper()}\n{path}\n{hashlib.sha256(body or b'').hexdigest()}"


def signature(key, ts, method, path, body):
    return hmac.new(key.encode("utf-8"), canonical(ts, method, path, body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def signed_headers(key, method, path, body, ts=None):
    ts = str(int(time.time()) if ts is None else int(ts))
    return {"X-QC-Admin-Timestamp": ts, "X-QC-Admin-Signature": signature(key, ts, method, path, body)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chama a API admin (8001) do assistente com HMAC")
    parser.add_argument("--host", default=os.environ.get("QC_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QC_ADMIN_PORT", "8001")))
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="reindex reconstroi grafo e indice: pode levar dezenas de segundos")
    parser.add_argument("method", choices=["GET", "POST"])
    parser.add_argument("path", help="ex.: /admin/graph/state")
    parser.add_argument("body", nargs="?", default="", help="JSON do corpo (POST)")
    args = parser.parse_args(argv)

    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"erro: defina {KEY_ENV} (leia do SSM dentro do runner; nunca grave em arquivo)",
              file=sys.stderr)
        return 2
    if not args.path.startswith("/admin/"):
        print("erro: o caminho precisa comecar com /admin/", file=sys.stderr)
        return 2
    body = b""
    if args.body:
        try:
            # Normaliza o JSON: os bytes assinados sao exatamente os bytes enviados.
            body = json.dumps(json.loads(args.body), separators=(",", ":")).encode("utf-8")
        except ValueError as err:
            print(f"erro: corpo nao e JSON valido: {err}", file=sys.stderr)
            return 2

    headers = signed_headers(key, args.method, args.path, body)
    if body:
        headers["Content-Type"] = "application/json"
    url = f"http://{args.host}:{args.port}{args.path}"
    req = urllib.request.Request(url, data=body if args.method == "POST" else None,
                                 headers=headers, method=args.method)
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    except (urllib.error.URLError, OSError) as err:
        print(f"erro: sem conexao com {args.host}:{args.port} ({err})", file=sys.stderr)
        return 1
    print(f"HTTP {status} {args.method} {args.path}", file=sys.stderr)
    sys.stdout.write(raw.decode("utf-8", "replace").rstrip("\n") + "\n")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
