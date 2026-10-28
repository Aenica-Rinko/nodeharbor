from __future__ import annotations

import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .config import find_mihomo, find_core, core_kind, validate_core, load_settings
from .exporter import publish, prune_exports
from .fetcher import fetch_all, SourcesUnavailable
from .nodes import normalize_nodes
from .storage import AppError, app_lock, write_json
from .nodes import identity
from .source_plan import fair_candidates, verification_sources, persist_sources, summary_text, read_mapping
from .verifier import verify_nodes
from .tasks import Cancelled, TaskControl
from .node_library import read_library, save_library
from .node_history import read_history, record_history, plan_checks, signature


def logger_for(paths, callback=None):
    paths.ensure()
    logger = logging.getLogger("nodeharbor." + str(paths.root))
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(paths.logs / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    def log(message):
        logger.info(message)
        if callback: callback(message)
    return log


def status(paths):
    result = {"root": str(paths.root), "last_run": read_mapping(paths.data / "state" / "last-run.json")}
    try:
        folder = paths.current()
        result.update(report=read_mapping(folder / "report.json"), export_dir=str(folder))
    except AppError: result.update(report={}, export_dir=None)
    nodes, library_report = read_library(paths)
    result['library_report'] = library_report
    result['library_count'] = len(nodes)
    result['export_pending'] = bool(library_report.get('library_id') and
                                    library_report['library_id'] != result['report'].get('library_id'))
    from .client_exports import verification_stale
    result['library_stale'] = bool(nodes) and verification_stale(library_report)
    result['export_stale'] = bool(result['export_dir']) and verification_stale(result['report'])
    history = read_history(paths)
    result['known_failed'] = sum(history.get(identity(n), {}).get('recent', [])[-1:] == [False] for n in nodes)
    return result


def execute(paths, action="refresh-github", callback=None, control=None, full_check=False):
    control = control or TaskControl()
    log = logger_for(paths, callback)
    with app_lock(paths):
        started = time.monotonic()
        state = {"status": "running", "action": action, "started_at": datetime.now(timezone.utc).isoformat()}
        write_json(paths.data / "state" / "last-run.json", state)
        try:
            settings = load_settings(paths)
            control.limit(settings["task"]["timeout_seconds"])
            control.check()
            if action in {'refresh', 'refresh-github', 'verify', 'export', 'acquire', 'verify-library'}:
                engine = core_kind(settings)
                core = None
                if action != 'export':
                    control.emit('core', 0, 1, '定位并检查检测核心身份')
                    core = find_mihomo(paths, settings) if engine == 'mihomo' else find_core(paths, settings)
                    validate_core(core, engine, control)
                    control.emit('core', 1, 1, f'{"Xray" if engine == "xray" else "Mihomo"} 核心身份检查通过')
                    log(f'检测核心：{engine}，身份检查通过')
                elif engine == 'mihomo':
                    try:
                        core = find_mihomo(paths, settings); validate_core(core, engine, control)
                    except AppError:
                        control.check(); core = None
                        log('未找到可用的 Mihomo 核心，本次执行结构校验导出，不更新节点检测时间。')
                try: working_nodes, previous_report = read_library(paths)
                except AppError:
                    if action not in {'acquire', 'refresh', 'refresh-github'}: raise
                    working_nodes, previous_report = [], {}
                    log('节点库无法读取，本次重新获取，不使用历史候选。')
                source_report = {}
                if action in {"refresh", "refresh-github", 'acquire'}:
                    settings['fetch']['_cache_folder'] = str(paths.data / 'source-cache')
                    previous = working_nodes if settings['fetch']['use_history'] else []
                    previous_checks = previous_report.get('verification', {})
                    delays = previous_checks.get('delays_ms', {}) if isinstance(previous_checks, dict) else {}
                    history, _ = fair_candidates([previous], min(len(previous), settings['fetch']['max_candidates']),
                                                 settings['fetch']['max_candidates'], delays)
                    reserve = min(len(history), 200, settings['fetch']['max_candidates'] // 5)
                    settings['fetch'].update(_source_budget=settings['fetch']['max_candidates'] - reserve, _preferred_nodes=delays)
                    try:
                        candidates, source_report = fetch_all(settings, log, control, public_only=True)
                        new_ids = {identity(node) for node in candidates}
                        supplement = [node for node in history if identity(node) not in new_ids][:reserve]
                        candidates = normalize_nodes(candidates + supplement)
                        source_report.setdefault('summary', {}).update(history_candidates=len(supplement), selected=len(candidates), data_origin='sources')
                    except SourcesUnavailable as error:
                        if not history: raise
                        candidates, source_report = history, error.report
                        source_report['summary'].update(history_candidates=len(history), selected=len(history), data_origin='history_only')
                        log('本轮所有来源均未取得数据，将重新检测历史免费节点；不会宣称取得新节点。')
                    if previous:
                        previous_sources = previous_report.get('node_sources', {})
                        for key, names in previous_sources.items(): source_report.setdefault('node_sources', {}).setdefault(key, names)
                        from .public_links import node_hosting
                        for key, kinds in node_hosting(previous_report).items():
                            source_report.setdefault('node_hosting', {}).setdefault(key, kinds)
                    state['summary'] = source_report.get('summary', {})
                    control.emit('download', source_report.get('sources', 0), source_report.get('sources', 0),
                                 summary_text(state['summary']), unique=source_report.get('unique', 0),
                                 selected=len(candidates), summary=state['summary'],
                                 sources=[{k: v for k, v in source.items() if k not in {'key', 'node_ids'}} for source in source_report.get('details', [])])
                elif action in {'verify', 'verify-library'}:
                    if not working_nodes: raise AppError("没有现有节点，请先获取节点。")
                    candidates = working_nodes
                    old_fetch = previous_report.get('fetch', {})
                    source_report = deepcopy(old_fetch) if isinstance(old_fetch, dict) else {}
                    from .public_links import node_hosting
                    source_report['node_hosting'] = node_hosting(previous_report)
                    source_report.setdefault('summary', {}).update(data_origin='recheck', downloaded=0, cache=0, stale_cache=0,
                                                                  history_candidates=len(candidates), selected=len(candidates))
                elif action == "export":
                    if not working_nodes: raise AppError('尚无免费节点，请先从已启用的公开源获取节点。')
                    candidates = working_nodes
                else: raise AppError("未知操作。")
                candidates = normalize_nodes(candidates)
                if not candidates: raise AppError('没有可导出的免费节点，请先获取节点；原导出保持不变。')
                if action == "export":
                    free = candidates
                    report = dict(previous_report, mode='reexport')
                    log('使用现有免费节点，重新生成全部客户端格式')
                else:
                    history_records = read_history(paths)
                    fingerprint = signature(engine, settings['verification'], core)
                    testing, reused, deferred = plan_checks(candidates, working_nodes, previous_report,
                        history_records, fingerprint, full=full_check or action in {'verify', 'verify-library'} or source_report.get('summary', {}).get('data_origin') == 'history_only')
                    log(f'检测计划：本轮检查 {len(testing)} 个，沿用 10 分钟内结果 {len(reused)} 个，失败退避 {len(deferred)} 个。')
                    control.emit('precheck', 0, len(testing), '检查新增或过期节点；完整重新检测可绕过历史复用',
                                 reused=len(reused), deferred=len(deferred))
                    if not testing:
                        free, checks = [], {'protocol_tested': 0, 'stable': 0, 'quality': {}, 'delays_ms': {}, 'outcomes': {}}
                    elif engine == 'xray':
                        from .xray_verifier import verify_xray
                        free, checks = verify_xray(core, testing, settings['verification'], log, control)
                    else: free, checks = verify_nodes(core, testing, settings["verification"], log, control)
                    control.check()
                    checked_at = time.time()
                    history_records = record_history(paths, history_records, testing, free, checks, fingerprint, checked_at)
                    fresh_passed = len(free)
                    free = normalize_nodes(free + reused)
                    old_checks = previous_report.get('verification', {})
                    for node in reused:
                        key = identity(node)
                        checks.setdefault('delays_ms', {})[key] = old_checks.get('delays_ms', {}).get(key, 0)
                        checks.setdefault('quality', {})[key] = dict(old_checks['quality'][key], reused=True)
                    checks.update(reused=len(reused), deferred=len(deferred), fresh_passed=fresh_passed,
                                  verified=len(free), checked_this_run=len(testing),
                                  reused_ids=[identity(n) for n in reused])
                    verification_sources(source_report, candidates, free, checks)
                    summary = source_report.setdefault('summary', {})
                    summary.update(passed=len(free), stable=checks.get('stable', 0), tested=checks.get('protocol_tested', 0),
                                   unsupported=checks.get('unsupported', 0), reused=len(reused), node_deferred=len(deferred),
                                   fresh_passed=fresh_passed, checked_this_run=len(testing))
                    state['summary'] = summary
                    persist_sources(paths, source_report)
                    control.emit('protocol', len(testing), len(testing), summary_text(summary), tested=summary['tested'],
                                 passed=len(free), unsupported=summary['unsupported'], stable=summary['stable'], summary=summary,
                                 sources=[{k: v for k, v in source.items() if k not in {'key', 'node_ids'}} for source in source_report.get('details', [])])
                    if not free:
                        raise AppError('本轮没有节点通过 HTTP / HTTPS 检测。' + (f'{len(deferred)} 个连续失败节点暂缓重试，可在公开源的高级选项使用“获取并完整检测”，或等待退避结束后再次获取。' if deferred else '') + '请查看来源结果、检测核心和网络；已有导出保持不变。')
                    evidence_times = [history_records.get(identity(n), {}).get('last_passed', checked_at) for n in free]
                    report = {"mode": action, "verification": checks, "fetch": source_report,
                              "verified_at": datetime.fromtimestamp(min(evidence_times), timezone.utc).isoformat(),
                              "stability": {identity(n): history_records[identity(n)] for n in free if identity(n) in history_records}}
                    passed_ids = {identity(node) for node in free}
                    report['node_sources'] = {key: names for key, names in source_report.get('node_sources', {}).items() if key in passed_ids}
                    report['node_hosting'] = {key: kinds for key, kinds in source_report.get('node_hosting', {}).items() if key in passed_ids}
                report['export_core'] = 'mihomo' if core and engine == 'mihomo' else 'structure'
                report['export_warning'] = '' if core and engine == 'mihomo' else '仅完成结构校验，未运行 Mihomo 配置检查；导出不代表重新检测。'
                report['verification_core'] = previous_report.get('verification_core') if action == 'export' else engine
                if action != 'export':
                    # All entry points share the working library. Combined CLI
                    # commands may fail to export; the verified library stays retryable.
                    report = save_library(paths, free, report, control)
                    state['library_id'] = report['library_id']
                if action in {'acquire', 'verify-library'}:
                    result = report
                    state['status'] = 'success'
                    log(f'节点库已更新：{len(free)} 个。请到“订阅导出”生成 / 更新导出文件。')
                    return result
                control.check()
                control.emit('export', 0, 3, '生成并校验各客户端的订阅文件')
                folder, result = publish(paths, free, report, core=core if engine == 'mihomo' else None, control=control,
                                         port=settings['exports']['mihomo_port'])
                log(f"免费节点已导出：{result['counts']['free']} 个")
                state["generation"] = folder.name
                # Publication is the commit point. A late cancel must not label a
                # successfully activated generation as failed or unchanged.
                try: prune_exports(paths, settings["exports"]["keep_generations"], log)
                except (AppError, OSError):
                    state['cleanup_warning'] = '导出已完成，部分历史文件未能清理，下次更新时重试。'
                    log(state['cleanup_warning'])
            else: raise AppError('未知操作。')
            state["status"] = "success"
            log("任务完成")
            return result
        except BaseException as exc:
            if isinstance(exc, SourcesUnavailable): state['summary'] = exc.report.get('summary', {})
            state["status"] = "cancelled" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "failed"
            state["error"] = str(exc) if isinstance(exc, AppError) else f"{type(exc).__name__}：运行失败"
            log(state["error"])
            raise
        finally:
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            state["elapsed_seconds"] = round(time.monotonic() - started, 2)
            try:
                write_json(paths.data / "state" / "last-run.json", state)
            except (AppError, OSError):
                # Diagnostic state is not the library/export commit. Do not turn a
                # completed operation into a failure, or hide the original exception.
                try: log('最近任务记录未能写入；节点库和导出以实际保存结果为准。')
                except (AppError, OSError): pass
