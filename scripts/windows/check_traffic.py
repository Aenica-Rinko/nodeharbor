"""Exercise a synthetic exported SS node through real cores, entirely on loopback."""
import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from nodeharbor.exporter import publish
from nodeharbor.initialize import initialize
from nodeharbor.storage import Paths, read_yaml, write_yaml


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def wait_port(port, process):
    for _ in range(100):
        if process.poll() is not None: raise RuntimeError('Test core exited; inspect its diagnostic log')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2): return
        except OSError: time.sleep(.05)
    raise RuntimeError('Test core did not start')


def check(mihomo, upstream_mihomo):
    payload = b'NodeHarbor controlled loopback proxy acceptance'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
        def log_message(self, *args): pass

    with tempfile.TemporaryDirectory(prefix='nodeharbor-traffic-') as temp, ExitStack() as stack:
        root = Path(temp)
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        stack.callback(http.server_close); stack.callback(http.shutdown)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        ss_port, proxy_port = free_port(), free_port()
        password = 'fixture-only:@/%41+'
        server = {'mode': 'rule', 'log-level': 'warning', 'allow-lan': False,
            'listeners': [dict(name='test-ss', type='shadowsocks', listen='127.0.0.1', port=ss_port,
                               cipher='aes-256-gcm', password=password, udp=False)],
            'rules': [f'AND,((IP-CIDR,127.0.0.1/32),(DST-PORT,{http.server_port})),DIRECT', 'MATCH,REJECT']}
        server_dir = root / 'upstream'; server_dir.mkdir()
        server_file = server_dir / 'server.yaml'; write_yaml(server_file, server)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        logs = stack.enter_context((root / 'cores.log').open('wb'))
        upstream = subprocess.Popen([str(upstream_mihomo.resolve()), '-d', str(server_dir), '-f', str(server_file)],
            stdout=logs, stderr=logs, creationflags=flags)
        stack.callback(stop, upstream); wait_port(ss_port, upstream)
        paths = Paths(root / 'profile'); initialize(paths)
        publish(paths, [dict(name='Loopback fixture', type='ss', server='nodeharbor.example', port=ss_port,
                            cipher='aes-256-gcm', password=password)], {}, port=proxy_port)
        config = read_yaml(paths.client_exports('Mihomo') / 'free.yaml')
        # Test harness only: map the fictional upstream locally and keep health checks local.
        # Preserve exported credentials, groups, routing and client listen options.
        config['hosts'] = {'nodeharbor.example': '127.0.0.1'}
        target = f'http://127.0.0.1:{http.server_port}/acceptance'
        for group in config['proxy-groups']:
            if group['type'] == 'url-test': group.update(url=target, interval=86400)
        config_file = root / 'client.yaml'; write_yaml(config_file, config)
        client = subprocess.Popen([str(mihomo.resolve()), '-d', str(root), '-f', str(config_file)],
            stdout=logs, stderr=logs, creationflags=flags)
        stack.callback(stop, client); wait_port(proxy_port, client)
        with requests.Session() as session:
            session.trust_env = False
            if session.get(target, timeout=3).content != payload: raise RuntimeError('Loopback fixture unavailable')
            try:
                response = session.get(target, proxies={'http': f'http://127.0.0.1:{proxy_port}'}, timeout=10)
            except requests.RequestException as exc:
                logs.flush()
                raise RuntimeError((root / 'cores.log').read_text(encoding='utf-8', errors='replace')[-4000:]) from exc
            if response.status_code != 200 or response.content != payload: raise RuntimeError('Proxy response differs')
            # A successful direct fallback must not masquerade as a working SS node.
            stop(upstream)
            try:
                response = session.get(target + '?upstream-stopped', proxies={'http': f'http://127.0.0.1:{proxy_port}'}, timeout=3)
            except requests.RequestException: pass
            else:
                if response.status_code == 200: raise RuntimeError('Request bypassed the test proxy')
        return {'ok': True, 'protocol': 'ss/aes-256-gcm', 'traffic': 'loopback only',
                'exporter_used': True, 'direct_fallback_rejected': True, 'public_nodes_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mihomo', type=Path, required=True)
    parser.add_argument('--upstream-mihomo', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.mihomo, args.upstream_mihomo), indent=2))
