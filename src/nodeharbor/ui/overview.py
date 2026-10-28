"""Overview desktop page behavior."""
import tkinter as tk
import webbrowser
from ..client_exports import freshness
from ..config import find_core, load_settings
from ..pipeline import status
from ..storage import AppError
from ..source_plan import summary_text
from ..public_sources import selected_pools
from ..source_catalog import enabled_urls
from .presentation import next_action
from .common import BG, BLUE, DOWNLOADS, MUTED, WHITE, display_time

class OverviewPage:
    def show_overview(self):
        snapshot = status(self.paths)
        report = snapshot['library_report']
        settings = load_settings(self.paths)
        try:
            find_core(self.paths, settings)
            core_ready = True
        except AppError:
            core_ready = False
        sources_ready = bool(selected_pools(settings['fetch']) or enabled_urls(settings['fetch']))
        action, title, hint = next_action(snapshot, core_ready, sources_ready)
        panel = tk.Frame(self.main, bg=WHITE, padx=22, pady=20)
        panel.pack(fill='x', pady=(0, 16))
        self.label(panel, '下一步', color=MUTED).pack(anchor='w')
        self.label(panel, title, 16, bold=True).pack(anchor='w', pady=(6, 8))
        self.label(panel, hint, color=MUTED, wraplength=800, justify='left').pack(anchor='w')
        command = (lambda: self.prepare_export('acquire')) if action == 'acquire' else (lambda: self.run('verify-library')) if action == 'verify-library' else lambda: self.show(action)
        self.next_action_button = self.button(panel, title, command, primary=True, task=True)
        self.next_action_button.pack(anchor='w', pady=(14, 0))
        row = tk.Frame(self.main, bg=BG); row.pack(fill='x', pady=(0, 16))
        values = [
            ('节点库', f"{snapshot['library_count']} 个", '已保存的免费节点'),
            ('最近检测', display_time(report.get('verified_at')), freshness(report)),
            ('客户端文件', '检测已过期' if snapshot['export_stale'] else '待更新' if snapshot['export_pending'] else '已生成' if snapshot['export_dir'] else '尚未生成',
             display_time(snapshot['report'].get('exported_at'))),
        ]
        stat_cards = []
        for index, (label, value, detail) in enumerate(values):
            row.grid_columnconfigure(index, weight=1, uniform='stats')
            cell = tk.Frame(row, bg=WHITE, padx=16, pady=16)
            stat_cards.append(cell)
            cell.grid(row=0, column=index, sticky='nsew', padx=(0, 10 if index < 2 else 0))
            self.label(cell, label, color=MUTED).pack(anchor='w')
            self.label(cell, value, 13, bold=True, wraplength=260, justify='left').pack(anchor='w', pady=(8, 6))
            self.label(cell, detail, color=MUTED, wraplength=260, justify='left').pack(anchor='w')
        self.card_grid(row, stat_cards)
        shortcuts = tk.Frame(self.main, bg=BG); shortcuts.pack(fill='x', pady=(0, 16))
        self.button(shortcuts, '管理公开源', lambda: self.show('sources')).pack(side='left', padx=(0, 8))
        self.button(shortcuts, '查看节点库', lambda: self.show('nodes')).pack(side='left', padx=(0, 8))
        self.button(shortcuts, '完整重新检测', lambda: self.run('verify-library'), task=True).pack(side='left')
        self.flow(shortcuts)
        if last_summary := snapshot.get('last_run', {}).get('summary'):
            self.label(self.main, summary_text(last_summary), color=MUTED, wraplength=800, justify='left').pack(anchor='w', pady=(0, 12))
        last = snapshot.get('last_run', {})
        if last:
            value = {'success': '完成', 'failed': '失败', 'cancelled': '已取消', 'running': '运行中或上次中断'}.get(last.get('status'), '未知')
            self.label(self.main, '最近任务：' + value + (' · ' + last['error'] if last.get('error') else ''),
                       color=MUTED, wraplength=800, justify='left').pack(anchor='w')
        if not core_ready:
            self.show_downloads(self.main)

    def show_downloads(self, parent):
        self.label(parent, '官方下载', 13, bold=True).pack(anchor='w', pady=(6, 10))
        row = tk.Frame(parent, bg=parent.cget('bg')); row.pack(anchor='w', pady=(0, 16))
        for client, url in DOWNLOADS.items():
            self.button(row, client, lambda url=url: webbrowser.open(url)).pack(side='left', padx=(0, 8))
        self.flow(row)
