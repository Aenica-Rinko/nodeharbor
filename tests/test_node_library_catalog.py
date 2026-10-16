from copy import deepcopy
import logging

import pytest

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.exporter import publish
from nodeharbor.fetcher import fetch_all
from nodeharbor.node_library import read_library, save_library
from nodeharbor.pipeline import execute, status
from nodeharbor.progress import TaskProgress
from nodeharbor.source_catalog import add_pool, enabled_urls, user_pools, validate_pools
from nodeharbor.storage import AppError, Paths, read_yaml, write_yaml
from nodeharbor.tasks import Cancelled, TaskControl


def node(name='New'):
    return dict(name=name, type='ss', server='example.com', port=443, cipher='aes-256-gcm', password='fixture-only')


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / 'settings.yaml', DEFAULTS)
    monkeypatch.setattr('nodeharbor.pipeline.find_mihomo', lambda *a: 'fixture-core')
    monkeypatch.setattr('nodeharbor.pipeline.validate_core', lambda *a: 'mihomo')
    monkeypatch.setattr('nodeharbor.exporter.check_config', lambda *a: True)
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', lambda *a, **kw: ([node()], {'summary': {}, 'details': []}))
    monkeypatch.setattr('nodeharbor.pipeline.verify_nodes', lambda core, candidates, *a: (candidates, {}))
    yield paths
    logger = logging.getLogger('nodeharbor.' + str(paths.root))
    for handler in list(logger.handlers): handler.close(); logger.removeHandler(handler)


def snapshot_exports(paths):
    return {str(p.relative_to(paths.exports)): p.read_bytes() for p in paths.exports.rglob('*') if p.is_file()}


def test_acquire_does_not_export_then_explicit_export_uses_library(pipeline):
    paths = pipeline
    result = execute(paths, 'acquire')
    assert result['counts']['free'] == 1
    assert not (paths.exports / 'current.json').exists()
    assert read_library(paths)[0] == [node()]
    assert status(paths)['export_pending']
    exported = execute(paths, 'export')
    assert exported['library_id'] == result['library_id']
    assert not status(paths)['export_pending']
    assert read_yaml(paths.current() / 'free-nodes.yaml')['proxies'] == [node()]


def test_acquire_and_recheck_preserve_existing_client_exports(pipeline):
    paths = pipeline
    publish(paths, [node('Old')], {})
    before = snapshot_exports(paths)
    execute(paths, 'acquire')
    assert snapshot_exports(paths) == before
    execute(paths, 'verify-library')
    assert snapshot_exports(paths) == before
    assert read_library(paths)[0] == [node()]
    assert status(paths)['export_pending']


@pytest.mark.parametrize('cancel', [True, False])
def test_failed_or_cancelled_acquisition_preserves_library_and_export(pipeline, monkeypatch, cancel):
    paths = pipeline
    execute(paths, 'acquire'); execute(paths, 'export')
    before_library = (paths.data / 'library/free.json').read_bytes()
    before_exports = snapshot_exports(paths)
    def fail(*args, **kwargs):
        raise Cancelled('cancelled') if cancel else AppError('fetch failed')
    monkeypatch.setattr('nodeharbor.pipeline.verify_nodes', fail)
    with pytest.raises(AppError): execute(paths, 'acquire')
    assert (paths.data / 'library/free.json').read_bytes() == before_library
    assert snapshot_exports(paths) == before_exports


def test_library_commit_is_complete_even_if_cancel_arrives_after_write(pipeline, monkeypatch):
    from nodeharbor.storage import write_json
    control = TaskControl()
    def write(path, data): write_json(path, data); control.cancel()
    monkeypatch.setattr('nodeharbor.node_library.write_json', write)
    result = execute(pipeline, 'acquire', control=control)
    assert result['library_id'] and status(pipeline)['last_run']['status'] == 'success'


def test_acquire_repairs_corrupt_library_without_touching_exports(pipeline):
    paths = pipeline
    execute(paths, 'acquire'); execute(paths, 'export')
    before = snapshot_exports(paths)
    (paths.data / 'library/free.json').write_text('{bad', encoding='utf-8')
    execute(paths, 'acquire')
    assert read_library(paths)[0] == [node()]
    assert snapshot_exports(paths) == before


