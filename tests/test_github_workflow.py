import hashlib
import logging
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.diagnostics import core_error
from nodeharbor.exporter import publish
from nodeharbor.fetcher import download_with_report, fetch_all
from nodeharbor.pipeline import execute
from nodeharbor.quality import assess
from nodeharbor.storage import AppError, Paths, read_json, read_yaml, write_json, write_yaml
from nodeharbor.tasks import Cancelled, NetworkPool, TaskControl


def node(name='Example', server='example.com'):
    return {'name': name, 'type': 'ss', 'server': server, 'port': 443,
            'cipher': 'aes-256-gcm', 'password': 'fixture-only'}


def test_reexport_without_core_keeps_real_detection_time_and_private_files(tmp_path, monkeypatch):
    paths = Paths(tmp_path); paths.ensure(); write_yaml(paths.config / 'settings.yaml', DEFAULTS)
    publish(paths, [node()], {'verified_at': '2020-01-01T00:00:00Z', 'verification_core': 'mihomo'})
    private = paths.config / 'secrets.json'; private.write_bytes(b'{"token":"keep-private-only"}')
    def missing(*args): raise AppError('missing core')
    monkeypatch.setattr('nodeharbor.pipeline.find_mihomo', missing)
    try:
        report = execute(paths, 'export')
        assert report['verified_at'] == '2020-01-01T00:00:00Z'
        assert not report['config_validated'] and report['export_core'] == 'structure'
        assert report['export_warning'] and report['verification_core'] == 'mihomo'
        assert private.read_bytes() == b'{"token":"keep-private-only"}'
        assert read_yaml(paths.client_exports('Mihomo') / 'free.yaml')['mixed-port'] == 7890
        assert 'mixed-port' not in read_yaml(paths.client_exports('Clash-Verge-Rev') / 'free.yaml')
    finally:
        logger = logging.getLogger('nodeharbor.' + str(paths.root))
        for handler in list(logger.handlers): handler.close(); logger.removeHandler(handler)


def test_legacy_private_sources_and_upload_settings_are_ignored_without_rewriting(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / 'settings.yaml', {'fetch': {'sources': ['https://private.invalid/?token=secret']},
                                              'upload': {'enabled': True}})
    before = (paths.config / 'settings.yaml').read_bytes()
    settings = load_settings(paths)
    assert 'upload' not in settings and 'sources' not in settings['fetch']
    assert (paths.config / 'settings.yaml').read_bytes() == before


def test_https_failure_never_counts_as_a_pass():
    options = DEFAULTS['verification']; calls = []
    def probe(url): calls.append(url); return None if url.startswith('https:') else 10
    result = assess(probe, options, TaskControl())
    assert result['quality'] == 'failed' and result['delay_ms'] is None
    assert len(calls) == 8  # two rounds, one retry each, HTTP plus HTTPS


def test_failed_round_is_retried_and_quality_distinguishes_partial_success():
    options = dict(DEFAULTS['verification'], retry_failed=0)
    values = iter([10, 20, 10, None])
    result = assess(lambda _: next(values), options, TaskControl())
    assert result['quality'] == 'basic' and result['successful_rounds'] == 1
    values = iter([None, 10, 20, 10, 30])
    result = assess(lambda _: next(values), DEFAULTS['verification'], TaskControl())
    assert result['quality'] == 'stable' and result['successful_rounds'] == 2


def test_core_diagnostics_do_not_echo_credentials_or_local_paths():
    message = core_error(b'bind: address already in use; password=private-value C:/private/config.yaml', 1)
    assert '端口' in message and 'private-value' not in message and 'C:/' not in message


def test_source_contributions_provenance_and_failed_source_backoff(tmp_path, monkeypatch):
    settings = deepcopy(DEFAULTS)
    urls = ['https://raw.githubusercontent.com/fixture/repo/main/' + suffix for suffix in ('a', 'b', 'bad')]
    settings['fetch'].update(github_sources=[], public_sources=urls, _cache_folder=str(tmp_path / 'data/source-cache'))
    calls = []
    def fetch(url, *args):
        calls.append(url)
        if url.endswith('bad'): raise AppError('do not echo secret')
        return [node(), node('Unique', 'other.example.com')] if url.endswith('/a') else [node()]
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', fetch)
    nodes, report = fetch_all(settings, lambda _: None)
    assert len(nodes) == 2 and report['unique'] == 2
    assert [s['exclusive'] for s in report['details']] == [1, 0, 0]
    assert sorted(map(len, report['node_sources'].values())) == [1, 2]
    assert report['details'][2]['failures'] == 1 and report['details'][2]['next_retry'] > time.time()
    calls.clear(); fetch_all(settings, lambda _: None)
    assert urls[2] not in calls
    settings['fetch']['_force'] = True; calls.clear(); fetch_all(settings, lambda _: None)
    assert urls[2] in calls


