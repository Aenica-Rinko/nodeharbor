"""Exports desktop page behavior."""
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from ..client_exports import freshness, CLIENTS, filename, instructions, prepared
from ..config import find_core, load_settings
from ..pipeline import status
from ..storage import AppError, read_yaml
from ..source_plan import read_mapping
from ..node_library import read_library
from ..subscription import create_server
from .common import BG, BLUE, MUTED, WHITE, display_time, open_path

class ExportsPage:
    def show_subscriptions(self):
        def panel(title):
            frame = tk.Frame(self.main, bg=WHITE, padx=20, pady=14)
            frame.pack(fill='x', pady=(0, 12))
            self.label(frame, title, 13, bold=True).pack(anchor='w', pady=(0, 10))
            return frame
        first = panel('获取与导出')
        self.label(first, '生成一次，即可得到三个客户端的免费节点文件，无需选择客户端或另行保存。',
                   color=MUTED, wraplength=760, justify='left').pack(anchor='w')
        row = tk.Frame(first, bg=WHITE); row.pack(anchor='w', pady=(14, 12))
        self.export_action_buttons = []
        for title, command, primary in [
            ('从 GitHub 公开源获取节点', lambda: self.prepare_export('acquire'), False),
            ('生成 / 更新导出文件', lambda: self.prepare_export('export'), True),
        ]:
            button = self.button(row, title, command, primary=primary, task=True)
            button.pack(side='left', padx=(0, 8))
            self.export_action_buttons.append(button)
        self.label(first, '获取节点只下载、检测并更新节点库；点击“生成 / 更新导出文件”才会更新三个客户端的文件。',
                   color=MUTED, wraplength=760, justify='left').pack(anchor='w')
        snapshot = status(self.paths)
        self.label(first, f"节点库：{snapshot['library_count']} 个节点 · " +
                   ('有更新待导出' if snapshot['export_pending'] else '生成文件后可导入客户端'),
                   color=BLUE).pack(anchor='w', pady=(8, 0))
        try:
            settings = load_settings(self.paths)
            find_core(self.paths, settings)
            core_hint = '检测核心已配置，执行前会自动检查。需要更换核心时请到设置页。'
        except AppError:
            core_hint = '获取或检测前请配置核心；已有节点可执行结构校验导出。'
        self.label(first, core_hint, color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(6, 0))
        try:
            report = read_mapping(self.paths.current() / 'report.json')
        except AppError: report = {}
        self.label(self.main, '导出结果 · ' + ('上次生成 ' + display_time(report.get('exported_at')) if report else '尚未生成'),
                   color=MUTED).pack(anchor='w', pady=(4, 12))
        if report:
            self.label(self.main, '最近真实检测：' + display_time(report.get('verified_at')) +
                       (' · Mihomo 配置校验通过' if report.get('config_validated') else ' · 仅结构校验') + '\n' + freshness(report),
                       color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(0, 12))
        self.client_export_links = {}
        cards = tk.Frame(self.main, bg=BG); cards.pack(fill='x', pady=(0, 12))
        for index, client in enumerate(('Mihomo', 'Clash-Verge-Rev', 'v2rayN')):
            cards.grid_columnconfigure(index, weight=1, uniform='export-clients')
            card = tk.Frame(cards, bg=WHITE, padx=16, pady=16)
            card.grid(row=0, column=index, sticky='nsew', padx=(0, 10 if index < 2 else 0))
            self.label(card, client, 13, bold=True).pack(anchor='w', pady=(0, 10))
            directory = self.paths.client_exports(client)
            v2 = client == 'v2rayN'
            count = report.get('v2rayn', {}).get('free', {}).get('exported', 0) if v2 else report.get('counts', {}).get('free', 0)
            available = bool(report) and (directory / ('free.txt' if v2 else 'free.yaml')).is_file()
            text = f'免费节点 {count} 个'
            self.label(card, text if available else '尚未生成此客户端的文件，请点击“生成 / 更新导出文件”。',
                       color=MUTED, wraplength=300, justify='left').pack(anchor='w')
            link = self.label(card, str(directory), color=BLUE, wraplength=760, justify='left', cursor='hand2', takefocus=True)
            link.pack(anchor='w', fill='x', pady=(6, 8))
            link.bind('<Configure>', lambda event, link=link: link.config(wraplength=max(140, event.width)))
            for event in ('<Button-1>', '<Return>', '<space>'):
                link.bind(event, lambda event, client=client: self.open_exports(client))
            self.client_export_links[client] = link
            suffix = '.txt' if v2 else '.yaml'
            additional = '链接订阅：free.base64.txt' if v2 else '仅节点：free-nodes.yaml'
            self.label(card, f'免费订阅：free{suffix}\n{additional}',
                       wraplength=760, justify='left').pack(anchor='w', pady=(0, 6))
            if client == 'Mihomo':
                standalone = read_yaml(directory / 'free.yaml', {}) if available else {}
                self.label(card, f"本机代理：127.0.0.1:{standalone.get('mixed-port', '请重新导出')}", color=MUTED).pack(anchor='w')
            self.label(card, instructions(client)[0], color=MUTED, wraplength=760, justify='left').pack(anchor='w')
            if v2:
                skipped = report.get('v2rayn', {}).get('free', {})
                if skipped.get('skipped'):
                    reasons = '；'.join(f'{reason}（{number}）' for reason, number in skipped.get('reasons', {}).items())
                    self.label(card, f"跳过 {skipped['skipped']} 个无法转换的节点：{reasons}。完整节点仍在 YAML 中。",
                               color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(6, 0))
            elif report.get('export_warning'):
                self.label(card, 'YAML 仅完成结构校验，未执行 Mihomo 语法检查。',
                           color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(6, 0))
        self.label(self.main, '点击目录路径即可打开文件夹。文件可离线使用；本地订阅地址在设置页。',
                   color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(4, 0))

    def prepare_export(self, action):
        if self.busy: return
        try:
            if action != 'export': find_core(self.paths, load_settings(self.paths))
            if action == 'export':
                nodes, _ = read_library(self.paths)
                if not nodes: raise AppError('还没有免费节点，请先点击“从 GitHub 公开源获取节点”。')
        except AppError as exc:
            messagebox.showinfo('准备导出', str(exc), parent=self.root)
            if '核心' in str(exc) or 'Mihomo' in str(exc): self.show('settings')
            return
        self.run(action)

    def copy_subscription(self, client=None):
        if self.busy: return
        try:
            client = client or self.export_client
            prepared(self.paths, client)
            if not self.server:
                port = load_settings(self.paths)['subscription']['port']
                self.server = create_server(self.paths, port)
                threading.Thread(target=self.server.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{self.server.server_port}/' + filename(client, subscription=True)
            self.copy(url)
            self.state_text.set('服务已启动，地址已复制。' + instructions(client)[1])
            if hasattr(self, 'service_note'): self.service_note.set('本地服务运行中，关闭应用后地址不可访问。')
        except (AppError, OSError) as exc:
            messagebox.showerror('无法复制订阅', str(exc) if isinstance(exc, AppError) else '端口被占用，请在设置中修改本地订阅端口。', parent=self.root)

    def open_exports(self, client=None):
        try:
            self.paths.current()
            folder = self.paths.client_exports(client or self.export_client)
            if not folder.is_dir(): raise AppError('请先生成导出，以创建客户端文件夹。')
            open_path(folder)
        except AppError as exc: messagebox.showinfo("暂无导出", str(exc), parent=self.root)

    def stop_service(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.server = None
            self.state_text.set("本地订阅服务已停止")
        if hasattr(self, 'service_note'): self.service_note.set('本地服务未启动。')

    def show_subscription_tools(self):
        panel = tk.Frame(self.main, bg=WHITE, padx=22, pady=18); panel.pack(fill='x', pady=(0, 12))
        self.label(panel, '本地订阅地址（可选）', 13, bold=True).pack(anchor='w', pady=(0, 8))
        self.label(panel, '先在“订阅导出”生成文件，再使用本地地址。地址只供同一台电脑使用，更新订阅时需保持 NodeHarbor 运行。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(0, 10))
        row = tk.Frame(panel, bg=WHITE); row.pack(fill='x')
        self.subscription_client_value = tk.StringVar(value=self.export_client)
        ttk.Combobox(row, textvariable=self.subscription_client_value, values=CLIENTS, state='readonly', width=18).pack(side='left')
        self.button(row, '启动服务并复制地址', lambda: self.copy_subscription(self.subscription_client_value.get()), task=True).pack(side='left', padx=8)
        self.button(row, '停止本地服务', self.stop_service).pack(side='left')
        self.service_note = tk.StringVar(value='本地服务运行中。' if self.server else '本地服务未启动。')
        tk.Label(panel, textvariable=self.service_note, bg=WHITE, fg=MUTED).pack(anchor='w')
