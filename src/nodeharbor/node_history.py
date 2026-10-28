"""Bounded, credential-free cross-run evidence and conservative incremental checks."""
import hashlib
import json
import time

from .nodes import identity
from .source_plan import read_mapping, nonnegative
from .storage import write_json

RECENT_LIMIT = 20
RETENTION = 30 * 86400
REUSE_SECONDS = 10 * 60


def signature(engine, options, core=None):
    fields = {k: options.get(k) for k in ('test_url', 'https_test_url', 'rounds', 'retry_failed',
                                        'timeout_ms', 'max_delay_ms', 'tcp_timeout_seconds')}
    fields['engine'] = engine
    try:
        stat = core.stat()
        fields['core'] = [str(core), stat.st_size, stat.st_mtime_ns]
    except (OSError, AttributeError): fields['core'] = str(core)
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def read_history(paths, now=None):
    now = time.time() if now is None else now
    raw = read_mapping(paths.data / 'state/node-history.json')
    result = {}
    for key, item in raw.items():
        if not isinstance(item, dict) or not isinstance(item.get('recent'), list): continue
        recent = [v for v in item['recent'] if type(v) is bool][-RECENT_LIMIT:]
        checked = nonnegative(item.get('last_checked'))
        if not recent or not 0 <= now - checked <= RETENTION: continue
        result[key] = dict(recent=recent, last_checked=checked, last_passed=nonnegative(item.get('last_passed')),
                           failures=int(nonnegative(item.get('failures'))), signature=str(item.get('signature', '')),
                           delay_ms=nonnegative(item.get('delay_ms')))
    return result


def record_history(paths, history, tested, alive, checks, fingerprint, now=None):
    now = time.time() if now is None else now
    passed = {identity(n) for n in alive}
    # Verifiers provide only assessed outcomes, excluding unsupported configurations.
    outcomes = checks.get('outcomes', {k: k in passed for k in checks.get('quality', {})})
    outcomes = dict(outcomes)
    for key in passed: outcomes[key] = True
    for node in tested:
        key = identity(node)
        if type(outcomes.get(key)) is not bool: continue
        ok = outcomes[key]
        old = history.get(key, {})
        if old.get('signature') != fingerprint: old = {}
        stamp = min(now, nonnegative(checks.get('checked_at', {}).get(key), now))
        history[key] = dict(recent=(old.get('recent', []) + [ok])[-RECENT_LIMIT:], last_checked=stamp,
                            last_passed=stamp if ok else old.get('last_passed', 0),
                            failures=0 if ok else old.get('failures', 0) + 1, signature=fingerprint,
                            delay_ms=checks.get('delays_ms', {}).get(key, old.get('delay_ms', 0)))
    history = dict(sorted(history.items(), key=lambda p: p[1]['last_checked'], reverse=True)[:20000])
    write_json(paths.data / 'state/node-history.json', history)
    return history


def plan_checks(candidates, previous, report, history, fingerprint, full=False, now=None):
    now = time.time() if now is None else now
    previous_ids = {identity(n) for n in previous}
    checks = report.get('verification', {})
    quality = checks.get('quality', {}) if isinstance(checks, dict) else {}
    quality = {k: v for k, v in quality.items() if isinstance(v, dict)} if isinstance(quality, dict) else {}
    tested, reused, deferred = [], [], []
    for node in candidates:
        key = identity(node)
        item = history.get(key, {})
        age = now - item.get('last_checked', 0)
        same = not full and item.get('signature') == fingerprint and age >= 0
        if (same and key in previous_ids and item.get('recent', [])[-1:] == [True]
                and age < REUSE_SECONDS and quality.get(key, {}).get('quality') in {'stable', 'basic'}):
            reused.append(node)
        elif same and item.get('failures', 0) >= 3 and age < min(21600, 1800 * 2 ** min(4, item['failures'] - 3)):
            deferred.append(node)
        else: tested.append(node)
    # Unknown/new nodes precede previously tested ones; oldest evidence checked first.
    tested.sort(key=lambda n: history.get(identity(n), {}).get('last_checked', 0))
    return tested, reused, deferred


def stability_text(item):
    recent = recent_results(item)
    if not recent: return '暂无跨轮记录'
    return f"{sum(recent)}/{len(recent)} 轮通过 · {sum(recent) / len(recent):.0%}" + ('（样本不足）' if len(recent) < 3 else '')


def stability_order(item):
    recent = recent_results(item)
    if recent[-1:] == [False]: return 1
    # Smoothed rate prevents one successful run outranking sustained reliability.
    return -(sum(recent) + 1) / (len(recent) + 2) if recent else 0


def recent_results(item):
    recent = item.get('recent', []) if isinstance(item, dict) else []
    return [r for r in recent if type(r) is bool][-RECENT_LIMIT:] if isinstance(recent, list) else []
