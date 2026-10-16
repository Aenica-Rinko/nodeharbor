import base64
import json
import os
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest
import requests

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.exporter import publish
from nodeharbor.fetcher import fetch_all
from nodeharbor.nodes import normalize_nodes, parse_subscription
from nodeharbor.storage import AppError, Paths, read_json, write_json, write_yaml


def node(**changes):
    return dict(dict(name='Fixture', type='ss', server='example.com', port=443,
                     cipher='aes-256-gcm', password='example-only'), **changes)


@pytest.mark.parametrize('section', ['fetch', 'verification', 'exports'])
@pytest.mark.parametrize('value', [None, [], 'invalid'])
def test_invalid_settings_sections_have_actionable_errors(tmp_path, section, value):
    paths = Paths(tmp_path);paths.ensure()
    write_yaml(paths.config/'settings.yaml', {section: value})
    with pytest.raises(AppError, match=section):load_settings(paths)


@pytest.mark.parametrize('value', ['https://example.com:wrong/probe', 'https://example.com:0/',
                                  'https://example.com:65536/', 'https://example.com/with space'])
def test_invalid_probe_urls_fail_before_any_network_work(tmp_path, value):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / 'settings.yaml', {'verification': {'https_test_url': value}})
    with pytest.raises(AppError, match='https_test_url'): load_settings(paths)


@pytest.mark.parametrize('value', [float('inf'), float('nan'), 10**100, ['bad'], {'bad': 'record'}])
def test_invalid_source_timestamp_cannot_crash_the_desktop(value):
    from nodeharbor.ui.common import display_time
    assert display_time(value) == '时间记录无效'


def test_bad_vmess_alpn_does_not_discard_other_links():
    malformed=dict(add='example.com', port=443, id='fixture', alpn=42)
    uri='vmess://'+base64.b64encode(json.dumps(malformed).encode()).decode()
    good='ss://YWVzLTI1Ni1nY206dGVzdA@example.com:443#good'
    parsed=parse_subscription(uri+'\n'+good)
    assert len(parsed)==1 and parsed[0]['name']=='good'


@pytest.mark.parametrize('port', [True, 443.5, 'secret-fixture-not-a-port'])
def test_bad_port_is_rejected_without_echoing_untrusted_value(port):
    assert normalize_nodes([node(port=port),node()])==[node()]
    with pytest.raises(AppError) as error:normalize_nodes([node(port=port)],strict=True)
    assert 'secret-fixture-not-a-port' not in str(error.value)


def test_invalid_transport_is_counted_instead_of_aborting_export():
    from nodeharbor.uri_export import export_uris
    malformed=dict(name='bad',type='vmess',server='example.com',port=443,uuid='fixture',network='ws',**{'ws-opts':[]})
    links,report=export_uris([malformed,node()])
    assert report['exported']==1 and report['skipped']==1 and links.startswith('ss://')


@pytest.mark.parametrize('value', [None, [], 'bad', {'generation': []}, {'generation': '../outside'}])
def test_invalid_export_pointer_has_actionable_error(tmp_path,value):
    paths=Paths(tmp_path);paths.ensure();write_json(paths.exports/'current.json',value)
    with pytest.raises(AppError):paths.current()


def test_corrupt_health_state_does_not_break_other_sources(tmp_path,monkeypatch):
    from nodeharbor.public_sources import GITHUB_POOLS
    from nodeharbor.source_plan import source_key
    settings=deepcopy(DEFAULTS);settings['fetch'].update(github_sources=[p[0] for p in GITHUB_POOLS[:2]],_cache_folder=str(tmp_path))
    write_json(tmp_path/'health.json',{source_key(GITHUB_POOLS[0][1]):dict(next_retry='bad',failures='bad')})
    monkeypatch.setattr('nodeharbor.fetcher.fetch_one',lambda *a:[node()])
    nodes,report=fetch_all(settings,lambda _:None)
    assert nodes==[node()] and report['sources_ok']==2


def test_cli_handles_corrupt_settings_without_traceback(tmp_path,capsys):
    from nodeharbor.cli import main
    paths=Paths(tmp_path);paths.ensure();(paths.config/'settings.yaml').write_text('exports: [broken')
    assert main(['--home',str(tmp_path),'status'])==1
    output=capsys.readouterr().err
    assert '配置格式错误' in output and 'Traceback' not in output


