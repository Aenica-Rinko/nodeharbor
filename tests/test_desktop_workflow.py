"""User-facing workflow and scoped retry regressions with isolated data."""
from copy import deepcopy
import tkinter as tk

import pytest

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.desktop import Desktop
from nodeharbor.exporter import publish
from nodeharbor.fetcher import fetch_all
from nodeharbor.initialize import initialize
from nodeharbor.node_library import save_library
from nodeharbor.nodes import identity
from nodeharbor.public_sources import GITHUB_POOLS
from nodeharbor.source_plan import source_key
from nodeharbor.storage import Paths, read_json, write_json, write_yaml
from nodeharbor.tasks import TaskControl
from nodeharbor.ui.presentation import next_action


def node(name='Example', protocol='ss'):
    return dict(name=name, type=protocol, server='example.com', port=443,
                password='fixture', cipher='aes-256-gcm')


@pytest.mark.parametrize('count,pending,export,core,sources,expected', [
    (0, False, None, False, True, 'settings'),
    (0, False, None, True, False, 'sources'),
    (0, False, None, True, True, 'acquire'),
    (1, True, 'previous', False, False, 'subscriptions'),
    (1, False, None, False, False, 'subscriptions'),
    (1, False, 'current', True, True, 'subscriptions'),
    (1, False, 'current', False, False, 'subscriptions'),
])
def test_next_step_preserves_export_without_core(count, pending, export, core, sources, expected):
    snapshot = dict(library_count=count, export_pending=pending, export_dir=export)
    assert next_action(snapshot, core, sources)[0] == expected


def test_first_run_journey_acquires_then_explicitly_exports(desktop, monkeypatch):
    """Real UI/task/storage path; only network and core execution use fixtures."""
    import time
    app = desktop
    assert app.next_action_button.cget('text') == '配置检测核心'
    app.next_action_button.invoke()
    core = app.paths.root / 'fixture-core.exe'; core.write_text('test fixture, never executed')
    app.core_path.set(str(core)); app.save_settings()
    monkeypatch.setattr('nodeharbor.pipeline.validate_core', lambda *args: None)
    monkeypatch.setattr('nodeharbor.exporter.check_config', lambda *args: True)
    n = node(); key = identity(n)
    monkeypatch.setattr('nodeharbor.pipeline.fetch_all', lambda *args, **kwargs: ([n], {
        'unique': 1, 'sources': 1, 'details': [], 'summary': {}, 'node_sources': {key: ['example/pool']}}))
    monkeypatch.setattr('nodeharbor.pipeline.verify_nodes', lambda *args: ([n], {
        'protocol_tested': 1, 'stable': 1, 'quality': {key: {'quality': 'stable'}}, 'delays_ms': {key: 12}}))
    monkeypatch.setattr('nodeharbor.ui.activity.messagebox.showerror', lambda *a, **kw: pytest.fail(str(a)))
    app.show('sources'); app.choose_public_sources([])
    app.add_catalog_repository(dict(name='example/pool', branch='main'), ['https://raw.githubusercontent.com/example/pool/main/sub'])
    assert not app.custom_pools[0]['enabled']
    app.sources_tree.selection_set('pool-0'); app.toggle_public_source()
    def finish():
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            app.root.update(); time.sleep(.01)
        assert not app.busy and app.task_progress.status == 'success'
    app.fetch_public_sources(); finish()
    from nodeharbor.pipeline import status
    assert status(app.paths)['library_count'] == 1 and not status(app.paths)['export_dir']
    app.show('overview'); assert app.next_action_button.cget('text') == '生成 / 更新导出文件'
    app.next_action_button.invoke(); app.export_action_buttons[1].invoke(); finish()
    assert all((app.paths.client_exports(c) / f).is_file() for c, f in
               [('Mihomo', 'free.yaml'), ('Clash-Verge-Rev', 'free.yaml'), ('v2rayN', 'free.txt')])
    app.show('overview'); assert app.next_action_button.cget('text') == '导入客户端使用'
    copied = []; app.copy = copied.append; app.copy_export_links()
    assert copied and copied[0].startswith('ss://')