def test_download_cache_and_conditional_revalidation(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from nodeharbor.public_sources import GITHUB_POOLS
    session = MagicMock(); session.__enter__.return_value = session
    response = session.get.return_value.__enter__.return_value
    response.status_code = 200; response.headers = {'ETag': 'fixture-etag'}
    response.iter_content.return_value = [b'proxies:\n- {name: Example, type: ss, server: example.com, port: 443, cipher: aes-256-gcm, password: fixture-only}']
    monkeypatch.setattr('nodeharbor.fetcher.requests.Session', lambda: session)
    options = dict(DEFAULTS['fetch'], _cache_folder=str(tmp_path), auto_local_proxy=False)
    url = GITHUB_POOLS[0][1]
    assert download_with_report(url, options)['nodes'] == [node()]
    assert download_with_report(url, options)['cached']
    assert session.get.call_count == 1
    response.status_code = 304
    result = download_with_report(url, dict(options, _force=True))
    assert result['nodes'] == [node()] and session.get.call_count == 2
    assert session.get.call_args.kwargs['headers']['If-None-Match'] == 'fixture-etag'


def test_rate_limit_stops_fallback_and_returns_safe_wait_hint(monkeypatch):
    import requests
    from nodeharbor.download_network import DownloadFailed
    from nodeharbor.public_sources import GITHUB_POOLS
    response = requests.Response(); response.status_code = 429; response.headers['Retry-After'] = '120'
    calls = []
    def limited(*args, **kwargs):
        calls.append(1); raise requests.HTTPError('secret details', response=response)
    monkeypatch.setattr('nodeharbor.fetcher.download_single', limited)
    with pytest.raises(DownloadFailed) as failure:
        download_with_report(GITHUB_POOLS[0][1], dict(DEFAULTS['fetch'], auto_local_proxy=False))
    assert len(calls) == 1 and failure.value.attempts[0]['retry_after'] == 120
    assert 'secret' not in str(failure.value)


def test_persistent_workers_reuse_process_and_cancel_blocked_request():
    started = threading.Event(); stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            if self.path == '/slow':
                started.set()
                while not stop.wait(.05):
                    try: self.wfile.write(b'x'); self.wfile.flush()
                    except OSError: break
            else: self.wfile.write(b'{"delay":12}')
        def log_message(self, *args): pass
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            control = TaskControl(); url = f'http://127.0.0.1:{server.server_port}'
            with NetworkPool(1, control) as pool:
                assert pool.request('probe', {'url': url, 'timeout': 3}) == 12
                process = pool.workers[0][0]
                for _ in range(3):
                    assert pool.request('probe', {'url': url, 'timeout': 3}) == 12
                    assert pool.workers[0][0] is process
                canceller = threading.Thread(target=lambda: (started.wait(5), control.cancel())); canceller.start()
                try:
                    with pytest.raises(Cancelled): pool.request('probe', {'url': url + '/slow', 'timeout': 20}, 25)
                    assert started.is_set() and process.poll() is not None
                finally: stop.set(); canceller.join(6)
        finally: stop.set(); server.shutdown(); thread.join(2)


def test_mihomo_standalone_config_opens_only_a_loopback_proxy_port(tmp_path):
    import requests, subprocess, socket
    from nodeharbor.verifier import free_port, NO_WINDOW
    core = Path(__file__).resolve().parents[1] / 'runtime/mihomo.exe'
    if not core.is_file(): pytest.skip('Local Mihomo not installed')
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path); self.send_response(204); self.end_headers()
        def do_CONNECT(self):
            if self.path.endswith(':443'): self.send_error(502); return
            self.send_response(200); self.end_headers()
            line = self.rfile.readline().decode('ascii')
            while self.rfile.readline() not in (b'\r\n', b'\n', b''): pass
            received.append(line.split()[1]); self.connection.sendall(b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
            self.close_connection = True
        def log_message(self, *args): pass
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as upstream:
        thread = threading.Thread(target=upstream.serve_forever); thread.start()
        process = None
        try:
            port = free_port(); paths = Paths(tmp_path); paths.ensure()
            proxy = {'name': 'Fixture', 'type': 'http', 'server': 'example.com', 'port': 443}
            folder, _ = publish(paths, [proxy], {}, core=core, port=port)
            config = read_yaml(folder / 'free.mihomo.yaml')
            assert config['allow-lan'] is False and config['bind-address'] == '127.0.0.1'
            # Replace only the upstream with a local fixture; keep the exported listener and routing.
            config['proxies'][0].update(server='127.0.0.1', port=upstream.server_port)
            file = tmp_path / 'runtime-test.yaml'; write_yaml(file, config)
            process = subprocess.Popen([str(core), '-d', str(tmp_path), '-f', str(file)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1): break
                except OSError:
                    if time.monotonic() > deadline: pytest.fail('Exported proxy listener did not start')
                    time.sleep(.05)
            with requests.Session() as session:
                session.trust_env = False
                response = session.get('http://1.1.1.1/exported-proxy', proxies={'http': f'http://127.0.0.1:{port}'}, timeout=3)
            assert response.status_code == 204 and any('/exported-proxy' in value for value in received)
        finally:
            if process is not None and process.poll() is None: process.terminate(); process.wait(5)
            upstream.shutdown(); thread.join(2)



def test_api_cooldown_cannot_be_bypassed_by_manual_retry(tmp_path):
    from nodeharbor.fetcher import api_gate
    import requests
    options = dict(DEFAULTS['fetch'], _cache_folder=str(tmp_path), _force=True)
    write_json(tmp_path / 'github-api-wait.json', {'until': time.time() + 120})
    with pytest.raises(requests.HTTPError) as error:
        with api_gate('https://api.github.com/repos/fixture/repo/contents/sub', options):
            pytest.fail('Network call must not start during API cooldown')
    assert error.value.response.status_code == 429


def test_old_or_missing_verification_time_is_not_presented_as_fresh():
    from nodeharbor.client_exports import freshness
    assert '超过' in freshness({'verified_at': '2000-01-01T00:00:00Z'})
    assert '重新检测' in freshness({})
    assert '异常' in freshness({'verified_at': '2100-01-01T00:00:00Z'})


def test_download_failure_keeps_real_classification_across_worker_boundary(monkeypatch):
    import json
    from nodeharbor.network_worker import respond
    from nodeharbor.download_network import DownloadFailed
    def fail(*args): raise DownloadFailed('tls', [{'channel': 'GitHub 原始文件', 'result': '失败'}])
    monkeypatch.setattr('nodeharbor.network_worker.perform', fail)
    result = json.loads(respond(b'{"operation":"fetch","payload":{}}'))
    assert result['error_code'] == 'tls' and result['attempts']


def test_incompatible_nodes_are_not_counted_as_actual_protocol_tests(monkeypatch):
    from nodeharbor.verifier import verify_nodes
    monkeypatch.setattr('nodeharbor.tasks.NetworkPool.request', lambda *args: True)
    monkeypatch.setattr('nodeharbor.verifier.compatible_nodes', lambda *args: [])
    events = []
    alive, report = verify_nodes('unused', [node()], DEFAULTS['verification'], lambda _: None, TaskControl(progress=events.append))
    assert not alive and report['protocol_tested'] == 0 and report['unsupported'] == 1
    assert events[-1]['tested'] == 0 and events[-1]['unsupported'] == 1


def test_installation_checks_execute_with_optimized_bytecode(tmp_path):
    import os, subprocess, sys
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / 'check.json'
    code = 'from nodeharbor.install_check import check_installation; import sys; sys.exit(check_installation(sys.argv[1]))'
    result = subprocess.run([sys.executable, '-O', '-c', code, str(report)],
                            env=dict(os.environ, PYTHONPATH=str(root / 'src'), PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, timeout=30)
    data = read_json(report)
    if data.get('error_type') == 'TclError': pytest.skip('Display unavailable')
    assert result.returncode == 0 and data['ok']
    assert 'persistent network worker reuse, streaming fetch progress and cleanup' in data['checks']
    assert 'background worker cancellation' in data['checks']
