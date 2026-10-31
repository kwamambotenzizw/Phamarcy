"""Vercel ASGI adapter for the existing pharmacy HTTP handlers."""
import io
import os
import threading
from email.message import Message

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

import server

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_init_lock = threading.Lock()
_initialized = False
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
]


class DeploymentGuard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0].lower().rstrip(".")
        allowed = {h.strip().lower().rstrip(".") for h in os.environ.get("PHARMA_ALLOWED_HOSTS", "").split(",") if h.strip()}
        for name in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
            value = os.environ.get(name, "").strip().lower().rstrip(".")
            if value:
                allowed.add(value)
        if not allowed or host not in allowed:
            return Response("Invalid host", status_code=400, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS:
            response.headers.setdefault(name.decode(), value.decode())
        return response


app.add_middleware(DeploymentGuard)


def _ensure_ready():
    global _initialized
    if os.environ.get("PHARMA_ENV") != "production":
        raise RuntimeError("Set PHARMA_ENV=production in Vercel")
    if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        raise RuntimeError("Configure a persistent PostgreSQL DATABASE_URL before deployment")
    with _init_lock:
        if not _initialized:
            # Schema migrations only. Production startup never seeds demo records.
            server.init(seed_demo=False)
            with server.connect() as connection:
                server.ensure_production_workspace(connection)
            _initialized = True


def _dispatch(method, path, headers, body, client_ip):
    """Run an existing Handler API method without opening a listening socket."""
    instance = server.Handler.__new__(server.Handler)
    instance.command = method
    instance.path = path
    instance.request_version = "HTTP/1.1"
    instance.requestline = f"{method} {path} HTTP/1.1"
    instance.close_connection = True
    instance.client_address = (client_ip, 0)
    instance.server = None
    instance.headers = Message()
    for key, value in headers.items():
        instance.headers[key] = value
    instance.rfile = io.BytesIO(body)
    instance.wfile = io.BytesIO()
    instance._headers_buffer = []
    if method == "GET":
        instance.do_GET()
    else:
        instance.do_POST()
    raw = instance.wfile.getvalue()
    marker = raw.find(b"\r\n\r\n")
    if marker < 0:
        return Response(status_code=500, content=b"Internal server error")
    status_line, *header_lines = raw[:marker].split(b"\r\n")
    status = int(status_line.split(b" ", 2)[1])
    response_headers = []
    for line in header_lines:
        if b":" in line:
            name, value = line.split(b":", 1)
            response_headers.append((name.strip().lower(), value.strip()))
    response_headers.extend(_SECURITY_HEADERS)
    return Response(content=raw[marker + 4 :], status_code=status, raw_headers=response_headers)


@app.get("/api/{path:path}")
@app.get("/api")
async def get_api(request: Request, path: str = ""):
    try:
        await run_in_threadpool(_ensure_ready)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers={"Cache-Control": "no-store"})
    return await run_in_threadpool(_dispatch, "GET", request.url.path + ("?" + request.url.query if request.url.query else ""), dict(request.headers), b"", request.client.host if request.client else "unknown")


@app.post("/api/{path:path}")
@app.post("/api")
async def post_api(request: Request, path: str = ""):
    try:
        await run_in_threadpool(_ensure_ready)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503, headers={"Cache-Control": "no-store"})
    body = await request.body()
    if len(body) > 2_000_000:
        return Response('{"error":"Request too large"}', status_code=413, media_type="application/json")
    return await run_in_threadpool(_dispatch, "POST", request.url.path, dict(request.headers), body, request.client.host if request.client else "unknown")


@app.get("/healthz")
async def health():
    return Response("ok", media_type="text/plain", headers={"Cache-Control": "no-store"})
