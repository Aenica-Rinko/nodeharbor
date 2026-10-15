"""Bounded, anonymous GitHub discovery; search results are never trusted as nodes."""
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote

import requests

from . import __version__
from .download_network import local_proxy_candidates, primary_proxies
from .public_sources import github_file_url
from .storage import AppError
from .tasks import network_job

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
    if mode == 'search': value = clean_query(value)
    control.emit('search', message='正在搜索 GitHub 公开仓库…' if mode == 'search' else '正在查找仓库中的候选订阅文件…')
    result = network_job('github-discovery', {'mode': mode, 'value': value,
                         'options': {k: options[k] for k in ('proxy', 'auto_local_proxy') if k in options}}, control, timeout=35)
    control.check()
    if result.get('error'): raise AppError(ERRORS.get(result['error'], ERRORS['response']))
    return result


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
        if name.startswith(('readme', 'license', 'requirements', 'changelog')): continue
        if p.suffix.lower() not in {'.yaml', '.yml', '.txt', '.conf', '.base64'} and name not in {'sub', 'clash', 'v2ray', 'v2', 'base64', 'subscribe'}: continue
        try:
            url = github_file_url('https://raw.githubusercontent.com/' + repository + '/' + quote(branch, safe='') + '/' + quote(path, safe='/'))
        except AppError: continue
        score = sum(word in path.lower() for word in ('clash', 'meta', 'verified', 'sub', 'node', 'base64', 'v2ray'))
        files.append({'path': path, 'url': url, 'size': size, 'score': score})
    files.sort(key=lambda f: (-f['score'], len(f['path']), f['path']))
    return {'mode': 'files', 'repository': repository, 'files': files[:100],
            'limited': len(files) > 100 or bool(data.get('truncated'))}


def perform(payload):
    mode, value, options = payload.get('mode'), payload.get('value'), payload.get('options', {})
    if mode == 'search':
        query = clean_query(value)
        data = api_json('search/repositories', {'q': query + ' is:public archived:false fork:false',
                        'sort': 'updated', 'order': 'desc', 'per_page': 20}, options)
        if data.get('error'): return data
        if not isinstance(data.get('items'), list): return {'error': 'response'}
        repositories = []
        for item in data['items']:
            if not isinstance(item, dict): continue
            name, branch = item.get('full_name'), item.get('default_branch')
            if not isinstance(name, str) or not REPOSITORY.fullmatch(name) or item.get('private') is not False: continue
            if not isinstance(branch, str) or not branch or len(branch) > 250: continue
            repositories.append({'name': name, 'branch': branch,
                                 'description': str(item.get('description') or '')[:220],
                                 'updated': str(item.get('pushed_at') or '')[:10]})
        return {'mode': 'search', 'repositories': repositories[:20], 'incomplete': bool(data.get('incomplete_results'))}
    if mode == 'files' and isinstance(value, dict):
        repo, branch = value.get('name'), value.get('branch')
        if not isinstance(repo, str) or not REPOSITORY.fullmatch(repo) or not isinstance(branch, str) or not branch or len(branch) > 250:
            return {'error': 'response'}
        # Reconfirm visibility independently before reading the tree.
        metadata = api_json('repos/' + repo, {}, options)
        if metadata.get('error'): return metadata
        if metadata.get('private') is not False: return {'error': 'not_found'}
        data = api_json('repos/' + repo + '/git/trees/' + quote(branch, safe=''), {'recursive': '1'}, options)
        return data if data.get('error') else candidate_files(data, repo, branch)
    return {'error': 'response'}
