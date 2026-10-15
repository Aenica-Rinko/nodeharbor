"""User-managed public repository catalog, separate from enabled downloads."""
import re
from copy import deepcopy
from urllib.parse import urlsplit

from .public_sources import GITHUB_POOLS, RECOMMENDED, github_file_url
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
        urls = list(dict.fromkeys(github_file_url(url) for url in urls))
        if any(repository_of(url).casefold() != name.casefold() for url in urls):
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


def catalog(options):
    chosen = options.get('github_sources')
    chosen = RECOMMENDED if chosen is None else chosen
    rows = [dict(name=name, enabled=name in chosen, urls=[url], builtin=True) for name, url in GITHUB_POOLS]
    rows.extend(dict(pool, builtin=False) for pool in user_pools(options))
    return rows


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
    pool['urls'] = list(dict.fromkeys([*pool['urls'], *urls]))
    return validate_pools(pools), added