def test_v2rayn_copy_uses_committed_links_and_rejects_zero_compatible_nodes(desktop):
    from nodeharbor.storage import AppError
    app = desktop; folder, _ = publish(app.paths, [node()], {})
    (app.paths.client_exports('v2rayN') / 'free.txt').write_text('incomplete convenience file')
    copied = []; app.copy = copied.append; app.copy_export_links()
    assert copied == [(folder / 'free.v2ray.txt').read_text(encoding='utf-8')]
    publish(app.paths, [dict(node(), type='http')], {})
    with pytest.raises(AppError, match='暂不能转换'): app.copy_export_links()
    assert len(copied) == 1


def test_retry_downloads_only_failed_enabled_files_and_preserves_other_data(tmp_path, monkeypatch):
    paths = Paths(tmp_path); initialize(paths)
    settings = load_settings(paths)
    good, bad, disabled = [url for _, url in GITHUB_POOLS[:3]]
    custom_good = 'https://raw.githubusercontent.com/example/pool/main/good.yaml'
    custom_bad = 'https://raw.githubusercontent.com/example/pool/main/bad.yaml'
    settings['fetch'].update(github_sources=[name for name, _ in GITHUB_POOLS[:2]],
        node_pools=[dict(name='example/pool', branch='main', enabled=True, urls=[custom_good, custom_bad])])
    write_yaml(paths.config / 'settings.yaml', settings)
    report = save_library(paths, [node()], {}, TaskControl())
    publish(paths, [node()], report)
    protected = {p: p.read_bytes() for folder in (paths.config, paths.data / 'library', paths.exports)
                 for p in folder.rglob('*') if p.is_file()}
    healthy = dict(name=GITHUB_POOLS[0][0], nodes=3, failures=0, status='成功', verified=2)
    health = {source_key(good): healthy, source_key(bad): dict(failures=2),
              source_key(disabled): dict(failures=3), source_key(custom_good): dict(failures=0),
              source_key(custom_bad): dict(failures=1)}
    write_json(paths.data / 'source-cache/health.json', health)
    write_json(paths.data / 'state/public-sources.json', {'sources': [healthy]})
    called = []
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one', lambda url, options, control: called.append(url) or [node()])
    app = Desktop.__new__(Desktop)
    app.paths, app.busy = paths, False
    import queue
    app.events = queue.Queue()
    app.start_task = lambda action, work: work(TaskControl())
    app.retry_failed_sources()
    assert set(called) == {bad, custom_bad}
    assert read_json(paths.data / 'source-cache/health.json')[source_key(good)] == healthy
    assert healthy in read_json(paths.data / 'state/public-sources.json')['sources']
    assert all(p.read_bytes() == data for p, data in protected.items())


@pytest.fixture
def desktop(tmp_path):
    try: root = tk.Tk()
    except tk.TclError as error: pytest.skip('Display unavailable: ' + str(error))
    root.withdraw()
    app = Desktop(root, Paths(tmp_path))
    yield app
    app.close()


def test_source_filters_toggle_and_advanced_defaults(desktop):
    app = desktop; app.show('sources')
    assert not app.source_advanced.winfo_manager()
    app.source_query.set(GITHUB_POOLS[0][0].upper())
    assert app.sources_tree.get_children() == ('0',)
    app.sources_tree.selection_set('0')
    enabled = GITHUB_POOLS[0][0] in app.selected_public_sources
    app.toggle_public_source()
    assert (GITHUB_POOLS[0][0] in load_settings(app.paths)['fetch']['github_sources']) != enabled
    app.source_filter.set('已启用'); app.fill_public_sources()
    assert bool(app.sources_tree.get_children()) != enabled
    app.source_query.set('does-not-exist')
    assert not app.sources_tree.get_children()
    assert '显示 0' in app.source_selection_hint.get()
    app.toggle_source_advanced(); assert app.source_advanced.winfo_manager() == 'pack'


