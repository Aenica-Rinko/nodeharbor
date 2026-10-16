import os
from pathlib import Path
import tkinter as tk

import pytest
import requests

from nodeharbor.config import DEFAULTS
from nodeharbor.desktop import Desktop
from nodeharbor.exporter import publish
from nodeharbor.storage import Paths, write_yaml
from nodeharbor.verifier import compatible_nodes, running_core


def test_real_core_isolated_start_and_cleanup():
    core = Path(__file__).resolve().parents[1] / "runtime" / ("mihomo.exe" if os.name == "nt" else "mihomo")
    if not core.exists(): pytest.skip("Local Mihomo binary not installed")
    with running_core(core, []) as (url, headers, process):
        with requests.Session() as session:
            session.trust_env = False
            assert session.get(url + "/version", headers=headers, timeout=2).status_code == 200
            assert session.get(url + "/version", timeout=2).status_code == 401
        assert process.poll() is None
    assert process.poll() is not None


def test_invalid_core_nodes_are_isolated(monkeypatch):
    from nodeharbor.storage import read_yaml
    def check(core, file, cwd, control=None):
        return all(n.get('type') != 'invalid' for n in read_yaml(file)['proxies'])
    monkeypatch.setattr("nodeharbor.verifier.check_config", check)
    nodes = [{"name": "good-1"}, {"name": "invalid", "type": "invalid"}, {"name": "good-2"}]
    assert compatible_nodes("fake", nodes, lambda _: None) == [nodes[0], nodes[2]]


def test_desktop_pages_search_and_settings(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    write_yaml(paths.config / "settings.yaml", DEFAULTS)
    write_yaml(paths.config / "nodes.yaml", {"proxies": []})
    publish(paths, [{"name": "Example node", "type": "ss", "server": "example.com", "port": 443,
                     "password": "test", "cipher": "aes-256-gcm"}], {})
    try: root = tk.Tk()
    except tk.TclError: pytest.skip("Display unavailable")
    root.withdraw()
    app = Desktop(root, paths)
    try:
        for page in ["overview", "nodes", "subscriptions", "settings", "logs"]:
            app.show(page); root.update_idletasks()
            assert app.page == page
        app.show("nodes")
        assert len(app.tree.get_children()) == 1
        app.search.set("no match")
        assert len(app.tree.get_children()) == 0
        app.search.set("example")
        assert len(app.tree.get_children()) == 1
        app.show("settings")
        app.port_value.set("18299")
        app.save_settings()
        from nodeharbor.config import load_settings
        assert load_settings(paths)["subscription"]["port"] == 18299
    finally: app.close()


def test_first_run_sources_save_and_reject_invalid_urls(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw()
    app = Desktop(root, paths)
    errors = []
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: errors.append(a))
    try:
        assert app.page == 'overview'
        assert not (paths.config / 'secrets.json').exists()
        app.show('sources')
        app.extra_public_sources_text.insert('1.0', 'https://raw.githubusercontent.com/fixture/repo/main/sub\nhttps://raw.githubusercontent.com/fixture/repo/main/sub\n')
        app.add_manual_sources()
        from nodeharbor.config import load_settings
        assert load_settings(paths)['fetch']['node_pools'][0]['urls'] == ['https://raw.githubusercontent.com/fixture/repo/main/sub']
        assert load_settings(paths)['fetch']['node_pools'][0]['enabled'] is False
        before = (paths.config / 'settings.yaml').read_bytes()
        app.extra_public_sources_text.delete('1.0', 'end')
        app.extra_public_sources_text.insert('1.0', 'file:///local/config.yaml')
        from nodeharbor.storage import AppError
        with pytest.raises(AppError): app.add_manual_sources()
        assert (paths.config / 'settings.yaml').read_bytes() == before
    finally: app.close()


def test_switching_core_validates_settings_before_replacing_them(tmp_path, monkeypatch):
    from copy import deepcopy
    paths = Paths(tmp_path); paths.ensure()
    settings = deepcopy(DEFAULTS)
    settings['verification'].update(core_client='v2rayN', v2rayn_core='xray', timeout_ms=60000)
    write_yaml(paths.config / 'settings.yaml', settings)
    before = (paths.config / 'settings.yaml').read_bytes()
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    root.withdraw()
    errors = []
    monkeypatch.setattr('nodeharbor.desktop.messagebox.showerror', lambda *a, **kw: errors.append(a))
    app = Desktop(root, paths)
    try:
        app.show('settings')
        app.core_client_value.set('Mihomo'); app.change_core_client()
        app.save_settings()
        assert errors and '32767' in str(errors)
        assert (paths.config / 'settings.yaml').read_bytes() == before
    finally: app.close()
