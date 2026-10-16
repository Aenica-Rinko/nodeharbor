import logging
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from nodeharbor.config import DEFAULTS, core_kind, find_core, load_settings, resolve_core_path, validate_core
from nodeharbor.exporter import publish
from nodeharbor.fetcher import fetch_all
from nodeharbor.initialize import initialize
from nodeharbor.pipeline import execute
from nodeharbor.progress import TaskProgress
from nodeharbor.storage import AppError, Paths, write_yaml
from nodeharbor.tasks import TaskControl, Cancelled, network_job


def node(**kwargs):
    return dict({'type': 'ss', 'server': 'example.com', 'port': 443, 'name': 'Example',
                 'cipher': 'aes-256-gcm', 'password': 'example-only'}, **kwargs)


def close_logger(paths):
    logger = logging.getLogger('nodeharbor.' + str(paths.root))
    for handler in list(logger.handlers): handler.close(); logger.removeHandler(handler)


def test_download_check_respects_another_running_pipeline(tmp_path, monkeypatch):
    from nodeharbor.desktop import Desktop
    from nodeharbor.storage import app_lock
    app = Desktop.__new__(Desktop)
    app.busy = False
    app.paths = Paths(tmp_path)
    app.persist_source_choices = lambda: True
    app.public_source_settings = lambda: deepcopy(DEFAULTS)
    app.start_task = lambda action, work, **kw: work(TaskControl())
    fetch = Mock()
    monkeypatch.setattr('nodeharbor.ui.sources.fetch_all', fetch)
    with app_lock(app.paths):
        with pytest.raises(AppError, match='另一项更新'): app.test_public_sources()
    fetch.assert_not_called()


def test_client_path_profiles_preserve_legacy_config_and_find_bundled_core(tmp_path):
    paths = Paths(tmp_path); initialize(paths)
    root = tmp_path / 'Client'; root.mkdir()
    gui = root / 'Clash Party.exe'; gui.write_bytes(b'gui-placeholder')
    core = root / 'resources' / 'mihomo.exe'; core.parent.mkdir(); core.write_bytes(b'core-placeholder')
    settings = {'verification': {'mihomo_path': str(gui)}}
    write_yaml(paths.config / 'settings.yaml', settings)
    before = (paths.config / 'settings.yaml').read_bytes()
    loaded = load_settings(paths)
    assert loaded['verification']['core_client'] == 'Mihomo'
    assert find_core(paths, loaded) == core.resolve()
    assert (paths.config / 'settings.yaml').read_bytes() == before
    xray = root / 'bin/xray/xray.exe'; xray.parent.mkdir(parents=True); xray.write_bytes(b'xray-placeholder')
    v2 = root / 'v2rayN.exe'; v2.write_bytes(b'gui-placeholder')
    loaded['verification'].update(core_client='v2rayN', core_paths={'v2rayN': str(v2)}, v2rayn_core='xray')
    assert core_kind(loaded) == 'xray' and find_core(paths, loaded) == xray.resolve()
    with pytest.raises(AppError, match='没有找到'): resolve_core_path(paths, root / 'resources', 'xray')


def test_version_exit_zero_alone_is_not_a_valid_core(monkeypatch):
    process = MagicMock(); process.__enter__.return_value = process; process.returncode = 0
    monkeypatch.setattr('subprocess.Popen', lambda *a, **kw: process)
    control = TaskControl(); monkeypatch.setattr(control, 'communicate', lambda *a, **kw: (b'', b''))
    with pytest.raises(AppError, match='不是可用'): validate_core('Client.exe', 'mihomo', control)
    monkeypatch.setattr(control, 'communicate', lambda *a, **kw: (b'Mihomo Meta example', b''))
    assert validate_core('core.exe', 'mihomo', control) == 'mihomo'
    with pytest.raises(AppError): validate_core('core.exe', 'xray', control)


