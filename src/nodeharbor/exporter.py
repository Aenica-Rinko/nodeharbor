"""Publish complete immutable generations and switch one atomic pointer."""
from __future__ import annotations

import re
import shutil
import uuid
from copy import deepcopy
from datetime import datetime, timezone

import yaml

from .geo import get_country_from_name, COUNTRY_FLAG, COUNTRY_NAME
from .nodes import normalize_nodes
from .storage import AppError, atomic_write, write_json, write_yaml, file_lock
from .export_locations import client_export_transaction
from .verifier import check_config
from .tasks import TaskControl
from .uri_export import b64, export_uris

BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}


def make_config(nodes, client=None, standalone=False, port=7890):
    nodes = deepcopy(normalize_nodes(nodes, strict=True))
    countries = {}
    for p in nodes:
        cc = get_country_from_name(p["name"])
        if cc: countries.setdefault(cc, []).append(p)
    country_names = {cc: f"{COUNTRY_FLAG.get(cc, '')} {COUNTRY_NAME.get(cc, cc)}".strip() for cc in countries}
    reserved = BUILTINS | {"节点选择", "自动选择", "手动切换"} | set(country_names.values())
    used = set(reserved)
    for p in nodes:
        name, original, n = p["name"], p["name"], 1
        while name in used:
            name = f"{original} · {n}"; n += 1
        p["name"] = name; used.add(name)
    groups = [{"name": "节点选择", "type": "select", "proxies": ["自动选择", "手动切换", *country_names.values(), "DIRECT"]}]
    if nodes:
        groups.extend([
            {"name": "自动选择", "type": "url-test", "proxies": [p["name"] for p in nodes],
             "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 50},
            {"name": "手动切换", "type": "select", "proxies": [p["name"] for p in nodes]},
        ])
    else:
        groups[0]["proxies"] = ["DIRECT"]
    for cc, members in countries.items():
        groups.append({"name": country_names[cc], "type": "url-test", "proxies": [p["name"] for p in members],
                       "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 50})
    config = deepcopy(client or {})
    # Avoid implicit GeoIP database downloads from Mihomo's default DNS fallback filter.
    if isinstance(config.get("dns"), dict) and config["dns"].get("fallback"):
        config["dns"].setdefault("fallback-filter", {"geoip": False})
    for key in ["external-controller", "external-controller-tls", "secret", "tun", "listeners", "proxy-providers", "rule-providers"]:
        config.pop(key, None)
    config.update({"proxies": nodes, "proxy-groups": groups, "rules": ["MATCH,节点选择"]})
    if standalone:
        config.update({'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
                       'mode': 'rule', 'log-level': 'warning'})
    validate_references(config)
    return config


def validate_references(config):
    nodes = [p["name"] for p in config.get("proxies", [])]
    groups = config.get("proxy-groups", [])
    group_names = [g["name"] for g in groups]
    if len(set(nodes + group_names)) != len(nodes + group_names): raise AppError("配置包含重复节点名或策略组名。")
    valid = set(nodes + group_names) | BUILTINS
    for group in groups:
        if not group.get("proxies") and not group.get("include-all"): raise AppError("配置包含空策略组。")
        for name in group.get("proxies", []):
            if name not in valid: raise AppError("配置包含无效策略组引用。")


def publish(paths, free, report, core=None, control=None, port=7890):
    with file_lock(paths.exports / 'exports.lock'):
        return _publish(paths, free, report, core, control, port)


def _publish(paths, free, report, core=None, control=None, port=7890):
    control = control or TaskControl()
    control.check()
    free = normalize_nodes(free, strict=True)
    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    folder = paths.exports / generation
    folder.mkdir(parents=True)
    try:
        full = make_config(free)
        write_yaml(folder / 'free.yaml', full)
        write_yaml(folder / 'free.mihomo.yaml', make_config(free, standalone=True, port=port))
        write_yaml(folder / 'free-nodes.yaml', {'proxies': full['proxies']})
        links, uri_report = export_uris(full['proxies'])
        atomic_write(folder / 'free.v2ray.txt', links)
        atomic_write(folder / 'free.v2ray.base64.txt', b64(links))
        if core and not check_config(core, folder / 'free.yaml', folder, control):
            raise AppError('免费节点导出未通过 Mihomo 检查，上一版本保持有效。' + getattr(control, 'core_diagnostic', ''))
        if core and not check_config(core, folder / 'free.mihomo.yaml', folder, control):
            raise AppError('Mihomo 独立配置检查失败，上一版本保持有效。' + getattr(control, 'core_diagnostic', ''))
        report = dict(report, generation=generation, exported_at=datetime.now(timezone.utc).isoformat(),
                      scope='free', counts={'free': len(free)},
                      config_validated=bool(core), v2rayn={'free': uri_report})
        write_json(folder / "report.json", report)
        # Everything is ready before this one atomic operation changes the active generation.
        with client_export_transaction(paths.export_root, folder, control):
            control.check()
            write_json(paths.exports / "current.json", {"generation": generation})
    except BaseException:
        try: shutil.rmtree(folder)
        except OSError: pass  # Keep the original failure; never hide it with cleanup errors.
        raise
    return folder, report


def prune_exports(paths, keep, log=lambda _: None):
    # Profiles may share an export root; never prune another profile's active publication.
    with file_lock(paths.exports / 'exports.lock'):
        _prune_exports(paths, keep, log)


def _prune_exports(paths, keep, log):
    """Called under app_lock after publication; only remove owned generation folders."""
    root = paths.exports.resolve()
    current = paths.current().resolve()
    folders = sorted((p for p in root.iterdir()
                      if re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", p.name)
                      and p.is_dir() and not p.is_symlink()
                      and not getattr(p, "is_junction", lambda: False)()
                      and p.resolve().parent == root),
                     key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    others = [p for p in folders if p.resolve() != current]
    for folder in others[max(keep - 1, 1):]:
        try:
            # Reject redirects anywhere in a generation before recursive deletion.
            if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
                   or not p.resolve().is_relative_to(root) for p in folder.rglob("*")):
                continue
            if folder.resolve() == paths.current().resolve(): continue
            shutil.rmtree(folder)
        except OSError:
            log("一个历史导出暂时无法清理，下次发布时重试。")
