"""Installation diagnostics using only temporary fixtures and loopback networking."""
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .desktop import Desktop
from .exporter import publish
from .storage import Paths
from .tasks import Cancelled, TaskControl, network_job, NetworkPool
from .progress import TaskProgress
from .public_sources import GITHUB_POOLS


def require(condition, message):
    # Bundles use optimized bytecode: assertions and their side effects disappear.
    if not condition: raise AssertionError(message)


def check_installation(report_file):
    result = {'ok': False, 'checks': []}
    root = app = None
    try:
        with tempfile.TemporaryDirectory(prefix='nodeharbor-check-') as tmp:
            paths = Paths(Path(tmp))
            root = tk.Tk(); root.withdraw()
            callbacks = []
            root.report_callback_exception = lambda *error: callbacks.append(error[0].__name__)
            app = Desktop(root, paths)
            require(len(list(paths.config.iterdir())) == 1, 'First-run configuration')
            result['checks'].append('first-run configuration')
            publish(paths, [{'name': 'Example', 'type': 'ss', 'server': 'example.com', 'port': 443,
                             'cipher': 'aes-256-gcm', 'password': 'example-only'}], {})
            for page in ('overview', 'nodes', 'sources', 'subscriptions', 'settings', 'logs'):
                app.show(page); root.update()
            result['checks'].append('all desktop pages and Tcl/Tk')
            app.show('sources'); root.update()
            require(len(app.sources_tree.get_children()) == len(GITHUB_POOLS), 'Public source catalog')
            app.choose_source_preset('快速')
            require(len(app.selected_public_sources) == 6, 'Quick source preset')
            require(app.source_budget_value.get() == '400', 'Quick candidate budget')
            result['checks'].append('public source catalog and selections')
            app.open_source_search(); root.update()
            dialog = app.search_dialog
            dialog.receive({'mode': 'search', 'incomplete': False, 'repositories': [
                {'name': 'example/free', 'branch': 'main', 'description': 'Fixture', 'updated': ''}]})
            dialog.repo_tree.selection_set('0'); root.update()
            require(dialog.inspect_button.cget('state') == 'normal', 'Search repository selection')
            dialog.receive({'mode': 'files', 'repository': 'example/free', 'limited': False,
                            'files': [{'path': 'sub', 'url': 'https://raw.githubusercontent.com/example/free/main/sub', 'size': 10}]})
            dialog.file_tree.selection_set('0'); root.update(); dialog.add(); dialog.add()
            require(len(app.custom_pools) == 1 and app.custom_pools[0]['urls'] == ['https://raw.githubusercontent.com/example/free/main/sub'] and not app.custom_pools[0]['enabled'],
                    'Search selection deduplication')
            dialog.close()
            require(network_job('github-discovery', {'mode': 'invalid'}) == {'error': 'response'}, 'Packaged discovery worker')
            result['checks'].append('public repository search dialog and packaged discovery worker')
            from .source_catalog import update_catalog, remove_pool
            from .diagnostic_report import export_report
            app.replace_catalog_files(dict(app.custom_pools[0]), ['https://raw.githubusercontent.com/example/free/main/replacement.yaml'])
            require(not app.custom_pools[0]['enabled'] and app.custom_pools[0]['urls'][0].endswith('replacement.yaml'), 'Replace source files')
            update_catalog(paths, lambda options: remove_pool(options, 'example/free'))
            app.show('sources'); root.update()
            require(not app.custom_pools, 'Remove user source')
            diagnostic = json.loads(export_report(paths).read_text(encoding='utf-8'))
            require(diagnostic['schema_version'] == 1 and str(paths.root) not in json.dumps(diagnostic), 'Private paths excluded')
            result['checks'].append('source maintenance and offline redacted diagnostics')
            app.show('subscriptions'); root.update()
            require(len(app.export_action_buttons) == 2, 'Export actions')
            require(set(app.client_export_links) == {'Mihomo', 'Clash-Verge-Rev', 'v2rayN'}, 'Client cards')
            require((paths.client_exports('v2rayN') / 'free.txt').is_file(), 'URI export')
            require((paths.current() / 'free.v2ray.base64.txt').is_file(), 'URI subscription')
            require(not callbacks, 'Desktop callback failed')
            result['checks'].append('free-only client exports and two-action export page')
            app.show('settings')
            app.core_client_value.set('v2rayN'); app.change_core_client(); root.update()
            require(app.v2rayn_core_value.get() == 'xray', 'Core selector')
            app.task_progress = TaskProgress('refresh-github')
            app.task_progress.update({'stage': 'protocol', 'completed': 3, 'total': 5, 'passed': 1,
                                      'message': 'Fixture progress'})
            app.show('overview'); root.update()
            app.render_task_progress()
            require(app.progress_panel is None and app.footer_progress['maximum'] == 5, 'Compact task progress')
            app.show('logs'); root.update()
            require(app.progress_panel.winfo_exists() and app.progress_bar['maximum'] == 5, 'Task detail progress')
            require(not callbacks, 'Progress callback failed')
            result['checks'].append('client core selection and visual task progress')

            started = threading.Event()
            stopped = threading.Event()

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers()
                    if self.path == '/slow':
                        started.set()
                        while not stopped.wait(0.1):
                            try: self.wfile.write(b'x'); self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): break
                    elif self.path == '/subscription':
                        self.wfile.write(b'ss://YWVzLTI1Ni1nY206dGVzdA@example.com:443#fixture')
                    else:
                        self.wfile.write(b'{"delay": 12}')

                def log_message(self, *args): pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                require(network_job('probe', {'url': url, 'timeout': 3}) == 12, 'JSON worker')
                require(network_job('http-probe', {'url': 'http://example.invalid/fixture',
                                   'port': server.server_port, 'timeout': 3}) > 0, 'HTTP worker')
                result['checks'].append('packaged background worker and JSON pipes')
                with NetworkPool(1, TaskControl()) as pool:
                    require(pool.request('probe', {'url': url, 'timeout': 3}) == 12, 'Persistent worker')
                    worker = pool.workers[0][0]
                    require(pool.request('probe', {'url': url, 'timeout': 3}) == 12, 'Persistent worker second request')
                    require(pool.workers[0][0] is worker, 'Worker reuse')
                    events = []
                    nodes = pool.request('fetch', {'url': url + '/subscription', 'options': {
                        'timeout_seconds': 3, 'retries': 0, 'max_response_mb': 1, '_stream_progress': True}},
                        5, on_progress=events.append)
                    require(len(nodes) == 1, 'Streaming fetch result')
                    require({'connecting', 'reading', 'parsing'} <= {e['phase'] for e in events}, 'Streaming source progress')
                require(worker.poll() is not None, 'Worker cleanup')
                result['checks'].append('persistent network worker reuse, streaming fetch progress and cleanup')
                control = TaskControl()
                canceller = threading.Thread(target=lambda: (started.wait(5), control.cancel()))
                canceller.start()
                try:
                    network_job('probe', {'url': url + '/slow', 'timeout': 10}, control, timeout=10)
                    raise AssertionError('Worker did not cancel')
                except Cancelled:
                    require(started.is_set(), 'Blocked request did not start')
                    result['checks'].append('background worker cancellation')
                finally:
                    stopped.set(); canceller.join(6)
            finally:
                stopped.set(); server.shutdown(); server.server_close(); thread.join(2)
            app.close(); app = root = None
            result['ok'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
        import traceback
        result['error_location'] = [{'function': frame.name, 'line': frame.lineno}
                                    for frame in traceback.extract_tb(error.__traceback__)]
    finally:
        if app is not None: app.close()
        elif root is not None: root.destroy()
        Path(report_file).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1
