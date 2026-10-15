from __future__ import annotations

import os
import shutil
import sys
import re
from copy import deepcopy
from pathlib import Path

from .storage import AppError, read_yaml

DEFAULTS = {
    "version": 1,
    "fetch": {"workers": 6, "timeout_seconds": 15, "retries": 2,
              "source_timeout_seconds": 60,
              "proxy": "", "github_mirror": "", "use_history": True,
              "max_response_mb": 16, "cache_minutes": 10, 'stale_cache_hours': 24,
              'max_candidates': 1200, 'nodes_per_source': 200,
              "github_sources": None, "public_sources": [], "node_pools": [], "auto_local_proxy": True},
    "verification": {"mihomo_path": "", "workers": 24, "batch_size": 200,
                     "core_client": "Mihomo", "core_paths": {"Mihomo": "", "Clash-Verge-Rev": "", "v2rayN": ""},
                     "v2rayn_core": "xray",
                     "tcp_timeout_seconds": 2, "timeout_ms": 6000,
                     "test_url": "http://cp.cloudflare.com/", "https_test_url": "https://www.gstatic.com/generate_204",
                     "rounds": 2, "retry_failed": 1, "max_delay_ms": 5000},
    "subscription": {"port": 8199},
    "task": {"timeout_seconds": 1800},
    "exports": {"keep_generations": 5, "directory": "", "mihomo_port": 7890},
}


def load_settings(paths):
    raw = read_yaml(paths.config / "settings.yaml", {})
    if not isinstance(raw, dict): raise AppError("settings.yaml 必须是键值配置。")
    out = deepcopy(DEFAULTS)
    for key, value in raw.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict): out[key].update(value)
        else: out[key] = value
    out.pop('upload', None)
    for section in ("fetch", "verification", "subscription", "task", "exports"):
        if not isinstance(out.get(section), dict): raise AppError(f"配置项 {section} 必须是对象。")
    for key in ('sources', 'github_public_pool'): out['fetch'].pop(key, None)
    for section, key in (("fetch", "use_history"), ('fetch', 'auto_local_proxy')):
        if type(out[section].get(key)) is not bool:
            raise AppError(f"{section}.{key} 必须是布尔值 true/false，不能加引号。")
    verification = out['verification']
    if verification.get('core_client') not in ('Mihomo', 'Clash-Verge-Rev', 'v2rayN'):
        raise AppError('请选择 Mihomo、Clash-Verge-Rev 或 v2rayN 作为检测客户端。')
    profiles = verification.get('core_paths')
    if not isinstance(profiles, dict) or any(k not in DEFAULTS['verification']['core_paths'] or not isinstance(v, str) for k, v in profiles.items()):
        raise AppError('各客户端核心路径必须是文本。')
    verification['core_paths'] = dict(DEFAULTS['verification']['core_paths'], **profiles)
    if verification.get('v2rayn_core') not in ('xray', 'mihomo'):
        raise AppError('v2rayN 检测核心请选择 Xray 或 Mihomo。')
    for value in (verification.get('mihomo_path'), out['fetch'].get('proxy'), out['fetch'].get('github_mirror')):
        if not isinstance(value, str): raise AppError('核心路径、下载代理和镜像地址必须是文本。')
    for section, key, low, high in [
        ("fetch", "workers", 1, 100), ("fetch", "timeout_seconds", 1, 120),
        ("fetch", "retries", 0, 5), ("fetch", "max_response_mb", 1, 64),
        ("fetch", "source_timeout_seconds", 1, 600),
        ("fetch", "cache_minutes", 0, 1440), ("exports", "mihomo_port", 1024, 65535),
        ('fetch', 'stale_cache_hours', 0, 168), ('fetch', 'max_candidates', 1, 10000),
        ('fetch', 'nodes_per_source', 1, 2000),
        ("verification", "rounds", 2, 5), ("verification", "retry_failed", 0, 2),
        ("task", "timeout_seconds", 1, 86400), ("exports", "keep_generations", 2, 100),
        ("verification", "workers", 1, 100), ("verification", "batch_size", 1, 500),
        ("verification", "timeout_ms", 500, 60000), ("verification", "max_delay_ms", 1, 60000),
        ("verification", "tcp_timeout_seconds", 1, 30), ("subscription", "port", 1024, 65535),
    ]:
        value = out[section].get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise AppError(f"{section}.{key} 必须是 {low}–{high} 的整数。")
    from urllib.parse import urlsplit
    # Mihomo's delay endpoint parses a signed 16-bit millisecond timeout.
    if core_kind(out) == 'mihomo' and verification['timeout_ms'] > 32767:
        raise AppError('verification.timeout_ms：Mihomo 接口最多支持 32767 毫秒，请减小检测超时。')
    for key, scheme in [('test_url', 'http'), ('https_test_url', 'https')]:
        value = verification.get(key)
        try:
            u = urlsplit(value)
            if u.scheme not in ({'http', 'https'} if key == 'test_url' else {'https'}) or not u.hostname or u.username or u.password:
                raise ValueError()
        except (TypeError, ValueError): raise AppError(f'verification.{key} 必须是无认证的 {scheme.upper()} 测试地址。') from None
    from .public_sources import GITHUB_POOLS, github_file_url
    chosen = out['fetch']['github_sources']
    if chosen is not None and (not isinstance(chosen, list) or any(not isinstance(name, str) or name not in dict(GITHUB_POOLS) for name in chosen)):
        raise AppError('公开源选择必须是来源列表中的仓库名称。')
    extra = out['fetch']['public_sources']
    if not isinstance(extra, list) or any(not isinstance(url, str) for url in extra): raise AppError('额外公开源必须是 GitHub 地址列表。')
    out['fetch']['public_sources'] = list(dict.fromkeys(github_file_url(url) for url in extra))
    from .source_catalog import validate_pools
    out['fetch']['node_pools'] = validate_pools(out['fetch']['node_pools'])
    from .storage import export_root
    export_root(paths.root, out['exports']['directory'])
    return out


