"""Classify core errors; never echo untrusted paths, nodes, or credentials."""
def core_error(data, code=None):
    text = data.decode('utf-8', errors='replace').lower() if isinstance(data, bytes) else str(data).lower()
    rules = [
        (('address already in use', 'only one usage', 'bind:'), '监听端口被占用，请关闭冲突实例或更换端口'),
        (('permission denied', 'access is denied'), '文件或端口访问被拒绝，请检查权限'),
        (('no such file', 'cannot find', 'not found'), '核心所需文件缺失，请核对安装与配置'),
        (('certificate', 'x509'), '证书或 TLS 配置错误，请核对节点参数'),
        (('parse', 'unmarshal', 'invalid', 'unsupported', 'configuration'), '配置格式或协议参数不兼容，请更新核心或更换来源'),
    ]
    reason = next((message for terms, message in rules if any(t in text for t in terms)),
                  '核心未能启动，请在设置检查核心版本、架构及配置')
    return (f'退出码 {code}；' if code is not None else '') + reason


def error_tail(file):
    file.flush(); file.seek(0, 2); size = file.tell(); file.seek(max(0, size - 16384))
    return file.read(16384)