def test_bad_core_fails_before_any_download_and_preserves_current(tmp_path, monkeypatch):
    paths = Paths(tmp_path); initialize(paths)
    previous, _ = publish(paths, [node()], {})
    monkeypatch.setattr('nodeharbor.pipeline.find_mihomo', lambda *args: 'bad-gui')
    def invalid(*args): raise AppError('所选文件不是核心，请重新选择。')
    monkeypatch.setattr('nodeharbor.pipeline.validate_core', invalid)
    download = Mock(); monkeypatch.setattr('nodeharbor.pipeline.fetch_all', download)
    events = []
    try:
        with pytest.raises(AppError, match='不是核心'): execute(paths, 'refresh-github', control=TaskControl(progress=events.append))
        assert events[-1]['stage'] == 'core' and paths.current() == previous
        download.assert_not_called()
    finally: close_logger(paths)


def test_progress_events_are_snapshots_and_do_not_contain_private_source_urls(monkeypatch):
    settings = deepcopy(DEFAULTS)
    settings['fetch'].update(github_sources=[], public_sources=['https://raw.githubusercontent.com/fixture/repo/main/sub'])
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', lambda *args: [node()])
    events = []
    fetch_all(settings, lambda _: None, TaskControl(progress=events.append))
    assert events[0]['sources'][0]['status'] == '等待下载'
    assert events[-1]['sources'][0]['status'] == '成功'
    assert events[-1]['completed'] == 1 and events[-1]['parsed'] == 1
    assert 'hidden' not in str(events) and 'example.com/sub' not in str(events)


def test_progress_model_retains_counts_and_failed_stage():
    model = TaskProgress('refresh-github')
    model.update({'stage': 'download', 'completed': 3, 'total': 3, 'parsed': 120})
    model.update({'stage': 'precheck', 'completed': 120, 'total': 120})
    model.update({'stage': 'protocol', 'completed': 40, 'total': 60, 'passed': 7, 'tested': 32, 'unsupported': 8})
    model.finish('failed', '核心提前退出')
    assert model.steps['download']['finished'] and model.status == 'failed'
    assert model.counters['parsed'] == 120 and model.counters['tested'] == 32
    assert model.counters['skipped'] == 8 and model.counters['passed'] == 7
    assert model.stage == 'protocol'


def test_v2rayn_xray_pipeline_uses_selected_engine(tmp_path, monkeypatch):
    paths = Paths(tmp_path); initialize(paths)
    settings = deepcopy(DEFAULTS); settings['verification']['core_client'] = 'v2rayN'
    write_yaml(paths.config / 'settings.yaml', settings)
    monkeypatch.setattr('nodeharbor.pipeline.find_core', lambda *args: 'fake-xray')
    identity = Mock(); monkeypatch.setattr('nodeharbor.pipeline.validate_core', identity)
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', lambda *a, **kw: ([node()], {}))
    verify = Mock(return_value=([node()], {'verified': 1, 'core': 'xray'}))
    monkeypatch.setattr('nodeharbor.xray_verifier.verify_xray', verify)
    mihomo = Mock(); monkeypatch.setattr('nodeharbor.pipeline.verify_nodes', mihomo)
    try:
        report = execute(paths, 'refresh-github')
        assert report['verification_core'] == 'xray' and report['counts']['free'] == 1
        assert report['config_validated'] is False
        assert identity.call_args.args[1] == 'xray'
        verify.assert_called_once(); mihomo.assert_not_called()
        assert (paths.current() / 'free.v2ray.txt').is_file()
    finally: close_logger(paths)


