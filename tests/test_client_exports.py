import base64
import json
import threading
from copy import deepcopy
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import requests

from nodeharbor.client_exports import filename, prepared
from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.exporter import publish
from nodeharbor.fetcher import fetch_all
from nodeharbor.nodes import parse_subscription
from nodeharbor.public_sources import GITHUB_POOLS, public_fallbacks
from nodeharbor.storage import AppError, Paths, write_yaml
from nodeharbor.subscription import create_server
from nodeharbor.uri_export import export_uris, to_uri


def node(kind='ss', **extras):
    return dict(name='示例 / # 节点', type=kind, server='2606:4700:4700::1111', port=443,
                password='p@ss:%41+/?', cipher='aes-256-gcm',
                uuid='11111111-1111-4111-8111-111111111111', **extras)


@pytest.mark.parametrize('kind', ['ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'tuic', 'socks5', 'anytls', 'wireguard'])
def test_standard_links_preserve_unicode_ipv6_and_authentication(kind):
    n = node(kind)
    if kind == 'wireguard': n.update({'private-key': 'private+key/=', 'public-key': 'public+key/=', 'ip': '10.0.0.2'})
    uri = to_uri(n)
    if kind == 'vmess':
        d = json.loads(base64.b64decode(uri[8:])); assert d['add'] == n['server'] and d['ps'] == n['name'] and d['id'] == n['uuid']
        return
    u = urlsplit(uri)
    assert u.hostname == n['server'] and u.port == 443 and unquote(u.fragment) == n['name']
    if kind == 'ss':
        assert base64.urlsafe_b64decode(u.username + '=' * (-len(u.username) % 4)).decode() == n['cipher'] + ':' + n['password']
    elif kind == 'socks5': assert base64.b64decode(unquote(u.username)).decode() == ':' + n['password']
    elif kind == 'tuic': assert unquote(u.password) == n['password'] and unquote(u.username) == n['uuid']
    elif kind == 'wireguard':
        assert unquote(u.username) == n['private-key']
        assert parse_qs(u.query)['address'] == ['10.0.0.2/32']
    else: assert unquote(u.username) == (n['uuid'] if kind == 'vless' else n['password'])


def test_reality_ws_grpc_and_hysteria_options_are_encoded_once():
    n = node('vless', **{'tls': True, 'servername': 'sni.example.com', 'flow': 'xtls-rprx-vision',
                         'client-fingerprint': 'chrome', 'reality-opts': {'public-key': 'key+/', 'short-id': 'abcd'},
                         'network': 'grpc', 'grpc-opts': {'grpc-service-name': 'svc/%41'}})
    q = parse_qs(urlsplit(to_uri(n)).query)
    assert q['security'] == ['reality'] and q['pbk'] == ['key+/'] and q['serviceName'] == ['svc/%41']
    n = node('trojan', **{'network': 'ws', 'ws-opts': {'path': '/ws?ed=2048', 'headers': {'Host': 'host.example.com'}}, 'skip-cert-verify': True})
    q = parse_qs(urlsplit(to_uri(n)).query)
    assert q['path'] == ['/ws?ed=2048'] and q['insecure'] == ['1']
    n = node('hysteria2', **{'obfs': 'salamander', 'obfs-password': 'ob%41+fs', 'ports': '2000:3000', 'skip-cert-verify': True})
    q = parse_qs(urlsplit(to_uri(n)).query)
    assert q['obfs-password'] == ['ob%41+fs'] and q['mport'] == ['2000-3000']


def test_vmess_tls_and_transport_fields_match_v2rayn_format():
    n = node('vmess', **{'tls': True, 'skip-cert-verify': True, 'network': 'ws',
                         'ws-opts': {'path': '/socket', 'headers': {'Host': 'ws.example.com'}}})
    d = json.loads(base64.b64decode(to_uri(n)[8:]))
    assert d['insecure'] == '1' and d['tls'] == 'tls' and d['host'] == 'ws.example.com' and d['path'] == '/socket'


def test_unsupported_options_are_reported_without_credentials():
    nodes = [node(), node('http'), node('vless', **{'skip-cert-verify': True}),
             node('ss', **{'plugin': 'obfs', 'plugin-opts': {'mode': 'tls'}}),
             node('trojan', **{'network': 'ws', 'ws-opts': {'headers': {'Authorization': 'secret'}}})]
    text, report = export_uris(nodes)
    assert report['exported'] == 1 and report['skipped'] == 4 and len(text.splitlines()) == 1
    assert 'secret' not in json.dumps(report) and nodes[0]['password'] not in json.dumps(report)


def test_generation_and_http_serve_client_specific_formats(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    folder, report = publish(paths, [node()], {})
    text = (folder / 'free.v2ray.txt').read_text(encoding='utf-8')
    assert base64.b64decode((folder / 'free.v2ray.base64.txt').read_bytes()).decode() == text
    assert parse_subscription(text)[0]['password'] == node()['password']
    assert report['v2rayn']['free']['exported'] == 1
    assert prepared(paths, 'v2rayN')[0].name.endswith('.v2ray.txt')
    assert filename('Clash-Verge-Rev') == 'free.yaml'
    assert filename('Mihomo') == 'free.mihomo.yaml'
    with create_server(paths, 0) as server:
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(f'http://127.0.0.1:{server.server_port}/free.v2ray.base64.txt', timeout=2)
                assert response.status_code == 200 and base64.b64decode(response.content).decode() == text
        finally: server.shutdown(); thread.join(2)
    (folder / 'free.v2ray.txt').unlink()
    with pytest.raises(AppError, match='旧版本'): prepared(paths, 'v2rayN')


def test_new_pool_default_and_legacy_settings_are_preserved(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / 'settings.yaml', {'fetch': {'sources': ['https://example.com/private-sub']}})
    before = (paths.config / 'settings.yaml').read_bytes()
    assert 'sources' not in load_settings(paths)['fetch']
    assert 'github_public_pool' not in load_settings(paths)['fetch']
    assert (paths.config / 'settings.yaml').read_bytes() == before


def test_public_pools_deduplicate_and_isolate_failed_sources(monkeypatch):
    settings = deepcopy(DEFAULTS)
    settings['fetch']['github_sources'] = [name for name, _ in GITHUB_POOLS]
    settings['fetch']['sources'] = [GITHUB_POOLS[0][1], 'https://example.com/private-sub?token=secret']
    calls, logs = [], []
    def fetch(url, options, control):
        calls.append(url)
        if url == GITHUB_POOLS[-1][1]: raise AppError('private-url-secret')
        return [node()]
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', fetch)
    nodes, report = fetch_all(settings, logs.append)
    assert len(nodes) == 1 and len(calls) == len(GITHUB_POOLS)
    assert report['github_sources'] == len(GITHUB_POOLS) and report['github_sources_ok'] == len(GITHUB_POOLS) - 1
    assert 'secret' not in '\n'.join(logs)
    calls.clear()
    fetch_all(settings, logs.append, public_only=True)
    assert set(calls) == {url for _, url in GITHUB_POOLS}
    assert public_fallbacks('https://raw.githubusercontent.com/private/repo/main/sub?token=secret') == []


def test_download_environment_proxy_does_not_enable_netrc(monkeypatch):
    from unittest.mock import MagicMock
    from nodeharbor.fetcher import download_single
    session = MagicMock(); session.__enter__.return_value = session
    response = session.get.return_value.__enter__.return_value
    response.iter_content.return_value = [b'proxies: []']
    monkeypatch.setattr('nodeharbor.fetcher.requests.Session', lambda: session)
    monkeypatch.setattr('nodeharbor.fetcher.requests.utils.get_environ_proxies', lambda url: {'https': 'http://proxy.example.com'})
    download_single('https://example.com/sub', DEFAULTS['fetch'])
    assert session.trust_env is False and session.proxies == {'https': 'http://proxy.example.com'}
    download_single('https://example.com/sub', dict(DEFAULTS['fetch'], proxy='http://explicit.example.com'))
    assert session.proxies['https'] == 'http://explicit.example.com'


def test_simple_export_page_and_optional_address_tools(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from nodeharbor.desktop import Desktop
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw()
    callbacks = []
    root.report_callback_exception = lambda *error: callbacks.append(error)
    paths = Paths(tmp_path / 'application')
    app = Desktop(root, paths)
    messages = []
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showinfo', lambda *a, **kw: messages.append(a))
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: pytest.fail(str(a)))
    def widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from widgets(child)
    try:
        app.show('subscriptions')
        assert not any(isinstance(widget, ttk.Combobox) for widget in widgets(app.main))
        assert [widget.cget('text') for widget in widgets(app.main) if isinstance(widget, tk.Button)] == [
            '从 GitHub 公开源获取节点', '生成 / 更新导出文件']
        publish(paths, [node()], {})
        app.show('subscriptions')
        assert (paths.client_exports('v2rayN') / 'free.txt').read_text(encoding='utf-8').startswith('ss://')
        assert (paths.client_exports('Mihomo') / 'free.yaml').is_file()
        opened = []
        monkeypatch.setattr('nodeharbor.desktop.open_path', lambda path: opened.append(path))
        app.open_exports('v2rayN')
        assert opened == [paths.client_exports('v2rayN')]
        # Random port for isolated tests, actual UI settings retain valid configured ports.
        monkeypatch.setattr('nodeharbor.desktop.load_settings', lambda _: dict(DEFAULTS, subscription={'port': 0}))
        app.show('settings')
        app.copy_subscription('v2rayN')
        assert app.server and root.clipboard_get().endswith('/free.v2ray.base64.txt')
        assert app.page == 'settings'
        app.copy_subscription('Mihomo')
        assert root.clipboard_get().endswith('/free.mihomo.yaml')
        app.stop_service(); assert app.server is None
        root.geometry('940x620'); root.update()
        app.show('overview'); root.update(); app.show('subscriptions'); root.update()
        assert not callbacks
    finally: app.close()
