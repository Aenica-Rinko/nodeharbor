"""User-managed public repository catalog, separate from enabled downloads."""
import re
from copy import deepcopy
from urllib.parse import urlsplit

from .public_sources import GITHUB_POOLS, github_file_url
from .public_links import subscription_url, external_link
from .storage import AppError


def repository_of(url):
    return '/'.join(urlsplit(github_file_url(url)).path.strip('/').split('/')[:2])


def validate_pools(value):
    if not isinstance(value, list): raise AppError('node_pools 必须是节点池列表。')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict): raise AppError('节点池条目必须是对象。')
        name, branch, enabled, urls = item.get('name'), item.get('branch', 'main'), item.get('enabled', False), item.get('urls', [])
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+', name):
            raise AppError('节点池名称应为 GitHub 用户名/仓库名。')
        if name.casefold() in seen: raise AppError('节点池列表中存在重复仓库。')
        if not isinstance(branch, str) or not branch or len(branch) > 250 or type(enabled) is not bool:
            raise AppError('节点池分支或启用状态无效。')
        if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
            raise AppError('节点池订阅文件必须是地址列表。')
        urls = list(dict.fromkeys(subscription_url(url) for url in urls))
        if any(not external_link(url) and repository_of(url).casefold() != name.casefold() for url in urls):
            raise AppError('订阅文件必须属于对应的 GitHub 仓库。')
        if enabled and not urls: raise AppError('请先为节点池选择订阅文件，再启用。')
        seen.add(name.casefold())
        result.append(dict(name=name, branch=branch, enabled=enabled, urls=urls))
    return result


def user_pools(options):
    pools = deepcopy(validate_pools(options.get('node_pools', [])))
    # Existing manual sources retain their enabled state during migration.
    for url in options.get('public_sources', []):
        url = github_file_url(url)
        name = repository_of(url)
        entry = next((p for p in pools if p['name'].casefold() == name.casefold()), None)
        if entry is None:
            entry = dict(name=name, branch='main', enabled=True, urls=[]); pools.append(entry)
        if url not in entry['urls']: entry['urls'].append(url)
    return pools


def enabled_urls(options):
    return list(dict.fromkeys(url for pool in user_pools(options) if pool['enabled'] for url in pool['urls']))


def add_pool(options, repository, urls=()):
    """Return updated settings and whether a new, disabled repository was added."""
    pools = user_pools(options)
    name = repository['name']
    if name.casefold() in {n.casefold() for n, _ in GITHUB_POOLS}:
        return pools, False  # Never change an existing built-in selection implicitly.
    pool = next((p for p in pools if p['name'].casefold() == name.casefold()), None)
    added = pool is None
    if added:
        pool = dict(name=name, branch=repository.get('branch', 'main'), enabled=False, urls=[])
        pools.append(pool)
    new_urls = list(dict.fromkeys([*pool['urls'], *urls]))
    if new_urls != pool['urls']: pool['enabled'] = False
    pool['urls'] = new_urls
    return validate_pools(pools), added


def remove_pool(options, name):
    """Remove only a user entry, including migrated manual URLs."""
    pools = user_pools(options)
    if not any(p['name'].casefold() == name.casefold() for p in pools):
        raise AppError('该自选来源已不存在，请刷新列表。')
    return [p for p in pools if p['name'].casefold() != name.casefold()]


def replace_pool(options, name, urls, expected=None):
    """Replace files atomically; changed files require explicit re-enabling."""
    pools = user_pools(options)
    pool = next((p for p in pools if p['name'].casefold() == name.casefold()), None)
    if pool is None: raise AppError('该自选来源已不存在，请刷新列表。')
    if expected is not None and pool != expected:
        raise AppError('该来源已被其他操作修改，请关闭窗口后重新打开。')
    urls = list(dict.fromkeys(subscription_url(url) for url in urls))
    if urls != pool['urls']: pool.update(urls=urls, enabled=False)
    return validate_pools(pools)


def update_catalog(paths, change):
    """Serialize read/modify/write with node tasks; preserve unrelated settings."""
    from .config import load_settings, validate_settings
    from .storage import app_lock, write_yaml
    with app_lock(paths):
        settings = load_settings(paths)
        settings['fetch']['node_pools'] = validate_pools(change(settings['fetch']))
        settings['fetch']['public_sources'] = []
        settings = validate_settings(paths, settings)
        write_yaml(paths.config / 'settings.yaml', settings)
    return user_pools(settings['fetch'])


def pool_health(pool, health):
    """Match exact file URLs, not positional source names that change on edits."""
    from .source_plan import source_key, nonnegative
    records = []
    for url in pool['urls']:
        state = health.get(source_key(url), {})
        state = state if isinstance(state, dict) else {}
        records.append(state)
    if not records: return {'status': '待选择订阅文件'}, records
    failed = sum(bool(r.get('failure_reason')) or nonnegative(r.get('failures')) > 0 for r in records)
    untested = sum(not r for r in records)
    summary = {
        'status': (f'{failed} 个文件下载失败' if failed else f'{untested} 个文件尚未测试' if untested else
                   str(records[0].get('status', '尚未测试')) if len(records) == 1 else '多个文件，点击查看详情'),
        'failures': max(nonnegative(r.get('failures')) for r in records),
        'last_success': max(nonnegative(r.get('last_success')) for r in records),
        'last_verified_at': max(nonnegative(r.get('last_verified_at')) for r in records),
        'next_retry': max(nonnegative(r.get('next_retry')) for r in records),
        'verified': sum(nonnegative(r.get('verified')) for r in records) if all(r.get('verified') is not None for r in records) else None,
    }
    for key in ('nodes', 'eligible'): summary[key] = sum(nonnegative(r.get(key)) for r in records)
    return summary, records
