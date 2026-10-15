from __future__ import annotations

import requests
import threading
import time
import hashlib
from pathlib import Path
from collections import Counter
from contextlib import contextmanager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .nodes import normalize_nodes, parse_subscription, identity
from .public_sources import GITHUB_POOLS, public_fallbacks, selected_pools, source_urls
from .download_network import primary_proxies, local_proxy_candidates, proxy_label, error_code, DownloadFailed
from .storage import AppError, read_json, write_json, file_lock
from .tasks import Cancelled, TaskControl, network_job, parallel_map, NetworkFailure, DeadlineExceeded, NetworkPool
from .source_plan import source_key, priority, read_mapping, fair_candidates, nonnegative
from . import __version__


class SourcesUnavailable(AppError):
    def __init__(self, report):
        self.report = report
        super().__init__('本轮未取得可读取的公开节点。请查看各源详情或重试下载；已有导出保持不变。')


def notify(options, phase, **details):
    if options.get('_progress'): options['_progress'](dict(phase=phase, **details))


def load_cache(url, options):
    if not options.get('_cache_folder'): return {}
    cache_file = Path(options['_cache_folder']) / (source_key(url) + '.json')
    cached = read_mapping(cache_file)
    if (not isinstance(cached.get('result'), dict) or not isinstance(cached['result'].get('nodes'), list)
            or nonnegative(cached.get('saved_at'), None) is None): return {}
    cached['result']['nodes'] = normalize_nodes(cached['result']['nodes'])
    return cached if cached['result']['nodes'] else {}


def cached_result(cached, stale=False):
    return dict(cached['result'], route=cached['result'].get('route', '历史通道未记录'),
                channel='旧缓存（将重新检测）' if stale else '本地缓存', attempts=[],
                cached=True, stale=stale, fetched_at=cached['saved_at'])


def stale_cache(url, options):
    cached = load_cache(url, options)
    age = time.time() - cached.get('saved_at', 0)
    return cached_result(cached, True) if cached and 0 <= age <= options.get('stale_cache_hours', 24) * 3600 else None


@contextmanager
def api_gate(url, options):
    """Serialize shared API fallbacks and respect cooldown even on manual retry."""
    if not url.startswith('https://api.github.com/') or not options.get('_cache_folder'):
        yield; return
    folder = Path(options['_cache_folder'])
    lock = file_lock(folder / 'github-api.lock')
    deadline = time.monotonic() + options.get('source_timeout_seconds', 60)
    while True:
        try: lock.__enter__(); break
        except AppError:
            notify(options, 'api_queue')
            if time.monotonic() >= deadline: raise requests.Timeout('API queue timeout') from None
            time.sleep(.1)
    try:
        state = read_mapping(folder / 'github-api-wait.json')
        remaining = nonnegative(state.get('until')) - time.time()
        if remaining > 0:
            response = requests.Response(); response.status_code = 429
            response.headers['Retry-After'] = str(max(1, int(remaining)))
            raise requests.HTTPError('API cooldown', response=response)
        try: yield
        except requests.HTTPError as error:
            response = error.response
            if response is not None and (response.status_code == 429 or
                    (response.status_code == 403 and response.headers.get('X-RateLimit-Remaining') == '0')):
                try: wait = int(response.headers.get('Retry-After') or max(60, int(response.headers.get('X-RateLimit-Reset') or 0) - time.time()))
                except ValueError: wait = 60
                write_json(folder / 'github-api-wait.json', {'until': time.time() + max(60, min(86400, wait))})
            raise
    finally: lock.__exit__(None, None, None)


def download(url, options):
    return download_with_report(url, options)['nodes']


