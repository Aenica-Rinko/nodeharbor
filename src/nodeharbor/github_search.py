"""Bounded, anonymous GitHub discovery; search results are never trusted as nodes."""
import json
import base64
from collections import Counter
import re
import time
from pathlib import PurePosixPath
from urllib.parse import quote

import requests

from . import __version__
from .download_network import local_proxy_candidates, primary_proxies
from .public_sources import github_file_url
from .public_links import readme_links, repository_kind, subscription_url, link_kind
from .storage import AppError
from .tasks import network_job, DeadlineExceeded

REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z')
ERRORS = {
    'rate_limit': 'GitHub 暂时限制了搜索请求，请稍后重试。',
    'not_found': '仓库或分支已不存在，或不是公开仓库，请重新搜索。',
    'query': 'GitHub 无法识别搜索条件，请换用简单关键词。',
    'network': '无法连接 GitHub，请检查网络或设置中的下载代理后重试。',
    'response': 'GitHub 返回了无法读取的结果，请稍后重试。',
}


def clean_query(value):
    value = value.strip()
    if not value or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise AppError('请输入 1–160 字的搜索关键词，例如 free v2ray 或 免费节点。')
    return value


def discover(mode, value, options, control):
    if mode == 'search-cloudflare':
        return discover_cloudflare(value, options, control)
    if mode == 'search': value = clean_query(value)
    control.emit('search', message='正在搜索 GitHub 公开仓库…' if mode == 'search' else '正在解析订阅预览…' if mode == 'preview' else '正在查找仓库中的候选订阅文件…')
    result = network_job('github-discovery', {'mode': mode, 'value': value,
                         'options': {k: options[k] for k in ('proxy', 'auto_local_proxy') if k in options}}, control, timeout=35)
    control.check()
    if result.get('error'): raise AppError(ERRORS.get(result['error'], ERRORS['response']))
    return result


def discover_cloudflare(value, options, control):
    """Find actual public links, with bounded requests and cancellable progress."""
    query = clean_query(value) if value.strip() else ''
    options = {k: options[k] for k in ('proxy', 'auto_local_proxy') if k in options}
    deadline = time.monotonic() + 90
    repositories, found, files, warnings = {}, [], {}, []
    checked = 0

    def request(mode, value):
        control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0: return {'error': 'timeout'}
        try:
            return network_job('github-discovery', {'mode': mode, 'value': value,
                               'options': options}, control, timeout=min(25, remaining))
        except DeadlineExceeded:
            control.check()  # Cancellation and the parent deadline still take precedence.
            return {'error': 'timeout'}

    def warn(result):
        message = ERRORS.get(result.get('error'), '本次搜索达到时限，已保留找到的结果。')
        if message not in warnings: warnings.append(message)

    for domain in ('workers.dev', 'pages.dev'):
        control.emit('search', message=f'正在查找公开分享的 {domain} 订阅链接…')
        result = request('cloudflare-repositories', {'query': query, 'domain': domain})
        if result.get('error'):
            warn(result)
            if result['error'] in ('rate_limit', 'timeout'): break
            continue
        if result.get('incomplete'): warnings.append('GitHub 仅返回了部分匹配仓库。')
        for repo in result['repositories']:
            repositories.setdefault(repo['name'].casefold(), repo)
    candidates = list(repositories.values())[:6]
    for index, repo in enumerate(candidates):
        control.emit('search', completed=index, total=len(candidates),
                     message=f"正在读取公开说明 {index + 1}/{len(candidates)}：{repo['name']}")
        result = request('cloudflare-links', repo)
        if result.get('error'):
            warn(result)
            if result['error'] in ('rate_limit', 'timeout'): break
            continue
        checked += 1
        if result['files']:
            found.append(repo)
            files[repo['name']] = result['files']
    control.check()
    return {'mode': 'search-cloudflare', 'repositories': found, 'files_by_repository': files,
            'checked': checked, 'warnings': list(dict.fromkeys(warnings)),
            'incomplete': bool(warnings)}