def test_partial_progress_retains_candidate_count():
    from nodeharbor.progress import TaskProgress
    model=TaskProgress('verify')
    model.update(dict(stage='precheck',candidates=10,completed=0,total=10))
    model.update(dict(stage='precheck',tcp_passed=3,completed=5,total=10))
    assert model.counters['selected']==10 and model.counters['unique']==10


def test_subscription_rejects_foreign_host_and_supports_head(tmp_path):
    from nodeharbor.subscription import create_server
    paths=Paths(tmp_path);paths.ensure();folder,_=publish(paths,[node()],{})
    with create_server(paths,0) as server:
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with requests.Session() as session:
                session.trust_env=False;url=f'http://127.0.0.1:{server.server_port}/free.yaml'
                assert session.get(url,headers={'Host':'external.example'},timeout=2).status_code==403
                result=session.head(url,timeout=2)
                assert result.status_code==200 and int(result.headers['Content-Length'])==(folder/'free.yaml').stat().st_size and not result.content
                assert server.daemon_threads
        finally:server.shutdown();thread.join(2)


def test_committed_export_is_not_reported_cancelled(tmp_path,monkeypatch):
    from nodeharbor.pipeline import execute
    from nodeharbor.initialize import initialize
    from nodeharbor.tasks import TaskControl
    import logging
    paths=Paths(tmp_path);initialize(paths);old,_=publish(paths,[node()],{})
    monkeypatch.setattr('nodeharbor.pipeline.find_mihomo',lambda *a: (_ for _ in ()).throw(AppError('No core')))
    control=TaskControl()
    monkeypatch.setattr('nodeharbor.pipeline.prune_exports',lambda *a:control.cancel())
    try:
        result=execute(paths,'export',control=control)
        assert paths.current()!=old and result['counts']['free']==1
        assert read_json(paths.data/'state/last-run.json')['status']=='success'
    finally:
        logger=logging.getLogger('nodeharbor.'+str(paths.root))
        for handler in list(logger.handlers):handler.close();logger.removeHandler(handler)


def test_pool_can_cancel_a_worker_that_never_reads_input(monkeypatch):
    import nodeharbor.tasks as tasks
    monkeypatch.setattr(tasks,'worker_command',lambda:[sys.executable,'-c','import time; time.sleep(30)'])
    control=tasks.TaskControl();completed=threading.Event();errors=[]
    with tasks.NetworkPool(1,control) as pool:
        def request():
            try:pool.request('fixture',{'content':'x'*2_000_000},10)
            except BaseException as error:errors.append(error)
            finally:completed.set()
        thread=threading.Thread(target=request,daemon=True);thread.start();time.sleep(.2)
        control.cancel()
        if not completed.wait(3):
            # Release the fixture on failure so the test never leaves a blocked thread.
            process=pool.workers[0][0];process.terminate();process.wait(2)
            thread.join(2)
            pytest.fail('Cancellation blocked while writing the request to the worker')
        thread.join(2)
        assert isinstance(errors[0],tasks.Cancelled)


def test_one_shot_process_can_cancel_while_writing_input():
    from nodeharbor.tasks import TaskControl,Cancelled
    control=TaskControl();finished=threading.Event();errors=[]
    with subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
        def communicate():
            try:control.communicate(process,b'x'*2_000_000,timeout=10)
            except BaseException as error:errors.append(error)
            finally:finished.set()
        thread=threading.Thread(target=communicate,daemon=True);thread.start();time.sleep(.2);control.cancel()
        if not finished.wait(3):
            process.terminate();process.wait(2);thread.join(2)
            pytest.fail('One-shot process cancellation blocked while writing input')
        thread.join(2)
        assert isinstance(errors[0],Cancelled) and process.poll() is not None


@pytest.mark.parametrize('cache', [[], {'until':'broken'}, {'until':float('inf')}])
def test_corrupt_api_cooldown_does_not_disable_download(tmp_path,cache):
    from nodeharbor.fetcher import api_gate
    write_json(tmp_path/'github-api-wait.json',cache)
    with api_gate('https://api.github.com/repos/fixture/repo/contents/sub',dict(DEFAULTS['fetch'],_cache_folder=str(tmp_path))):pass


@pytest.mark.parametrize('options', [{'ws-opts':None,'network':'ws'}, {'ws-opts':{'headers':[]},'network':'ws'},
                                    {'grpc-opts':42,'network':'grpc'}])
