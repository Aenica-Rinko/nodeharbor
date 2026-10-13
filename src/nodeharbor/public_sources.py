"""Curated public GitHub subscriptions; no account or token is required."""
from urllib.parse import urlsplit

GITHUB_POOLS = (
    ('Au1rxx/free-vpn-subscriptions', 'https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/clash.yaml'),
    ('steam-100/free-proxy-sub', 'https://raw.githubusercontent.com/steam-100/free-proxy-sub/data/clash.yaml'),
    ('ermaozi/get_subscribe', 'https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/clash.yml'),
    ('peasoft/NoMoreWalls', 'https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.meta.yml'),
    ('snakem982/proxypool', 'https://raw.githubusercontent.com/snakem982/proxypool/main/source/clash-meta-2.yaml'),
    ('free18/v2ray', 'https://raw.githubusercontent.com/free18/v2ray/main/c.yaml'),
    ('shaoyouvip/free', 'https://raw.githubusercontent.com/shaoyouvip/free/main/all.yaml'),
    ('Pawdroid/Free-servers', 'https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub'),
    ('free-nodes/v2rayfree', 'https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub'),
    ('ts-sf/fly', 'https://raw.githubusercontent.com/ts-sf/fly/main/clash'),
    ('0xRadikal/Free-v2ray-Configs', 'https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/verified/configs_base64.txt'),
    ('dongchengjie/airport', 'https://raw.githubusercontent.com/dongchengjie/airport/main/subs/merged/tested_within.yaml'),
    ('ishalumi/proxy-node-collector', 'https://raw.githubusercontent.com/ishalumi/proxy-node-collector/main/output/nodes_base64.txt'),
    ('Ruk1ng001/freeSub', 'https://raw.githubusercontent.com/Ruk1ng001/freeSub/main/clash.yaml'),
    ('kooker/FreeSubsCheck', 'https://raw.githubusercontent.com/kooker/FreeSubsCheck/main/all.yaml'),
    ('LeilaoMi/AutoMergePublicNodes-Optimized', 'https://raw.githubusercontent.com/LeilaoMi/AutoMergePublicNodes-Optimized/main/output/verified.yaml'),
    ('mahdibland/V2RayAggregator', 'https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/Eternity.yml'),
    ('codingbox/Free-Node-Merge', 'https://raw.githubusercontent.com/codingbox/Free-Node-Merge/main/node.txt'),
    ('freev2/free', 'https://raw.githubusercontent.com/freev2/free/main/v2'),
    ('Barabama/FreeNodes', 'https://raw.githubusercontent.com/Barabama/FreeNodes/feat/ai-crawler-v2/nodes/cfmem.yaml'),
    ('freenodess/freenodess', 'https://raw.githubusercontent.com/freenodess/freenodess/main/nodes/clash.yaml'),
    ('lanzm/MetaFetch', 'https://raw.githubusercontent.com/lanzm/MetaFetch/master/list.meta.yml'),
    ('vxiaov/free_proxies', 'https://raw.githubusercontent.com/vxiaov/free_proxies/main/clash/clash.provider.yaml'),
    ('AzadNetCH/Clash', 'https://raw.githubusercontent.com/AzadNetCH/Clash/main/AzadNet.txt'),
    ('xiaoji235/airport-free', 'https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/v2rayshare.txt'),
    ('ebrasha/free-v2ray-public-list', 'https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/main/V2Ray-Config-By-EbraSha.txt'),
)

LARGE_POOLS = {'dongchengjie/airport', 'ishalumi/proxy-node-collector', 'freenodess/freenodess',
               'AzadNetCH/Clash', 'ebrasha/free-v2ray-public-list', 'lanzm/MetaFetch'}
OPTIONAL = LARGE_POOLS | {'ts-sf/fly', 'Barabama/FreeNodes', 'vxiaov/free_proxies', 'xiaoji235/airport-free'}
RECOMMENDED = tuple(name for name, _ in GITHUB_POOLS if name not in OPTIONAL)
QUICK = ('Au1rxx/free-vpn-subscriptions', 'steam-100/free-proxy-sub', 'shaoyouvip/free',
         'Ruk1ng001/freeSub', 'kooker/FreeSubsCheck', 'LeilaoMi/AutoMergePublicNodes-Optimized')
