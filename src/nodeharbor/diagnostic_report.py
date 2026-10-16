"""Offline support reports built from allowed fields, never copied raw logs/configs."""
import json
import platform
import uuid
from datetime import datetime, timezone

from . import __version__
from .config import load_settings, core_kind, find_core
from .public_sources import GITHUB_POOLS, selected_pools
from .source_catalog import user_pools
from .source_plan import nonnegative, source_key
from .storage import AppError, write_json
from .tasks import NETWORK_ERRORS

SUMMARY_KEYS = ('sources', 'downloaded', 'cache', 'stale_cache', 'failed', 'deferred',
                'parsed', 'unique', 'selected', 'not_selected', 'passed', 'stable')
SOURCE_KEYS = ('nodes', 'eligible', 'verified', 'failures', 'elapsed_seconds')
ACTIONS = {'refresh', 'refresh-github', 'verify', 'acquire', 'verify-library', 'export',
           'test-sources', 'search-sources', 'check-core'}


def mapping(value): return value if isinstance(value, dict) else {}


def counters(value, keys):
    value = mapping(value)
    return {key: nonnegative(value[key]) for key in keys if key in value}


def read_record(path):
    """Bound input size and suppress arbitrary parser/OS messages and filenames."""
    try:
        if not path.exists(): return {}, 'missing'
        with path.open('rb') as file: raw = file.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024: return {}, 'too_large'
        value = json.loads(raw)
        return (value, 'ok') if isinstance(value, dict) else ({}, 'invalid')
    except (OSError, ValueError, UnicodeError): return {}, 'unreadable'


def timestamp(value):
    try:
        if type(value) in (int, float): date = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str): date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else: return None
        if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError): return None


def error_category(message):
    """Return a fixed category only; even an unknown exception never echoes text."""
    if not isinstance(message, str) or not message: return None
    value = message.lower()
    for key, terms in (
        ('rate_limit', ('429', '限流', '频率限制')),
        ('network', ('dns', '网络', '连接', '下载失败')),
        ('timeout', ('超时', '时限', 'timeout')),
        ('core', ('核心', 'mihomo', 'xray')),
        ('format', ('解析', '格式', '配置')),
        ('permission', ('权限', 'permission', '拒绝访问')),
        ('cancelled', ('取消',)),
    ):
        if any(term in value for term in terms): return key
    return 'other'


def source_record(identifier, state):
    state = mapping(state)
    result = dict(id=identifier, **counters(state, SOURCE_KEYS))
    for key in ('cached', 'stale', 'deferred'):
        if type(state.get(key)) is bool: result[key] = state[key]
    for key in ('last_success', 'last_verified_at', 'next_retry'):
        result[key] = timestamp(state.get(key))
    result['error_category'] = error_category(state.get('failure_reason'))
    attempts = state.get('attempts', [])
    result['attempts'] = []
    for item in (attempts[:20] if isinstance(attempts, list) else []):
        item = mapping(item)
        code = item.get('error_code')
        code = code if isinstance(code, str) and code in NETWORK_ERRORS else 'unknown'
        status = item.get('http_status')
        result['attempts'].append({'error_code': code, 'http_status': status if type(status) is int and 100 <= status <= 599 else None})
    return result


def build_report(paths, task=None):
    report = {
        'schema_version': 1, 'app_version': __version__,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'platform': {'Windows': 'Windows', 'Linux': 'Linux', 'Darwin': 'macOS'}.get(platform.system(), 'other'),
        'privacy': '仅允许的统计和状态字段；不含节点、订阅地址、仓库自定义名称、配置原文、日志原文、令牌和本地路径。',
        'record_status': {},
    }
    states = report['record_status']
    try:
        settings = load_settings(paths)
        states['settings'] = 'ok'
    except (AppError, OSError):
        settings = None; states['settings'] = 'invalid'
    if settings:
        v, f = settings['verification'], settings['fetch']
        report['configuration'] = {
            'client': v['core_client'], 'core_type': core_kind(settings),
            'proxy_configured': bool(f['proxy']), 'auto_local_proxy': f['auto_local_proxy'],
            'max_candidates': f['max_candidates'], 'nodes_per_source': f['nodes_per_source'],
        }
        try: find_core(paths, settings); report['configuration']['core_found'] = True
        except (AppError, OSError): report['configuration']['core_found'] = False
        health, states['source_health'] = read_record(paths.data / 'source-cache/health.json')
        sources = []
        chosen = {name for name, _ in selected_pools(f)}
        for name, url in GITHUB_POOLS:
            entry = source_record(name, health.get(source_key(url)))
            entry['enabled'] = name in chosen
            sources.append(entry)
        for index, pool in enumerate(user_pools(f), 1):
            for file_index, url in enumerate(pool['urls'], 1):
                entry = source_record(f'custom-{index}/file-{file_index}', health.get(source_key(url)))
                entry['enabled'] = pool['enabled']; sources.append(entry)
        report['sources'] = sources
        report['configuration']['custom_pools'] = len(user_pools(f))
    last, states['last_run'] = read_record(paths.data / 'state/last-run.json')
    report['last_run'] = {
        'action': last.get('action') if isinstance(last.get('action'), str) and last['action'] in ACTIONS else 'unknown',
        'status': last.get('status') if isinstance(last.get('status'), str) and last['status'] in {'running', 'success', 'failed', 'cancelled'} else 'unknown',
        'elapsed_seconds': nonnegative(last.get('elapsed_seconds')),
        'error_category': error_category(last.get('error')),
        'summary': counters(last.get('summary'), SUMMARY_KEYS),
    }
    for key in ('started_at', 'finished_at'): report['last_run'][key] = timestamp(last.get(key))
    library, states['library'] = read_record(paths.data / 'library/free.json')
    report['library'] = {'nodes': len(library['nodes']) if isinstance(library.get('nodes'), list) else 0,
                         'verified_at': timestamp(mapping(library.get('report')).get('verified_at'))}
    try:
        exported, states['export'] = read_record(paths.current() / 'report.json')
        report['export'] = {'counts': counters(exported.get('counts'), ('free',)),
                            'exported_at': timestamp(exported.get('exported_at')),
                            'config_validated': exported.get('config_validated') is True}
    except (AppError, OSError): states['export'] = 'unavailable'
    if task:
        report['current_session'] = {
            'action': task.action if task.action in ACTIONS else 'unknown',
            'status': task.status if task.status in {'running', 'success', 'failed', 'cancelled'} else 'unknown',
            'error_category': error_category(task.message) if task.status == 'failed' else None,
            'counts': counters(task.counters, ('parsed', 'unique', 'selected', 'tested', 'passed', 'skipped', 'stable')),
        }
    return report


def export_report(paths, task=None):
    folder = paths.logs / 'diagnostics'
    name = 'NodeHarbor-diagnostics-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8] + '.json'
    target = folder / name
    write_json(target, build_report(paths, task))
    return target
