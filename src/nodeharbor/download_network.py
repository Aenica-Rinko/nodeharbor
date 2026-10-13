"""Download routing without .netrc or changes to Windows/system proxy configuration."""
from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit

import requests


def loopback_proxy(value):
    value = value.strip()
    if '://' not in value: value = 'http://' + value
    try:
        u = urlsplit(value)
        if u.scheme not in {'http', 'https'} or u.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            return None
        if u.username or u.password or not u.port or u.query or u.fragment or u.path not in {'', '/'}: return None
        host = '[::1]' if u.hostname == '::1' else '127.0.0.1'
        return f'{u.scheme}://{host}:{u.port}'
    except ValueError: return None


def saved_windows_loopback_proxies():
    if os.name != 'nt': return []
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            value = winreg.QueryValueEx(key, 'ProxyServer')[0]
    except OSError: return []
    result = []
    for part in str(value).split(';'):
        if '=' in part:
            scheme, part = part.split('=', 1)
            if scheme.strip().lower() not in {'http', 'https'}: continue
        proxy = loopback_proxy(part)
        if proxy: result.append(proxy)
    return result


def local_proxy_candidates():
    urls = [*saved_windows_loopback_proxies(), *(f'http://127.0.0.1:{p}' for p in (7890, 7897, 10809, 10808, 17890))]
    result = []
    for url in dict.fromkeys(urls):
        u = urlsplit(url)
        try:
            with socket.create_connection((u.hostname, u.port), timeout=0.15): result.append(url)
        except OSError: pass
    return result


def primary_proxies(url, options):
    if options.get('proxy'): return {'http': options['proxy'], 'https': options['proxy']}
    return requests.utils.get_environ_proxies(url)


def proxy_label(proxies):
    if not proxies: return '直连'
    value = proxies.get('https', proxies.get('http', ''))
    local = loopback_proxy(value)
    return '本机代理 · ' + str(urlsplit(local).port) if local else '配置 / 环境代理'


def error_code(error):
    if isinstance(error, requests.exceptions.ProxyError): return 'proxy'
    if isinstance(error, requests.exceptions.SSLError): return 'tls'
    if isinstance(error, requests.exceptions.Timeout): return 'timeout'
    if isinstance(error, requests.exceptions.HTTPError): return 'http'
    # Retries may wrap timeout and DNS errors inside requests.ConnectionError.
    pending, seen = [error], set()
    for _ in range(20):
        if not pending: break
        value = pending.pop()
        if id(value) in seen: continue
        seen.add(id(value))
        name = type(value).__name__
        if name in {'ReadTimeoutError', 'ConnectTimeoutError', 'TimeoutError'}: return 'timeout'
        if name in {'NameResolutionError', 'gaierror'}: return 'dns'
        pending.extend(v for v in getattr(value, 'args', ()) if isinstance(v, BaseException))
        for key in ('reason', '__cause__', '__context__'):
            cause = getattr(value, key, None)
            if isinstance(cause, BaseException): pending.append(cause)
    if isinstance(error, requests.exceptions.ConnectionError): return 'connection'
    return 'format'


class DownloadFailed(Exception):
    def __init__(self, code, attempts):
        self.code, self.attempts = code, attempts
        super().__init__(code)
