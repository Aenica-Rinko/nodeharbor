"""Run only our own Mihomo instance in a temporary workspace."""
from __future__ import annotations

import ipaddress
import secrets
import socket
import subprocess
import tempfile
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import requests

from .nodes import UDP_PROTOCOLS, identity
from .storage import AppError, write_yaml
from .tasks import Cancelled, TaskControl, NetworkPool, parallel_map
from .quality import assess
from .diagnostics import core_error, error_tail

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_config(core, config, cwd, control=None):
    control = control or TaskControl()
    control.check()
    try:
        with subprocess.Popen([str(core), "-d", str(cwd), "-f", str(config), "-t"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW) as process:
            out, err = control.communicate(process, timeout=30)
            if process.returncode: control.core_diagnostic = core_error(out + err, process.returncode)
            return process.returncode == 0
    except OSError as exc:
        raise AppError("无法运行 Mihomo 配置检查，请检查核心文件。") from exc


def base_config(nodes):
    return {"mode": "rule", "log-level": "silent", "ipv6": True, "allow-lan": False,
            "dns": {"enable": False}, "tun": {"enable": False}, "proxies": nodes,
            "rules": ["MATCH,DIRECT"]}


@contextmanager
def running_core(core, nodes, control=None):
    control = control or TaskControl()
    with tempfile.TemporaryDirectory(prefix="nodeharbor-core-") as tmp:
        folder = Path(tmp)
        port, token = free_port(), secrets.token_urlsafe(32)
        config = base_config(nodes)
        config.update({"external-controller": f"127.0.0.1:{port}", "secret": token})
        file = folder / "config.yaml"
        write_yaml(file, config)
        if not check_config(core, file, folder, control): raise AppError('Mihomo 拒绝了待测配置。' + getattr(control, 'core_diagnostic', ''))
        control.check()
        errors = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen([str(core), "-d", str(folder), "-f", str(file)],
                                       stdout=errors, stderr=errors, creationflags=NO_WINDOW)
        except OSError:
            errors.close(); raise AppError('Mihomo 无法启动，请检查核心权限和架构。') from None
        process.core_diagnostic = lambda: core_error(error_tail(errors), process.returncode)
        url = f"http://127.0.0.1:{port}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            with requests.Session() as session:
                session.trust_env = False
                ready = False
                for _ in range(80):
                    control.check()
                    if process.poll() is not None: raise AppError('Mihomo 提前退出：' + core_error(error_tail(errors), process.returncode))
                    try:
                        response = session.get(url + "/version", headers=headers, timeout=0.5)
                        if response.ok:
                            ready = True; break
                    except requests.RequestException: pass
                    time.sleep(0.1)
                if not ready: raise AppError("Mihomo 控制接口启动超时。")
            yield url, headers, process
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            errors.close()


def tcp_ready(node, timeout):
    # QUIC/UDP nodes must be tested by their actual protocol, never by a TCP handshake.
    if node["type"] in UDP_PROTOCOLS: return True
    try:
        addresses = socket.getaddrinfo(node["server"], node["port"], type=socket.SOCK_STREAM)
        public = [a for a in addresses if ipaddress.ip_address(a[4][0]).is_global]
        for family, kind, proto, _, addr in public:
            try:
                with socket.socket(family, kind, proto) as sock:
                    sock.settimeout(timeout); sock.connect(addr)
                    return True
            except OSError: pass
    except OSError: pass
    return False


def compatible_nodes(core, nodes, log, control=None):
    """Bisect invalid batches so one malformed subscription cannot reject all nodes."""
    control = control or TaskControl()
    with tempfile.TemporaryDirectory(prefix="nodeharbor-check-") as tmp:
        folder = Path(tmp); path = folder / "check.yaml"
        def check(batch):
            control.check()
            write_yaml(path, base_config([dict(p, name=f"compat-{i}") for i, p in enumerate(batch)]))
            if check_config(core, path, folder, control): return batch
            if len(batch) == 1:
                log("跳过 1 个核心不支持或配置不完整的节点")
                return []
            middle = len(batch) // 2
            return check(batch[:middle]) + check(batch[middle:])
        return check(nodes) if nodes else []


def verify_nodes(core, nodes, options, log, control=None):
    control = control or TaskControl()
    with NetworkPool(options['workers'], control) as pool:
        return _verify_nodes(core, nodes, options, log, control, pool)


def _verify_nodes(core, nodes, options, log, control, pool):
    log(f"预检查 {len(nodes)} 个候选节点；UDP 协议直接进入核心检测")
    completed, tcp_passed, udp_skipped = 0, 0, 0
    checked_at = {}
    lock = threading.Lock()
    control.emit('precheck', 0, len(nodes), '检查服务器可连接性；UDP 节点使用协议检测', candidates=len(nodes))
    def precheck(node):
        nonlocal completed, tcp_passed, udp_skipped
        ready = False
        control.check()
        try:
            if node["type"] in UDP_PROTOCOLS: ready = True; return True
            ready = pool.request('tcp', {"node": node, "timeout": options["tcp_timeout_seconds"]},
                                 max(10, options["tcp_timeout_seconds"] * 2))
            return ready
        except Cancelled: raise
        except AppError:
            control.check()
            return False
        finally:
            with lock:
                checked_at[identity(node)] = time.time()
                completed += 1
                if node['type'] in UDP_PROTOCOLS: udp_skipped += 1
                elif ready: tcp_passed += 1
                control.emit('precheck', completed, len(nodes), '服务器连通性预检', candidates=len(nodes),
                             tcp_passed=tcp_passed, udp_skipped=udp_skipped)
    flags = parallel_map(precheck, nodes, options["workers"], control)
    outcomes = {identity(p): False for p, ok in zip(nodes, flags) if not ok}
    candidates = [p for p, ok in zip(nodes, flags) if ok]
    log(f"{len(candidates)} 个节点进入 Mihomo 协议检测")
    alive, delays, unsupported, tested, stable = [], {}, 0, 0, 0
    quality = {}
    control.emit('protocol', 0, len(candidates), '正在检查核心兼容性，随后检测代理出口', passed=0)
    size = options["batch_size"]
    for offset in range(0, len(candidates), size):
        control.check()
        original = candidates[offset:offset + size]
        batch = compatible_nodes(core, original, log, control)
        unsupported += len(original) - len(batch)
        protocol_done, batch_passed = offset + len(original) - len(batch), 0
        if not batch:
            control.emit('protocol', offset + len(original), len(candidates), '已跳过核心不支持的节点', passed=len(alive), unsupported=unsupported, tested=tested, stable=stable)
            continue
        control.emit('protocol', protocol_done, len(candidates), '启动隔离的 Mihomo 检测实例', passed=len(alive), unsupported=unsupported, tested=tested, stable=stable)
        test_nodes = [dict(p, name=f"probe-{i}") for i, p in enumerate(batch)]
        with running_core(core, test_nodes, control) as (url, headers, process):
            def probe(index):
                nonlocal protocol_done, batch_passed, tested, stable
                timeout = options["timeout_ms"] / 1000 + 3
                passed = False
                try:
                    def request(target):
                        return pool.request('probe', {
                            'url': url + '/proxies/' + quote(f'probe-{index}', safe='') + '/delay',
                            'params': {'url': target, 'timeout': options['timeout_ms'], 'expected': '200-299'},
                            'headers': headers, 'timeout': timeout, 'confirm_expected': True}, timeout + 5)
                    result = assess(request, options, control)
                    with lock: quality[identity(batch[index])] = result
                    if result['quality'] != 'failed':
                        passed = True
                        return result['delay_ms']
                except Cancelled: raise
                except AppError: control.check()
                finally:
                    with lock:
                        outcomes[identity(batch[index])] = passed
                        checked_at[identity(batch[index])] = time.time()
                        protocol_done += 1
                        tested += 1
                        if passed: batch_passed += 1
                        if quality.get(identity(batch[index]), {}).get('quality') == 'stable': stable += 1
                        control.emit('protocol', protocol_done, len(candidates), '重复检测 HTTP / HTTPS 出口',
                                     passed=len(alive) + batch_passed, unsupported=unsupported, tested=tested, stable=stable)
                return None
            results = parallel_map(probe, range(len(batch)), options["workers"], control)
            if process.poll() is not None: raise AppError('检测过程中 Mihomo 意外退出：' + process.core_diagnostic())
        for node, delay in zip(batch, results):
            if delay is not None:
                alive.append(node); delays[identity(node)] = delay
        log(f"已完成 {min(offset + size, len(candidates))}/{len(candidates)}，通过 {len(alive)} 个")
    return alive, {"candidates": len(nodes), 'tcp_passed': tcp_passed, 'udp_skipped': udp_skipped,
                   "protocol_tested": tested, "unsupported": unsupported, 'stable': stable,
                   "verified": len(alive), "delays_ms": delays, 'quality': quality, 'outcomes': outcomes, 'checked_at': checked_at,
                   'test_urls': [options['test_url'], options.get('https_test_url', 'https://www.gstatic.com/generate_204')]}
