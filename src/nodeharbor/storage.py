"""Paths, atomic file writes and a process-scoped application lock."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml


class AppError(RuntimeError):
    """An actionable application error safe to show to the user."""


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def discover(cls, root=None):
        configured = root or os.environ.get("NODEHARBOR_HOME")
        source_root = Path(__file__).resolve().parents[2]
        if configured:
            base = Path(configured)
        elif not getattr(sys, "frozen", False) and (source_root / "pyproject.toml").is_file():
            base = source_root
        elif os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NodeHarbor"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "nodeharbor"
        return cls(base.resolve())

    @property
    def config(self): return self.root / "config"
    @property
    def data(self): return self.root / "data"
    @property
    def logs(self): return self.root / "logs"
    @property
    def export_root(self):
        settings = read_yaml(self.config / 'settings.yaml', {}) or {}
        options = settings.get('exports', {}) if isinstance(settings, dict) else {}
        directory = options.get('directory', '') if isinstance(options, dict) else ''
        return export_root(self.root, directory)

    @property
    def exports(self):
        base = self.export_root
        return base if base == self.data / 'exports' else base / '.history'

    def client_exports(self, client):
        names = {'Mihomo': 'Mihomo', 'Clash-Verge-Rev': 'clash-verge-Rev', 'v2rayN': 'v2rayN'}
        if client not in names: raise AppError('请选择支持的客户端。')
        return self.export_root / names[client]

    def ensure(self):
        for p in [self.config, self.data / "state", self.exports, self.logs, self.root / "runtime"]:
            p.mkdir(parents=True, exist_ok=True)

    def current(self):
        pointer = read_json(self.exports / "current.json", {})
        name = pointer.get("generation", "")
        if not name or Path(name).name != name or name in (".", ".."):
            raise AppError("尚无可用导出，请先更新节点。")
        folder = self.exports / name
        if not folder.is_dir():
            raise AppError("导出版本不存在，请重新生成。")
        return folder


def export_root(root, directory):
    if not isinstance(directory, str) or '\x00' in directory:
        raise AppError('导出目录必须是有效的文件夹路径。')
    root = Path(root).resolve()
    if not directory.strip(): return root / 'data/exports'
    base = Path(directory.strip()).expanduser()
    base = (base if base.is_absolute() else root / base).resolve()
    if base == root / 'data/exports': return base
    protected = [root / name for name in ('config', 'logs', 'runtime', 'src', 'tests', 'scripts', '.release')]
    if base == root or any(base == p or base.is_relative_to(p) for p in protected) or base.is_relative_to(root / 'data'):
        raise AppError('导出目录不能覆盖配置、运行数据、源码或构建目录。')
    if base.exists() and not base.is_dir(): raise AppError('导出位置已存在同名文件，请选择文件夹。')
    return base


def read_yaml(path, default=None):
    try:
        with Path(path).open(encoding="utf-8-sig") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return default
    except yaml.YAMLError as exc:
        raise AppError(f"配置格式错误：{Path(path).name}") from exc


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except ValueError as exc:
        raise AppError(f"JSON 格式错误：{Path(path).name}") from exc


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def write_yaml(path, data):
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140))


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def app_lock(paths):
    """OS lock is released even after a crash; never kill another process."""
    paths.ensure()
    with file_lock(paths.data / 'state/pipeline.lock'):
        yield


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b"0"); f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AppError("另一项更新正在运行，请等待它完成。") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