def test_node_filters_compose_and_export_copy_uses_client_file(desktop):
    app = desktop
    nodes = [node('Stable'), dict(node('Basic', 'trojan'), server='basic.example.com')]
    a, b = map(identity, nodes)
    report = save_library(app.paths, nodes, {'verification': {'quality': {a: {'quality': 'stable'}, b: {'quality': 'basic'}}},
                          'node_sources': {a: ['example/a'], b: ['example/b']}}, TaskControl())
    app.show('nodes')
    app.node_filters['source'].set('example/a'); app.fill_nodes(reload=False)
    assert len(app.node_rows) == 1
    app.node_filters['quality'].set('部分轮次通过'); app.fill_nodes(reload=False)
    assert not app.node_rows
    app.node_filters['source'].set('全部'); app.fill_nodes(reload=False)
    assert next(iter(app.node_rows.values()))['type'] == 'trojan'
    publish(app.paths, nodes, report)
    copied = []; app.copy = copied.append
    for client, name in [('Mihomo', 'free.yaml'), ('Clash-Verge-Rev', 'free.yaml'), ('v2rayN', 'free.txt')]:
        app.copy_export_file(client)
        assert copied[-1] == str(app.paths.client_exports(client) / name)


def test_progress_is_stage_local_and_details_are_optional(desktop):
    from nodeharbor.progress import TaskProgress
    app = desktop
    app.task_progress = TaskProgress('acquire')
    app.task_progress.update(dict(stage='protocol', completed=128, total=600, passed=34, message='Checking'))
    app.show('sources')
    app.render_task_progress()
    assert app.progress_panel is None
    assert app.footer_progress['maximum'] == 600
    assert '128/600' in app.state_text.get()
    app.show('logs')
    assert app.progress_details.winfo_manager() == 'pack'
    assert '128/600' in app.progress_detail.get() and '34' in app.progress_detail.get()
    assert app.progress_bar['maximum'] == 600
    app.toggle_progress_details(); assert not app.progress_details.winfo_manager()
    app.show('logs'); assert app.progress_details.winfo_manager() == 'pack'
    app.task_progress.finish('success', '节点库已更新')
    app.render_task_progress()
    assert '客户端导出尚未改变' in app.progress_guidance.get()


def test_settings_save_respects_active_pipeline_lock(desktop, monkeypatch):
    from nodeharbor.storage import app_lock
    app = desktop; app.show('settings')
    original = (app.paths.config / 'settings.yaml').read_bytes()
    errors = []
    monkeypatch.setattr('nodeharbor.ui.settings.messagebox.showerror', lambda *args, **kw: errors.append(args))
    app.port_value.set('8299')
    with app_lock(app.paths): app.save_settings()
    assert errors and (app.paths.config / 'settings.yaml').read_bytes() == original
    app.save_settings()
    assert load_settings(app.paths)['subscription']['port'] == 8299


def test_settings_does_not_overwrite_changes_from_another_window(desktop, monkeypatch):
    app = desktop; app.show('settings')
    changed = load_settings(app.paths)
    changed['subscription']['port'] = 8298
    write_yaml(app.paths.config / 'settings.yaml', changed)
    errors = []
    monkeypatch.setattr('nodeharbor.ui.settings.messagebox.showerror', lambda *args, **kw: errors.append(args))
    app.port_value.set('8299'); app.save_settings()
    assert errors and load_settings(app.paths)['subscription']['port'] == 8298
    app.show('settings'); app.port_value.set('8299'); app.save_settings()
    assert load_settings(app.paths)['subscription']['port'] == 8299


def test_node_selection_follows_identity_after_sorting(desktop):
    app = desktop
    nodes = [node('Zulu'), dict(node('Alpha'), server='alpha.example.com')]
    save_library(app.paths, nodes, {}, TaskControl())
    app.show('nodes')
    app.tree.selection_set('0'); app.root.update()
    selected_id = identity(app.node_rows['0'])
    app.sort_nodes.set('名称'); app.fill_nodes(reload=False)
    assert identity(app.node_rows[app.tree.selection()[0]]) == selected_id
    assert len(app.tree['displaycolumns']) == 5


def test_node_details_tolerate_invalid_history(desktop):
    app = desktop
    save_library(app.paths, [node()], {'stability': {identity(node()): {'recent': None}}}, TaskControl())
    app.show('nodes'); app.tree.selection_set('0'); app.describe_node()
    assert '暂无跨轮记录' in app.node_detail.get('1.0', 'end')


