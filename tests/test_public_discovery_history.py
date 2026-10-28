import base64
import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from nodeharbor import github_search, pipeline
from nodeharbor.client_exports import verification_stale
from nodeharbor.config import DEFAULTS
from nodeharbor.node_history import read_history, record_history, plan_checks, stability_order
from nodeharbor.nodes import identity
from nodeharbor.public_links import subscription_url, readme_links, repository_kind, check_public_host
from nodeharbor.source_catalog import add_pool, enabled_urls, replace_pool
from nodeharbor.storage import AppError, Paths, write_yaml
from nodeharbor.tasks import Cancelled


def node(i=1):
    return dict(name=f'fixture-{i}', type='ss', server=f'n{i}.example.com', port=443,
                cipher='aes-256-gcm', password='synthetic-only')


@pytest.mark.parametrize('url', ['http://example.com/sub', 'https://localhost/sub', 'https://127.0.0.1/sub',
    'https://example.local/sub', 'https://example.com/sub?token=secret', 'https://user:pass@example.com/sub',
    'https://example.com/', 'https://example.com/{uuid}/sub', 'https://example.com/%7Buuid%7D/sub',
    'https://example.com:444/sub', 'https://example.com/../sub', 'https://example.com/sub#secret'])
def test_public_url_rejects_local_credentials_and_templates(url):
    with pytest.raises(AppError): subscription_url(url)


def test_readme_filters_classifies_and_preserves_provenance():
    text = '''[订阅](https://sample.workers.dev/sub)
https://sample.workers.dev/sub
https://example.com/clash.yaml
https://sample.pages.dev/ip.txt
https://example.com/sub?token=private
https://raw.githubusercontent.com/other/repo/main/sub
https://raw.githubusercontent.com/example/repo/main/sub
https://example.com/deploy
'''
    links = readme_links(text, 'example/repo')
    assert len(links) == 3 and len({l['url'] for l in links}) == 3
    assert links[0]['kind'] == 'Cloudflare 托管（第三方）'
    assert all(l['provenance'] == 'https://github.com/example/repo#readme' for l in links)
    assert 'IP' in repository_kind('XIU2/CloudflareSpeedTest')
    assert '部署' in repository_kind('zizifn/edgetunnel')


def test_external_links_are_disabled_and_replacing_re_disables():
    url = 'https://sample.workers.dev/sub'
    pools, added = add_pool({}, {'name': 'example/repo'}, [url])
    assert added and not pools[0]['enabled'] and enabled_urls({'node_pools': pools}) == []
    pools[0]['enabled'] = True
    assert enabled_urls({'node_pools': pools}) == [url]
    changed = replace_pool({'node_pools': pools}, 'example/repo', ['https://example.com/sub'])
    assert not changed[0]['enabled']
    appended, _ = add_pool({'node_pools': pools}, {'name': 'example/repo'}, ['https://example.com/sub'])
    assert not appended[0]['enabled']


def test_discovery_readme_failure_preserves_candidates(monkeypatch):
    def api(path, params, options):
        if path.endswith('/readme'): return {'error': 'rate_limit'}
        if '/git/trees/' in path: return {'tree': [dict(type='blob', size=50, path='sub')]}
        return {'private': False}
    monkeypatch.setattr(github_search, 'api_json', api)
    result = github_search.perform(dict(mode='files', value={'name': 'example/repo', 'branch': 'main'}))
    assert len(result['files']) == 1 and result['readme_warning']
    original = api
    monkeypatch.setattr(github_search, 'api_json', lambda path, params, options:
        {'encoding': 'base64', 'content': base64.b64encode(b'https://sample.workers.dev/sub').decode()}
        if path.endswith('/readme') else original(path, params, options))
    assert len(github_search.perform(dict(mode='files', value={'name': 'example/repo', 'branch': 'main'}))['files']) == 2


def test_preview_returns_only_counts_and_protocols(monkeypatch):
    monkeypatch.setattr('nodeharbor.fetcher.download_with_report', lambda url, options: {'nodes': [node()]})
    result = github_search.perform(dict(mode='preview', value='https://sample.workers.dev/sub'))
    assert result['count'] == 1 and result['protocols'] == {'ss': 1}
    assert 'synthetic-only' not in json.dumps(result) and 'n1.example.com' not in json.dumps(result)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.2.3', '192.168.1.3', '169.254.169.254', '::1'])