def download_with_report(url, options):
    originals = source_urls(url)
    urls = list(dict.fromkeys(originals + [u for original in originals for u in public_fallbacks(original)]))
    public = url in dict((url, name) for name, url in GITHUB_POOLS) or options.get('_public_source', False)
    cache_file = (Path(options['_cache_folder']) / (hashlib.sha256(url.encode()).hexdigest() + '.json')) if public and options.get('_cache_folder') else None
    cached = load_cache(url, options) if cache_file else {}
    if cached and not options.get('_force') and 0 <= time.time() - cached['saved_at'] < options.get('cache_minutes', 10) * 60:
        return cached_result(cached)
    routes = [primary_proxies(url, options)]
    if public and options.get('auto_local_proxy', True) and not options.get('proxy'):
        for proxy in local_proxy_candidates():
            route = {'http': proxy, 'https': proxy}
            if route not in routes: routes.append(route)
    attempts = []
    # Try working local clients before repeatedly waiting for a blocked raw/CDN host.
    for candidate, route in [(candidate, route) for candidate in urls for route in routes]:
        channel = 'GitHub 原始文件' if candidate in originals else 'GitHub API' if candidate.startswith('https://api.github.com/') else '公开 CDN 镜像'
        entry = {'channel': channel, 'route': proxy_label(route)}
        try:
            bounded = dict(options)
            info = {}; bounded['_response_info'] = info
            bounded.update(_channel=channel, _route=entry['route'])
            notify(bounded, 'connecting', channel=channel, route=entry['route'])
            if cached.get('content_url') == candidate: bounded['_conditional'] = cached
            if len(urls) > 1 or len(routes) > 1: bounded['timeout_seconds'] = min(options['timeout_seconds'], 8)
            nodes = download_single(candidate, bounded, retries=0 if len(urls) > 1 or len(routes) > 1 else None, proxies=route)
            entry['result'] = '成功' if nodes else '没有可解析节点'
            attempts.append(entry)
            if nodes:
                result = {'nodes': nodes, 'channel': channel, 'route': entry['route'], 'attempts': attempts,
                          'cached': bool(info.get('unchanged')), 'stale': False, 'fetched_at': time.time()}
                if cache_file:
                    write_json(cache_file, {'saved_at': time.time(), 'content_url': candidate,
                                           'etag': info.get('etag', cached.get('etag')),
                                           'modified': info.get('modified', cached.get('modified')), 'result': result})
                return result
            entry['error_code'] = 'empty'
        except requests.RequestException as error:
            entry.update(error_code=error_code(error), result='失败')
            if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
                entry['http_status'] = error.response.status_code
                if error.response.status_code == 429 or (error.response.status_code == 403 and error.response.headers.get('X-RateLimit-Remaining') == '0'):
                    try: entry['retry_after'] = max(60, min(86400, int(error.response.headers.get('Retry-After') or max(60, int(error.response.headers.get('X-RateLimit-Reset') or 0) - time.time()))))
                    except ValueError: entry['retry_after'] = 60
            attempts.append(entry)
            if entry.get('retry_after'): break
        except AppError as error:
            entry.update(error_code='oversize' if str(error) == '订阅响应过大' else 'format', result='失败'); attempts.append(entry)
        notify(options, 'retry', channel=channel, route=entry['route'])
    if public:
        old = stale_cache(url, options)
        if old: return dict(old, attempts=attempts, download_failed=True)
    raise DownloadFailed(attempts[-1].get('error_code', 'network'), attempts)


