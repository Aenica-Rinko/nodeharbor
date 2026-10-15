"""Atomic working node library; client exports change only on explicit export."""
import uuid

from .nodes import normalize_nodes
from .storage import AppError, read_json, read_yaml, write_json
from .source_plan import read_mapping


def read_library(paths):
    path = paths.data / 'library/free.json'
    if path.exists():
        value = read_json(path)
        if not isinstance(value, dict) or not isinstance(value.get('nodes'), list) or not isinstance(value.get('report'), dict):
            raise AppError('本地节点库格式无效，请重新获取节点。')
        return normalize_nodes(value['nodes']), value['report']
    try: folder = paths.current()
    except AppError: return [], {}
    value = read_yaml(folder / 'free-nodes.yaml', {})
    if not isinstance(value, dict) or not isinstance(value.get('proxies'), list):
        raise AppError('节点库格式无效，请重新获取节点。')
    return normalize_nodes(value['proxies']), read_mapping(folder / 'report.json')


def save_library(paths, nodes, report, control):
    report = dict(report, library_id=uuid.uuid4().hex, counts={'free': len(nodes)})
    control.check()
    write_json(paths.data / 'library/free.json', {'nodes': nodes, 'report': report})
    return report
