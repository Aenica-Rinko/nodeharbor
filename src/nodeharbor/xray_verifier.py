"""Isolated Xray checks for the protocols supported by this adapter."""
from __future__ import annotations

import subprocess
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .nodes import identity
from .storage import AppError, write_json
from .tasks import TaskControl, Cancelled, NetworkPool, parallel_map
from .quality import assess
from .diagnostics import core_error, error_tail
from .uri_export import Unsupported, to_uri, transport
from .verifier import free_port, NO_WINDOW


def outbound(node, tag='node'):
    kind = node['type']
    if kind not in {'ss', 'vmess', 'vless', 'trojan', 'socks5', 'http'}:
        raise Unsupported('当前 Xray 检测适配不支持该协议；可改用 Mihomo 检测')
    if kind != 'http': to_uri(node)  # Reject settings that cannot be preserved by the adapter.
    if node.get('plugin') or node.get('dialer-proxy') or (kind == 'http' and node.get('headers')):
        raise Unsupported('当前 Xray 检测不支持该插件或链式代理')
    server = {'address': node['server'], 'port': node['port']}
    protocol = {'ss': 'shadowsocks', 'socks5': 'socks'}.get(kind, kind)
    if kind == 'ss':
        server.update(method=node['cipher'], password=node['password'])
        settings = {'servers': [server]}
    elif kind in {'vless', 'vmess'}:
        user = {'id': node['uuid']}
        if kind == 'vless':
            user['encryption'] = node.get('encryption', 'none')
            if node.get('flow'): user['flow'] = node['flow']
        else: user.update(alterId=node.get('alterId', 0), security=node.get('cipher', 'auto'))
        server['users'] = [user]
        settings = {'vnext': [server]}
    else:
        if kind == 'trojan': server['password'] = node['password']
        elif node.get('username') or node.get('password'):
            server['users'] = [{'user': node.get('username', ''), 'pass': node.get('password', '')}]
        settings = {'servers': [server]}
    result = {'tag': tag, 'protocol': protocol, 'settings': settings}
    if kind in {'vless', 'vmess', 'trojan'}:
        q = transport(node)
        stream = {'network': q['type'], 'security': 'none'}
        if q['type'] == 'ws': stream['wsSettings'] = {'path': q['path'], 'headers': {'Host': q['host']}}
        if q['type'] == 'grpc': stream['grpcSettings'] = {'serviceName': q['serviceName']}
        if node.get('tls', kind == 'trojan'):
            stream['security'] = 'tls'
            tls = {'serverName': node.get('servername', node.get('sni', '')),
                   'allowInsecure': bool(node.get('skip-cert-verify'))}
            if node.get('alpn'): tls['alpn'] = node['alpn']
            if node.get('client-fingerprint'): tls['fingerprint'] = node['client-fingerprint']
            stream['tlsSettings'] = tls
        if node.get('reality-opts'):
            r = node['reality-opts']
            stream['security'] = 'reality'
            stream.pop('tlsSettings', None)
            stream['realitySettings'] = {'serverName': node.get('servername', node.get('sni', '')),
                                         'fingerprint': node.get('client-fingerprint', 'chrome'),
                                         'publicKey': r['public-key'], 'shortId': r.get('short-id', '')}
        result['streamSettings'] = stream
    elif node.get('tls'):
        raise Unsupported('当前 Xray 检测不支持该协议的 TLS 参数')
    return result