def test_desktop_core_profiles_downloads_and_live_progress(tmp_path, monkeypatch):
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    from nodeharbor.ui.common import DOWNLOADS
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw(); paths = Paths(tmp_path); app = Desktop(root, paths)
    callbacks = []; root.report_callback_exception = lambda *e: callbacks.append(e)
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: pytest.fail(str(a)))
    release = threading.Event()
    try:
        app.show('settings')
        m = tmp_path / 'mihomo.exe'; m.write_bytes(b'core-placeholder')
        x = tmp_path / 'xray.exe'; x.write_bytes(b'xray-placeholder')
        app.core_path.set(str(m)); app.core_client_value.set('v2rayN'); app.change_core_client()
        app.core_path.set(str(x)); app.core_client_value.set('Mihomo'); app.change_core_client()
        assert app.core_path.get() == str(m)
        app.core_client_value.set('v2rayN'); app.change_core_client(); app.save_settings()
        saved = load_settings(paths)
        assert saved['verification']['core_paths']['Mihomo'] == str(m)
        assert saved['verification']['core_paths']['v2rayN'] == str(x)
        assert saved['verification']['core_client'] == 'v2rayN'
        assert app.export_client == 'v2rayN'
        assert set(DOWNLOADS) == {'Mihomo', 'Clash-Verge-Rev', 'v2rayN'}
        def job(control):
            control.emit('download', 1, 3, '下载中', parsed=42, sources=[{'name': 'Public fixture', 'status': '成功', 'nodes': 42}])
            while not release.wait(0.01): control.check()
            control.emit('protocol', 4, 4, '检测完成', passed=2)
        app.show('overview'); app.start_task('refresh-github', job)
        deadline = time.monotonic() + 3
        while app.task_progress.stage != 'download' and time.monotonic() < deadline: root.update(); time.sleep(0.01)
        assert app.busy and app.progress_panel.winfo_exists()
        assert app.task_progress.counters['parsed'] == 42
        app.show('subscriptions'); root.update()
        assert app.task_progress.stage == 'download' and app.progress_bar['maximum'] == 3
        release.set()
        deadline = time.monotonic() + 3
        while app.busy and time.monotonic() < deadline: root.update(); time.sleep(0.01)
        assert not app.busy and app.task_progress.status == 'success'
        assert app.task_progress.counters['passed'] == 2 and not callbacks
    finally:
        release.set()
        if app.busy:
            app.cancel_task()
            deadline = time.monotonic() + 3
            while app.busy and time.monotonic() < deadline: root.update(); time.sleep(0.01)
        app.close()


def test_real_xray_http_probe_is_routed_and_process_is_reclaimed():
    from nodeharbor.xray_verifier import running_xray, check_xray
    core = Path(__file__).resolve().parents[1] / '.release/xray-test-v26.3.27/xray.exe'
    if not core.is_file(): pytest.skip('Optional local Xray test binary unavailable')
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path); self.send_response(204); self.end_headers()
        def do_CONNECT(self):
            received.append(self.path)
            self.send_response(200); self.end_headers()
            request_line = self.rfile.readline().decode('ascii')
            while self.rfile.readline() not in (b'\r\n', b'\n', b''): pass
            received.append(request_line.split()[1])
            self.connection.sendall(b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
        def log_message(self, *args): pass
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            proxy = {'name': 'Local fixture', 'type': 'http', 'server': '127.0.0.1', 'port': server.server_port}
            assert validate_core(core, 'xray') == 'xray' and check_xray(core, [proxy])
            control = TaskControl()
            with pytest.raises(Cancelled):
                with running_xray(core, [proxy], control) as (ports, process):
                    assert network_job('http-probe', {'url': 'http://example.invalid/probe', 'port': ports[0], 'timeout': 3}, control, 8) > 0
                    control.cancel(); control.check()
            assert process.poll() is not None
            assert received in (['http://example.invalid/probe'], ['example.invalid:80', '/probe'])
        finally: server.shutdown(); thread.join(2)


@pytest.mark.parametrize('kind', ['ss', 'vmess', 'vless', 'trojan', 'socks5', 'http'])
def test_actual_xray_accepts_representative_adapter_configs(kind):
    from nodeharbor.xray_verifier import check_xray
    core = Path(__file__).resolve().parents[1] / '.release/xray-test-v26.3.27/xray.exe'
    if not core.is_file(): pytest.skip('Optional local Xray test binary unavailable')
    n = node(type=kind, uuid='11111111-1111-4111-8111-111111111111')
    if kind in {'vless', 'trojan'}: n['tls'] = True
    if kind == 'vmess':
        n.update(tls=True, network='ws', **{'ws-opts': {'path': '/ws', 'headers': {'Host': 'ws.example.com'}}})
    if kind == 'vless':
        import base64
        n.update(network='grpc', servername='sni.example.com', **{'grpc-opts': {'grpc-service-name': 'service'},
                     'client-fingerprint': 'chrome', 'reality-opts': {'public-key': base64.urlsafe_b64encode(b'\x01' * 32).decode().rstrip('='), 'short-id': 'abcd'}})
    assert check_xray(core, [n])
