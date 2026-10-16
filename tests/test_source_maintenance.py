from copy import deepcopy
import tkinter as tk

import pytest

from nodeharbor.config import load_settings
from nodeharbor.initialize import initialize
from nodeharbor.source_catalog import add_pool, remove_pool, replace_pool, update_catalog, pool_health
from nodeharbor.source_plan import source_key
from nodeharbor.storage import AppError, Paths, app_lock, write_yaml, write_json

URL = 'https://raw.githubusercontent.com/example/free/main/sub.yaml'
NEW = 'https://raw.githubusercontent.com/example/free/main/new.yaml'
POOL = dict(name='example/free', branch='main', enabled=True, urls=[URL])


@pytest.fixture
def paths(tmp_path):
    paths = Paths(tmp_path); initialize(paths)
    settings = load_settings(paths)
    settings['fetch']['node_pools'] = [deepcopy(POOL)]
    write_yaml(paths.config / 'settings.yaml', settings)
    return paths


def test_replace_disables_only_changed_files_and_validates_before_write(paths):
    options = load_settings(paths)['fetch']
    assert replace_pool(options, POOL['name'], [URL, URL])[0]['enabled']
    old = (paths.config / 'settings.yaml').read_bytes()
    with pytest.raises(AppError, match='属于'):
        update_catalog(paths, lambda f: replace_pool(f, POOL['name'], ['https://raw.githubusercontent.com/other/repo/main/sub']))
    assert (paths.config / 'settings.yaml').read_bytes() == old
    changed = update_catalog(paths, lambda f: replace_pool(f, POOL['name'], [NEW], expected=POOL))
    assert changed[0]['urls'] == [NEW] and changed[0]['enabled'] is False
    with pytest.raises(AppError, match='修改'):
        update_catalog(paths, lambda f: replace_pool(f, POOL['name'], [URL], expected=POOL))
    cleared = update_catalog(paths, lambda f: replace_pool(f, POOL['name'], []))
    assert cleared[0]['urls'] == [] and not cleared[0]['enabled']


def test_delete_migrated_source_cannot_reappear_and_preserves_nodes(paths):
    settings = load_settings(paths)
    settings['fetch'].update(public_sources=[URL], node_pools=[])
    write_yaml(paths.config / 'settings.yaml', settings)
    write_json(paths.data / 'library/free.json', {'nodes': [{'private': 'fixture'}], 'report': {}})
    before = (paths.data / 'library/free.json').read_bytes()
    assert update_catalog(paths, lambda f: remove_pool(f, POOL['name'])) == []
    settings = load_settings(paths)
    assert settings['fetch']['public_sources'] == [] and settings['fetch']['node_pools'] == []
    assert (paths.data / 'library/free.json').read_bytes() == before
    assert settings['exports']['mihomo_port'] == 7890
    with pytest.raises(AppError): update_catalog(paths, lambda f: remove_pool(f, POOL['name']))


def test_catalog_changes_respect_other_task_lock(paths):
    before = (paths.config / 'settings.yaml').read_bytes()
    with app_lock(paths):
        with pytest.raises(AppError, match='另一项更新'):
            update_catalog(paths, lambda f: remove_pool(f, POOL['name']))
    assert (paths.config / 'settings.yaml').read_bytes() == before


def test_health_matches_files_after_reordering_and_excludes_retired_files():
    health = {source_key(URL): dict(nodes=10, verified=4, last_success=1000),
              source_key(NEW): dict(nodes=0, failures=2, failure_reason='网络失败', last_success=900)}
    pool = dict(POOL, urls=[NEW, URL])
    summary, records = pool_health(pool, health)
    assert records[0]['failures'] == 2 and summary['last_success'] == 1000
    assert summary['status'] == '1 个文件下载失败' and summary['verified'] is None
    summary, _ = pool_health(dict(POOL, urls=[URL]), health)
    assert summary['failures'] == 0 and summary['verified'] == 4


def test_maintenance_ui_replace_delete_cancel_and_diagnostic_flow(paths, monkeypatch):
    from nodeharbor.desktop import Desktop
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw()
    callbacks, messages = [], []
    root.report_callback_exception = lambda *args: callbacks.append(args)
    app = Desktop(root, paths)
    monkeypatch.setattr('tkinter.messagebox.showinfo', lambda *args, **kw: messages.append(args))
    monkeypatch.setattr('tkinter.messagebox.showerror', lambda *args, **kw: pytest.fail(str(args)))
    try:
        app.show('sources'); app.sources_tree.selection_set('pool-0'); root.update()
        app.edit_public_source(); root.update()
        editor_window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        editor = next(w for w in widgets(editor_window) if isinstance(w, tk.Text))
        editor.delete('1.0', 'end'); editor.insert('1.0', NEW)
        next(w for w in widgets(editor_window) if isinstance(w, tk.Button) and w.cget('text') == '保存文件列表').invoke()
        root.update()
        assert app.custom_pools[0]['urls'] == [NEW] and not app.custom_pools[0]['enabled']
        app.open_source_search(app.custom_pools[0], replace=True); root.update()
        dialog = app.search_dialog
        assert dialog.search_button.cget('state') == 'disabled'
        dialog.receive({'mode': 'files', 'repository': POOL['name'], 'limited': False,
                        'files': [{'path': 'sub.yaml', 'url': URL, 'size': 20}]})
        dialog.file_tree.selection_set('0'); root.update(); dialog.add(); dialog.add()
        assert app.custom_pools[0]['urls'] == [URL]
        dialog.close()
        monkeypatch.setattr('tkinter.messagebox.askyesno', lambda *a, **kw: False)
        app.remove_public_source(); assert len(app.custom_pools) == 1
        monkeypatch.setattr('tkinter.messagebox.askyesno', lambda *a, **kw: True)
        app.remove_public_source(); assert not app.custom_pools
        app.show('logs'); app.export_diagnostics()
        app.runner.thread.join(5)
        assert not app.runner.thread.is_alive()
        # Flush queued worker events deterministically; keep just one scheduled poll.
        root.after_cancel(app.poll_id); app.poll(); root.update()
        assert not app.busy and app.last_diagnostic_path.is_file() and messages
        assert '导出' not in app.progress_guidance.get() or '报告' in app.progress_guidance.get()
        assert not callbacks
    finally:
        if app.busy: app.runner.thread.join(5); app.busy = False
        app.close()
