"""Protecoes HTTP da camada web: prefixo do ALB, limite de corpo, headers e HMAC.

Tudo aqui e middleware ASGI puro (sem BaseHTTPMiddleware) por dois motivos:
1. roda no MESMO contexto da requisicao, entao o span OpenTelemetry aberto no
   middleware de observabilidade continua valendo dentro do endpoint;
2. recusa o pedido ANTES do FastAPI ler e validar o corpo (413/401 baratos).
"""
import hashlib
import hmac
import json
import time

MAX_BODY_BYTES = 8 * 1024
HMAC_WINDOW_S = 120

# CSP restrita a 'self': a UI nao usa CDN nem script inline (Cytoscape vem em
# web/static/vendor). style-src-attr libera SO o atributo style="" que a UI usa
# para largura de barra e cor de legenda; <style> e <script> inline seguem bloqueados.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", CSP.encode("ascii")),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


async def send_json(send, status, body, extra_headers=()):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8"),
               (b"content-length", str(len(data)).encode("ascii")),
               (b"cache-control", b"no-store")]
    headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


class SecurityHeadersMiddleware:
    """Mesmos headers em TODA resposta (JSON, estatico, erro). Sem CORS: nenhum
    Access-Control-Allow-Origin e emitido, entao o navegador so aceita a mesma origem.
    Sem cookies: a aplicacao nao emite Set-Cookie em nenhuma rota."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"server", b"set-cookie")]
                headers.extend(SECURITY_HEADERS)
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, wrapped)


class PathPrefixMiddleware:
    """O ALB encaminha /candidate/* para o service candidate SEM remover o prefixo.

    Com QC_PATH_PREFIX=/candidate, /candidate/ready vira /ready aqui dentro; a rota
    sem prefixo tambem responde (o health check e o teste interno usam as duas).
    /candidate (sem barra) redireciona para /candidate/: sem a barra, o navegador
    resolveria os caminhos relativos da UI contra "/" e cairia no service LIVE.
    """

    def __init__(self, app, prefix):
        self.app = app
        self.prefix = (prefix or "").rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.prefix:
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == self.prefix:
            location = self.prefix + "/"
            if scope.get("query_string"):
                location += "?" + scope["query_string"].decode("latin-1")
            await send({"type": "http.response.start", "status": 308,
                        "headers": [(b"location", location.encode("latin-1")), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        if path.startswith(self.prefix + "/"):
            new_path = path[len(self.prefix):]
            raw = scope.get("raw_path")
            scope = dict(scope, path=new_path,
                         raw_path=raw[len(self.prefix):] if raw and raw.startswith(self.prefix.encode()) else raw)
        await self.app(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


async def read_limited_body(receive, limit):
    """Le o corpo inteiro ate `limit` bytes. Corpo pequeno por contrato (8 KB)."""
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _BodyTooLarge("cliente desconectou")
        body = message.get("body", b"")
        size += len(body)
        if size > limit:
            raise _BodyTooLarge(f"corpo maior que {limit} bytes")
        chunks.append(body)
        if not message.get("more_body"):
            return b"".join(chunks)


def _replay(body):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _header(scope, name):
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return v.decode("latin-1")
    return None


class BodyLimitMiddleware:
    """413 para corpo acima do limite, conferido no Content-Length E nos bytes lidos
    (um cliente pode mentir no header ou usar chunked)."""

    def __init__(self, app, limit=MAX_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        too_large = {"error": "payload_too_large", "detail": f"corpo maior que {self.limit} bytes"}
        declared = _header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.limit:
            return await send_json(send, 413, too_large)
        try:
            body = await read_limited_body(receive, self.limit)
        except _BodyTooLarge:
            return await send_json(send, 413, too_large)
        await self.app(scope, _replay(body), send)


# ------------------------------------------------------------------ HMAC
def canonical_string(ts, method, path, body):
    return f"{ts}\n{method.upper()}\n{path}\n{hashlib.sha256(body or b'').hexdigest()}"


def sign(key, ts, method, path, body):
    return hmac.new(key.encode("utf-8"), canonical_string(ts, method, path, body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_hmac(key, ts_header, sig_header, method, path, body, now=None):
    """Devolve None se valido ou o motivo da recusa (vai para o log, nunca a chave)."""
    if not ts_header or not sig_header:
        return "missing_signature"
    if not ts_header.isdigit():
        return "bad_timestamp"
    now = time.time() if now is None else now
    if abs(now - int(ts_header)) > HMAC_WINDOW_S:
        return "timestamp_out_of_window"
    expected = sign(key, ts_header, method, path, body)
    # compare_digest: tempo constante, nao vaza quantos caracteres bateram.
    if not hmac.compare_digest(expected, sig_header.strip().lower()):
        return "bad_signature"
    return None


class HmacMiddleware:
    """Porta admin: TODA rota exige assinatura (inclusive 404), para nao revelar
    quais rotas existem a quem nao tem a chave."""

    def __init__(self, app, key_provider, on_reject=None, limit=MAX_BODY_BYTES):
        self.app = app
        self.key_provider = key_provider
        self.on_reject = on_reject
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        key = self.key_provider()
        if not key:
            return await send_json(send, 503, {"error": "admin_disabled",
                                               "detail": "QC_ADMIN_HMAC_KEY nao configurada nesta task"})
        try:
            body = await read_limited_body(receive, self.limit)
        except _BodyTooLarge:
            return await send_json(send, 413, {"error": "payload_too_large",
                                               "detail": f"corpo maior que {self.limit} bytes"})
        reason = verify_hmac(key, _header(scope, b"x-qc-admin-timestamp"),
                             _header(scope, b"x-qc-admin-signature"), scope["method"], scope["path"], body)
        if reason:
            if self.on_reject:
                self.on_reject(scope["method"], scope["path"], reason)
            return await send_json(send, 401, {"error": "unauthorized", "detail": reason})
        await self.app(scope, _replay(body), send)
