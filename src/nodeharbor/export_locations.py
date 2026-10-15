"""Client folders and local export relocation; source data is never removed."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
import re
import shutil

from .storage import AppError, app_lock, atomic_write, export_root, file_lock, read_json, read_yaml, write_json, write_yaml

CLIENT_FOLDERS = {'Mihomo': 'Mihomo', 'Clash-Verge-Rev': 'clash-verge-Rev', 'v2rayN': 'v2rayN'}
GENERATION = re.compile(r'\d{8}T\d{6}Z-[0-9a-f]{8}')
HISTORY_FILES = {'free.yaml', 'free.mihomo.yaml', 'free-nodes.yaml', 'free.v2ray.txt', 'free.v2ray.base64.txt', 'report.json'}


def free_report(report):
    report = dict(report)
    report.pop('self_verification', None)
    report['scope'] = 'free'
    report['counts'] = {'free': report.get('counts', {}).get('free', 0)}
    uri = report.get('v2rayn', {}).get('free')
    report['v2rayn'] = {'free': uri} if uri is not None else {}
    return report


def client_files(client):
    result = {'report.json': 'report.json'}
    for kind in ('free',):
        if client == 'v2rayN':
            result[kind + '.txt'] = kind + '.v2ray.txt'
            result[kind + '.base64.txt'] = kind + '.v2ray.base64.txt'
        else:
            result[kind + '.yaml'] = 'free.mihomo.yaml' if client == 'Mihomo' else kind + '.yaml'
            result[kind + '-nodes.yaml'] = kind + '-nodes.yaml'
    return result


def linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def complete_legacy_formats(folder):
    from .uri_export import b64, export_uris
    original = read_json(folder / 'report.json', {})
    report = free_report(original)
    uri_report = dict(report.get('v2rayn', {}))
    changed = report != original
    if not (folder / 'free.mihomo.yaml').is_file():
        from .exporter import make_config
        nodes = read_yaml(folder / 'free-nodes.yaml', {}).get('proxies', [])
        write_yaml(folder / 'free.mihomo.yaml', make_config(nodes, standalone=True))
    for kind in ('free',):
        raw, encoded = folder / (kind + '.v2ray.txt'), folder / (kind + '.v2ray.base64.txt')
        if not raw.is_file() or not encoded.is_file() or kind not in uri_report:
            nodes = read_yaml(folder / (kind + '-nodes.yaml'), {}).get('proxies', [])
            text, uri_report[kind] = export_uris(nodes)
            if not raw.exists(): atomic_write(raw, text)
            if not encoded.exists(): atomic_write(encoded, b64(text))
            changed = True
    if changed:
        write_json(folder / 'report.json', dict(report, v2rayn=uri_report))


def copied_history_matches(source, target):
    old_files = {Path(name) for name in HISTORY_FILES if (source / name).is_file()}
    new_files = {p.relative_to(target) for p in target.rglob('*') if p.is_file()}
    if not old_files.issubset(new_files): return False
    for name in old_files:
        if (source / name).read_bytes() == (target / name).read_bytes(): continue
        if name != Path('report.json'): return False
        old, new = free_report(read_json(source / name)), free_report(read_json(target / name))
        # Conversion metadata can be added to a legacy copy, without altering its history.
        if not old['v2rayn']: new['v2rayn'] = {}
        if old != new: return False
    return True


@contextmanager
def client_export_transaction(root, generation, control=None):
    """Roll back visible client files if the generation cannot become current."""
    root, generation = Path(root), Path(generation)
    entries = []
    for client, name in CLIENT_FOLDERS.items():
        folder = root / name
        if linked(root) or linked(folder): raise AppError('导出目录不能是符号链接或目录联接。')
        folder.mkdir(parents=True, exist_ok=True)
        for target, source in client_files(client).items():
            path = folder / target
            if linked(path): raise AppError('导出文件不能指向其他位置。')
            content = (generation / source).read_text(encoding='utf-8')
            previous = path.read_text(encoding='utf-8') if path.exists() else None
            entries.append((path, content, previous))
    written = []
    try:
        completed = 0
        for path, content, previous in entries:
            if control: control.check()
            atomic_write(path, content)
            written.append((path, previous))
            if path.name == 'free.base64.txt' or path.name == 'free-nodes.yaml':
                completed += 1
                if control: control.emit('export', completed, 3, '按客户端保存免费节点文件')
        yield
    except BaseException:
        for path, previous in reversed(written):
            if previous is None: path.unlink(missing_ok=True)
            else: atomic_write(path, previous)
        raise


def relocate_exports(paths, directory, settings=None):
    """Copy history, prepare client files, then persist the new local setting."""
    from .config import load_settings
    settings = deepcopy(settings if settings is not None else load_settings(paths))
    settings['exports']['directory'] = directory.strip() if isinstance(directory, str) else directory
    destination = export_root(paths.root, directory)
    source = paths.exports
    target = destination if destination == paths.data / 'exports' else destination / '.history'
    if linked(destination) or linked(target): raise AppError('导出目录不能是符号链接或目录联接。')
    with app_lock(paths), ExitStack() as locks:
        for lock in sorted({source.resolve() / 'exports.lock', target.resolve() / 'exports.lock'}, key=str):
            locks.enter_context(file_lock(lock))
        copied = 0
        if source.resolve() != target.resolve() and source.is_dir():
            for folder in source.iterdir():
                if not GENERATION.fullmatch(folder.name): continue
                if not folder.is_dir() or linked(folder) or any(linked(p) for p in folder.rglob('*')):
                    raise AppError('历史导出包含链接，迁移已停止，原数据保持不变。')
                new = target / folder.name
                if new.exists():
                    if linked(new) or not new.is_dir() or any(linked(p) for p in new.rglob('*')):
                        raise AppError('目标历史导出目录无效。')
                    if not copied_history_matches(folder, new):
                        raise AppError('目标存在内容不同的同名历史导出，原数据保持不变。')
                else:
                    new.mkdir()
                    for name in HISTORY_FILES:
                        if (folder / name).is_file(): shutil.copyfile(folder / name, new / name)
                    copied += 1
                complete_legacy_formats(new)
        pointer = read_json(source / 'current.json', {})
        name = pointer.get('generation')
        if name:
            if not isinstance(name, str) or not GENERATION.fullmatch(name) or not (target / name).is_dir():
                raise AppError('当前导出版本无效，原设置保持不变。')
            previous_pointer = read_json(target / 'current.json', None)
            try:
                with client_export_transaction(destination, target / name):
                    write_json(target / 'current.json', pointer)
                    write_yaml(paths.config / 'settings.yaml', settings)
            except BaseException:
                if previous_pointer is None: (target / 'current.json').unlink(missing_ok=True)
                else: write_json(target / 'current.json', previous_pointer)
                raise
        else:
            write_yaml(paths.config / 'settings.yaml', settings)
        return {'copied_generations': copied, 'directory': str(destination)}
