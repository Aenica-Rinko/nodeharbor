import base64

from nodeharbor.geo import get_country_from_name
from nodeharbor.nodes import normalize_nodes, parse_subscription
from nodeharbor.verifier import tcp_ready


def node(**values):
    return dict({"name": "Example", "type": "vless", "server": "example.com", "port": 443,
                 "uuid": "12345678-1234-1234-1234-123456789abc"}, **values)


def test_distinct_credentials_and_transport_are_preserved():
    nodes = normalize_nodes([node(), node(uuid="aaaaaaaa-1234-1234-1234-123456789abc"), node(network="ws"), node()])
    assert len(nodes) == 3
    assert len({p["name"] for p in nodes}) == 3


def test_reality_and_ipv6_uri_are_preserved():
    text = "vless://12345678-1234-1234-1234-123456789abc@[2606:4700::1111]:443?security=reality&sni=example.org&pbk=abc&sid=1234&flow=xtls-rprx-vision#hello"
    result = parse_subscription(text)[0]
    assert result["server"] == "2606:4700::1111"
    assert result["reality-opts"] == {"public-key": "abc", "short-id": "1234"}
    assert result["flow"] == "xtls-rprx-vision"
    assert result["servername"] == "example.org"


def test_ss_sip002_base64_auth_and_hy2_alias():
    auth = base64.urlsafe_b64encode(b"aes-256-gcm:my-password").decode().rstrip("=")
    nodes = parse_subscription(f"ss://{auth}@example.com:8388#ss\nhy2://password@example.net:443?sni=example.org#hy2")
    assert len(nodes) == 2
    assert nodes[0]["password"] == "my-password"
    assert nodes[1]["type"] == "hysteria2"


def test_yaml_preserves_protocol_specific_fields():
    result = parse_subscription("proxies:\n- {name: test, type: ss, server: example.com, port: 1234, cipher: aes-256-gcm, password: xyz, udp: false, plugin: obfs, plugin-opts: {mode: tls}}")
    assert result[0]["udp"] is False
    assert result[0]["plugin-opts"] == {"mode": "tls"}


def test_invalid_nodes_do_not_invalidate_subscription():
    assert len(normalize_nodes([node(port="bad"), node(), node(server="127.0.0.1")])) == 1


def test_udp_is_not_tested_with_tcp(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("TCP called")))
    assert tcp_ready({"type": "hysteria2"}, 1)
    assert tcp_ready({"type": "tuic"}, 1)


def test_country_short_codes_do_not_match_inside_words():
    assert get_country_from_name("BUSINESS PREMIUM") is None
    assert get_country_from_name("🇯🇵 Japan") == "JP"
    assert get_country_from_name("HK premium") == "HK"
    assert get_country_from_name("印度尼西亚") == "ID"
