"""Overview desktop page behavior."""
import tkinter as tk
import webbrowser
from ..client_exports import freshness
from ..config import find_core, load_settings
from ..pipeline import status
from ..storage import AppError
from ..source_plan import summary_text
from .common import BG, BLUE, DOWNLOADS, MUTED, WHITE, display_time

class OverviewPage:
    def show_overview(self):
        snapshot = status(self.paths)
        report = snapshot["library_report"]
        counts = report.get('counts', {}) if isinstance(report.get('counts', {}), dict) else {}
        if not snapshot["export_dir"]:
            self.show_first_steps()
        row = tk.Frame(self.main, bg=BG)
        row.pack(fill="x")
        for i, (key, title) in enumerate([('free', '免费节点')]):
            row.grid_columnconfigure(i, weight=1, uniform="stats")
            cell = tk.Frame(row, bg=WHITE, padx=22, pady=20)
            cell.grid(row=0, column=i, sticky="ew", padx=(0, 12 if i < 2 else 0))
            self.label(cell, title, color=MUTED).pack(anchor="w")
            self.label(cell, str(counts.get(key, "—")), 32, bold=True).pack(anchor="w", pady=(8, 0))
        panel = tk.Frame(self.main, bg=WHITE, padx=24, pady=24)
        panel.pack(fill="x", pady=20)
        self.label(panel, "订阅工作台", 15, bold=True).pack(anchor="w")
        self.label(panel, '获取并检测免费节点；需要客户端文件时，请到“订阅导出”生成。', color=MUTED).pack(anchor='w', pady=(8, 20))
        buttons = tk.Frame(panel, bg=WHITE); buttons.pack(anchor="w")
        self.button(buttons, "从 GitHub 公开源获取节点", lambda: self.prepare_export("acquire"), True, True).pack(side="left", padx=(0, 10))
        self.button(buttons, "重新检测", lambda: self.run("verify-library"), task=True).pack(side="left", padx=(0, 10))
        self.button(buttons, "订阅导出", lambda: self.show("subscriptions")).pack(side="left")
        self.button(panel, '选择 / 添加公开源', lambda: self.show('sources')).pack(anchor='w', pady=(8, 0))
        if snapshot['export_dir']: self.show_downloads(self.main)
        if last_summary := snapshot.get('last_run', {}).get('summary'):
            self.label(self.main, summary_text(last_summary), color=BLUE, wraplength=760, justify='left').pack(anchor='w', pady=(8, 12))
        self.label(self.main, "当前版本", 13, bold=True).pack(anchor="w", pady=(6, 12))
        verified = report.get("verified_at")
        detail = "免费节点最近检测：" + (display_time(verified) if verified else "尚未执行连通性检测") + " · " + freshness(report)
        self.label(self.main, detail, color=MUTED).pack(anchor="w", pady=4)
        self.label(self.main, "导出时间：" + display_time(snapshot["report"].get("exported_at")), color=MUTED).pack(anchor="w", pady=4)
        if snapshot['export_pending']:
            self.label(self.main, '节点库有未导出的更新，请到“订阅导出”生成文件。', color=BLUE).pack(anchor='w', pady=4)
        last = snapshot.get("last_run", {})
        if last:
            value = {"success": "完成", "failed": "失败", "cancelled": "已取消", "running": "运行中或上次中断"}.get(last.get("status"), "未知")
            self.label(self.main, "最近任务：" + value + (" · " + last["error"] if last.get("error") else ""), color=MUTED,
                       wraplength=760, justify="left").pack(anchor="w", pady=4)

    def show_first_steps(self):
        panel = tk.Frame(self.main, bg=WHITE, padx=22, pady=18)
        panel.pack(fill="x", pady=(0, 18))
        self.label(panel, "开始使用", 15, bold=True).pack(anchor="w")
        try:
            settings = load_settings(self.paths)
            find_core(self.paths, settings)
            core_text = "1. 当前客户端的检测核心已找到，执行任务前会检查核心身份。"
        except AppError:
            core_text = "1. 在设置中选择客户端，再选择它的检测核心或主程序以自动查找核心。"
        self.label(panel, core_text, color=MUTED, wraplength=700, justify="left").pack(anchor="w", pady=(10, 5))
        self.label(panel, '2. 从 GitHub 公开源获取节点，应用会去重并检测免费节点。', color=MUTED).pack(anchor='w', pady=5)
        self.label(panel, "3. 到“订阅导出”点击“生成 / 更新导出文件”，再打开对应目录导入。", color=MUTED).pack(anchor="w", pady=5)
        buttons = tk.Frame(panel, bg=WHITE); buttons.pack(anchor="w", pady=(12, 0))
        self.button(buttons, "配置核心和订阅源", lambda: self.show("settings"), True).pack(side="left", padx=(0, 10))
        self.button(buttons, '从 GitHub 公开源获取节点', lambda: self.prepare_export('acquire'), task=True).pack(side='left', padx=(0, 10))
        self.show_downloads(panel)

    def show_downloads(self, parent):
        self.label(parent, '官方下载', 13, bold=True).pack(anchor='w', pady=(6, 10))
        row = tk.Frame(parent, bg=parent.cget('bg')); row.pack(anchor='w', pady=(0, 16))
        for client, url in DOWNLOADS.items():
            self.button(row, client, lambda url=url: webbrowser.open(url)).pack(side='left', padx=(0, 8))
