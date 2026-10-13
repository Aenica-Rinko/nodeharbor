from nodeharbor.initialize import initialize
from nodeharbor.config import load_settings
from nodeharbor.storage import Paths, read_yaml


def test_initialization_is_empty_and_preserves_existing_files(tmp_path):
    paths = Paths(tmp_path)
    assert len(initialize(paths)) == 1
    assert load_settings(paths)['fetch']['public_sources'] == []
    assert 'sources' not in load_settings(paths)['fetch']
    assert 'upload' not in load_settings(paths)
    assert not (paths.config / 'secrets.json').exists()
    assert not (paths.config / 'nodes.yaml').exists()
    personal = paths.config / 'secrets.json'
    personal.write_text('{"token":"keep-this-local"}', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in paths.config.iterdir()}
    assert initialize(paths) == []
    assert before == {p.name: p.read_bytes() for p in paths.config.iterdir()}


def test_partial_initialization_does_not_replace_nodes(tmp_path):
    paths = Paths(tmp_path); paths.ensure()
    nodes = paths.config / 'nodes.yaml'
    original = b'proxies: []\n# user comment\n'
    nodes.write_bytes(original)
    assert len(initialize(paths)) == 1
    assert nodes.read_bytes() == original


def test_cli_init_from_clean_directory(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / 'src'))
    result = subprocess.run([sys.executable, '-B', '-m', 'nodeharbor', '--home', str(tmp_path), 'init'],
                            env=env, capture_output=True)
    assert result.returncode == 0
    assert len(list((tmp_path / 'config').iterdir())) == 1
