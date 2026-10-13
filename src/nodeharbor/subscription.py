"""Loopback-only subscription service, with an explicit file allowlist."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .storage import AppError

ROUTES = {f"/free{suffix}": f"free{suffix}"
          for suffix in [".yaml", ".mihomo.yaml", "-nodes.yaml", ".v2ray.txt", ".v2ray.base64.txt"]}
ROUTES['/mihomo.yaml'] = 'free.mihomo.yaml'


def create_server(paths, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = ROUTES.get(urlsplit(self.path).path)
            if not name:
                self.send_error(404); return
            try:
                try: data = (paths.current() / name).read_bytes()
                except FileNotFoundError:
                    # A reader can hold an old path while publication prunes it.
                    data = (paths.current() / name).read_bytes()
            except (AppError, OSError):
                self.send_error(503, "No subscription available"); return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args): pass
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
