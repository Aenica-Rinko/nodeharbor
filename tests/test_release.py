import importlib.util
from pathlib import Path
import subprocess
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def test_prepare_excludes_private_data(tmp_path):
    put(tmp_path, 'README.md', 'Public documentation')
    put(tmp_path, 'config/settings.example.yaml', 'fetch: {sources: []}')
    for name in ('config/settings.yaml', 'data/state/test.json', 'backups/old.zip',
                 'runtime/mihomo.exe', '.env', 'logs/app.log', 'src/nodeharbor/__pycache__/old.pyc'):
        put(tmp_path, name, '{}')
    target, count = release.prepare(tmp_path)
    assert count == 2
    assert {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()} == {
        'README.md', 'config/settings.example.yaml'}


def test_known_private_value_blocks_without_echo(tmp_path):
    secret = 'private-fixture-' + 'credential'
    put(tmp_path, 'config/secrets.json', '{"token": "' + secret + '"}')
    put(tmp_path, 'README.md', 'Accidental copy: ' + secret)
    with pytest.raises(release.AuditError) as error:
        release.audit(tmp_path)
    assert secret not in str(error.value)
    assert 'README.md' in str(error.value)


def test_exact_known_public_source_does_not_exempt_secret_fields(tmp_path):
    import json
    url = next(iter(release.PUBLIC_SUBSCRIPTION_BASELINES))
    put(tmp_path, 'config/settings.yaml', json.dumps({'fetch': {'sources': [url]}}))
    put(tmp_path, 'README.md', url)
    assert release.audit(tmp_path)
    put(tmp_path, 'config/settings.yaml', json.dumps({'fetch': {'sources': [url + '?token=private-fixture']}}))
    put(tmp_path, 'README.md', url + '?token=private-fixture')
    with pytest.raises(release.AuditError): release.audit(tmp_path)
    put(tmp_path, 'config/secrets.json', json.dumps({'token': url}))
    put(tmp_path, 'README.md', url)
    with pytest.raises(release.AuditError): release.audit(tmp_path)


def test_common_secret_pattern_blocks(tmp_path):
    put(tmp_path, 'README.md', 'gh' + 'p_' + 'a' * 36)
    with pytest.raises(release.AuditError, match='github-token'):
        release.audit(tmp_path)


def test_private_archive_member_blocks(tmp_path):
    dist = tmp_path / 'dist'; dist.mkdir()
    with zipfile.ZipFile(dist / 'demo.whl', 'w') as archive:
        archive.writestr('nodeharbor/__init__.py', '')
        archive.writestr('config/secrets.json', '{}')
    with pytest.raises(release.AuditError, match='unexpected package member'):
        release.check_artifacts(tmp_path, dist)


def test_staged_secret_cannot_hide_behind_clean_worktree(tmp_path):
    import shutil
    if not shutil.which('git'): pytest.skip('git unavailable')
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    path = put(tmp_path, 'README.md', 'gh' + 'p_' + 'b' * 36)
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'README.md'], check=True, capture_output=True)
    path.write_text('Clean working copy', encoding='utf-8')
    with pytest.raises(release.AuditError, match='github-token'):
        release.audit(tmp_path)