def test_new_pools_are_disabled_until_explicitly_enabled(monkeypatch):
    options = deepcopy(DEFAULTS['fetch']); options['github_sources'] = []
    url = 'https://raw.githubusercontent.com/example/pool/main/sub'
    pools, added = add_pool(options, {'name': 'example/pool'}, [url])
    assert added and not pools[0]['enabled']
    options['node_pools'] = pools
    assert enabled_urls(options) == []
    with pytest.raises(AppError, match='没有启用'): fetch_all({'fetch': options}, lambda _: None)
    options['node_pools'][0]['enabled'] = True
    calls = []
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', lambda url, *a: calls.append(url) or [node()])
    result, _ = fetch_all({'fetch': options}, lambda _: None)
    assert result == [node()] and calls == [url]
    pools, added = add_pool(options, {'name': 'EXAMPLE/pool'}, [url])
    assert not added and len(pools) == 1 and pools[0]['enabled'] and pools[0]['urls'] == [url]


def test_repository_without_files_can_be_saved_but_not_enabled(tmp_path):
    pools, _ = add_pool(DEFAULTS['fetch'], {'name': 'example/pool'})
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / 'settings.yaml', {'fetch': {'node_pools': pools}})
    assert not load_settings(paths)['fetch']['node_pools'][0]['enabled']
    pools[0]['enabled'] = True
    with pytest.raises(AppError, match='选择订阅文件'): validate_pools(pools)


def test_legacy_manual_sources_keep_enabled_state():
    url = 'https://raw.githubusercontent.com/example/pool/main/sub'
    pools = user_pools({'public_sources': [url, url]})
    assert len(pools) == 1 and pools[0]['urls'] == [url] and pools[0]['enabled']


def test_repo_cannot_claim_another_repositories_files():
    with pytest.raises(AppError, match='对应'):
        add_pool({}, {'name': 'example/pool'}, ['https://raw.githubusercontent.com/other/repo/main/sub'])


def test_acquisition_progress_never_promises_export():
    assert 'export' not in TaskProgress('acquire').steps
    assert 'export' not in TaskProgress('verify-library').steps
    assert list(TaskProgress('export').steps) == ['export']


@pytest.mark.parametrize('action', ['refresh', 'refresh-github', 'verify'])
def test_combined_cli_commands_share_the_latest_library(pipeline, monkeypatch, action):
    paths = pipeline
    publish(paths, [node('Old export')], {})
    fresh = dict(node('Latest library'), password='latest-fixture')
    save_library(paths, [fresh], {}, TaskControl())
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', lambda *a, **kw: ([fresh], {'summary': {}, 'details': []}))
    result = execute(paths, action)
    assert read_library(paths)[0] == [fresh]
    assert result['library_id'] == read_library(paths)[1]['library_id']
    assert not status(paths)['export_pending']
    execute(paths, 'export')
    assert read_yaml(paths.current() / 'free-nodes.yaml')['proxies'] == [fresh]


def test_cli_export_failure_keeps_verified_library_for_retry(pipeline, monkeypatch):
    paths = pipeline
    publish(paths, [node('Old export')], {})
    before = snapshot_exports(paths)
    def reject(*a, **kw): raise AppError('export rejected')
    with monkeypatch.context() as patch:
        patch.setattr('nodeharbor.pipeline.publish', reject)
        with pytest.raises(AppError, match='export rejected'): execute(paths, 'refresh-github')
    assert snapshot_exports(paths) == before
    assert read_library(paths)[0] == [node()] and status(paths)['export_pending']
    execute(paths, 'export')
    assert not status(paths)['export_pending']


@pytest.mark.parametrize('action', ['acquire', 'export'])
def test_diagnostic_write_failure_does_not_reverse_a_success(pipeline, monkeypatch, action):
    from nodeharbor.storage import write_json
    execute(pipeline, 'acquire')
    def fail_final_state(path, data):
        if path.name == 'last-run.json' and data['status'] != 'running': raise OSError('fixture disk failure')
        write_json(path, data)
    monkeypatch.setattr('nodeharbor.pipeline.write_json', fail_final_state)
    result = execute(pipeline, action)
    assert result['library_id'] == read_library(pipeline)[1]['library_id']
    if action == 'export': assert not status(pipeline)['export_pending']


def test_diagnostic_write_failure_does_not_hide_the_original_error(pipeline, monkeypatch):
    from nodeharbor.storage import write_json
    def fail_final_state(path, data):
        if path.name == 'last-run.json' and data['status'] != 'running': raise OSError('fixture disk failure')
        write_json(path, data)
    def fail_fetch(*a, **kw): raise AppError('original fetch error')
    monkeypatch.setattr('nodeharbor.pipeline.write_json', fail_final_state)
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', fail_fetch)
    with pytest.raises(AppError, match='original fetch error'): execute(pipeline, 'acquire')