def test_external_dns_rejects_private_targets(monkeypatch, address):
    monkeypatch.setattr('nodeharbor.public_links.socket.getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', (address, 443))])
    with pytest.raises(AppError): check_public_host('https://sample.workers.dev/sub')


def test_external_redirect_is_not_followed(monkeypatch):
    from nodeharbor.fetcher import download_single
    monkeypatch.setattr('nodeharbor.fetcher.check_public_host', lambda url: url)
    session = MagicMock(); session.__enter__.return_value = session
    response = session.get.return_value.__enter__.return_value
    response.status_code = 302; response.headers = {'Location': 'http://127.0.0.1/admin'}
    monkeypatch.setattr('nodeharbor.fetcher.requests.Session', lambda: session)
    with pytest.raises(AppError, match='重定向'):
        download_single('https://sample.workers.dev/sub', DEFAULTS['fetch'])
    assert session.get.call_args.kwargs['allow_redirects'] is False
    assert not session.trust_env


def test_history_records_failures_not_unsupported_or_reuse(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    good, failed, unsupported = node(1), node(2), node(3)
    a, b, c = map(identity, [good, failed, unsupported])
    history = record_history(paths, {}, [good, failed, unsupported], [good],
                             {'outcomes': {a: True, b: False}}, 'sig', 10000)
    assert history[a]['recent'] == [True] and history[b]['failures'] == 1 and c not in history
    history = record_history(paths, history, [], [], {}, 'sig', 10001)
    assert history[a]['recent'] == [True]
    assert 'synthetic-only' not in (paths.data / 'state/node-history.json').read_text()
    assert not read_history(paths, now=10000 + 31 * 86400)


def test_incremental_expiry_full_check_signature_and_backoff():
    nodes = [node(i) for i in range(1, 5)]
    a, b, c, d = map(identity, nodes)
    history = {a: dict(last_checked=9500, recent=[True], signature='sig'),
               b: dict(last_checked=9000, recent=[True], signature='sig'),
               c: dict(last_checked=9500, recent=[False] * 3, failures=3, signature='sig')}
    report = {'verification': {'quality': {a: {'quality': 'stable'}, b: {'quality': 'stable'}}}}
    tested, reused, deferred = plan_checks(nodes, nodes[:2], report, history, 'sig', now=10000)
    assert tested == [nodes[3], nodes[1]] and reused == [nodes[0]] and deferred == [nodes[2]]
    assert len(plan_checks(nodes, nodes[:2], report, history, 'sig', full=True, now=10000)[0]) == 4
    assert len(plan_checks(nodes, nodes[:2], report, history, 'other', now=10000)[0]) == 4
    assert stability_order({'recent': [True] * 10}) < stability_order({'recent': [True]})


def test_pipeline_reuse_fullcheck_and_export_preserve_evidence(tmp_path, monkeypatch):
    paths = Paths(tmp_path); paths.ensure(); write_yaml(paths.config / 'settings.yaml', DEFAULTS)
    monkeypatch.setattr(pipeline, 'logger_for', lambda *a: lambda _: None)
    monkeypatch.setattr(pipeline, 'find_mihomo', lambda *a: 'fixture-core')
    monkeypatch.setattr(pipeline, 'validate_core', lambda *a: None)
    hosting = {identity(node()): ['GitHub 文件', 'Cloudflare 托管（第三方）']}
    monkeypatch.setattr(pipeline, 'fetch_all', lambda *a, **kw: ([node()], {'summary': {}, 'details': [], 'node_hosting': hosting}))
    calls = []
    def verify(core, nodes, *a):
        calls.append(nodes)
        return nodes, {'quality': {identity(n): {'quality': 'stable'} for n in nodes},
                       'delays_ms': {identity(n): 30 for n in nodes}, 'protocol_tested': len(nodes)}
    monkeypatch.setattr(pipeline, 'verify_nodes', verify)
    first = pipeline.execute(paths, 'acquire')
    assert not (paths.exports / 'current.json').exists()
    second = pipeline.execute(paths, 'acquire')
    assert len(calls) == 1 and second['verification']['reused'] == 1
    assert second['verified_at'] == first['verified_at']
    assert second['stability'][identity(node())]['recent'] == [True]
    third = pipeline.execute(paths, 'verify-library')
    assert len(calls) == 2 and third['stability'][identity(node())]['recent'] == [True, True]
    monkeypatch.setattr('nodeharbor.exporter.check_config', lambda *a: True)
    exported = pipeline.execute(paths, 'export')
    assert exported['verified_at'] == third['verified_at']
    assert all(r['node_hosting'] == hosting for r in (first, second, third, exported))
    before = (paths.data / 'library/free.json').read_bytes()
    history_before = (paths.data / 'state/node-history.json').read_bytes()
    def cancel(*a): raise Cancelled('fixture cancel')
    monkeypatch.setattr(pipeline, 'verify_nodes', cancel)
    with pytest.raises(Cancelled): pipeline.execute(paths, 'verify-library')
    assert (paths.data / 'library/free.json').read_bytes() == before
    assert (paths.data / 'state/node-history.json').read_bytes() == history_before
    def failed(core, nodes, *a):
        return [], {'outcomes': {identity(n): False for n in nodes}}
    monkeypatch.setattr(pipeline, 'verify_nodes', failed)
    with pytest.raises(AppError, match='没有节点'): pipeline.execute(paths, 'verify-library')
    assert (paths.data / 'library/free.json').read_bytes() == before
    assert pipeline.status(paths)['known_failed'] == 1
    monkeypatch.setattr(pipeline, 'verify_nodes', verify)
    result = pipeline.execute(paths, 'acquire', full_check=True)
    assert result['stability'][identity(node())]['recent'] == [True, True, False, True]
    assert pipeline.status(paths)['known_failed'] == 0


def test_long_run_does_not_refresh_early_probe_timestamp(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    key = identity(node())
    history = record_history(paths, {}, [node()], [node()], {'checked_at': {key: 5000}}, 'sig', now=10000)
    assert history[key]['last_passed'] == 5000
    checked, reused, _ = plan_checks([node()], [node()], {'verification': {'quality': {key: {'quality': 'stable'}}}}, history, 'sig', now=10000)
    assert checked and not reused


def test_export_age_is_based_on_verification_not_generation():
    now = datetime.now(timezone.utc)
    report = {'exported_at': now.isoformat(), 'verified_at': (now - timedelta(hours=2)).isoformat()}
    assert verification_stale(report, now)
    assert verification_stale({'verified_at': 'invalid'}, now)
    assert verification_stale({'verified_at': (now + timedelta(hours=2)).isoformat()}, now)
    assert not verification_stale({'verified_at': now.isoformat()}, now)


def test_fetch_external_single_segment_and_deduplicated_hosting(monkeypatch):
    from nodeharbor.fetcher import fetch_all
    from nodeharbor.tasks import TaskControl
    settings = deepcopy(DEFAULTS)
    github = 'https://raw.githubusercontent.com/example/repo/main/sub'
    cloudflare = 'https://sample.workers.dev/sub'
    other = 'https://sample.example.com/sub'
    settings['fetch'].update(github_sources=[], node_pools=[dict(name='example/repo', branch='main', enabled=True,
                                                                  urls=[github, cloudflare, other])])
    batches = {github: [node(1), node(2)], cloudflare: [node(1), node(3)], other: [node(1)]}
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', lambda url, *a: batches[url])
    found, report = fetch_all(settings, lambda _: None, TaskControl())
    assert len(found) == 3 and report['sources'] == 3 and report['github_sources'] == 1
    assert set(report['node_hosting'][identity(node(1))]) == {'GitHub 文件', 'Cloudflare 托管（第三方）', '其他公开订阅'}
    assert report['node_hosting'][identity(node(2))] == ['GitHub 文件']
    assert report['node_hosting'][identity(node(3))] == ['Cloudflare 托管（第三方）']
    assert all(source['name'].startswith('example/repo') for source in report['details'])


def test_hosting_does_not_guess_custom_domains_or_legacy_node_evidence():
    from nodeharbor.public_links import link_kind, node_hosting
    assert link_kind('https://custom.example.com/sub') == '其他公开订阅'
    assert link_kind('https://fake.workers.dev.example.com/sub') == '其他公开订阅'
    assert node_hosting({'node_sources': {'id': ['example/repo']}}) == {}
    assert node_hosting({'node_hosting': {'id': ['Cloudflare 托管（第三方）', 'invalid']}}) == {'id': ['Cloudflare 托管（第三方）']}
