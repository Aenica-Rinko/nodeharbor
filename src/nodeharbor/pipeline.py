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
from .storage import AppError, app_lock, read_json, read_yaml, write_json
from .nodes import identity
from .source_plan import fair_candidates, verification_sources, persist_sources, summary_text
from .verifier import verify_nodes
from .tasks import Cancelled, TaskControl


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
    result = {"root": str(paths.root), "last_run": read_json(paths.data / "state" / "last-run.json", {})}
    try:
        folder = paths.current()
        result.update(report=read_json(folder / "report.json", {}), export_dir=str(folder))
    except AppError: result.update(report={}, export_dir=None)
    return result


def execute(paths, action="refresh-github", callback=None, control=None):
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
            if action in {'refresh', 'refresh-github', 'verify', 'export'}:
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
                prior = None
                try: prior = paths.current()
                except AppError: pass
                previous_report = read_json(prior / 'report.json', {}) if prior else {}
                source_report = {}
                if action in {"refresh", "refresh-github"}:
                    settings['fetch']['_cache_folder'] = str(paths.data / 'source-cache')
                    previous = normalize_nodes(read_yaml(prior / 'free-nodes.yaml', {}).get('proxies', [])) if prior and settings['fetch']['use_history'] else []
                    delays = previous_report.get('verification', {}).get('delays_ms', {})
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
                    state['summary'] = source_report.get('summary', {})
                    control.emit('download', source_report.get('sources', 0), source_report.get('sources', 0),
                                 summary_text(state['summary']), unique=source_report.get('unique', 0),
                                 selected=len(candidates), summary=state['summary'],
                                 sources=[{k: v for k, v in source.items() if k not in {'key', 'node_ids'}} for source in source_report.get('details', [])])
                elif action == "verify":
                    if not prior: raise AppError("没有现有订阅，请先更新节点。")
                    candidates = read_yaml(prior / "free-nodes.yaml", {}).get("proxies", [])
                    source_report = deepcopy(previous_report.get('fetch', {}))
                    source_report.setdefault('summary', {}).update(data_origin='recheck', downloaded=0, cache=0, stale_cache=0,
                                                                  history_candidates=len(candidates), selected=len(candidates))
                elif action == "export":
                    if not prior: raise AppError('尚无免费节点，请先获取 GitHub 节点池。')
                    candidates = read_yaml(prior / "free-nodes.yaml", {}).get("proxies", [])
                else: raise AppError("未知操作。")
                candidates = normalize_nodes(candidates)
                if not candidates: raise AppError('没有可导出的免费节点，请先获取节点池；原导出保持不变。')
                if action == "export":
                    free = candidates
                    report = dict(previous_report, mode='reexport')
                    log('使用现有免费节点，重新生成全部客户端格式')
                else:
                    if not candidates: raise AppError("没有可检测的免费节点，保留上次导出。")
                    if engine == 'xray':
                        from .xray_verifier import verify_xray
                        free, checks = verify_xray(core, candidates, settings['verification'], log, control)
                    else: free, checks = verify_nodes(core, candidates, settings["verification"], log, control)
                    verification_sources(source_report, candidates, free, checks)
                    summary = source_report.setdefault('summary', {})
                    summary.update(passed=len(free), stable=checks.get('stable', 0), tested=checks.get('protocol_tested', 0),
                                   unsupported=checks.get('unsupported', 0))
                    state['summary'] = summary
                    persist_sources(paths, source_report)
                    control.emit('protocol', len(candidates), len(candidates), summary_text(summary), tested=summary['tested'],
                                 passed=len(free), unsupported=summary['unsupported'], stable=summary['stable'], summary=summary,
                                 sources=[{k: v for k, v in source.items() if k not in {'key', 'node_ids'}} for source in source_report.get('details', [])])
                    if not free: raise AppError('本轮没有节点通过 HTTP / HTTPS 检测。请查看来源结果、检测核心和网络；已有导出保持不变。')
                    report = {"mode": action, "verification": checks, "fetch": source_report,
                              "verified_at": datetime.now(timezone.utc).isoformat()}
                    passed_ids = {identity(node) for node in free}
                    report['node_sources'] = {key: names for key, names in source_report.get('node_sources', {}).items() if key in passed_ids}
                report['export_core'] = 'mihomo' if core and engine == 'mihomo' else 'structure'
                report['export_warning'] = '' if core and engine == 'mihomo' else '仅完成结构校验，未运行 Mihomo 配置检查；导出不代表重新检测。'
                report['verification_core'] = previous_report.get('verification_core') if action == 'export' else engine
                control.check()
                control.emit('export', 0, 3, '生成并校验各客户端的订阅文件')
                folder, result = publish(paths, free, report, core=core if engine == 'mihomo' else None, control=control,
                                         port=settings['exports']['mihomo_port'])
                log(f"免费节点已导出：{result['counts']['free']} 个")
                state["generation"] = folder.name
                prune_exports(paths, settings["exports"]["keep_generations"], log)
                control.check()
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
            write_json(paths.data / "state" / "last-run.json", state)
