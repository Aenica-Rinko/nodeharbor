import json
import time
import tkinter as tk
from pathlib import Path

import pytest

from nodeharbor import github_search as search
from nodeharbor.storage import AppError, Paths
from nodeharbor.tasks import TaskControl


@pytest.mark.parametrize('query', ['', ' ', 'x' * 161, 'free\nv2ray'])
def test_query_validation(query):
    with pytest.raises(AppError): search.clean_query(query)


def test_search_only_returns_public_repositories(monkeypatch):
    calls = []
    def api(path, params, options):
        calls.append((path, params))
        return {'incomplete_results': True, 'items': [
            {'full_name': 'sample/public', 'default_branch': 'main', 'private': False},
            {'full_name': 'sample/private', 'default_branch': 'main', 'private': True},
            {'full_name': '../invalid', 'default_branch': 'main', 'private': False}, None]}
    monkeypatch.setattr(search, 'api_json', api)
    result = search.perform({'mode': 'search', 'value': 'free v2ray'})
    assert [r['name'] for r in result['repositories']] == ['sample/public']
    assert result['incomplete']
    assert 'is:public' in calls[0][1]['q'] and calls[0][1]['per_page'] == 20


def test_candidates_are_bounded_and_urls_encoded():
    entries = [dict(type='blob', path=p, size=100) for p in
               ['clash.yaml', 'README.txt', '.github/ci.yml', 'docs/config.yaml',
                'app.py', 'sub', 'nodes/免费 #1.txt']]
    entries += [dict(type='blob', path='linked.yaml', size=100, mode='120000')]
    result = search.candidate_files({'tree': entries, 'truncated': True}, 'sample/repo', 'feature/data')
    assert {f['path'] for f in result['files']} == {'clash.yaml', 'sub', 'nodes/免费 #1.txt'}
    assert result['limited']
    assert all('feature%2Fdata/' in f['url'] for f in result['files'])
    assert '%23' in next(f['url'] for f in result['files'] if '#' in f['path'])
    many = search.candidate_files({'tree': [dict(type='blob', path=f'{i}.txt', size=10) for i in range(130)]}, 'a/b', 'main')
    assert len(many['files']) == 100 and many['limited']


def test_tree_rechecks_visibility_and_escapes_branch(monkeypatch):
    calls = []
    def api(path, params, options):
        calls.append(path)
        return {'private': False} if len(calls) == 1 else {'tree': []}
    monkeypatch.setattr(search, 'api_json', api)
    assert search.perform({'mode': 'files', 'value': {'name': 'a/b', 'branch': 'feature/data'}})['files'] == []
    assert calls == ['repos/a/b', 'repos/a/b/git/trees/feature%2Fdata']
    monkeypatch.setattr(search, 'api_json', lambda *a: {'private': True})
    assert search.perform({'mode': 'files', 'value': {'name': 'a/b', 'branch': 'main'}})['error'] == 'not_found'


@pytest.mark.parametrize('status,error', [(403, 'rate_limit'), (429, 'rate_limit'), (404, 'not_found'), (422, 'query')])
def test_api_status_is_safe_and_anonymous(monkeypatch, status, error):
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert not self.trust_env and not kwargs['allow_redirects']
            assert 'Authorization' not in kwargs['headers']
            return Response()
    monkeypatch.setattr(search.requests, 'Session', Session)
    assert search.api_json('search/repositories', {}, {}) == {'error': error}


def test_discovery_reports_failure_without_response_or_credentials(monkeypatch):
    monkeypatch.setattr(search, 'network_job', lambda *a, **kw: {'error': 'rate_limit', 'body': 'secret'})
    with pytest.raises(AppError, match='限制') as error:
        search.discover('search', 'free', {}, TaskControl())
    assert 'secret' not in str(error.value)


def test_source_search_ui_add_dedup_and_cancel(tmp_path, monkeypatch):
    from nodeharbor.desktop import Desktop
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *a: errors.append(a)
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: errors.append(a))
    app = Desktop(root, Paths(tmp_path))
    def pump():
        deadline = time.monotonic() + 4
        while app.busy and time.monotonic() < deadline:
            root.update(); time.sleep(.02)
        root.update()
        assert not app.busy
    try:
        app.show('sources')
        before = (tmp_path / 'config/settings.yaml').read_bytes()
        app.open_source_search(); dialog = app.search_dialog
        def discover(mode, value, options, control):
            control.emit('search', message='test')
            if mode == 'search': return {'mode': 'search', 'incomplete': False, 'repositories':
                [{'name': 'example/free', 'branch': 'main', 'updated': '2026-01-01', 'description': 'Public test'}]}
            return {'mode': 'files', 'repository': 'example/free', 'limited': False,
                    'files': [{'path': 'clash.yaml', 'url': 'https://raw.githubusercontent.com/example/free/main/clash.yaml', 'size': 30}]}
        monkeypatch.setattr('nodeharbor.source_search_dialog.discover', discover)
        assert dialog.query.get() == ''
        dialog.query.set('free v2ray')
        dialog.search(); pump()
        assert dialog.repo_tree.get_children() == ('0',)
        assert not app.progress_counts.get()
        dialog.repo_tree.selection_set('0'); root.update()
        dialog.join_button.invoke(); root.update()
        assert len(app.custom_pools) == 1 and not app.custom_pools[0]['enabled']
        assert 'pool-0' in app.sources_tree.get_children()
        dialog.inspect(); pump()
        dialog.file_tree.selection_set('0'); root.update()
        dialog.add(); dialog.add()
        assert len(app.custom_pools) == 1 and len(app.custom_pools[0]['urls']) == 1
        assert not app.custom_pools[0]['enabled']
        assert (tmp_path / 'config/settings.yaml').read_bytes() != before
        def slow(mode, value, options, control):
            while True:
                control.check(); time.sleep(.01)
        monkeypatch.setattr('nodeharbor.source_search_dialog.discover', slow)
        dialog.search()
        assert app.progress_cancel_button.cget('state') == 'normal'
        dialog.cancel_button.invoke(); pump()
        assert app.task_progress.status == 'cancelled'
        assert dialog.search_button.cget('state') == 'normal'
        assert len(app.custom_pools) == 1 and len(app.custom_pools[0]['urls']) == 1
        assert not app.custom_pools[0]['enabled']
        assert not errors
        dialog.close()
        app.sources_tree.selection_set('pool-0'); app.toggle_public_source()
        app.show('sources')
        assert app.custom_pools[0]['enabled']
    finally:
        if app.control: app.control.cancel()
        app.close()
