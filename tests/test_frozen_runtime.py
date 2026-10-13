import sys

import pytest

from nodeharbor.config import DEFAULTS, find_mihomo
from nodeharbor.storage import AppError, Paths
from nodeharbor.tasks import worker_command


def test_frozen_worker_uses_sibling_console_and_requires_it(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'NodeHarbor.exe'))
    with pytest.raises(AppError, match='完整解压'):
        worker_command()
    helper = tmp_path / 'NodeHarbor.Console.exe'; helper.touch()
    assert worker_command() == [str(helper), '--network-worker']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows bundle paths')
def test_frozen_default_data_is_user_directory_and_home_override_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.delenv('NODEHARBOR_HOME', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
    assert Paths.discover().root == (tmp_path / 'profile' / 'NodeHarbor').resolve()
    assert Paths.discover(tmp_path / 'explicit').root == (tmp_path / 'explicit').resolve()


def test_frozen_core_can_live_next_to_exe_without_being_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'app' / 'NodeHarbor.exe'))
    monkeypatch.delenv('MIHOMO_PATH', raising=False)
    core = tmp_path / 'app/runtime/mihomo.exe'; core.parent.mkdir(parents=True); core.touch()
    assert find_mihomo(Paths(tmp_path / 'data'), DEFAULTS) == core
