# 贡献指南

使用 Python 3.10+，安装 `python -m pip install -e ".[dev]"`。桌面测试需要 Tcl/Tk 和图形会话；Linux 可使用 Xvfb。

提交前运行：

```text
python -B -m pytest -q -rs -p no:cacheprovider
python -B scripts/windows/check_clients.py --format-only
python scripts/release.py --check
```

测试必须使用临时目录和虚构节点，不连接贡献者真实服务器、不上传真实订阅。缺少 Mihomo 的测试会跳过；修改核心集成时请使用自行下载的官方核心验证，并说明版本。

Windows 桌面修改还需运行 `python -B scripts/windows/check_ui.py`，检查 8 种状态 × 4 档 Tk 缩放；每个场景使用独立进程，缺少 Tcl/Tk 会直接失败。此项检查布局边界和状态，不是像素差分。首次使用流程、截图复核及真实客户端核心验收命令见 [验收说明](docs/acceptance.md)。不能用格式往返测试代替客户端实际导入或真实网络可用性测试。

修复应附上对应的行为回归测试。节点解析应保留未知的高级字段，导出失败应保留当前有效版本。PR 说明应包含问题、行为变化、验证结果和已知限制。

不要提交真实配置、节点密码、订阅地址、日志或备份。截图仅使用虚构数据。请确保你有权以项目 GPL-3.0-only 许可证提交贡献。
