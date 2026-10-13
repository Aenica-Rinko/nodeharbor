# 发布源码

1. 运行测试和 `python scripts/release.py --check`。
2. 运行 `python scripts/release.py --prepare`，获得 `.release/` 下的新源码副本。脚本只复制允许的文件，不复制 Git 历史和个人运行数据。
3. 审阅该副本中的文档、示例和截图，再在副本中初始化 Git。提交身份使用自己的公开身份或 GitHub noreply 邮箱，避免意外暴露私人邮箱。
4. 在副本中运行 `python -m build` 和 `python scripts/release.py --check-artifacts dist`，检查 wheel 与源码包。
5. 发布前确认远端文件列表与审阅过的副本一致，检查 CI 结果。不要直接上传原始工作目录的压缩包。

`.gitignore`、源码文件筛选、MANIFEST.in 和 CI 提供多层检查。检查会拒绝常见凭据模式以及本地配置中可识别的敏感值，错误信息只报告文件和规则，不输出匹配值。已知值扫描不能发现所有个人信息；请始终使用虚构截图和示例。

真实配置、节点、订阅输出、日志、备份和 Mihomo 二进制保留在本地。用户自行从官方来源获取 Mihomo。需要提供二进制安装包时，应另外核对第三方许可证并设计配置隔离，不直接打包现有运行环境。

## Windows EXE

在 64 位 Windows 上安装 `.[windows]` 后运行 `python scripts/windows/build_exe.py`。脚本先执行源码隐私检查，创建新的干净源码副本，再构建共享运行时的桌面与 Console 程序。完整 ZIP 包含 EXE、配套运行库、文档和完整第三方许可；用户完整解压即可运行，无需安装 Python。Mihomo 不随包分发。

运行 `python scripts/windows/check_exe.py <NodeHarbor 目录>` 检查实际冻结程序。该检查清除子进程环境中的 Python 路径，验证默认数据目录、初始化、状态命令、所有 GUI 页面、后台 JSON 管道和取消，使用临时数据与本机 HTTP 服务。

发布前再次检查 EXE 内部解压后的字节码、运行库和文档，以及源码与 Python 包。为匹配修改后的冻结运行逻辑，应发布对应的新源码版本与标签，并更新 ZIP、wheel、源码包的 SHA-256 校验文件。不要用新程序替换旧标签下的对应源码。
