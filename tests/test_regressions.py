import base64
import json
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.exporter import publish, prune_exports
from nodeharbor.fetcher import fetch_all, fetch_one
from nodeharbor.nodes import normalize_nodes, parse_subscription
from nodeharbor.pipeline import execute
from nodeharbor.storage import AppError, Paths, app_lock, read_json, read_yaml, write_json, write_yaml
from nodeharbor.tasks import Cancelled, DeadlineExceeded, TaskControl
from nodeharbor.verifier import base_config, check_config, running_core


@pytest.fixture
def paths(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / "settings.yaml", deepcopy(DEFAULTS))
    write_yaml(paths.config / "nodes.yaml", {"proxies": []})
    yield paths
    # Windows cannot remove an open rotating log in a temporary application root.
    import logging
    logger = logging.getLogger("nodeharbor." + str(paths.root))
    for handler in list(logger.handlers):
        handler.close(); logger.removeHandler(handler)


def node(**kwargs):
    return dict({"name": "Example", "type": "ss", "server": "example.com", "port": 443,
                 "cipher": "aes-256-gcm", "password": "test"}, **kwargs)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
@pytest.mark.parametrize("section,key", [("fetch", "use_history"), ('fetch', 'auto_local_proxy')])
def test_boolean_settings_reject_non_booleans(paths, value, section, key):
    settings = deepcopy(DEFAULTS); settings[section][key] = value
    write_yaml(paths.config / "settings.yaml", settings)
    with pytest.raises(AppError, match="布尔值"): load_settings(paths)




@pytest.mark.parametrize("invalid", [[], None, 3, "bad", {}, {"port": "bad"}])
def test_malformed_vmess_keeps_valid_entries(invalid):
    malformed = "vmess://" + base64.b64encode(json.dumps(invalid).encode()).decode()
    valid = "ss://YWVzLTI1Ni1nY206dGVzdA@example.com:443#good"
    result = parse_subscription(malformed + "\n" + valid)
    assert len(result) == 1 and result[0]["name"] == "good"


def test_broken_source_is_isolated_and_source_order_is_stable(monkeypatch):
    settings = deepcopy(DEFAULTS)
    settings['fetch']['github_public_pool'] = False
    settings["fetch"].update(github_sources=[], public_sources=["https://raw.githubusercontent.com/fixture/repo/main/"+s for s in ["bad", "slow", "fast"]])
    def fetch(url, options, control):
        if url.endswith("bad"): raise AttributeError("secret URL must not be logged")
        if url.endswith("slow"): time.sleep(0.05)
        return [node(name=url.rsplit("/", 1)[-1])]
    monkeypatch.setattr("nodeharbor.fetcher.fetch_one", fetch)
    logs = []
    nodes, report = fetch_all(settings, logs.append)
    assert nodes[0]["name"] == "slow"
    assert report["sources_ok"] == 2 and report["sources_failed"] == 1
    assert all("secret" not in line for line in logs)


def test_tls_variants_are_not_deduplicated():
    assert len(normalize_nodes([node(**{"client-fingerprint": "chrome"}),
                                node(**{"client-fingerprint": "firefox"}),
                                node(**{"skip-cert-verify": True}),
                                node(**{"skip-cert-verify": False})])) == 4


def test_obfs_uri_passes_actual_core(tmp_path):
    nodes = parse_subscription("ss://YWVzLTEyOC1nY206dGVzdA@example.com:443?plugin=obfs-local%3Bobfs%3Dtls%3Bobfs-host%3Dexample.org#obfs")
    assert nodes[0]["plugin-opts"] == {"mode": "tls", "host": "example.org"}
    core = Path(__file__).resolve().parents[1] / "runtime" / ("mihomo.exe" if os.name == "nt" else "mihomo")
    if not core.exists(): pytest.skip("Mihomo unavailable")
    write_yaml(tmp_path / "test.yaml", base_config(nodes))
    assert check_config(core, tmp_path / "test.yaml", tmp_path)


