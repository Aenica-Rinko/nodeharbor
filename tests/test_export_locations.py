from copy import deepcopy
from pathlib import Path
import threading

import pytest
import requests

from nodeharbor.config import DEFAULTS, load_settings
from nodeharbor.exporter import publish
from nodeharbor.export_locations import relocate_exports
from nodeharbor.storage import AppError, Paths, read_json, write_yaml
from nodeharbor.subscription import create_server


def configured(tmp_path):
    paths = Paths(tmp_path / 'profile')
    paths.ensure()
    write_yaml(paths.config / 'settings.yaml', deepcopy(DEFAULTS))
    write_yaml(paths.config / 'nodes.yaml', {'proxies': []})
    return paths


def node(password='fixture-only'):
    return {'name': 'Example', 'type': 'ss', 'server': 'example.com', 'port': 443,
            'cipher': 'aes-256-gcm', 'password': password}


def test_relocation_preserves_history_and_client_formats(tmp_path):
    paths = configured(tmp_path)
    first, _ = publish(paths, [node('first')], {})
    old, report = publish(paths, [node('second')], {})
    old_bytes = (old / 'free.yaml').read_bytes()
    result = relocate_exports(paths, str(tmp_path / 'output'))
    assert result['copied_generations'] == 2
    assert paths.current() == tmp_path / 'output/.history' / old.name
    assert (paths.exports / first.name).is_dir()
    assert old.is_dir() and (old / 'free.yaml').read_bytes() == old_bytes
    for client in ('Mihomo', 'Clash-Verge-Rev'):
        assert (paths.client_exports(client) / 'free.yaml').read_bytes() == (old / ('free.mihomo.yaml' if client == 'Mihomo' else 'free.yaml')).read_bytes()
        assert (paths.client_exports(client) / 'free-nodes.yaml').read_bytes() == (old / 'free-nodes.yaml').read_bytes()
    v2 = paths.client_exports('v2rayN')
    assert (v2 / 'free.txt').read_bytes() == (old / 'free.v2ray.txt').read_bytes()
    assert (v2 / 'free.base64.txt').read_bytes() == (old / 'free.v2ray.base64.txt').read_bytes()
    assert not list(v2.glob('*.yaml'))
    assert read_json(v2 / 'report.json')['generation'] == report['generation']


def test_future_exports_and_subscription_follow_new_directory(tmp_path):
    paths = configured(tmp_path)
    publish(paths, [node()], {})
    relocate_exports(paths, str(tmp_path / 'output'))
    folder, _ = publish(paths, [node('updated')], {})
    assert folder.parent == tmp_path / 'output/.history'
    assert (paths.client_exports('Mihomo') / 'free.yaml').read_bytes() == (folder / 'free.mihomo.yaml').read_bytes()
    server = create_server(paths, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with requests.Session() as session:
            session.trust_env = False
            for name in ('free.yaml', 'free.v2ray.base64.txt'):
                result = session.get(f'http://127.0.0.1:{server.server_port}/{name}', timeout=2)
                assert result.status_code == 200 and result.content == (folder / name).read_bytes()
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_failed_pointer_commit_rolls_back_client_files(tmp_path, monkeypatch):
    paths = configured(tmp_path)
    old, _ = publish(paths, [node()], {})
    files = {p: p.read_bytes() for client in ('Mihomo', 'Clash-Verge-Rev', 'v2rayN') for p in paths.client_exports(client).iterdir()}
    import nodeharbor.exporter as exporter
    real_write = exporter.write_json
    def fail_pointer(path, data):
        if Path(path).name == 'current.json': raise OSError('fixture error')
        real_write(path, data)
    monkeypatch.setattr(exporter, 'write_json', fail_pointer)
    with pytest.raises(OSError): publish(paths, [node('new')], {})
    assert paths.current() == old
    assert all(p.read_bytes() == data for p, data in files.items())


def test_relocation_collision_keeps_original_configuration(tmp_path):
    paths = configured(tmp_path)
    old, _ = publish(paths, [node()], {})
    conflicting = tmp_path / 'output/.history' / old.name
    conflicting.mkdir(parents=True)
    (conflicting / 'report.json').write_text('{}')
    with pytest.raises(AppError, match='同名历史导出'):
        relocate_exports(paths, str(tmp_path / 'output'))
    assert paths.current() == old and load_settings(paths)['exports']['directory'] == ''


@pytest.mark.parametrize('name', ['config', 'data', 'runtime', 'src', 'scripts', '.release'])
def test_protected_locations_are_rejected(tmp_path, name):
    paths = configured(tmp_path)
    with pytest.raises(AppError): relocate_exports(paths, str(paths.root / name))
    assert load_settings(paths)['exports']['directory'] == ''


def test_relative_path_and_empty_profile(tmp_path):
    paths = configured(tmp_path)
    relocate_exports(paths, 'output')
    assert paths.export_root == paths.root / 'output'
    folder, _ = publish(paths, [node()], {})
    assert folder.parent == paths.root / 'output/.history'


def test_legacy_formats_are_added_without_changing_verification(tmp_path):
    from nodeharbor.storage import write_json
    paths = configured(tmp_path)
    old, report = publish(paths, [node()], {'verified_at': '2020-01-01T00:00:00Z'})
    for file in old.glob('*.v2ray*.txt'): file.unlink()
    report.pop('v2rayn')
    write_json(old / 'report.json', report)
    relocate_exports(paths, str(tmp_path / 'output'))
    current = paths.current()
    assert (current / 'free.v2ray.txt').is_file()
    converted = read_json(current / 'report.json')
    assert converted['verified_at'] == report['verified_at']
    assert converted['counts'] == report['counts']
    assert converted['v2rayn']['free']['exported'] == 1
    assert not (old / 'free.v2ray.txt').exists()
    # A second relocation to an already copied history remains safe and repeatable.
    settings = load_settings(paths); settings['exports']['directory'] = ''
    write_yaml(paths.config / 'settings.yaml', settings)
    relocate_exports(paths, str(tmp_path / 'output'))


def test_relocation_only_copies_free_data_from_old_export(tmp_path):
    from nodeharbor.storage import write_json
    paths = configured(tmp_path)
    old, report = publish(paths, [node()], {})
    (old / 'self.yaml').write_text('legacy-private-data')
    (old / 'combined.yaml').write_text('legacy-private-data')
    report['counts'] = {'free': 1, 'self': 3, 'combined': 4}
    report['v2rayn']['self'] = {'exported': 3}
    report['self_verification'] = 'not_tested'
    write_json(old / 'report.json', report)
    relocate_exports(paths, str(tmp_path / 'output'))
    assert not (paths.current() / 'self.yaml').exists()
    assert not (paths.current() / 'combined.yaml').exists()
    migrated = read_json(paths.current() / 'report.json')
    assert migrated['scope'] == 'free' and migrated['counts'] == {'free': 1}
    assert set(migrated['v2rayn']) == {'free'} and 'self_verification' not in migrated
    assert (old / 'self.yaml').read_text() == 'legacy-private-data'