def test_xray_skips_bad_transport_without_poisoning_good_nodes(options):
    from nodeharbor.xray_verifier import outbound
    from nodeharbor.uri_export import Unsupported
    bad=dict(name='bad',type='vless',server='example.com',port=443,uuid='fixture',**options)
    with pytest.raises(Unsupported):outbound(bad)
    assert outbound(node())['protocol']=='shadowsocks'


@pytest.mark.parametrize('name',['dist','backups','.git','.github'])
def test_export_directory_cannot_overlap_builds_or_backups(tmp_path,name):
    from nodeharbor.storage import export_root
    with pytest.raises(AppError):export_root(tmp_path,str(tmp_path/name))


def test_optional_corrupt_runtime_reports_do_not_crash_status(tmp_path):
    from nodeharbor.pipeline import status
    paths=Paths(tmp_path);paths.ensure();folder,_=publish(paths,[node()],{})
    write_json(paths.data/'state/last-run.json',[])
    (folder/'report.json').write_text('{broken')
    snapshot=status(paths)
    assert snapshot['last_run']=={} and snapshot['report']=={} and snapshot['export_dir']==str(folder)


def test_failed_generation_cleanup_preserves_original_error(tmp_path,monkeypatch):
    import nodeharbor.exporter as exporter
    paths=Paths(tmp_path);paths.ensure();old,_=publish(paths,[node()],{})
    monkeypatch.setattr(exporter,'check_config',lambda *a:False)
    monkeypatch.setattr(exporter.shutil,'rmtree',lambda *a: (_ for _ in ()).throw(OSError('cleanup-fixture')))
    with pytest.raises(AppError,match='Mihomo'):publish(paths,[node()],{},core='fake')
    assert paths.current()==old


def test_post_publish_cleanup_failure_is_a_warning_not_failed_task(tmp_path,monkeypatch):
    from nodeharbor.pipeline import execute
    from nodeharbor.initialize import initialize
    import logging
    paths=Paths(tmp_path);initialize(paths);publish(paths,[node()],{})
    monkeypatch.setattr('nodeharbor.pipeline.find_mihomo',lambda *a: (_ for _ in ()).throw(AppError('No core')))
    monkeypatch.setattr('nodeharbor.pipeline.prune_exports',lambda *a: (_ for _ in ()).throw(OSError('cleanup-fixture')))
    try:
        assert execute(paths,'export')['counts']['free']==1
        state=read_json(paths.data/'state/last-run.json')
        assert state['status']=='success' and state['cleanup_warning']
    finally:
        logger=logging.getLogger('nodeharbor.'+str(paths.root))
        for handler in list(logger.handlers):handler.close();logger.removeHandler(handler)


@pytest.mark.parametrize('name,code',[('🇨🇴 节点','CO'),('🇵🇰 节点','PK'),('🇧🇩 节点','BD'),('🇵🇪 节点','PE')])
def test_supported_flags_create_the_correct_region_group(name,code):
    from nodeharbor.geo import get_country_from_name,COUNTRY_NAME
    from nodeharbor.exporter import make_config
    assert get_country_from_name(name)==code
    groups=make_config([node(name=name)])['proxy-groups']
    assert any(COUNTRY_NAME[code] in group['name'] for group in groups)


def test_whitespace_only_name_gets_a_nonempty_display_name():
    result=normalize_nodes([node(name='   ')])
    assert result[0]['name'].strip() and result[0]['password']==node()['password']


def test_desktop_handles_bad_optional_state_without_callback_errors(tmp_path):
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    paths=Paths(tmp_path);paths.ensure();folder,_=publish(paths,[node()],{})
    write_json(paths.data/'state/public-sources.json',{'sources':None,'summary':'bad'})
    write_json(folder/'report.json',{'verification':[],'node_sources':[]})
    try:root=tk.Tk()
    except tk.TclError:pytest.skip('Display unavailable')
    root.withdraw();errors=[];root.report_callback_exception=lambda *e:errors.append(e[0].__name__)
    app=Desktop(root,paths)
    try:
        app.show('sources');root.update()
        assert len(app.sources_tree.get_children())==26
        app.show('nodes');root.update()
        assert len(app.tree.get_children())==1 and not errors
        (paths.config/'settings.yaml').write_text('exports: [broken')
        app.show('settings');root.update()
        assert '配置格式错误' in app.state_text.get() and not errors
    finally:app.close()


