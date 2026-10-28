"""Check synthetic client exports, optionally with actual Mihomo/Verge cores (-t only)."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from nodeharbor.config import validate_core
from nodeharbor.exporter import publish
from nodeharbor.initialize import initialize
from nodeharbor.nodes import parse_subscription
from nodeharbor.storage import Paths, read_yaml
from nodeharbor.verifier import check_config


def fixtures():
    nodes = []
    for i, kind in enumerate(('ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'tuic', 'anytls')):
        node = dict(name=f'演示 {kind} / % #', type=kind, server='example.com', port=440 + i)
        if kind in ('vmess', 'vless', 'tuic'): node['uuid'] = '11111111-1111-4111-8111-111111111111'
        if kind not in ('vmess', 'vless'): node['password'] = 'fixture-only:@/%41+'
        if kind == 'ss': node['cipher'] = 'aes-256-gcm'
        if kind in ('vmess', 'vless'):
            node.update(tls=True, servername='example.com', network='ws')
            node['ws-opts'] = {'path': '/test?ed=1024', 'headers': {'Host': 'example.com'}}
        if kind == 'trojan':
            node.update(network='grpc', sni='example.com', tls=True)
            node['grpc-opts'] = {'grpc-service-name': 'fixture/%41'}
        nodes.append(node)
    return nodes


def check(mihomo=None, verge_core=None):
    result = {'ok': False, 'fixture_protocols': [n['type'] for n in fixtures()],
              'network_traffic_tested': False, 'gui_import_tested': False, 'checks': []}
    with tempfile.TemporaryDirectory(prefix='nodeharbor-clients-') as temporary:
        paths = Paths(Path(temporary)); initialize(paths)
        folder, report = publish(paths, fixtures(), {})
        if report['v2rayn']['free']['exported'] != 7: raise RuntimeError('Fixture unexpectedly skipped')
        yaml_nodes = read_yaml(folder / 'free-nodes.yaml')['proxies']
        plain = (paths.client_exports('v2rayN') / 'free.txt').read_text(encoding='utf-8')
        encoded = (paths.client_exports('v2rayN') / 'free.base64.txt').read_bytes()
        if base64.b64decode(encoded).decode() != plain: raise RuntimeError('Base64 export differs')
        decoded = parse_subscription(plain)
        if len(decoded) != len(yaml_nodes): raise RuntimeError('URI count differs')
        for original, restored in zip(yaml_nodes, decoded):
            for field in ('type', 'name', 'server', 'port', 'uuid', 'password', 'cipher', 'ws-opts', 'grpc-opts'):
                if field in original and original[field] != restored.get(field):
                    raise RuntimeError('Round-trip mismatch: ' + original['type'] + '/' + field)
        result['checks'].append('v2rayN standard URI and Base64 round-trip (NodeHarbor parser, not GUI import)')
        gui_config = read_yaml(paths.client_exports('Clash-Verge-Rev') / 'free.yaml')
        if any(key in gui_config for key in ('mixed-port', 'tun', 'external-controller')):
            raise RuntimeError('GUI configuration overrides client runtime')
        standalone = read_yaml(paths.client_exports('Mihomo') / 'free.yaml')
        if standalone.get('bind-address') != '127.0.0.1' or standalone.get('allow-lan') is not False:
            raise RuntimeError('Standalone configuration is not loopback-only')
        for client, executable in [('Mihomo', mihomo), ('Clash-Verge-Rev', verge_core)]:
            if not executable: continue
            executable = Path(executable).resolve()
            validate_core(executable)
            version = subprocess.run([str(executable), '-v'], capture_output=True, timeout=10,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.decode(errors='replace').splitlines()[0]
            config = paths.client_exports(client) / 'free.yaml'
            if not check_config(executable, config, paths.root): raise RuntimeError(client + ' core rejected export')
            result['checks'].append({'client': client, 'core_version': version, 'configuration_test': 'passed'})
        result['ok'] = True
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mihomo', type=Path)
    parser.add_argument('--verge-core', type=Path)
    parser.add_argument('--format-only', action='store_true')
    args = parser.parse_args()
    if not (args.mihomo or args.verge_core or args.format_only): parser.error('Select a core or explicitly use --format-only')
    print(json.dumps(check(args.mihomo, args.verge_core), ensure_ascii=True, indent=2))
