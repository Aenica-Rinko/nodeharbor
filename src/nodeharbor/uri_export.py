"""Standard v2rayN subscriptions; unsupported settings are counted, never discarded silently."""
from __future__ import annotations

import base64
import json
from collections import Counter
from urllib.parse import quote, urlencode


class Unsupported(ValueError):
    pass


def b64(value):
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def endpoint(node):
    host = node['server']
    if ':' in host: host = f'[{host}]'
    return f"{host}:{node['port']}"


def link(scheme, node, auth, query=None):
    suffix = '?' + urlencode(query, quote_via=quote) if query else ''
    return f"{scheme}://{auth}@{endpoint(node)}{suffix}#{quote(node['name'], safe='')}"


def transport(node):
    network = node.get('network', 'tcp')
    if network not in {'tcp', 'ws', 'grpc'}:
        raise Unsupported('传输方式暂不支持链接导出')
    q = {'type': network}
    if network == 'ws':
        opts = node.get('ws-opts', {})
        headers = opts.get('headers', {})
        if set(headers) - {'Host', 'host'} or set(opts) - {'path', 'headers'}:
            raise Unsupported('WebSocket 自定义参数无法完整转换')
        q.update(path=opts.get('path', '/'), host=headers.get('Host', headers.get('host', '')))
    if network == 'grpc':
        opts = node.get('grpc-opts', {})
        if set(opts) - {'grpc-service-name'}:
            raise Unsupported('gRPC 自定义参数无法完整转换')
        q['serviceName'] = opts.get('grpc-service-name', '')
    if any(node.get(key) for key in ('http-opts', 'h2-opts', 'smux')):
        raise Unsupported('自定义传输参数无法完整转换')
    return q


def tls_query(node, default_tls=False):
    q = {'security': 'tls' if node.get('tls', default_tls) else 'none'}
    for key, target in [('servername', 'sni'), ('sni', 'sni'), ('client-fingerprint', 'fp')]:
        if node.get(key): q[target] = node[key]
    if node.get('alpn'): q['alpn'] = ','.join(node['alpn'])
    reality = node.get('reality-opts')
    if reality:
        if set(reality) - {'public-key', 'short-id'}:
            raise Unsupported('Reality 自定义参数无法完整转换')
        q.update(security='reality', pbk=reality['public-key'], sid=reality.get('short-id', ''))
    return q


