import argparse
import json
import sys

from . import __version__
from .config import find_core, core_kind, validate_core, load_settings
from .pipeline import execute, status
from .storage import AppError, Paths
from .subscription import create_server
from .initialize import initialize


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="NodeHarbor", description="节点港 · 代理订阅管理")
    parser.add_argument("--home", help="数据根目录；源码默认项目目录，Windows EXE 默认本地应用数据目录")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, help_text in [('acquire', '获取并检测节点，仅更新内部节点库'), ('verify-library', '重新检测内部节点库，不导出'),
                               ("refresh", "获取并验证导出免费节点"), ("verify", "重新检测并导出现有免费节点"),
                               ("refresh-github", "从 GitHub 公开源获取节点并验证导出"),
                               ("export", "使用现有免费节点重新导出")]:
        sub.add_parser(command, help=help_text)
    for command, help_text in [("status", "查看真实状态与统计"), ("doctor", "检查依赖、配置、核心和导出"),
                               ("gui", "打开桌面窗口"),
                               ("init", "创建空白本地配置，保留已有设置")]:
        sub.add_parser(command, help=help_text)
    serve = sub.add_parser("serve", help="启动本地订阅服务")
    serve.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    paths = Paths.discover(args.home)
    try:
        paths.ensure()
        if args.command == "init":
            created = initialize(paths)
            print("已初始化：" + "、".join(created) if created else "配置已存在，未覆盖任何文件。")
            print('下一步：配置检测核心，从 GitHub 公开源获取节点。')
        elif args.command == "gui":
            from .desktop import main as desktop_main
            desktop_main(paths)
        elif args.command == "status":
            print(json.dumps(status(paths), ensure_ascii=False, indent=2))
        elif args.command == "doctor":
            settings = load_settings(paths)
            core = find_core(paths, settings)
            validate_core(core, core_kind(settings))
            print(f"NodeHarbor {__version__}\n配置：正常\n检测核心：{core_kind(settings)} · {core}")
            from .exporter import validate_references
            from .storage import read_yaml
            from .verifier import check_config
            if not (paths.exports / "current.json").exists():
                print('尚未生成免费订阅：请先执行 refresh-github。')
                return 0
            current = paths.current()
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory(prefix="nodeharbor-doctor-") as tmp:
                for kind in ('free',):
                    file = current / f"{kind}.yaml"
                    validate_references(read_yaml(file))
                    if core_kind(settings) == 'mihomo':
                        if not check_config(core, file, Path(tmp)): raise AppError(f"{kind} 配置未通过核心检查。")
                    else:
                        from .xray_verifier import check_xray
                        if not check_xray(core, read_yaml(file).get('proxies', [])): raise AppError(f'{kind} 节点未通过 Xray 检查。')
            print('免费节点导出：引用检查、所选核心语法检查通过')
            print("网络连通性：未检测；请使用 refresh 或 verify")
        elif args.command == "serve":
            port = args.port if args.port is not None else load_settings(paths)["subscription"]["port"]
            if not 1 <= port <= 65535: raise AppError('本地订阅端口必须是 1–65535 的整数。')
            with create_server(paths, port) as server:
                print(f"订阅服务：http://127.0.0.1:{port}/free.yaml", flush=True)
                server.serve_forever()
        else:
            execute(paths, args.command, callback=print)
        return 0
    except KeyboardInterrupt:
        print("操作已停止。", file=sys.stderr); return 130
    except AppError as exc:
        print(f"错误：{exc}", file=sys.stderr); return 1
    except Exception as exc:
        print(f"运行失败：{type(exc).__name__}。请查看 logs/app.log。", file=sys.stderr); return 1