@contextmanager
def local_source(slow=False):
    started = threading.Event()
    stopped = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            started.set()
            try:
                if slow:
                    while not stopped.wait(0.05):
                        self.wfile.write(b"x"); self.wfile.flush()
                else:
                    self.wfile.write(b"ss://YWVzLTI1Ni1nY206dGVzdA@example.com:443#local")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}", started
    finally:
        stopped.set(); server.shutdown(); server.server_close(); thread.join(2)


def test_real_worker_fetches_local_subscription():
    with local_source() as (url, _):
        assert fetch_one(url, DEFAULTS["fetch"])[0]["name"] == "local"


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop interpreter")
def test_pythonw_worker_can_return_results(monkeypatch):
    import sys
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists(): pytest.skip("pythonw unavailable")
    monkeypatch.setattr("nodeharbor.tasks.sys.executable", str(pythonw))
    with local_source() as (url, _):
        assert fetch_one(url, DEFAULTS["fetch"])[0]["name"] == "local"


def test_slow_drip_has_a_total_source_deadline(monkeypatch):
    import nodeharbor.tasks as tasks
    original = tasks.subprocess.Popen
    processes = []
    def record(*args, **kwargs):
        process = original(*args, **kwargs); processes.append(process); return process
    monkeypatch.setattr(tasks.subprocess, "Popen", record)
    options = dict(DEFAULTS["fetch"], source_timeout_seconds=2)
    with local_source(slow=True) as (url, started):
        before = time.monotonic()
        with pytest.raises(DeadlineExceeded): fetch_one(url, options)
        assert started.is_set()
        assert time.monotonic() - before < 5
    assert processes and all(p.poll() is not None for p in processes)


def test_cancel_interrupts_slow_response():
    control = TaskControl()
    with local_source(slow=True) as (url, started):
        canceller = threading.Thread(target=lambda: (started.wait(5), control.cancel()))
        canceller.start()
        before = time.monotonic()
        with pytest.raises(Cancelled): fetch_one(url, DEFAULTS["fetch"], control)
        canceller.join(5)
        assert time.monotonic() - before < 5


def test_cancel_before_publish_preserves_current_and_releases_lock(paths, monkeypatch):
    previous, _ = publish(paths, [node()], {})
    control = TaskControl()
    monkeypatch.setattr("nodeharbor.pipeline.find_mihomo", lambda *args: "fake")
    monkeypatch.setattr("nodeharbor.pipeline.validate_core", lambda *args: "mihomo")
    def cancel(*args): control.cancel(); return True
    monkeypatch.setattr("nodeharbor.exporter.check_config", cancel)
    with pytest.raises(Cancelled): execute(paths, "export", control=control)
    assert paths.current() == previous
    assert len(list(paths.exports.glob("20*"))) == 1
    assert read_json(paths.data / "state" / "last-run.json")["status"] == "cancelled"
    with app_lock(paths): pass


def test_task_deadline_preserves_current(paths, monkeypatch):
    previous, _ = publish(paths, [node()], {})
    control = TaskControl(timeout=0)
    with pytest.raises(DeadlineExceeded): execute(paths, "export", control=control)
    assert paths.current() == previous
    assert read_json(paths.data / "state" / "last-run.json")["status"] == "failed"


def test_cancel_reclaims_owned_core():
    core = Path(__file__).resolve().parents[1] / "runtime" / ("mihomo.exe" if os.name == "nt" else "mihomo")
    if not core.exists(): pytest.skip("Mihomo unavailable")
    control = TaskControl()
    with pytest.raises(Cancelled):
        with running_core(core, [], control) as (_, _, process):
            control.cancel(); control.check()
    assert process.poll() is not None


