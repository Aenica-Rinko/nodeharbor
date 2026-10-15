"""Deterministic fair candidate selection and local source health learning."""
import hashlib
import time
from collections import deque

from .nodes import identity, normalize_nodes
from .storage import AppError, read_json, write_json


def source_key(url): return hashlib.sha256(url.encode()).hexdigest()


def read_mapping(path):
    try: value = read_json(path, {})
    except AppError: return {}
    return value if isinstance(value, dict) else {}


def nonnegative(value, default=0):
    # Reject strings, booleans, NaN, infinity and unreasonable persisted counters.
    return value if type(value) in (int, float) and 0 <= value < 1e15 else default


def priority(state):
    # Previous verification evidence is retained separately from this download's status.
    if not isinstance(state, dict): return (1, 0, 0, 60)
    def number(key, default=0):
        value = state.get(key, default)
        return nonnegative(value, default)
    eligible, verified = number('last_eligible'), number('last_verified')
    category = 0 if verified else 2 if eligible else 1
    rate = verified / max(eligible, 1)
    return (category, -rate, number('failures'), number('elapsed_seconds', 60))


def fair_candidates(batches, limit, per_source, preferred=None):
    preferred = preferred if isinstance(preferred, dict) else {}
    queues = [deque(sorted(normalize_nodes(batch), key=lambda node: (
        nonnegative(preferred.get(identity(node)), float('inf')), identity(node)))) for batch in batches]
    selected, seen, contributions = [], set(), [0] * len(queues)
    while len(selected) < limit:
        moved = False
        for index, nodes in enumerate(queues):
            if contributions[index] >= per_source: continue
            while nodes:
                node = nodes.popleft(); key = identity(node)
                if key in seen: continue
                seen.add(key); selected.append(node); contributions[index] += 1; moved = True
                break
            if len(selected) == limit: break
        if not moved: break
    return selected, contributions


def verification_sources(report, selected, alive, checks):
    eligible, passed = {identity(n) for n in selected}, {identity(n) for n in alive}
    # Quality results exist only for nodes actually sent to protocol probes.
    tested = set(checks.get('quality', {}))
    for source in report.get('details', []):
        keys = set(source.get('node_ids', []))
        source.update(eligible=len(keys & eligible), tested=len(keys & tested), verified=len(keys & passed))
        if source['eligible']:
            source.update(last_eligible=source['eligible'], last_verified=source['verified'], last_verified_at=time.time())
    return report


def persist_sources(paths, report):
    details = report.get('details', [])
    health_file = paths.data / 'source-cache/health.json'
    health = read_mapping(health_file)
    for source in details:
        if source.get('key'): health[source['key']] = source
    write_json(health_file, health)
    write_json(paths.data / 'state/public-sources.json', {'checked_at': time.time(), 'sources': details,
                                                       'summary': report.get('summary', {})})


def summary_text(summary):
    if not isinstance(summary, dict) or not summary: return ''
    origin = summary.get('data_origin')
    if origin == 'history_only': prefix = '本轮没有取得新数据，已重新检测历史节点。'
    elif origin == 'recheck': prefix = '本轮重新检测已有节点。'
    else:
        prefix = (f"新下载 {summary.get('downloaded', 0)} 源 · 缓存 {summary.get('cache', 0)} 源 · "
                  f"旧缓存 {summary.get('stale_cache', 0)} 源 · 未取得数据 {summary.get('failed', 0)} 源。")
    text = prefix + f" 本轮送检 {summary.get('selected', 0)} 个（历史 {summary.get('history_candidates', 0)} 个）"
    if 'passed' in summary: text += f"，通过 {summary['passed']} 个，多次通过 {summary.get('stable', 0)} 个。"
    if summary.get('not_selected'): text += f" 另有 {summary['not_selected']} 个来源节点未送检，可提高名额后再获取。"
    return text