PRESETS = {'快速': (QUICK, 400, 100), '推荐均衡': (RECOMMENDED, 1200, 200),
           '全面': (tuple(name for name, _ in GITHUB_POOLS), 4000, 400)}
ALTERNATIVES = {
    '0xRadikal/Free-v2ray-Configs': ['https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/top100.txt'],
    'kooker/FreeSubsCheck': ['https://raw.githubusercontent.com/kooker/FreeSubsCheck/main/base64.txt'],
    'LeilaoMi/AutoMergePublicNodes-Optimized': ['https://raw.githubusercontent.com/LeilaoMi/AutoMergePublicNodes-Optimized/main/output/verified.txt'],
    'mahdibland/V2RayAggregator': ['https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/Eternity.txt'],
    'freenodess/freenodess': ['https://raw.githubusercontent.com/freenodess/freenodess/main/nodes/v2ray-base64.txt'],
}


def source_note(name):
    if name == 'ts-sf/fly': return '可读取公开文件，上游节点仍需本机检测'
    if name == 'xiaoji235/airport-free': return '近期文件可连接但无可解析节点，保留为可选来源'
    if name in {'0xRadikal/Free-v2ray-Configs', 'LeilaoMi/AutoMergePublicNodes-Optimized'}:
        return '优先读取上游筛选版本，本机仍会重新检测'
    if name == 'Barabama/FreeNodes': return '指定带斜杠的分支和固定文件，不使用日期文件'
    return '大型聚合来源，可能与其他来源重复' if name in LARGE_POOLS else '固定公开文件地址，本机去重后检测'


def source_urls(url):
    name = next((name for name, primary in GITHUB_POOLS if primary == url), None)
    return [url, *ALTERNATIVES.get(name, [])]


def selected_pools(options):
    selected = options.get('github_sources')
    selected = RECOMMENDED if selected is None else selected
    return [(name, url) for name, url in GITHUB_POOLS if name in selected]


def github_file_url(value):
    """Normalize explicitly public GitHub file URLs without credentials or query strings."""
    from .storage import AppError
    value = value.strip()
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or u.username or u.password or u.query or u.fragment or u.port:
            raise ValueError()
        parts = u.path.strip('/').split('/')
        if u.hostname == 'github.com' and len(parts) >= 5 and parts[2] == 'blob':
            parts.pop(2)
        elif u.hostname != 'raw.githubusercontent.com': raise ValueError()
        if len(parts) < 4 or any(part in {'', '.', '..'} for part in parts) or any(c.isspace() for c in value): raise ValueError()
        return 'https://raw.githubusercontent.com/' + '/'.join(parts)
    except ValueError:
        raise AppError('请填写公开 GitHub 文件的 HTTPS 地址（raw 或 blob），不要包含 Token、查询参数或目录地址。') from None


def public_fallbacks(url):
    # Only public built-ins may use the CDN: never forward a private subscription URL.
    allowed = {source for _, source in GITHUB_POOLS} | {u for urls in ALTERNATIVES.values() for u in urls}
    if url not in allowed: return []
    owner, repo, ref, *path = urlsplit(url).path.strip('/').split('/')
    from urllib.parse import quote
    if owner == 'Barabama' and repo == 'FreeNodes':
        ref, path = 'feat/ai-crawler-v2', ['nodes', 'cfmem.yaml']
        return [f'https://api.github.com/repos/{owner}/{repo}/contents/' + '/'.join(path) + '?ref=' + quote(ref, safe='')]
    return [f'https://cdn.jsdelivr.net/gh/{owner}/{repo}@{ref}/' + '/'.join(path),
            f'https://api.github.com/repos/{owner}/{repo}/contents/' + '/'.join(path) + '?ref=' + quote(ref, safe='')]
