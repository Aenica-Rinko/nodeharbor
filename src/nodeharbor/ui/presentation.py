"""Small, deterministic presentation decisions shared by desktop pages."""
from ..source_plan import nonnegative


def next_action(snapshot, core_ready, sources_ready):
    if (snapshot.get('library_stale') or snapshot.get('known_failed')) and core_ready:
        return 'verify-library', '完整重新检测', '检测结果已过期、时间未知或出现后续失败，建议先重新检测节点库，再更新导出。'
    # Existing verified data remains exportable even without an installed core.
    if snapshot['library_count'] and (snapshot['export_pending'] or not snapshot['export_dir']):
        return 'subscriptions', '生成 / 更新导出文件', '节点库已准备好，下一步生成三个客户端的文件。'
    if snapshot['export_dir'] and not snapshot['export_pending']:
        return 'subscriptions', '导入客户端使用', '文件已生成。打开对应客户端的文件夹导入；v2rayN 可以直接复制节点链接。' + (
            '检测结果已过期或出现后续失败，请配置核心后重新检测。' if snapshot.get('export_stale') or snapshot.get('known_failed') else '')
    if not core_ready:
        return 'settings', '配置检测核心', '选择检测核心后，即可开始获取和检测免费节点。'
    if not sources_ready:
        return 'sources', '选择节点来源', '请至少启用一个包含订阅文件的公开来源。'
    return 'acquire', '获取免费节点', '下载、去重并检测已启用来源中的节点；完成后再生成导出文件。'


def needs_attention(state):
    if not isinstance(state, dict): return False
    return bool(nonnegative(state.get('failures')) or state.get('failure_reason') or state.get('stale')
                or state.get('download_failed'))


def source_matches(name, enabled, state, query, category, has_files=True):
    if query.strip().casefold() not in name.casefold():
        return False
    return (category == '全部' or category == '已启用' and enabled
            or category == '未启用' and not enabled
            or category == '需处理' and (not has_files or needs_attention(state)))


def quality_label(result):
    if result.get('quality') == 'failed': return '最近检测失败'
    if result.get('reused'): return '沿用历史结果'
    return {'stable': '多次通过', 'basic': '部分轮次通过'}.get(result.get('quality'), '旧检测结果')


def source_evidence(state):
    """Explain observations without presenting an untested source as recommended."""
    if not isinstance(state, dict): state = {}
    eligible = nonnegative(state.get('eligible'))
    verified = state.get('verified')
    if verified is None or not eligible:
        result = '暂无本轮可用性结论：下载成功不代表节点可用。'
    else:
        passed = min(nonnegative(verified), eligible)
        reused = min(nonnegative(state.get('reused')), passed)
        result = f'本轮候选 {eligible:g} 个，保留 {passed:g} 个，其中沿用历史 {reused:g} 个。'
        if not passed: result += '可先查看失败原因；此结果不代表该来源全部节点均不可用。'
    unique, exclusive = state.get('unique'), state.get('exclusive')
    if type(unique) is int and unique > 0 and type(exclusive) is int and 0 <= exclusive <= unique:
        result += f'\n与本轮其他来源重复 {unique-exclusive}/{unique} 个，独有 {exclusive} 个；独有节点尚不等于独有可用节点。'
    return result


def node_evidence(quality, history):
    from .common import display_time
    from ..node_history import stability_text, recent_results
    quality = quality if isinstance(quality, dict) else {}
    history = history if isinstance(history, dict) else {}
    mode = '沿用历史检测，本轮未重新探测' if quality.get('reused') else '最近实测失败' if recent_results(history)[-1:] == [False] else '最近保存的实测结果'
    rounds = nonnegative(quality.get('rounds'))
    success = min(nonnegative(quality.get('successful_rounds')), rounds)
    detail = f'该次 HTTP / HTTPS 检测：{success:g}/{rounds:g} 轮通过' if rounds else '该次检测轮数未记录'
    return (f'{mode}\n最后实测：{display_time(history.get("last_checked"))}\n{detail}\n'
            f'跨次记录：{stability_text(history)}\n检测通过不代表下载速度、UDP/QUIC 或长期稳定性。')