def core_kind(settings):
    options = settings['verification']
    return options.get('v2rayn_core', 'xray') if options.get('core_client') == 'v2rayN' else 'mihomo'


def resolve_core_path(paths, configured, kind='mihomo'):
    p = Path(configured).expanduser()
    if not p.is_absolute(): p = paths.root / p
    gui_names = {'clash party.exe', 'clash-party.exe', 'clash-verge.exe', 'clash verge.exe', 'v2rayn.exe', 'nodeharbor.exe'}
    if p.is_file() and p.name.lower() not in gui_names: return p.resolve()
    if p.is_dir() or (p.is_file() and p.name.lower() in gui_names):
        root = p if p.is_dir() else p.parent
        names = ('mihomo.exe', 'verge-mihomo.exe', 'clash-meta.exe', 'mihomo') if kind == 'mihomo' else ('xray.exe', 'xray')
        # Bounded search within the directory explicitly selected by the user.
        visited = 0
        for folder, children, files in os.walk(root, followlinks=False):
            visited += 1
            if visited > 500: break
            base = Path(folder)
            if len(base.relative_to(root).parts) >= 4: children[:] = []
            children[:] = sorted(c for c in children if not (base / c).is_symlink()
                                 and not getattr(base / c, 'is_junction', lambda: False)()
                                 and (base / c).resolve().is_relative_to(root.resolve()))
            for name in names:
                if name in files: return (base / name).resolve()
        raise AppError(f'选中的是客户端或安装目录，但没有找到 {"Xray" if kind == "xray" else "Mihomo"} 核心。请选择真正的核心文件，或下载对应核心。')
    raise AppError('选择的核心文件不存在，请在设置中重新选择。')


def validate_core(core, kind='mihomo', control=None):
    import subprocess
    from .tasks import TaskControl, DeadlineExceeded
    control = control or TaskControl()
    try:
        with subprocess.Popen([str(core), 'version' if kind == 'xray' else '-v'], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            out, err = control.communicate(process, timeout=5)
            output = (out + err).decode('utf-8', errors='replace').lower()
            expected = 'xray' if kind == 'xray' else 'mihomo'
            if process.returncode == 0 and re.search(rf'(?m)^{expected}\s+\S+', output): return expected
    except OSError as exc:
        raise AppError('核心无法运行，请检查文件权限、系统架构，或重新下载对应核心。') from exc
    except DeadlineExceeded:
        control.check()
        raise AppError('核心身份检查超时。请确认选择的是核心文件，而不是客户端主程序。') from None
    raise AppError(f'所选文件不是可用的 {"Xray" if kind == "xray" else "Mihomo"} 核心。请勿直接选择客户端主程序；应选择它内置的核心文件。')


def find_mihomo(paths, settings):
    options = settings['verification']
    client = options.get('core_client', 'Mihomo')
    configured = (os.environ.get("MIHOMO_PATH") or options.get('core_paths', {}).get(client)
                  or options.get("mihomo_path"))
    if configured:
        return resolve_core_path(paths, configured)
    directories = [paths.root / "runtime"]
    if getattr(sys, "frozen", False):
        directories.append(Path(sys.executable).parent / "runtime")
    for name in ("mihomo.exe", "mihomo"):
        for folder in directories:
            p = folder / name
            if p.is_file(): return p
        found = shutil.which(name)
        if found: return Path(found)
    raise AppError("未找到 Mihomo。请放入 runtime 目录，或在设置中选择 mihomo.exe。")


def find_core(paths, settings):
    if core_kind(settings) == 'mihomo': return find_mihomo(paths, settings)
    configured = os.environ.get('XRAY_PATH') or settings['verification'].get('core_paths', {}).get('v2rayN')
    if configured: return resolve_core_path(paths, configured, 'xray')
    p = paths.root / 'runtime' / ('xray.exe' if os.name == 'nt' else 'xray')
    if p.is_file(): return p
    found = shutil.which('xray.exe' if os.name == 'nt' else 'xray')
    if found: return Path(found)
    raise AppError('未找到 Xray 核心。请在设置中选择 v2rayN 的 xray.exe 或客户端主程序以自动定位核心。')