def download_single(url, options, retries=None, proxies=None):
    if options.get("github_mirror") and url.startswith("https://raw.githubusercontent.com/"):
        url = options["github_mirror"].rstrip("/") + "/" + url
    with requests.Session() as session:
        session.trust_env = False
        retry = Retry(total=options["retries"] if retries is None else retries, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        # Honor download proxies without enabling .netrc credentials via trust_env.
        session.proxies = primary_proxies(url, options) if proxies is None else proxies
        headers = {"User-Agent": f"clash.meta NodeHarbor/{__version__}"}
        cached = options.get('_conditional', {})
        if cached.get('etag'): headers['If-None-Match'] = cached['etag']
        elif cached.get('modified'): headers['If-Modified-Since'] = cached['modified']
        if url.startswith('https://api.github.com/'): headers['Accept'] = 'application/vnd.github.raw+json'
        with api_gate(url, options), session.get(url, timeout=options["timeout_seconds"], stream=True,
                         headers=headers) as response:
            response.raise_for_status()
            info = options.get('_response_info')
            if response.status_code == 304 and cached:
                if info is not None: info['unchanged'] = True
                return cached['result']['nodes']
            if info is not None: info.update(etag=response.headers.get('ETag'), modified=response.headers.get('Last-Modified'))
            chunks, size, last_event = [], 0, 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > options["max_response_mb"] * 1024 * 1024: raise AppError("订阅响应过大")
                chunks.append(chunk)
                if time.monotonic() - last_event > .25:
                    notify(options, 'reading', bytes=size, channel=options.get('_channel', ''), route=options.get('_route', ''))
                    last_event = time.monotonic()
            notify(options, 'parsing', bytes=size)
            return parse_subscription(b"".join(chunks).decode("utf-8-sig", errors="replace"))


def fetch_one(url, options, control=None):
    options = dict(options)
    pool, progress = options.pop('_pool', None), options.pop('_on_progress', None)
    options.pop('_preferred_nodes', None)
    payload = {'url': url, 'options': options}
    timeout = options.get('source_timeout_seconds', 60)
    if pool:
        return pool.request('fetch', payload, timeout, on_progress=progress)
    return network_job('fetch', payload, control, timeout)


def fetch_all(settings, log, control=None, public_only=False):
    control = control or TaskControl()
    options = settings['fetch']
    pools = {url: name for name, url in selected_pools(options)}
    from .source_catalog import enabled_urls
    for index, url in enumerate(enabled_urls(options), 1):
        from urllib.parse import urlsplit
        owner, repository, *_ = urlsplit(url).path.strip('/').split('/')
        pools.setdefault(url, owner + '/' + repository + f' · 自选 {index}')
    if not pools: raise AppError('没有启用公开来源，请在“公开源”页选择或添加至少一个来源。')
    health_file = Path(options['_cache_folder']) / 'health.json' if options.get('_cache_folder') else None
    health = read_mapping(health_file) if health_file else {}
    urls = sorted(pools, key=lambda url: priority(health.get(source_key(url), {})))
    source_states = []
    for url in urls:
        previous = health.get(source_key(url), {})
        if not isinstance(previous, dict): previous = {}
        previous = dict(previous)
        for key in ('failures', 'next_retry', 'last_success', 'last_verified_at', 'last_eligible', 'last_verified', 'elapsed_seconds'):
            if key in previous: previous[key] = nonnegative(previous[key])
        previous['failures'] = int(previous.get('failures', 0))
        source_states.append(dict(previous, key=source_key(url), name=pools[url], status='等待下载',
                                  nodes=0, verified=None, eligible=0, tested=None, node_ids=[], attempts=[],
                                  cached=False, stale=False, deferred=False))
    completed, parsed = 0, 0
    lock = threading.Lock()
    def snapshot(): return [{k: v for k, v in source.items() if k not in {'node_ids', 'key'}} for source in source_states]
    def emit(message, **extra):
        control.emit('download', completed, len(urls), message, sources=snapshot(), parsed=parsed, **extra)
    log(f'获取 {len(urls)} 个公开来源；最多同时下载 {min(options["workers"], 8)} 个，随后按来源分配检测名额')
    emit('正在安排公开来源，优先处理有成功记录的来源')
    workers = min(options['workers'], 8, len(urls))
    with NetworkPool(workers, control) as pool:
        def fetch(item):
            nonlocal completed, parsed
            index, url = item; source = source_states[index]
            start = time.monotonic(); found, error_text, failed = [], '', False
            control.check()
            with lock: source['status'] = '下载 / 解析中'; emit('开始获取公开来源')
            def progress(event):
                phase = event.get('phase')
                message = {'connecting': '连接中', 'reading': '下载中', 'parsing': '解析中',
                           'retry': '切换下载通道', 'api_queue': '等待 GitHub API'}.get(phase, '处理中')
                if phase in {'reading', 'parsing'} and event.get('bytes'):
                    message += f' · {event["bytes"] / 1024:.0f} KiB'
                with lock:
                    source['status'] = message
                    for key in ('channel', 'route'):
                        if event.get(key): source[key] = event[key]
                    emit('正在下载 / 解析；各源显示当前通道和已接收大小')
            try:
                if not options.get('_force') and source.get('next_retry', 0) > time.time():
                    source['deferred'] = True
                    result = stale_cache(url, options)
                    if not result:
                        error_text = '失败退避中，可手动重试'; return []
                else:
                    result = fetch_one(url, dict(options, _pool=pool, _on_progress=progress,
                                       _stream_progress=True, _public_source=True, _download_report=True), control)
                found = result['nodes'] if isinstance(result, dict) else result
                if isinstance(result, dict):
                    with lock:
                        source.update(route=result['route'], channel=result['channel'], attempts=result['attempts'],
                                      cached=result.get('cached', False), stale=result.get('stale', False))
                    failed = result.get('download_failed', False)
                    if failed: error_text = '新下载失败，已回退到有时效限制的旧缓存'
                if not found: error_text = '内容为空或格式不支持'; failed = True
                elif not source['stale']:
                    source.update(failures=0, last_success=result.get('fetched_at', time.time()) if isinstance(result, dict) else time.time(), next_retry=0)
                return found
            except Cancelled:
                error_text = '已取消'; raise
            except Exception as error:
                control.check(); failed = True
                error_text = (str(error) if isinstance(error, NetworkFailure) else
                              '下载超过单源时限' if isinstance(error, DeadlineExceeded) else '下载或解析失败')
                if isinstance(error, NetworkFailure): source['attempts'] = error.attempts
                result = stale_cache(url, options)
                if result:
                    found = result['nodes']; source.update(cached=True, stale=True, channel=result['channel'], route=result['route'])
                return found
            finally:
                with lock:
                    completed += 1; parsed += len(found)
                    source.update(nodes=len(found), elapsed_seconds=round(time.monotonic() - start, 2))
                    source['status'] = ('退避中，使用旧缓存' if source['deferred'] else '下载失败，使用旧缓存') if found and source['stale'] else ('缓存命中' if source['cached'] else '成功') if found else error_text
                    source['failure_reason'] = error_text if failed else ''
                    if failed and not source['deferred']:
                        failures = source.get('failures', 0) + 1
                        retry = max([a.get('retry_after', 0) for a in source['attempts']] + [min(3600, 60 * 2 ** min(failures, 6))])
                        source.update(failures=failures, next_retry=time.time() + retry)
                    log(f'GitHub · {source["name"]}：{source["status"]} · {len(found)} 个节点')
                    emit('公开来源获取进度')
        batches = parallel_map(fetch, enumerate(urls), workers, control)
    batches = [normalize_nodes(batch) for batch in batches]
    frequencies = Counter(key for batch in batches for key in {identity(node) for node in batch})
    selected, contributions = fair_candidates(batches, options.get('_source_budget', options.get('max_candidates', 1200)),
                                              options.get('nodes_per_source', 200), options.get('_preferred_nodes'))
    selected_ids = {identity(node) for node in selected}
    provenance = {}
    for index, (url, batch) in enumerate(zip(urls, batches)):
        source = source_states[index]; keys = {identity(node) for node in batch}
        chosen = keys & selected_ids
        source.update(node_ids=sorted(chosen), unique=len(keys), exclusive=sum(frequencies[k] == 1 for k in keys),
                      eligible=len(chosen), selected_own=contributions[index])
        for key in chosen: provenance.setdefault(key, []).append(source['name'])
        health[source_key(url)] = source
    successes = sum(bool(batch) for batch in batches)
    summary = {'sources': len(urls), 'downloaded': sum(bool(b) and not s['cached'] for b, s in zip(batches, source_states)),
               'cache': sum(bool(b) and s['cached'] and not s['stale'] for b, s in zip(batches, source_states)),
               'stale_cache': sum(bool(b) and s['stale'] for b, s in zip(batches, source_states)),
               'failed': len(urls) - successes, 'deferred': sum(s['deferred'] for s in source_states),
               'parsed': parsed, 'unique': len(frequencies), 'selected': len(selected),
               'not_selected': len(frequencies) - len(selected), 'limit': options.get('max_candidates', 1200)}
    report = {'sources': len(urls), 'sources_ok': successes, 'sources_failed': len(urls) - successes,
              'github_sources': len(urls), 'github_sources_ok': successes, 'details': source_states,
              'node_sources': provenance, 'unique': len(frequencies), 'summary': summary}
    if health_file:
        write_json(health_file, health)
        write_json(health_file.parent.parent / 'state/public-sources.json',
                   {'checked_at': time.time(), 'sources': snapshot(), 'summary': summary})
    emit(f'去重 {len(frequencies)} 个，本轮送检 {len(selected)} 个；其余保留在来源缓存',
         unique=len(frequencies), selected=len(selected), not_selected=summary['not_selected'], summary=summary)
    if not selected: raise SourcesUnavailable(report)
    return selected, report
