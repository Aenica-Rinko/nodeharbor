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
