from copy import deepcopy
import logging
import time

import pytest
import requests

from nodeharbor.config import DEFAULTS
from nodeharbor.fetcher import SourcesUnavailable, download_with_report, fetch_all, fetch_one, stale_cache
from nodeharbor.nodes import identity
from nodeharbor.public_sources import GITHUB_POOLS, PRESETS, public_fallbacks
from nodeharbor.source_plan import fair_candidates, priority, source_key, summary_text, verification_sources
from nodeharbor.storage import Paths, read_json, write_json, write_yaml
from nodeharbor.tasks import Cancelled, NetworkPool, TaskControl


def node(index=0):
    return dict(name=f'Fixture {index}', type='ss', server=f'fixture{index}.example.com', port=443,
                cipher='aes-256-gcm', password='fictional-test')


def test_fair_selection_deduplicates_and_reserves_space_for_small_sources():
    batches = [[node(i) for i in range(50)], [node(0), node(99)], [node(100)]]
    chosen, counts = fair_candidates(batches, 5, 3, {identity(node(2)): 10})
    assert chosen[0] == node(2) and node(99) in chosen and node(100) in chosen
    assert len({identity(n) for n in chosen}) == 5 and max(counts) <= 3
    assert sum(counts) == 5


def test_source_ranking_learns_actual_verification_and_retains_deferred_evidence():
    good = dict(node_ids=[identity(node())]); deferred = dict(node_ids=[], last_eligible=4, last_verified=2)
    report = dict(details=[good, deferred])
    verification_sources(report, [node()], [], {'quality': {identity(node()): {}}})
    assert good['last_verified'] == 0 and good['tested'] == 1
    assert deferred['last_verified'] == 2
    assert priority(deferred) < priority({}) < priority(good)
    assert priority({'last_eligible': 'corrupt'}) == priority({})


def test_stale_cache_retains_original_timestamp_and_is_age_limited(tmp_path, monkeypatch):
    url = GITHUB_POOLS[0][1]; saved = time.time() - 7200
    path = tmp_path / (source_key(url) + '.json')
    write_json(path, dict(saved_at=saved, result=dict(nodes=[node()], route='直连', channel='原始文件')))
    before = path.read_bytes()
    options = dict(DEFAULTS['fetch'], _cache_folder=str(tmp_path), auto_local_proxy=False)
    monkeypatch.setattr('nodeharbor.fetcher.primary_proxies', lambda *a: {})
    monkeypatch.setattr('nodeharbor.fetcher.download_single', lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout()))
    result = download_with_report(url, options)
    assert result['stale'] and result['download_failed'] and result['fetched_at'] == saved
    assert path.read_bytes() == before
    assert stale_cache(url, dict(options, stale_cache_hours=1)) is None


def test_source_backoff_can_reuse_old_cache_without_network(tmp_path, monkeypatch):
    url = GITHUB_POOLS[0][1]; key = source_key(url)
    write_json(tmp_path / (key + '.json'), dict(saved_at=time.time()-3600,
               result=dict(nodes=[node()], route='直连', channel='原始文件')))
    write_json(tmp_path / 'health.json', {key: dict(next_retry=time.time()+300, failures=3)})
    settings = deepcopy(DEFAULTS)
    settings['fetch'].update(github_sources=[GITHUB_POOLS[0][0]], _cache_folder=str(tmp_path))
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', lambda *a: pytest.fail('Backoff must not download'))
    chosen, report = fetch_all(settings, lambda _: None)
    assert chosen == [node()] and report['summary']['stale_cache'] == 1
    assert report['details'][0]['failures'] == 3 and report['details'][0]['deferred']


