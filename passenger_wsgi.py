"""Production WSGI entry point for cPanel/Passenger deployments."""
from email.message import Message
from http import HTTPStatus
from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
WEB_ROOT = (ROOT / 'web').resolve()
sys.path.insert(0, str(ROOT))

import server


class PassengerRequest(server.Handler):
    """Adapt the existing API handler to WSGI without exposing project files."""
    def __init__(self, environ):
        self.directory = str(WEB_ROOT)
        self.path = environ.get('PATH_INFO', '/') or '/'
        self.command = environ.get('REQUEST_METHOD', 'GET').upper()
        self.request_version = 'HTTP/1.1'
        self.requestline = self.command + ' ' + self.path + ' HTTP/1.1'
        self.client_address = (environ.get('REMOTE_ADDR', ''), 0)
        self.headers = Message()
        for name, value in environ.items():
            if name.startswith('HTTP_'):
                self.headers[name[5:].replace('_', '-')] = value
        for name in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if environ.get(name):
                self.headers[name.replace('_', '-')] = environ[name]
        if not self.headers.get('Host'):
            self.headers['Host'] = environ.get('HTTP_HOST', '')
        self.rfile = environ['wsgi.input']
        self.wfile = tempfile.SpooledTemporaryFile(max_size=1024*1024, mode='w+b')
        self.status_code = 500
        self.response_headers = []

    def send_response_only(self, code, message=None):
        self.status_code = int(code)

    def send_header(self, keyword, value):
        self.response_headers.append((str(keyword), str(value)))

    def end_headers(self):
        return


def _production_setup(environ):
    if os.environ.get('PHARMA_ENV') != 'production':
        raise RuntimeError('Set PHARMA_ENV=production in the cPanel Python application settings.')
    public_root_value = os.environ.get('PHARMA_PUBLIC_ROOT')
    db_value = os.environ.get('PHARMA_DB')
    allowed_hosts = {host.strip().lower() for host in os.environ.get('PHARMA_ALLOWED_HOSTS', '').split(',') if host.strip()}
    if not public_root_value or not db_value or not allowed_hosts:
        raise RuntimeError('Set PHARMA_PUBLIC_ROOT, PHARMA_DB, and PHARMA_ALLOWED_HOSTS before deploying.')
    public_root = Path(public_root_value).expanduser().resolve()
    app_root = ROOT.resolve()
    db_path = Path(db_value).expanduser()
    if not db_path.is_absolute():
        raise RuntimeError('PHARMA_DB must be an absolute path in persistent private storage.')
    db_path = db_path.resolve()
    if app_root == public_root or public_root in app_root.parents:
        raise RuntimeError('Place application source outside the website document root.')
    if db_path == public_root or public_root in db_path.parents or db_path == WEB_ROOT or WEB_ROOT in db_path.parents:
        raise RuntimeError('Place the database outside every public web directory.')
    if os.environ.get('PHARMA_SELF_REGISTRATION', '0') != '0':
        raise RuntimeError('Public self-registration must be disabled for this deployment.')
    os.environ['PHARMA_DB'] = str(db_path)
    server.DB = db_path
    server.init(seed_demo=False)
    with server.connect() as conn:
        server.ensure_production_workspace(conn)
    return public_root, allowed_hosts


PUBLIC_ROOT, ALLOWED_HOSTS = _production_setup({})


def application(environ, start_response):
    host = environ.get('HTTP_HOST', '').split(':', 1)[0].lower().rstrip('.')
    if host not in ALLOWED_HOSTS:
        body = b'Invalid host header'
        start_response('400 Bad Request', [('Content-Type', 'text/plain; charset=utf-8'), ('Content-Length', str(len(body)))])
        return [body]
    if environ.get('wsgi.url_scheme') != 'https':
        target = 'https://' + host + environ.get('PATH_INFO', '/')
        if environ.get('QUERY_STRING'):
            target += '?' + environ['QUERY_STRING']
        start_response('308 Permanent Redirect', [('Location', target), ('Content-Length', '0'), ('Cache-Control', 'no-store')])
        return [b'']

    request = PassengerRequest(environ)
    method = request.command
    if method in ('GET', 'HEAD'):
        request.do_GET() if method == 'GET' else request.do_HEAD()
    elif method == 'POST':
        request.do_POST()
    else:
        request.send_response(405)
        request.send_header('Allow', 'GET, HEAD, POST')
        request.end_headers()

    request.wfile.seek(0, 2)
    body_length = request.wfile.tell()
    request.wfile.seek(0)
    headers = [(k, v) for k, v in request.response_headers if k.lower() not in ('content-length', 'content-security-policy', 'x-frame-options')]
    headers.extend([
        ('Content-Length', str(body_length)),
        ('X-Content-Type-Options', 'nosniff'),
        ('X-Frame-Options', 'DENY'),
        ('Referrer-Policy', 'no-referrer'),
        ('Permissions-Policy', 'camera=(), microphone=(), geolocation=()'),
        ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"),
    ])
    if environ.get('wsgi.url_scheme') == 'https':
        headers.append(('Strict-Transport-Security', 'max-age=31536000'))
    start_response(f'{request.status_code} {HTTPStatus(request.status_code).phrase}', headers)
    if method == 'HEAD':
        request.wfile.close()
        return [b'']

    def stream_response():
        try:
            while True:
                chunk = request.wfile.read(64*1024)
                if not chunk:
                    break
                yield chunk
        finally:
            request.wfile.close()

    return stream_response()
