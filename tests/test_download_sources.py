from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.download_network import DownloadFailed, error_code, loopback_proxy
from nodeharbor.fetcher import download_with_report, fetch_all
from nodeharbor.public_sources import GITHUB_POOLS, RECOMMENDED, github_file_url, public_fallbacks, selected_pools
from nodeharbor.storage import AppError, Paths, read_json, write_yaml


def node():
    return {'name': 'Example', 'type': 'ss', 'server': 'example.com', 'port': 443,
            'cipher': 'aes-256-gcm', 'password': 'example-only'}


def test_public_download_falls_back_to_local_proxy_without_leaking_credentials(monkeypatch):
    url = GITHUB_POOLS[0][1]
    monkeypatch.setattr('nodeharbor.fetcher.primary_proxies', lambda *args: {})
    monkeypatch.setattr('nodeharbor.fetcher.local_proxy_candidates', lambda: ['http://127.0.0.1:7890'])
    calls = []
    def download(candidate, options, retries=None, proxies=None):
        calls.append((candidate, proxies))
        if not proxies: raise requests.ConnectionError('URL with private-details must not cross error boundary')
        return [node()]
    monkeypatch.setattr('nodeharbor.fetcher.download_single', download)
    result = download_with_report(url, DEFAULTS['fetch'])
    assert len(calls) == 2 and result['nodes'] == [node()]
    assert result['route'].endswith('7890') and 'private-details' not in str(result)


def test_private_source_never_uses_local_proxy_or_public_mirror(monkeypatch):
    discovery = Mock(); monkeypatch.setattr('nodeharbor.fetcher.local_proxy_candidates', discovery)
    monkeypatch.setattr('nodeharbor.fetcher.primary_proxies', lambda *a: {})
    download = Mock(side_effect=requests.ConnectionError('secret-token'))
    monkeypatch.setattr('nodeharbor.fetcher.download_single', download)
    with pytest.raises(DownloadFailed) as error: download_with_report('https://example.com/sub?token=secret', DEFAULTS['fetch'])
    discovery.assert_not_called(); download.assert_called_once()
    assert 'secret' not in str(error.value) and 'secret' not in str(error.value.attempts)
    assert public_fallbacks('https://raw.githubusercontent.com/private/repo/main/file?token=secret') == []


@pytest.mark.parametrize('options', [dict(DEFAULTS['fetch'], proxy='http://user:pass@proxy.example.com:8080'),
                                  dict(DEFAULTS['fetch'], auto_local_proxy=False)])
def test_explicit_proxy_and_opt_out_do_not_discover_other_routes(monkeypatch, options):
    discovery = Mock(); monkeypatch.setattr('nodeharbor.fetcher.local_proxy_candidates', discovery)
    download = Mock(return_value=[node()]); monkeypatch.setattr('nodeharbor.fetcher.download_single', download)
    report = download_with_report(GITHUB_POOLS[0][1], options)
    discovery.assert_not_called()
    assert 'user:pass' not in str(report)


def test_proxy_fallback_preserves_tls_validation_and_reports_http_errors(monkeypatch):
    from unittest.mock import MagicMock
    from nodeharbor.fetcher import download_single
    session = MagicMock(); session.__enter__.return_value = session
    response = session.get.return_value.__enter__.return_value
    response.iter_content.return_value = [b'proxies: []']
    monkeypatch.setattr('nodeharbor.fetcher.requests.Session', lambda: session)
    download_single('https://example.com/sub', DEFAULTS['fetch'], proxies={'https': 'http://127.0.0.1:7890'})
    assert session.trust_env is False and session.get.call_args.kwargs.get('verify', True) is True
    assert error_code(requests.exceptions.SSLError()) == 'tls'
    assert error_code(requests.Timeout()) == 'timeout'


@pytest.mark.parametrize('value', ['http://remote.example.com:7890', 'http://user:pass@127.0.0.1:7890',
                                 'socks5://127.0.0.1:7891', 'http://127.0.0.1:7890/private'])
def test_automatic_proxy_candidates_must_be_plain_loopback_http(value):
    assert loopback_proxy(value) is None
    assert loopback_proxy('127.0.0.1:7890') == 'http://127.0.0.1:7890'


def test_catalog_has_more_than_three_sources_and_selection_can_be_extended():
    assert len(GITHUB_POOLS) == 26 and len(RECOMMENDED) == 16
    assert len(selected_pools(DEFAULTS['fetch'])) == len(RECOMMENDED)
    assert selected_pools(dict(DEFAULTS['fetch'], github_sources=[GITHUB_POOLS[3][0]])) == [GITHUB_POOLS[3]]
    assert github_file_url('https://github.com/example/public/blob/main/sub.txt') == 'https://raw.githubusercontent.com/example/public/main/sub.txt'
    for invalid in ['https://github.com/example/public', 'https://raw.githubusercontent.com/example/public/main/sub?token=secret',
                    'https://not-github.example.com/sub', 'http://raw.githubusercontent.com/example/public/main/sub']:
        with pytest.raises(AppError): github_file_url(invalid)


def test_extra_public_source_is_included_in_public_only_fetch(monkeypatch):
    settings = deepcopy(DEFAULTS)
    settings['fetch'].update(github_sources=[], public_sources=['https://raw.githubusercontent.com/example/public/main/sub'],
                             sources=['https://private.example.com/sub?token=secret'])
    calls = []
    def fetch(url, options, control): calls.append((url, options)); return [node()]
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', fetch)
    nodes, report = fetch_all(settings, lambda _: None, public_only=True)
    assert nodes == [node()] and report['github_sources'] == 1
    assert len(calls) == 1 and calls[0][1]['_public_source'] is True


def test_public_catalog_page_remembers_selection_and_adds_disabled_urls(tmp_path, monkeypatch):
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw(); paths = Paths(tmp_path); app = Desktop(root, paths)
    callbacks = []; root.report_callback_exception = lambda *error: callbacks.append(error)
    errors = []; monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: errors.append(a))
    try:
        app.show('sources'); root.update()
        assert len(app.sources_tree.get_children()) == len(GITHUB_POOLS) and len(app.selected_public_sources) == len(RECOMMENDED)
        app.choose_public_sources([GITHUB_POOLS[3][0]])
        app.extra_public_sources_text.insert('1.0', 'https://github.com/example/public/blob/main/sub.txt')
        app.add_manual_sources()
        settings = load_settings(paths)
        assert settings['fetch']['github_sources'] == [GITHUB_POOLS[3][0]]
        assert settings['fetch']['public_sources'] == []
        assert settings['fetch']['node_pools'][0]['urls'] == ['https://raw.githubusercontent.com/example/public/main/sub.txt']
        assert settings['fetch']['node_pools'][0]['enabled'] is False
        before = (paths.config / 'settings.yaml').read_bytes()
        app.extra_public_sources_text.insert('end', '\nhttps://example.com/private?token=secret')
        with pytest.raises(AppError): app.add_manual_sources()
        assert not errors and (paths.config / 'settings.yaml').read_bytes() == before and not callbacks
    finally: app.close()
