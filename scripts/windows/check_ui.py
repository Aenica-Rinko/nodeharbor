"""Repeatable offline GUI scenarios. Each case uses a fresh process and temporary profile."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CASES = ('empty', 'sources', 'acquired', 'exported', 'running', 'failed', 'cancelled', 'cloudflare')


def require(condition, message):
    if not condition: raise RuntimeError(message)


def scenario(case, scale, preview=False):
    sys.path.insert(0, str(ROOT / 'src'))
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    from nodeharbor.exporter import publish
    from nodeharbor.node_library import save_library
    from nodeharbor.nodes import identity
    from nodeharbor.progress import TaskProgress
    from nodeharbor.storage import AppError, Paths
    from nodeharbor.tasks import TaskControl

    with tempfile.TemporaryDirectory(prefix='nodeharbor-ui-') as temporary, ExitStack() as stack:
        # This fixture must never use a locally installed core or start a real task.
        stack.enter_context(patch('nodeharbor.ui.overview.find_core', side_effect=AppError('Fixture: no core')))
        stack.enter_context(patch('nodeharbor.ui.exports.find_core', side_effect=AppError('Fixture: no core')))
        stack.enter_context(patch('nodeharbor.source_search_dialog.SourceSearchDialog.start', return_value=None))
        root = tk.Tk()  # Missing Tcl/Tk fails this command; it must not silently skip.
        if not preview:
            # Exercise the same compact desktop on developer monitors and CI runners.
            stack.enter_context(patch.object(root, 'winfo_screenwidth', return_value=min(1024, root.winfo_screenwidth())))
            stack.enter_context(patch.object(root, 'winfo_screenheight', return_value=min(768, root.winfo_screenheight())))
        root.tk.call('tk', 'scaling', scale * 96 / 72)
        callbacks = []
        root.report_callback_exception = lambda *error: callbacks.append(error[0].__name__)
        app = Desktop(root, Paths(Path(temporary)))
        app.run = lambda *args, **kwargs: app.state_text.set('演示数据：不会联网或执行检测。')
        app.start_task = app.run
        root.title('NodeHarbor UI fixture · ' + case)
        try:
            stamp = datetime.now(timezone.utc).isoformat()
            nodes = [dict(name='演示节点 ' + str(i + 1), type='ss', server='example.com', port=440 + i,
                          cipher='aes-256-gcm', password='fixture-only') for i in range(6)]
            report = {'verified_at': stamp, 'verification': {
                'quality': {identity(n): {'quality': 'stable'} for n in nodes},
                'delays_ms': {identity(n): 80 + i * 20 for i, n in enumerate(nodes)}},
                'node_sources': {identity(n): ['example/demo'] for n in nodes},
                'node_hosting': {identity(n): ['GitHub 文件'] for n in nodes}}
            if case not in ('empty', 'sources', 'cloudflare'):
                report = save_library(app.paths, nodes, report, TaskControl())
            if case in ('exported', 'failed', 'cancelled'): publish(app.paths, nodes, report)
            page = {'empty': 'overview', 'sources': 'sources', 'acquired': 'nodes', 'exported': 'subscriptions',
                    'running': 'sources', 'failed': 'logs', 'cancelled': 'logs', 'cloudflare': 'sources'}[case]
            if case in ('running', 'failed', 'cancelled'):
                app.task_progress = TaskProgress('acquire')
                app.task_progress.update(dict(stage='protocol', completed=128, total=600, passed=34, message='演示检测状态'))
                if case != 'running': app.task_progress.finish(case, '演示：任务未完成，已有导出保持不变。')
            app.show(page); app.render_task_progress()
            if case == 'cloudflare':
                app.open_source_search(); dialog = app.search_dialog
                dialog.search_kind.set('cloudflare'); dialog.change_mode()
                dialog.receive(dict(mode='search-cloudflare', checked=1, incomplete=False, warnings=[],
                    repositories=[dict(name='example/demo', branch='main', description='离线演示，非可用节点', updated='2026-01-01')],
                    files_by_repository={'example/demo': [dict(path='https://demo.workers.dev/sub',
                        url='https://demo.workers.dev/sub', size=0)]}))
                dialog.repo_tree.selection_set('0')
            root.update()
            require(not callbacks, 'Tk callback failure: ' + ','.join(callbacks))
            def buttons(parent):
                for child in parent.winfo_children():
                    if isinstance(child, tk.Button) and child.winfo_ismapped(): yield child
                    yield from buttons(child)
            surface = app.search_dialog.window if case == 'cloudflare' else app.main
            for button in buttons(surface):
                require(button.winfo_width() >= button.winfo_reqwidth(), 'Clipped button: ' + button.cget('text'))
                require(button.winfo_rootx() + button.winfo_width() <= surface.winfo_rootx() + surface.winfo_width(),
                        'Button outside page: ' + button.cget('text'))
            require(app.cancel_button.winfo_rooty() + app.cancel_button.winfo_height() <= root.winfo_rooty() + root.winfo_height(), 'Footer clipped')
            if case == 'cloudflare':
                for tree in (dialog.repo_tree, dialog.file_tree): require(tree.winfo_height() >= 90, 'Search list squeezed')
                for button in (dialog.add_button, dialog.join_button, dialog.cancel_button):
                    parent = button.master
                    while parent != dialog.window:
                        require(button.winfo_rooty() + button.winfo_height() <= parent.winfo_rooty() + parent.winfo_height(), 'Search action clipped')
                        parent = parent.master
            if case in ('failed', 'cancelled'):
                require(app.paths.current().is_dir(), 'Existing exports lost')
            if case == 'exported':
                app.show('overview'); root.update()
                require(app.next_action_button.cget('text') == '导入客户端使用', 'Onboarding loops back to acquisition')
                app.show('subscriptions'); root.update()
            if preview:
                root.after(180000, app.close)
                root.mainloop()
            return {'case': case, 'scale': scale, 'ok': True, 'tk': str(root.tk.call('info', 'patchlevel'))} if not preview else {'case': case, 'ok': True}
        finally:
            try:
                if root.winfo_exists(): app.close()
            except tk.TclError: pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--scale', type=float, choices=(1, 1.25, 1.5, 2), default=1)
    parser.add_argument('--preview', action='store_true', help='Open an offline fixture for visual review (closes after 3 minutes).')
    args = parser.parse_args()
    if args.preview and not args.case: parser.error('--preview requires --case')
    if args.case:
        print(json.dumps(scenario(args.case, args.scale, args.preview), ensure_ascii=True))
        return
    results = []
    environment = os.environ.copy()
    for name in ('NODEHARBOR_HOME', 'MIHOMO_PATH', 'XRAY_PATH', 'PYTHONPATH', 'PYTHONHOME'):
        environment.pop(name, None)
    for scale in (1, 1.25, 1.5, 2):
        for case in CASES:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--case', case, '--scale', str(scale)],
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=45, env=environment,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(f'GUI scenario failed: {case} @ {scale}; exit {result.returncode}\n' + result.stderr[-3000:])
            results.append(json.loads(result.stdout))
    print(json.dumps({'ok': True, 'isolated_processes': len(results), 'results': results}, indent=2))


if __name__ == '__main__': main()
