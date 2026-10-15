"""Lossless node normalization, URI parsing and credential-aware identity."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from copy import deepcopy
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from .storage import AppError

SUPPORTED = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2", "tuic", "http", "socks5", "mieru", "anytls", "wireguard", "ssh"}
UDP_PROTOCOLS = {"hysteria", "hysteria2", "tuic", "wireguard"}
IDENTITY_IGNORED = {"name", "_country", "_rtt", "_alive", "_speed"}


def identity(node):
    # Include authentication, transport, SNI and Reality settings. Same port is not same node.
    fields = {k: v for k, v in node.items() if k not in IDENTITY_IGNORED and not k.startswith("_")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public_host(server):
    if not server or server.lower() in {"localhost", "localhost.localdomain"}: return False
    try:
        return ipaddress.ip_address(server).is_global
    except ValueError:
        return not server.lower().endswith((".local", ".localhost"))


def normalize_nodes(nodes, strict=False):
    result, seen, names = [], set(), set()
    aliases = {"shadowsocks": "ss", "hy2": "hysteria2", "v2ray": "vmess"}
    for index, raw in enumerate(nodes):
        try:
            if not isinstance(raw, dict): raise ValueError("节点必须为对象")
            p = {k: deepcopy(v) for k, v in raw.items() if isinstance(k, str) and not k.startswith("_")}
            p["type"] = aliases.get(str(p.get("type", "")).lower(), str(p.get("type", "")).lower())
            if not isinstance(p.get('server'), str): raise ValueError('无效服务器或端口')
            p["server"] = p['server'].strip().strip("[]")
            if type(p.get('port')) not in (str, int): raise ValueError('无效服务器或端口')
            p["port"] = int(p.get("port", 0))
            if p["type"] not in SUPPORTED: raise ValueError("不支持的协议")
            if not 1 <= p["port"] <= 65535 or not public_host(p["server"]): raise ValueError("无效服务器或端口")
            if p["type"] in {"vless", "vmess", "tuic"} and not p.get("uuid"): raise ValueError("缺少 UUID")
            if p["type"] in {"ss", "trojan", "hysteria2", "tuic", "anytls"} and not p.get("password"): raise ValueError("缺少密码")
            if p["type"] == "ss" and not p.get("cipher"): raise ValueError("缺少加密方式")
            if p["type"] == "vmess": p.setdefault("cipher", "auto")
            key = identity(p)
            if key in seen: continue
            seen.add(key)
            original = str(p.get("name") or f"{p['type'].upper()} 节点 {index + 1}").strip()
            if not original: original = f"{p['type'].upper()} 节点 {index + 1}"
            name, suffix = original, 2
            while name in names:
                name = f"{original} · {suffix}"; suffix += 1
            names.add(name); p["name"] = name
            result.append(p)
        except (ValueError, TypeError, RecursionError) as exc:
            if strict:
                safe = {'节点必须为对象', '不支持的协议', '无效服务器或端口', '缺少 UUID', '缺少密码', '缺少加密方式'}
                reason = str(exc) if str(exc) in safe else '字段类型或格式无效'
                raise AppError(f"第 {index + 1} 个节点配置无效：{reason}") from None
    return result


def decode64(value):
    value = re.sub(r"\s+", "", value)
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def parse_link(link):
    try:
        if link.startswith("vmess://"):
            d = json.loads(decode64(link[8:]))
            if not isinstance(d, dict): return None
            p = {"type": "vmess", "name": d.get("ps", "VMess"), "server": d["add"], "port": int(d["port"]),
                 "uuid": d["id"], "alterId": int(d.get("aid", 0)), "cipher": d.get("scy", "auto"),
                 "network": d.get("net", "tcp"), "tls": d.get("tls") == "tls"}
            if d.get("sni"): p["servername"] = d["sni"]
            if d.get('insecure') in {'1', 1, True}: p['skip-cert-verify'] = True
            if d.get('fp'): p['client-fingerprint'] = d['fp']
            if d.get('alpn'): p['alpn'] = d['alpn'].split(',')
            if p["network"] == "ws": p["ws-opts"] = {"path": d.get("path", "/"), "headers": {"Host": d.get("host", "")}}
            if p["network"] == "grpc": p["grpc-opts"] = {"grpc-service-name": d.get("path", "")}
            return p
        if link.startswith("ss://"):
            raw = link[5:]
            body, _, fragment = raw.partition("#")
            if "@" not in body: body = decode64(body.split("?")[0])
            auth, endpoint = body.rsplit("@", 1)
            auth = unquote(auth)
            if ":" not in auth: auth = decode64(auth)
            cipher, password = auth.split(":", 1)
            u = urlsplit("ss://" + endpoint)
            p = {"type": "ss", "name": unquote(fragment) or "Shadowsocks", "server": u.hostname,
                 "port": u.port, "cipher": cipher, "password": password}
            q = parse_qs(u.query)
            if "plugin" in q:
                plugin, *opts = q["plugin"][0].split(";")
                p["plugin"] = "obfs" if plugin == "obfs-local" else plugin
                p["plugin-opts"] = dict(o.split("=", 1) if "=" in o else (o, True) for o in opts)
                if p["plugin"] == "obfs":
                    for source, target in (("obfs", "mode"), ("obfs-host", "host")):
                        if source in p["plugin-opts"]:
                            p["plugin-opts"][target] = p["plugin-opts"].pop(source)
            return p
        u = urlsplit(link)
        kind = {"hy2": "hysteria2"}.get(u.scheme, u.scheme)
        if kind not in {"vless", "trojan", "hysteria2", "tuic", "anytls", "socks", "wireguard"}: return None
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = {"type": kind, "name": unquote(u.fragment) or kind.upper(), "server": u.hostname, "port": u.port}
        auth = unquote(u.username or "")
        if kind == 'socks':
            p['type'] = 'socks5'
            userinfo = unquote(u.netloc.rsplit('@', 1)[0]) if '@' in u.netloc else ''
            if userinfo:
                userinfo = userinfo if ':' in userinfo else decode64(userinfo)
                p['username'], p['password'] = userinfo.split(':', 1)
            return p
        if kind == 'wireguard':
            p['private-key'], p['public-key'] = auth, q['publickey']
            if q.get('presharedkey'): p['pre-shared-key'] = q['presharedkey']
            for address in q.get('address', '').split(','):
                if address: p['ipv6' if ':' in address else 'ip'] = address
            if q.get('reserved'): p['reserved'] = [int(v) for v in q['reserved'].split(',')]
            if q.get('mtu'): p['mtu'] = int(q['mtu'])
            if q.get('dns'): p['dns'] = q['dns'].split(',')
            return p
        if kind in {"vless", "tuic"}: p["uuid"] = auth
        else: p["password"] = auth
        if kind == "tuic": p["password"] = unquote(u.password or "")
        if q.get("sni") or q.get("peer"):
            p["servername" if kind == "vless" else "sni"] = q.get("sni") or q["peer"]
        if q.get("insecure", q.get("allowInsecure", q.get('allow_insecure'))) in {"1", "true"}: p["skip-cert-verify"] = True
        if q.get("security") in {"tls", "reality"} or kind == "trojan": p["tls"] = True
        if q.get("flow"): p["flow"] = q["flow"]
        if q.get("fp"): p["client-fingerprint"] = q["fp"]
        if q.get("pbk"):
            p["reality-opts"] = {"public-key": q["pbk"], "short-id": q.get("sid", "")}
        network = q.get("type", "tcp")
        if kind in {"vless", "trojan"}:
            p["network"] = network
            if network == "ws": p["ws-opts"] = {"path": q.get("path", "/"), "headers": {"Host": q.get("host", "")}}
            if network == "grpc": p["grpc-opts"] = {"grpc-service-name": q.get("serviceName", "")}
        if q.get("alpn"): p["alpn"] = q["alpn"].split(",")
        if q.get("obfs"):
            p["obfs"] = q["obfs"]
            p["obfs-password"] = q.get("obfs-password", "")
        if kind == 'hysteria2':
            if q.get('mport'): p['ports'] = q['mport'].replace('-', ':')
            if q.get('pinSHA256'): p['fingerprint'] = q['pinSHA256']
        if kind == 'tuic' and q.get('congestion_control'): p['congestion-controller'] = q['congestion_control']
        return p
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        return None


LINK_RE = re.compile(r'(?:vmess|vless|trojan|ss|hysteria2|hy2|tuic|anytls|socks|wireguard)://[^\s<>"\'\\]+')


def parse_subscription(text, depth=0):
    if depth > 1: return []
    try:
        data = yaml.safe_load(text)
        if isinstance(data, dict) and isinstance(data.get("proxies"), list):
            return normalize_nodes(data["proxies"])
    except (yaml.YAMLError, RecursionError): pass
    links = [parse_link(m.group()) for m in LINK_RE.finditer(text)]
    nodes = normalize_nodes([p for p in links if p])
    if nodes: return nodes
    try:
        cleaned = re.sub(r"\s+", "", text)
        if re.fullmatch(r"[A-Za-z0-9+/=_-]+", cleaned): return parse_subscription(decode64(cleaned), depth + 1)
    except (ValueError, UnicodeError): pass
    return []
