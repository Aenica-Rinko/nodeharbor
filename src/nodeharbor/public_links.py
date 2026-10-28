"""Public subscription links and conservative repository-document discovery."""
import ipaddress
import re
import socket
from urllib.parse import unquote, urlsplit

from .public_sources import github_file_url
from .storage import AppError

HOSTING_TYPES = ('GitHub 文件', 'Cloudflare 托管（第三方）', '其他公开订阅')
UNKNOWN_HOSTING = '历史类型未记录'


def subscription_url(value):
    """Accept credential-free HTTPS links, never local addresses or URL templates."""
    try:
        value = value.strip()
        u = urlsplit(value)
        if u.hostname in {'github.com', 'raw.githubusercontent.com'}:
            return github_file_url(value)
        host = u.hostname or ''
        if (u.scheme != 'https' or u.username or u.password or u.query or u.fragment or u.port
                or not host or '.' not in host or host.endswith(('.localhost', '.local', '.internal'))
                or u.path in {'', '/'} or len(value) > 2048
                or any(c.isspace() or ord(c) < 32 for c in value)
                or any(c in unquote(value) for c in '<>{}\\')
                or any(p in {'.', '..'} for p in unquote(u.path).split('/'))):
            raise ValueError()
        try: address = ipaddress.ip_address(host)
        except ValueError: address = None
        if address is not None: raise ValueError()
        return 'https://' + host + u.path
    except (ValueError, AttributeError):
        raise AppError('请使用不含账号、Token 查询参数或占位符的公开 HTTPS 订阅地址，不能使用本机或内网地址。') from None


def external_link(url):
    return urlsplit(url).hostname not in {'github.com', 'raw.githubusercontent.com'}


def check_public_host(url):
    """Check every external destination before connecting, including redirects."""
    url = subscription_url(url)
    try:
        addresses = socket.getaddrinfo(urlsplit(url).hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise AppError('公开订阅地址不能指向本机或内网。')
    except socket.gaierror as error:
        from requests.exceptions import ConnectionError
        raise ConnectionError(error) from None
    return url


def link_kind(url):
    host = urlsplit(url).hostname or ''
    if host in {'github.com', 'raw.githubusercontent.com'}: return 'GitHub 文件'
    if host.endswith(('.workers.dev', '.pages.dev')): return 'Cloudflare 托管（第三方）'
    return '其他公开订阅'


def pool_hosting(urls):
    return [kind for kind in HOSTING_TYPES if any(link_kind(url) == kind for url in urls)]


def node_hosting(report):
    """Use acquisition-time evidence; never infer old nodes from edited settings."""
    raw = report.get('node_hosting', {})
    if not isinstance(raw, dict): return {}
    return {key: [kind for kind in HOSTING_TYPES if kind in values]
            for key, values in raw.items() if isinstance(values, list)}


def repository_kind(name, description=''):
    text = (name + ' ' + description).lower()
    if any(w in text for w in ('cloudflarespeedtest', '优选ip', '优选 ip', 'cdn speed')): return '优选 IP 工具 / 列表'
    if any(w in text for w in ('edgetunnel', '部署', 'deploy', 'serverless')): return '部署工具 / 待确认'
    return '订阅候选 / 待确认'


def readme_links(text, repository):
    """Extract only subscription-looking HTTPS links; never crawl linked pages."""
    result, seen = [], set()
    for line in text[:512_000].splitlines():
        for match in re.finditer(r'https://[^\s<>"\x27`）)\]]+', line):
            raw = match.group().rstrip('.,;，。；')
            try: url = subscription_url(raw)
            except AppError: continue
            if not external_link(url) and '/'.join(urlsplit(url).path.strip('/').split('/')[:2]).casefold() != repository.casefold(): continue
            path = unquote(urlsplit(url).path).lower()
            if not any(w in path for w in ('sub', 'clash', 'v2ray', 'nodes', 'base64', '.yaml', '.yml')): continue
            if any(w in path for w in ('speedtest', '/ip.txt', '/ips.txt', 'your-', 'your_')): continue
            if url in seen: continue
            seen.add(url)
            result.append({'path': url, 'url': url, 'size': 0, 'kind': link_kind(url),
                           'provenance': 'https://github.com/' + repository + '#readme'})
            if len(result) == 30: return result
    return result