def test_source_budget_reports_contribution_and_safe_live_events(monkeypatch):
    settings = deepcopy(DEFAULTS)
    settings['fetch'].update(github_sources=[p[0] for p in GITHUB_POOLS[:2]], max_candidates=4, nodes_per_source=2)
    def fetch(url, options, control):
        options['_on_progress']({'phase': 'reading', 'bytes': 1024})
        return [node(i) for i in (range(20) if url == GITHUB_POOLS[0][1] else [20])]
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', fetch)
    events = []; chosen, report = fetch_all(settings, lambda _: None, TaskControl(progress=events.append))
    assert len(chosen) == 3 and node(20) in chosen
    assert report['summary']['not_selected'] == 18
    assert all('node_ids' not in source for event in events for source in event.get('sources', []))
    assert any('KiB' in source['status'] for e in events for source in e.get('sources', []))


def test_pool_streams_progress_and_reuses_worker():
    from test_regressions import local_source
    with local_source() as (url, _), NetworkPool(1, TaskControl()) as pool:
        events = []
        options = dict(DEFAULTS['fetch'], _pool=pool, _stream_progress=True, _on_progress=events.append)
        assert fetch_one(url, options)[0]['name'] == 'local'
        process = pool.workers[0][0]
        assert fetch_one(url, options)[0]['name'] == 'local' and pool.workers[0][0] is process
        assert {'connecting', 'reading', 'parsing'} <= {e['phase'] for e in events}
        assert 'fictional-test' not in str(events)
    assert process.poll() is not None


@pytest.mark.parametrize('cancel', [False, True])
def test_history_fallback_is_explicit_capped_and_never_hides_cancellation(tmp_path, monkeypatch, cancel):
    from nodeharbor.initialize import initialize
    from nodeharbor.pipeline import execute
    from nodeharbor.exporter import publish
    paths = Paths(tmp_path); initialize(paths)
    settings = deepcopy(DEFAULTS); settings['verification']['core_client'] = 'v2rayN'
    settings['fetch']['max_candidates'] = 3
    write_yaml(paths.config / 'settings.yaml', settings)
    publish(paths, [node(i) for i in range(6)], {})
    before = paths.current()
    monkeypatch.setattr('nodeharbor.pipeline.find_core', lambda *a: 'fake-core')
    monkeypatch.setattr('nodeharbor.pipeline.validate_core', lambda *a: None)
    def fail(*a, **kw):
        if cancel: raise Cancelled()
        raise SourcesUnavailable({'summary': {'failed': 1}, 'details': []})
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', fail)
    monkeypatch.setattr('nodeharbor.xray_verifier.verify_xray', lambda core, nodes, *a: (nodes, {'protocol_tested': len(nodes)}))
    try:
        if cancel:
            with pytest.raises(Cancelled): execute(paths)
            assert paths.current() == before
        else:
            result = execute(paths)
            assert result['counts']['free'] == 3
            summary = read_json(paths.current() / 'report.json')['fetch']['summary']
            assert summary['data_origin'] == 'history_only' and '没有取得新数据' in summary_text(summary)
    finally:
        logger = logging.getLogger('nodeharbor.' + str(paths.root))
        for handler in list(logger.handlers): handler.close(); logger.removeHandler(handler)


def test_presets_and_slash_branch_have_exact_public_paths():
    assert [len(value[0]) for value in PRESETS.values()] == [6, 16, 26]
    url = dict(GITHUB_POOLS)['Barabama/FreeNodes']
    assert public_fallbacks(url) == ['https://api.github.com/repos/Barabama/FreeNodes/contents/nodes/cfmem.yaml?ref=feat%2Fai-crawler-v2']
    assert public_fallbacks(url + '?token=private') == []


def test_api_gate_waits_for_contention_with_fresh_lock_contexts(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from nodeharbor.fetcher import api_gate
    from nodeharbor.storage import AppError
    attempts, released = [], []
    @contextmanager
    def busy_lock(path):
        attempts.append(path)
        if len(attempts) < 3: raise AppError('busy')
        try: yield
        finally: released.append(path)
    monkeypatch.setattr('nodeharbor.fetcher.file_lock', busy_lock)
    monkeypatch.setattr('nodeharbor.fetcher.time.sleep', lambda _: None)
    with api_gate('https://api.github.com/repos/example/pool/contents/sub', {'_cache_folder': str(tmp_path)}):
        assert len(attempts) == 3 and not released
    assert len(released) == 1
