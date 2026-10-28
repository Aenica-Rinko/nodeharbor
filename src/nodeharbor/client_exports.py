"""Client-specific filenames and actionable export state, shared by UI and tests."""
from .storage import AppError, read_json
from datetime import datetime, timezone

CLIENTS = ('Clash-Verge-Rev', 'Mihomo', 'v2rayN')


def parse_timestamp(value):
    # Python 3.10 accepts UTC offsets but not the ISO 8601 trailing Z spelling.
    if isinstance(value, str) and value.endswith('Z'): value = value[:-1] + '+00:00'
    return datetime.fromisoformat(value)


def freshness(report):
    try:
        value = parse_timestamp(report.get('verified_at') or '')
        if value.tzinfo is None: return '检测时间无时区，请重新检测'
        minutes = (datetime.now(timezone.utc) - value).total_seconds() / 60
        if minutes < -5: return '检测时间异常，请重新检测'
        if minutes > 60: return '结果超过 60 分钟，建议重新检测'
        return '仅反映最近一次检测'
    except (ValueError, TypeError): return '尚无有效检测时间，请重新检测'


def verification_stale(report, now=None):
    try:
        checked = parse_timestamp(report.get('verified_at') or '')
        if checked.tzinfo is None: return True
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
        return not -300 <= age <= 3600
    except (ValueError, TypeError): return True


def filename(client, subscription=False):
    if client not in CLIENTS: raise AppError('请选择客户端。')
    if client == 'v2rayN': return 'free.v2ray.base64.txt' if subscription else 'free.v2ray.txt'
    return 'free.mihomo.yaml' if client == 'Mihomo' else 'free.yaml'


def prepared(paths, client):
    folder = paths.current()
    report = read_json(folder / 'report.json', {})
    file = folder / filename(client)
    if not file.is_file(): raise AppError('这是旧版本导出，请点击“生成 / 更新导出文件”生成新的客户端格式。')
    count = report.get('v2rayn', {}).get('free', {}).get('exported', 0) if client == 'v2rayN' else report.get('counts', {}).get('free', 0)
    if not count:
        if report.get('counts', {}).get('free', 0) and client == 'v2rayN':
            raise AppError('这些节点暂不能转换为 v2rayN 链接。请查看跳过原因，或选择 Mihomo / Clash-Verge-Rev。')
        raise AppError('尚无免费节点，请先从已启用的公开源获取节点。')
    return file, report, count


def instructions(client):
    if client == 'Clash-Verge-Rev':
        return ('打开“订阅”页 → 新建本地配置并选择 free.yaml，或拖入该文件 → 启用配置。客户端保存的是副本；以后重新导出后需要重新导入，或使用本地订阅地址更新。',
                '复制地址 → 在“订阅”页粘贴地址并导入 → 启用配置。以后更新节点后，在客户端更新订阅即可。')
    if client == 'v2rayN':
        return ('点击“复制节点链接” → 在 v2rayN 的节点列表区域按 Ctrl+V（不要在输入框中粘贴）→ 选择导入的节点。粘贴会追加节点，不会自动移除旧节点；建议单独建组管理，经常更新可使用本地订阅地址。也可打开 free.txt 复制全部链接。',
                '复制地址 → v2rayN“订阅分组设置”中新增分组，填入地址 →“更新当前订阅（不通过代理）”→ 选择节点。')
    return ('运行 mihomo -f free.yaml → 将软件代理设为上方本机地址。端口冲突时，在设置修改后重新导出。',
            '复制地址 → 在使用 Mihomo 的客户端中导入该 URL。Mihomo 核心本身需要下载 YAML 后加载配置。')