def api_json(path, params, options):
    url = 'https://api.github.com/' + path
    routes = [primary_proxies(url, options)]
    if options.get('auto_local_proxy') and not options.get('proxy'):
        routes.extend({'http': proxy, 'https': proxy} for proxy in local_proxy_candidates()[:2])
    for proxies in routes:
        try:
            with requests.Session() as session:
                session.trust_env = False  # Never read .netrc or GitHub credentials.
                with session.get(url, params=params, proxies=proxies, timeout=(4, 8),
                                 headers={'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'NodeHarbor/' + __version__},
                                 allow_redirects=False, stream=True) as response:
                    if response.status_code in (403, 429): return {'error': 'rate_limit'}
                    if response.status_code == 404: return {'error': 'not_found'}
                    if response.status_code == 422: return {'error': 'query'}
                    if response.status_code != 200: continue
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 8 * 1024 * 1024: return {'error': 'response'}
                        chunks.append(chunk)
                    data = json.loads(b''.join(chunks))
                    return data if isinstance(data, dict) else {'error': 'response'}
        except (requests.RequestException, ValueError):
            continue
    return {'error': 'network'}


def candidate_files(data, repository, branch):
    entries = data.get('tree')
    if not isinstance(entries, list): return {'error': 'response'}
    files = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('type') != 'blob' or entry.get('mode') == '120000': continue
        path = entry.get('path')
        size = entry.get('size', 0)
        if not isinstance(path, str) or type(size) is not int or not 0 < size <= 10 * 1024 * 1024: continue
        p = PurePosixPath(path)
        if any(part.startswith('.') or part.lower() in {'tests', 'test', 'docs', 'examples', 'templates'} for part in p.parts): continue
        name = p.name.lower()
        if name.startswith(('readme', 'license', 'requirements', 'changelog')) or name in {'ip.txt', 'ips.txt', 'ipv6.txt'}: continue
        if p.suffix.lower() not in {'.yaml', '.yml', '.txt', '.conf', '.base64'} and name not in {'sub', 'clash', 'v2ray', 'v2', 'base64', 'subscribe'}: continue
        try:
            url = github_file_url('https://raw.githubusercontent.com/' + repository + '/' + quote(branch, safe='') + '/' + quote(path, safe='/'))
        except AppError: continue
        score = sum(word in path.lower() for word in ('clash', 'meta', 'verified', 'sub', 'node', 'base64', 'v2ray'))
        files.append({'path': path, 'url': url, 'size': size, 'score': score, 'kind': 'GitHub 文件',
                      'provenance': 'https://github.com/' + repository})
    files.sort(key=lambda f: (-f['score'], len(f['path']), f['path']))
    return {'mode': 'files', 'repository': repository, 'files': files[:100],
            'limited': len(files) > 100 or bool(data.get('truncated'))}


def perform(payload):
    mode, value, options = payload.get('mode'), payload.get('value'), payload.get('options', {})
    if mode == 'preview' and isinstance(value, str):
        from .config import DEFAULTS
        from .fetcher import download_with_report
        from .download_network import DownloadFailed
        try:
            url = subscription_url(value)
            settings = dict(DEFAULTS['fetch'], **options)
            settings.update(timeout_seconds=8, retries=0, max_response_mb=2, _public_source=True)
            nodes = download_with_report(url, settings)['nodes']
            return {'mode': 'preview', 'url': url, 'count': len(nodes),
                    'protocols': dict(Counter(n['type'] for n in nodes)), 'kind': link_kind(url)}
        except (AppError, DownloadFailed, requests.RequestException):
            return {'mode': 'preview', 'url': value, 'count': 0, 'protocols': {},
                    'warning': '无法取得有效订阅：可能是网页、IP 列表、失效链接或网络问题。'}
    if mode in ('search', 'cloudflare-repositories'):
        if mode == 'cloudflare-repositories':
            if not isinstance(value, dict) or value.get('domain') not in ('workers.dev', 'pages.dev'):
                return {'error': 'query'}
            keyword = clean_query(value['query']) if value.get('query') else 'vless'
            query = f'"{value["domain"]}" {keyword} in:readme'
        else:
            query = clean_query(value)
        data = api_json('search/repositories', {'q': query + ' is:public archived:false fork:false',
                        'sort': 'updated', 'order': 'desc', 'per_page': 3 if mode == 'cloudflare-repositories' else 20}, options)
        if data.get('error'): return data
        if not isinstance(data.get('items'), list): return {'error': 'response'}
        repositories = []
        for item in data['items']:
            if not isinstance(item, dict): continue
            name, branch = item.get('full_name'), item.get('default_branch')
            if not isinstance(name, str) or not REPOSITORY.fullmatch(name) or item.get('private') is not False: continue
            if not isinstance(branch, str) or not branch or len(branch) > 250: continue
            repositories.append({'name': name, 'branch': branch,
                                 'kind': repository_kind(name, str(item.get('description') or '')),
                                 'description': str(item.get('description') or '')[:220],
                                 'updated': str(item.get('pushed_at') or '')[:10]})
        return {'mode': 'search', 'repositories': repositories[:3 if mode == 'cloudflare-repositories' else 20], 'incomplete': bool(data.get('incomplete_results'))}
    if mode in ('files', 'cloudflare-links') and isinstance(value, dict):
        repo, branch = value.get('name'), value.get('branch')
        if not isinstance(repo, str) or not REPOSITORY.fullmatch(repo) or not isinstance(branch, str) or not branch or len(branch) > 250:
            return {'error': 'response'}
        # Reconfirm visibility independently before reading the tree.
        metadata = api_json('repos/' + repo, {}, options)
        if metadata.get('error'): return metadata
        if metadata.get('private') is not False: return {'error': 'not_found'}
        if mode == 'cloudflare-links':
            result = {'mode': 'files', 'repository': repo, 'files': [], 'limited': False}
        else:
            data = api_json('repos/' + repo + '/git/trees/' + quote(branch, safe=''), {'recursive': '1'}, options)
            if data.get('error'): return data
            result = candidate_files(data, repo, branch)
            if result.get('error'): return result
        document = api_json('repos/' + repo + '/readme', {'ref': branch}, options)
        if mode == 'cloudflare-links' and document.get('error'): return document
        result['readme_warning'] = '仓库说明未能读取，仅显示仓库文件。' if document.get('error') else ''
        if document.get('encoding') == 'base64' and isinstance(document.get('content'), str):
            try:
                content = base64.b64decode(document['content'][:700_000]).decode('utf-8', errors='replace')
                # A hosted notification/RSS subscription is not a proxy subscription.
                if mode == 'cloudflare-links' and not re.search(
                        r'\b(?:vless|vmess|trojan|shadowsocks|hysteria2?|tuic|mihomo|clash|v2ray|sing-box)\b', content, re.I):
                    return result
                seen = {f['url'] for f in result['files']}
                result['files'].extend(f for f in readme_links(content, repo) if f['url'] not in seen)
            except (ValueError, TypeError): result['readme_warning'] = '仓库说明无法解析，仅显示仓库文件。'
        if mode == 'cloudflare-links':
            if result.get('readme_warning') or document.get('encoding') != 'base64' or not isinstance(document.get('content'), str):
                return {'error': 'response'}
            result['files'] = [f for f in result['files'] if link_kind(f['url']) == 'Cloudflare 托管（第三方）']
        return result
    return {'error': 'response'}