def test_cross_run_failure_and_external_preview_ui(desktop):
    import time
    app = desktop
    key = identity(node())
    report = {'verification': {'quality': {key: {'quality': 'stable'}}}, 'verified_at': '2020-01-01T00:00:00+00:00'}
    save_library(app.paths, [node()], report, TaskControl())
    write_json(app.paths.data / 'state/node-history.json', {key: {
        'recent': [True, True, False], 'last_checked': time.time(), 'last_passed': time.time() - 60,
        'failures': 1, 'signature': 'fixture', 'delay_ms': 20}})
    app.show('nodes')
    app.node_filters['quality'].set('最近检测失败'); app.fill_nodes(reload=False)
    assert len(app.node_rows) == 1
    row = app.tree.item(app.tree.get_children()[0], 'values')
    assert '2/3' in row[6] and row[8] == '1'
    app.show('subscriptions')
    app.show('sources'); app.open_source_search(); dialog = app.search_dialog
    repo = dict(name='example/repo', branch='main', description='fixture', updated='')
    dialog.receive(dict(mode='search', repositories=[repo], incomplete=False))
    dialog.receive(dict(mode='files', repository='example/repo', limited=False,
                        files=[dict(path='https://sample.workers.dev/sub', url='https://sample.workers.dev/sub',
                                    size=0, kind='Cloudflare 托管（第三方）')]))
    dialog.file_tree.selection_set('0'); app.root.update()
    assert dialog.preview_button['state'] == 'normal'
    dialog.receive(dict(mode='preview', url='https://sample.workers.dev/sub', count=3, protocols={'ss': 3}))
    assert '3 个节点' in dialog.file_note.get() and '尚未检测' in dialog.file_note.get()
    dialog.add()
    assert app.custom_pools[0]['urls'] == ['https://sample.workers.dev/sub'] and not app.custom_pools[0]['enabled']
    dialog.close()


def test_source_and_node_hosting_filters_preserve_mixed_sources(desktop):
    app = desktop
    raw = 'https://raw.githubusercontent.com/example/repo/main/sub'
    cf = 'https://sample.workers.dev/sub'
    settings = load_settings(app.paths)
    settings['fetch']['node_pools'] = [dict(name='example/repo', branch='main', enabled=False, urls=[raw, cf])]
    write_yaml(app.paths.config / 'settings.yaml', settings)
    app.show('sources')
    app.source_hosting.set('Cloudflare 托管（第三方）'); app.fill_public_sources()
    assert app.sources_tree.get_children() == ('pool-0',)
    assert 'GitHub 文件' in app.sources_tree.item('pool-0', 'values')[-1]
    app.source_hosting.set('GitHub 文件'); app.fill_public_sources()
    assert 'pool-0' in app.sources_tree.get_children() and '0' in app.sources_tree.get_children()
    nodes = [dict(node(f'Fixture {i}'), port=443+i) for i in range(4)]
    ids = [identity(n) for n in nodes]
    hosting = {ids[0]: ['GitHub 文件'], ids[1]: ['Cloudflare 托管（第三方）'],
               ids[2]: ['GitHub 文件', 'Cloudflare 托管（第三方）']}
    save_library(app.paths, nodes, {'node_hosting': hosting}, TaskControl())
    app.show('nodes')
    app.node_filters['hosting'].set('Cloudflare 托管（第三方）'); app.fill_nodes(reload=False)
    assert {identity(n) for n in app.node_rows.values()} == {ids[1], ids[2]}
    app.node_filters['hosting'].set('GitHub 文件'); app.fill_nodes(reload=False)
    assert {identity(n) for n in app.node_rows.values()} == {ids[0], ids[2]}
    app.node_filters['hosting'].set('历史类型未记录'); app.fill_nodes(reload=False)
    assert list(app.node_rows.values()) == [nodes[3]]


