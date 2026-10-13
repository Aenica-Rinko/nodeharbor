import base64
from copy import deepcopy
import json

import pytest
import yaml

from nodeharbor.config import DEFAULTS
from nodeharbor.exporter import make_config, publish, validate_references
from nodeharbor.storage import AppError, Paths, app_lock, write_yaml, write_json


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path); p.ensure()
    write_yaml(p.config / "settings.yaml", deepcopy(DEFAULTS))
    return p


def node(name="Example", server="example.com"):
    return {"name": name, "type": "ss", "server": server, "port": 443, "cipher": "aes-256-gcm", "password": "test-password"}


def test_free_formats_match_without_reading_legacy_configuration(paths):
    for name in ('nodes.yaml', 'client.yaml', 'servers.json'):
        (paths.config / name).write_text('not a valid legacy configuration')
    folder, report = publish(paths, [node(), node()], {})
    assert report['counts'] == {'free': 1} and report['scope'] == 'free'
    assert {p.name for p in folder.iterdir()} == {'free.yaml', 'free-nodes.yaml', 'free.mihomo.yaml',
                                                'free.v2ray.txt', 'free.v2ray.base64.txt', 'report.json'}
    for kind in ['free']:
        config = yaml.safe_load((folder / f"{kind}.yaml").read_text(encoding="utf-8"))
        nodes = yaml.safe_load((folder / f"{kind}-nodes.yaml").read_text(encoding="utf-8"))["proxies"]
        assert config["proxies"] == nodes
        assert not (folder / "free.base64.txt").exists()
        validate_references(config)
    for client in ('Mihomo', 'Clash-Verge-Rev', 'v2rayN'):
        assert not any(p.name.startswith(('self', 'combined')) for p in paths.client_exports(client).iterdir())


def test_group_collisions_and_group_references_are_valid():
    config = make_config([node("自动选择"), node("🇯🇵 日本", "other.example.com")])
    validate_references(config)
    selection = config["proxy-groups"][0]["proxies"]
    assert "自动选择" in selection and "🇯🇵 日本" in selection
    assert config["proxies"][0]["name"] != "自动选择"


def test_rejected_generation_does_not_replace_current(paths, monkeypatch):
    previous, _ = publish(paths, [node()], {})
    monkeypatch.setattr("nodeharbor.exporter.check_config", lambda *args: False)
    with pytest.raises(AppError): publish(paths, [node("new")], {}, core="fake")
    assert paths.current() == previous
    assert len(list(paths.exports.glob("20*"))) == 1


def test_lock_rejects_parallel_job_and_recovers(paths):
    with app_lock(paths):
        with pytest.raises(AppError):
            with app_lock(paths): pass
    with app_lock(paths): pass


def test_failed_pipeline_returns_error_and_preserves_exports(paths, monkeypatch):
    from nodeharbor.pipeline import execute
    folder, _ = publish(paths, [node()], {})
    monkeypatch.setattr("nodeharbor.pipeline.find_mihomo", lambda *args: "fake")
    monkeypatch.setattr("nodeharbor.pipeline.validate_core", lambda *args: "mihomo")
    monkeypatch.setattr("nodeharbor.pipeline.verify_nodes", lambda *args: ([], {}))
    with pytest.raises(AppError): execute(paths, "verify")
    assert paths.current() == folder
    state = json.loads((paths.data / "state" / "last-run.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"


def test_successful_refresh_commits_verified_report(paths, monkeypatch):
    from nodeharbor.pipeline import execute
    monkeypatch.setattr("nodeharbor.pipeline.find_mihomo", lambda *args: "fake")
    monkeypatch.setattr("nodeharbor.pipeline.validate_core", lambda *args: "mihomo")
    monkeypatch.setattr("nodeharbor.pipeline.fetch_all", lambda *args, **kwargs: ([node()], {"sources_ok": 1}))
    monkeypatch.setattr("nodeharbor.pipeline.verify_nodes", lambda *args: ([node()], {"verified": 1}))
    monkeypatch.setattr("nodeharbor.exporter.check_config", lambda *args: True)
    report = execute(paths, "refresh")
    assert report["counts"]["free"] == 1
    assert report["verified_at"]


def test_cli_missing_core_has_nonzero_exit(paths, monkeypatch, capsys):
    from nodeharbor.cli import main
    monkeypatch.delenv("MIHOMO_PATH", raising=False)
    monkeypatch.setattr("nodeharbor.config.shutil.which", lambda *args: None)
    assert main(["--home", str(paths.root), "refresh"]) == 1
    assert "Mihomo" in capsys.readouterr().err