@pytest.mark.parametrize("cancel", [False, True])
def test_verification_processes_results_and_reclaims_core(monkeypatch, cancel):
    from nodeharbor.verifier import verify_nodes
    core = Path(__file__).resolve().parents[1] / "runtime" / ("mihomo.exe" if os.name == "nt" else "mihomo")
    if not core.exists(): pytest.skip("Mihomo unavailable")
    control = TaskControl()
    processes = []
    @contextmanager
    def recorded_core(*args):
        with running_core(*args) as instance:
            processes.append(instance[2]); yield instance
    def network(operation, payload, control, timeout):
        if operation == "tcp": return True
        if cancel: control.cancel(); control.check()
        return 12
    monkeypatch.setattr("nodeharbor.verifier.running_core", recorded_core)
    monkeypatch.setattr("nodeharbor.tasks.NetworkPool.request", lambda self, operation, payload, timeout: network(operation, payload, self.control, timeout))
    if cancel:
        with pytest.raises(Cancelled): verify_nodes(core, [node()], DEFAULTS["verification"], lambda _: None, control)
    else:
        alive, report = verify_nodes(core, [node()], DEFAULTS["verification"], lambda _: None, control)
        assert alive == [node()] and report["verified"] == 1
    assert processes and all(p.poll() is not None for p in processes)


def test_desktop_close_cancels_running_download(paths, monkeypatch):
    import tkinter as tk
    from nodeharbor.desktop import Desktop
    previous, _ = publish(paths, [node()], {})
    monkeypatch.setattr("nodeharbor.pipeline.find_mihomo", lambda *args: "fake")
    monkeypatch.setattr("nodeharbor.pipeline.validate_core", lambda *args: "mihomo")
    try: root = tk.Tk()
    except tk.TclError: pytest.skip("Display unavailable")
    root.withdraw()
    closed = threading.Event()
    destroy = root.destroy
    def destroy_and_record(): destroy(); closed.set()
    monkeypatch.setattr(root, "destroy", destroy_and_record)
    app = Desktop(root, paths)
    try:
        with local_source(slow=True) as (url, started):
            settings = deepcopy(DEFAULTS); settings["fetch"]["sources"] = [url]; settings['fetch']['github_public_pool'] = False
            write_yaml(paths.config / "settings.yaml", settings)
            monkeypatch.setattr("nodeharbor.fetcher.selected_pools", lambda options: [("Loopback fixture", url)])
            app.run("refresh-github")
            deadline = time.monotonic() + 5
            while not started.is_set() and time.monotonic() < deadline:
                root.update(); time.sleep(0.01)
            assert started.is_set()
            app.close()
            deadline = time.monotonic() + 5
            while not closed.is_set() and time.monotonic() < deadline:
                root.update(); time.sleep(0.01)
            assert closed.is_set() and not app.busy
            assert paths.current() == previous
            assert read_json(paths.data / "state" / "last-run.json")["status"] == "cancelled"
    finally:
        if not closed.is_set():
            app.cancel_task()
            root.after_cancel(app.poll_id)
            root.destroy()


def test_retention_protects_current_even_if_old(paths):
    folders = [publish(paths, [node()], {})[0] for _ in range(6)]
    for i, folder in enumerate(folders): os.utime(folder, (100 + i, 100 + i))
    write_json(paths.exports / "current.json", {"generation": folders[0].name})
    unrelated = paths.exports / "notes"; unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep")
    with app_lock(paths): prune_exports(paths, 3)
    assert paths.current() == folders[0]
    assert all(p.exists() for p in (folders[0], folders[4], folders[5], unrelated))
    assert all(not p.exists() for p in folders[1:4])


def test_failed_export_does_not_prune_history(paths, monkeypatch):
    folders = [publish(paths, [node()], {})[0] for _ in range(6)]
    monkeypatch.setattr("nodeharbor.pipeline.find_mihomo", lambda *args: "fake")
    monkeypatch.setattr("nodeharbor.pipeline.validate_core", lambda *args: "mihomo")
    monkeypatch.setattr("nodeharbor.exporter.check_config", lambda *args: False)
    with pytest.raises(AppError): execute(paths, "export")
    assert all(p.exists() for p in folders)