def test_cloudflare_search_ui_mode_selection_add_and_clear(desktop, monkeypatch):
    app = desktop; app.show('sources'); app.open_source_search()
    dialog = app.search_dialog
    assert dialog.window.title() == '搜索节点池'
    dialog.search_kind.set('cloudflare'); dialog.change_mode()
    calls = []
    monkeypatch.setattr(dialog, 'start', lambda mode, value: calls.append((mode, value)))
    dialog.search()
    assert calls == [('search-cloudflare', '')]
    repo = dict(name='example/cf', branch='main', updated='', description='Fixture')
    link = dict(path='https://demo.workers.dev/sub', url='https://demo.workers.dev/sub', size=0)
    dialog.receive(dict(mode='search-cloudflare', repositories=[repo], checked=1, incomplete=True,
                        warnings=['Partial results'], files_by_repository={'example/cf': [link]}))
    dialog.repo_tree.selection_set('0'); app.root.update()
    assert len(dialog.files) == 1 and 'Partial results' in dialog.note.get()
    dialog.file_tree.selection_set('0'); app.root.update()
    dialog.add_button.invoke()
    assert app.custom_pools[0]['urls'] == [link['url']]
    assert not app.custom_pools[0]['enabled']
    dialog.search_kind.set('github'); dialog.change_mode()
    assert not dialog.files and not dialog.cached_files and not dialog.repo_tree.get_children()
    assert dialog.add_button['state'] == 'disabled'
    dialog.close()


@pytest.mark.parametrize('factor', [1, 1.5, 2])
def test_search_dialog_actions_remain_visible_at_scaled_fonts(tmp_path, factor):
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.tk.call('tk', 'scaling', (96 / 72) * factor)
    app = Desktop(root, Paths(tmp_path))
    try:
        app.open_source_search(); dialog = app.search_dialog
        dialog.search_kind.set('cloudflare'); dialog.change_mode()
        repo = dict(name='example/test', branch='main', description='布局验证用示例，非真实节点池', updated='2026-10-26')
        dialog.receive(dict(mode='search-cloudflare', repositories=[repo], checked=1, incomplete=False,
                            warnings=[], files_by_repository={'example/test': [dict(path='https://demo.workers.dev/sub', url='https://demo.workers.dev/sub', size=0)]}))
        dialog.repo_tree.selection_set('0')
        root.update()
        bottom = dialog.window.winfo_rooty() + dialog.window.winfo_height()
        right = dialog.window.winfo_rootx() + dialog.window.winfo_width()
        for button in [dialog.search_button, dialog.cancel_button, dialog.join_button,
                       dialog.add_button, dialog.preview_button]:
            assert button.winfo_ismapped(), button.cget('text')
            assert button.winfo_rooty() + button.winfo_height() <= bottom, button.cget('text')
            assert button.winfo_rootx() + button.winfo_width() <= right, button.cget('text')
            assert button.winfo_width() >= button.winfo_reqwidth(), button.cget('text')
            parent = button.master
            while parent != dialog.window:
                assert button.winfo_rooty() + button.winfo_height() <= parent.winfo_rooty() + parent.winfo_height(), button.cget('text')
                parent = parent.master
        assert dialog.repo_tree.winfo_height() >= 90
        assert dialog.file_tree.winfo_height() >= 90
        dialog.close()
    finally: app.close()


@pytest.mark.parametrize('factor', [1, 1.25, 1.5, 2])
def test_layout_at_narrow_window_and_scaled_fonts(tmp_path, factor):
    try: root = tk.Tk()
    except tk.TclError as error: pytest.skip('Display unavailable: ' + str(error))
    root.tk.call('tk', 'scaling', (96 / 72) * factor)
    root.withdraw()
    callbacks = []
    root.report_callback_exception = lambda *args: callbacks.append(args)
    app = Desktop(root, Paths(tmp_path))
    root.geometry(f'{round(940 * factor)}x{round(620 * factor)}')
    root.deiconify()
    try:
        for page in ('overview', 'sources', 'nodes', 'subscriptions', 'settings', 'logs'):
            app.show(page); root.update()
            def buttons(parent):
                for child in parent.winfo_children():
                    if isinstance(child, tk.Button) and child.winfo_ismapped(): yield child
                    yield from buttons(child)
            right = app.main.winfo_rootx() + app.main.winfo_width()
            for button in buttons(app.main):
                assert button.winfo_rootx() + button.winfo_width() <= right, (page, button.cget('text'))
                assert button.winfo_width() >= button.winfo_reqwidth(), (page, button.cget('text'), 'clipped')
            assert app.cancel_button.winfo_rooty() + app.cancel_button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        assert not callbacks
    finally: app.close()
