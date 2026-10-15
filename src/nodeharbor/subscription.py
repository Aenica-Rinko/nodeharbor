"""Loopback-only subscription service, with an explicit file allowlist."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .storage import AppError

ROUTES = {f"/free{suffix}": f"free{suffix}"
          for suffix in [".yaml", ".mihomo.yaml", "-nodes.yaml", ".v2ray.txt", ".v2ray.base64.txt"]}
ROUTES['/mihomo.yaml'] = 'free.mihomo.yaml'


def create_server(paths, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def do_GET(self): self.respond(send_body=True)

        def do_HEAD(self): self.respond(send_body=False)

        def respond(self, send_body):
            try:
                host = urlsplit('http://' + self.headers.get('Host', ''))
                if host.hostname not in {'127.0.0.1', 'localhost'} or host.username or host.password:
                    self.send_error(403); return
            except ValueError:
                self.send_error(403); return
            name = ROUTES.get(urlsplit(self.path).path)
            if not name:
                self.send_error(404); return
            try:
                def read():
                    folder = paths.current()
                    file = folder / name
                    if file.is_symlink() or file.resolve().parent != folder.resolve(): raise AppError('订阅文件不能指向其他位置。')
                    return file.read_bytes()
                try: data = read()
                except FileNotFoundError:
                    # A reader can hold an old path while publication prunes it.
                    data = read()
            except (AppError, OSError):
                self.send_error(503, "No subscription available"); return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                try: self.wfile.write(data)
                except OSError: pass  # Client disconnected or stopped consuming the subscription.

        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