def to_uri(node):
    kind = node['type']
    if any(node.get(key) for key in ('dialer-proxy', 'ca', 'ca-str', 'client-cert', 'client-key',
                                      'certificate', 'ech-opts', 'tls-fingerprint')):
        raise Unsupported('证书、链式代理或 TLS 自定义参数无法完整转换')
    if kind == 'ss':
        q = {}
        plugin = node.get('plugin')
        opts = node.get('plugin-opts', {})
        if plugin:
            if plugin in {'obfs', 'obfs-local'}:
                if opts.get('mode', opts.get('obfs')) != 'http' or set(opts) - {'mode', 'host', 'obfs', 'obfs-host'}:
                    raise Unsupported('v2rayN 不支持该 Shadowsocks 混淆设置')
                q['plugin'] = 'obfs-local;obfs=http;obfs-host=' + str(opts.get('host', opts.get('obfs-host', '')))
            elif plugin == 'v2ray-plugin':
                if opts.get('mode', 'websocket') != 'websocket' or set(opts) - {'mode', 'host', 'path', 'tls', 'mux'}:
                    raise Unsupported('Shadowsocks 插件参数无法完整转换')
                parts = ['v2ray-plugin', 'mode=websocket']
                for key in ('host', 'path'):
                    if opts.get(key): parts.append(f'{key}={opts[key]}')
                for key in ('tls', 'mux'):
                    if opts.get(key): parts.append(key)
                q['plugin'] = ';'.join(parts)
            else: raise Unsupported('Shadowsocks 插件暂不支持链接导出')
            if any(';' in str(v) for v in opts.values()):
                raise Unsupported('Shadowsocks 插件参数包含分隔符')
        auth = base64.urlsafe_b64encode(f"{node['cipher']}:{node['password']}".encode()).decode().rstrip('=')
        return link('ss', node, auth, q)
    if kind == 'vmess':
        q = transport(node)
        if node.get('reality-opts'): raise Unsupported('VMess Reality 暂不支持链接导出')
        d = {'v': '2', 'ps': node['name'], 'add': node['server'], 'port': str(node['port']),
             'id': node['uuid'], 'aid': str(node.get('alterId', 0)), 'scy': node.get('cipher', 'auto'),
             'net': q['type'], 'type': 'none', 'host': q.get('host', ''),
             'path': q.get('path', q.get('serviceName', '')), 'tls': 'tls' if node.get('tls') else '',
             'sni': node.get('servername', node.get('sni', '')), 'alpn': ','.join(node.get('alpn', [])),
             'fp': node.get('client-fingerprint', ''), 'insecure': '1' if node.get('skip-cert-verify') else '0'}
        return 'vmess://' + b64(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
    if kind in {'vless', 'trojan'}:
        q = tls_query(node, kind == 'trojan')
        q.update(transport(node))
        if node.get('flow'): q['flow'] = node['flow']
        if kind == 'vless':
            if node.get('skip-cert-verify'):
                raise Unsupported('v2rayN VLESS 链接不保留跳过证书校验设置')
            q['encryption'] = node.get('encryption', 'none')
            auth = node['uuid']
        else:
            auth = node['password']
            if node.get('skip-cert-verify'): q['insecure'] = '1'
        return link(kind, node, quote(auth, safe=''), q)
    if kind in {'hysteria2', 'tuic', 'anytls'}:
        q = tls_query(node, True)
        q.pop('security', None)
        if node.get('reality-opts'): raise Unsupported('该协议不支持 Reality 链接')
        auth = quote(node['password'], safe='')
        if kind == 'tuic':
            if node.get('udp-relay-mode', 'native') != 'native' or node.get('disable-sni'):
                raise Unsupported('TUIC 自定义参数无法完整转换')
            auth = quote(node['uuid'], safe='') + ':' + auth
            if node.get('congestion-controller'): q['congestion_control'] = node['congestion-controller']
            if node.get('skip-cert-verify'): q['allow_insecure'] = '1'
        else:
            if node.get('skip-cert-verify'): q['insecure'] = '1'
        if kind == 'hysteria2':
            if node.get('obfs'):
                if node['obfs'] != 'salamander': raise Unsupported('Hysteria2 混淆参数暂不支持链接导出')
                q.update(obfs=node['obfs'], **{'obfs-password': node.get('obfs-password', '')})
            if node.get('ports'): q['mport'] = str(node['ports']).replace(':', '-')
            if node.get('fingerprint'): q['pinSHA256'] = node['fingerprint']
        if kind == 'anytls': q.update(security='tls', type='tcp')
        return link(kind, node, auth, q)
    if kind == 'socks5':
        if node.get('tls'): raise Unsupported('SOCKS TLS 暂不支持链接导出')
        if ':' in str(node.get('username', '')): raise Unsupported('SOCKS 用户名包含链接分隔符')
        return link('socks', node, quote(b64(f"{node.get('username', '')}:{node.get('password', '')}"), safe=''))
    if kind == 'wireguard':
        if node.get('peers') or node.get('amnezia-wg-option'):
            raise Unsupported('WireGuard 多对端或混淆参数无法完整转换')
        q = {'publickey': node['public-key']}
        for key, target in [('pre-shared-key', 'presharedkey'), ('mtu', 'mtu')]:
            if node.get(key): q[target] = node[key]
        addresses = []
        for key, mask in [('ip', '/32'), ('ipv6', '/128')]:
            if node.get(key): addresses.append(node[key] if '/' in node[key] else node[key] + mask)
        if addresses: q['address'] = ','.join(addresses)
        for key in ('reserved', 'dns'):
            if node.get(key): q[key] = ','.join(map(str, node[key])) if isinstance(node[key], list) else node[key]
        return link(kind, node, quote(node['private-key'], safe=''), q)
    raise Unsupported(f'{kind.upper()} 暂不支持 v2rayN 标准链接导出')


def export_uris(nodes):
    links, reasons = [], Counter()
    for node in nodes:
        try: links.append(to_uri(node))
        except Unsupported as exc: reasons[str(exc)] += 1
        except (KeyError, TypeError, ValueError): reasons['协议参数不完整或类型无效'] += 1
    text = '\n'.join(links) + ('\n' if links else '')
    return text, {'exported': len(links), 'skipped': sum(reasons.values()), 'reasons': dict(reasons)}