def config_for(nodes, ports=None):
    config = {'log': {'loglevel': 'none'}, 'inbounds': [], 'outbounds': [], 'routing': {'rules': []}}
    for i, node in enumerate(nodes):
        config['outbounds'].append(outbound(node, f'node-{i}'))
        if ports:
            config['inbounds'].append({'tag': f'probe-{i}', 'listen': '127.0.0.1', 'port': ports[i],
                                        'protocol': 'http', 'settings': {}})
            config['routing']['rules'].append({'type': 'field', 'inboundTag': [f'probe-{i}'], 'outboundTag': f'node-{i}'})
    # A routing error must never fall through to a direct connection.
    config['outbounds'].append({'tag': 'blocked', 'protocol': 'blackhole', 'settings': {}})
    config['routing']['rules'].append({'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'blocked'})
    return config


def check_xray(core, nodes, control=None):
    control = control or TaskControl()
    with tempfile.TemporaryDirectory(prefix='nodeharbor-xray-check-') as tmp:
        file = Path(tmp) / 'config.json'
        try: write_json(file, config_for(nodes))
        except (Unsupported, KeyError, TypeError, ValueError):
            raise AppError('当前 Xray 适配不支持这些节点的协议或参数。请核对配置，或切换到 Mihomo 检测。') from None
        with subprocess.Popen([str(core), 'run', '-test', '-config', str(file)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, creationflags=NO_WINDOW) as process:
            control.communicate(process, timeout=30)
            return process.returncode == 0


def compatible(core, nodes, control):
    if not nodes: return []
    if check_xray(core, nodes, control): return nodes
    if len(nodes) == 1: return []
    middle = len(nodes) // 2
    return compatible(core, nodes[:middle], control) + compatible(core, nodes[middle:], control)


@contextmanager
def running_xray(core, nodes, control):
    with tempfile.TemporaryDirectory(prefix='nodeharbor-xray-') as tmp:
        ports = []
        while len(ports) < len(nodes):
            port = free_port()
            if port not in ports: ports.append(port)
        file = Path(tmp) / 'config.json'; write_json(file, config_for(nodes, ports))
        errors = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen([str(core), 'run', '-config', str(file)], stdout=errors,
                                       stderr=errors, creationflags=NO_WINDOW)
        except OSError:
            errors.close(); raise AppError('Xray 无法启动，请检查核心权限和架构。') from None
        process.core_diagnostic = lambda: core_error(error_tail(errors), process.returncode)
        try:
            import socket
            ready = False
            for _ in range(60):
                control.check()
                if process.poll() is not None: raise AppError('Xray 提前退出：' + core_error(error_tail(errors), process.returncode))
                try:
                    with socket.create_connection(('127.0.0.1', ports[0]), timeout=0.1): ready = True
                    break
                except OSError: time.sleep(0.05)
            if not ready: raise AppError('Xray 本地检测端口启动超时。')
            yield ports, process
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(3)
            errors.close()


def verify_xray(core, nodes, options, log, control=None):
    control = control or TaskControl()
    with NetworkPool(options['workers'], control) as pool:
        return _verify_xray(core, nodes, options, log, control, pool)


def _verify_xray(core, nodes, options, log, control, pool):
    supported, reasons = [], Counter()
    for node in nodes:
        try: outbound(node); supported.append(node)
        except (Unsupported, KeyError, TypeError, ValueError): reasons['Xray 适配不支持该协议或参数'] += 1
    done, tcp_passed = 0, 0
    import threading
    lock = threading.Lock()
    control.emit('precheck', 0, len(supported), 'Xray 协议适配及 TCP 预检', candidates=len(nodes), unsupported=sum(reasons.values()))
    def precheck(node):
        nonlocal done, tcp_passed
        ready = False
        try:
            ready = pool.request('tcp', {'node': node, 'timeout': options['tcp_timeout_seconds']}, 10)
            return ready
        except Cancelled: raise
        except AppError: control.check(); return False
        finally:
            with lock:
                done += 1
                if ready: tcp_passed += 1
                control.emit('precheck', done, len(supported), '检查服务器可连接性', unsupported=sum(reasons.values()), tcp_passed=tcp_passed)
    flags = parallel_map(precheck, supported, options['workers'], control)
    candidates = [n for n, flag in zip(supported, flags) if flag]
    alive, delays, completed, tested, stable = [], {}, 0, 0, 0
    quality = {}
    control.emit('protocol', 0, len(candidates), '使用 Xray 检测真实代理连接', passed=0)
    for offset in range(0, len(candidates), 50):
        batch = compatible(core, candidates[offset:offset + 50], control)
        rejected = len(candidates[offset:offset + 50]) - len(batch)
        reasons['Xray 核心配置检查不通过'] += rejected
        completed += rejected
        if not batch:
            control.emit('protocol', completed, len(candidates), '跳过 Xray 配置不兼容节点', passed=len(alive), unsupported=sum(reasons.values()), tested=tested, stable=stable)
            continue
        batch_passed = 0
        with running_xray(core, batch, control) as (ports, process):
            def probe(index):
                nonlocal completed, batch_passed, tested, stable
                delay = None
                passed = False
                try:
                    result = assess(lambda target: pool.request('http-probe', {'url': target, 'port': ports[index],
                                         'timeout': options['timeout_ms'] / 1000}, options['timeout_ms'] / 1000 + 5), options, control)
                    with lock: quality[identity(batch[index])] = result
                    if result['quality'] != 'failed':
                        passed = True
                        return result['delay_ms']
                    return None
                except Cancelled: raise
                except AppError: control.check(); return None
                finally:
                    with lock:
                        completed += 1
                        tested += 1
                        if passed: batch_passed += 1
                        if quality.get(identity(batch[index]), {}).get('quality') == 'stable': stable += 1
                        control.emit('protocol', completed, len(candidates), '重复检测 HTTP / HTTPS 出口', passed=len(alive) + batch_passed, unsupported=sum(reasons.values()), tested=tested, stable=stable)
            results = parallel_map(probe, range(len(batch)), min(options['workers'], 24), control)
            if process.poll() is not None: raise AppError('Xray 在检测过程中退出：' + process.core_diagnostic())
        for node, delay in zip(batch, results):
            if delay is not None: alive.append(node); delays[identity(node)] = delay
        control.emit('protocol', completed, len(candidates), '当前批次检测完成', passed=len(alive), unsupported=sum(reasons.values()), tested=tested, stable=stable)
        log(f'Xray 已检测 {completed}/{len(candidates)}，通过 {len(alive)} 个')
    return alive, {'candidates': len(nodes), 'tcp_passed': tcp_passed, 'protocol_tested': tested, 'unsupported': sum(reasons.values()),
                    'skipped_reasons': dict(reasons), 'verified': len(alive), 'delays_ms': delays, 'core': 'xray', 'stable': stable, 'quality': quality}
