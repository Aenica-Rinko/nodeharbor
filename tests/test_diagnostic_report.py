import json
import queue
import threading

import pytest

from nodeharbor.diagnostic_report import build_report, export_report
from nodeharbor.initialize import initialize
from nodeharbor.config import load_settings
from nodeharbor.source_plan import source_key
from nodeharbor.storage import AppError, Paths, write_json, write_yaml
from nodeharbor.task_runner import TaskRunner


def test_report_excludes_private_values_in_every_untrusted_field(tmp_path, monkeypatch):
    paths = Paths(tmp_path / 'private-home-sentinel'); initialize(paths)
    secret = 'DO-NOT-EXPORT-SECRET-59324'
    repo = 'private-owner/private-repository'
    url = f'https://raw.githubusercontent.com/{repo}/main/sub.yaml'
    settings = load_settings(paths)
    settings['fetch'].update(proxy='http://user:' + secret + '@private-proxy.example:9999',
                             node_pools=[dict(name=repo, branch='main', enabled=True, urls=[url])])
    settings['verification']['core_paths']['Mihomo'] = str(tmp_path / secret)
    write_yaml(paths.config / 'settings.yaml', settings)
    state = dict(name=repo, status=secret, nodes=12, failures=2, verified=4,
                 failure_reason='DNS ' + secret, route=secret, channel=secret,
                 node_ids=[secret], last_success='not-a-date-' + secret,
                 attempts=[dict(error_code='network', http_status=503, url=url, route=secret),
                           dict(error_code=secret, http_status=secret)])
    write_json(paths.data / 'source-cache/health.json', {source_key(url): state})
    write_json(paths.data / 'state/last-run.json', dict(action=secret, status=secret, error='核心错误 ' + secret,
        summary=dict(parsed=20, failed=secret, nested={'token': secret})))
    write_json(paths.data / 'library/free.json', {'nodes': [{'password': secret, 'server': 'private-server.example', 'uuid': secret}],
                                               'report': {'node_sources': {secret: [repo]}}})
    (paths.logs / 'app.log').write_text('raw-log-sentinel ' + secret)
    # Report generation is offline, even when a configured proxy is present.
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **kw: pytest.fail('Unexpected network request'))
    target = export_report(paths)
    raw = target.read_text(encoding='utf-8')
    for marker in (secret, repo, url, str(tmp_path), 'private-server.example', 'private-proxy.example', 'raw-log-sentinel', source_key(url)):
        assert marker not in raw
    report = json.loads(raw)
    custom = next(s for s in report['sources'] if s['id'] == 'custom-1/file-1')
    assert custom['nodes'] == 12 and custom['error_category'] == 'network'
    assert custom['attempts'][0]['http_status'] == 503
    assert custom['attempts'][1] == {'error_code': 'unknown', 'http_status': None}
    assert report['last_run']['error_category'] == 'core' and report['library']['nodes'] == 1
    assert target.parent == paths.logs / 'diagnostics'
    assert export_report(paths) != target


def test_corrupt_records_still_produce_report_without_error_text(tmp_path):
    paths = Paths(tmp_path); initialize(paths)
    (paths.config / 'settings.yaml').write_text('invalid: [not-valid')
    write_json(paths.data / 'state/last-run.json', {'action': [], 'status': {}, 'error': ['private']})
    (paths.data / 'library').mkdir(); (paths.data / 'library/free.json').write_bytes(b'\xff\xfe')
    report = build_report(paths)
    assert report['record_status']['settings'] == 'invalid'
    assert report['record_status']['library'] == 'unreadable'
    assert report['last_run']['action'] == 'unknown'


def test_task_runner_can_cancel_without_tk_and_does_not_leak_unknown_exceptions():
    events = queue.Queue(); runner = TaskRunner(events)
    started = threading.Event()
    def wait(control):
        started.set(); control.event.wait(3); control.check()
    control = runner.start(wait)
    assert started.wait(2)
    with pytest.raises(AppError): runner.start(wait)
    control.cancel(); runner.thread.join(3)
    assert events.get_nowait()[0] == 'cancelled'
    runner.start(lambda _: (_ for _ in ()).throw(ValueError('secret-path-and-password')))
    runner.thread.join(3)
    assert events.get_nowait() == ('error', 'ValueError：执行失败')
    runner.start(lambda control: (control.emit('download', 1, 1, 'safe'), 'ok')[1])
    runner.thread.join(3)
    assert events.get_nowait()[0] == 'progress'
    assert events.get_nowait() == ('done', 'ok')