def test_xray_http_probe_does_not_accept_a_redirect_without_a_destination():
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from nodeharbor.tasks import network_job
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):self.send_response(302);self.end_headers()
        def log_message(self,*a):pass
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            assert network_job('http-probe',{'url':'http://example.invalid/test','port':server.server_port,'timeout':2}) is None
        finally:server.shutdown();thread.join(2)


@pytest.mark.parametrize('status_code', [200, 302, 403, 500])
def test_actual_mihomo_probe_requires_success_http_status(status_code):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from nodeharbor.verifier import running_core
    from nodeharbor.tasks import network_job
    core=Path(__file__).resolve().parents[1]/'runtime'/('mihomo.exe' if os.name=='nt' else 'mihomo')
    if not core.is_file():pytest.skip('Local Mihomo not installed')
    received=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(.03);received.set();self.send_response(status_code);self.send_header('Content-Length','0');self.end_headers()
        def do_CONNECT(self):
            self.connection.settimeout(3);self.send_response(200);self.end_headers()
            while self.rfile.readline().strip():pass
            time.sleep(.03);received.set();self.wfile.write(f'HTTP/1.1 {status_code} Fixture\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())
        def log_message(self,*a):pass
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as proxy:
        thread=threading.Thread(target=proxy.serve_forever);thread.start()
        try:
            with running_core(core,[dict(name='fixture',type='http',server='127.0.0.1',port=proxy.server_port)]) as (url,headers,_):
                result=network_job('probe',dict(url=url+'/proxies/fixture/delay',headers=headers,
                    params=dict(url='http://example.invalid/fixture',timeout=2000,expected='200-299'),timeout=4,confirm_expected=True))
                assert received.is_set()
                assert (result is not None)==(status_code==200)
        finally:proxy.shutdown();thread.join(2)


def test_history_cleanup_cannot_overlap_another_profile_publication(tmp_path):
    from nodeharbor.exporter import prune_exports
    from nodeharbor.storage import file_lock
    paths=Paths(tmp_path);paths.ensure()
    generations=[publish(paths,[node()],{})[0] for _ in range(4)]
    with file_lock(paths.exports/'exports.lock'):
        with pytest.raises(AppError):prune_exports(paths,2)
    assert all(folder.exists() for folder in generations)


def test_relocation_requires_the_source_export_lock(tmp_path):
    from nodeharbor.export_locations import relocate_exports
    from nodeharbor.initialize import initialize
    from nodeharbor.storage import file_lock
    paths=Paths(tmp_path/'profile');initialize(paths);folder,_=publish(paths,[node()],{})
    before=(paths.config/'settings.yaml').read_bytes()
    with file_lock(paths.exports/'exports.lock'):
        with pytest.raises(AppError):relocate_exports(paths,str(tmp_path/'destination'))
    assert paths.current()==folder and (paths.config/'settings.yaml').read_bytes()==before


def test_desktop_file_permission_failure_is_shown_to_user(tmp_path,monkeypatch):
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    try:root=tk.Tk()
    except tk.TclError:pytest.skip('Display unavailable')
    root.withdraw();app=Desktop(root,Paths(tmp_path));messages=[]
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror',lambda *a,**kw:messages.append(a))
    try:
        button=app.button(app.main,'fixture',lambda:(_ for _ in ()).throw(PermissionError('private-path-fixture')))
        button.invoke()
        assert messages and '权限' in messages[0][1] and 'private-path-fixture' not in messages[0][1]
    finally:app.close()


@pytest.mark.parametrize('milliseconds',[32768,60000])
def test_mihomo_timeout_cannot_exceed_its_api_range(tmp_path,milliseconds):
    paths=Paths(tmp_path);paths.ensure();settings=deepcopy(DEFAULTS)
    settings['verification']['timeout_ms']=milliseconds
    write_yaml(paths.config/'settings.yaml',settings)
    with pytest.raises(AppError,match='32767'):load_settings(paths)


def test_xray_can_use_longer_request_timeout(tmp_path):
    paths=Paths(tmp_path);paths.ensure();settings=deepcopy(DEFAULTS)
    settings['verification'].update(core_client='v2rayN',v2rayn_core='xray',timeout_ms=60000)
    write_yaml(paths.config/'settings.yaml',settings)
    assert load_settings(paths)['verification']['timeout_ms']==60000
