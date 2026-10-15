"""Native desktop shell. Work runs off the Tk thread; all UI updates use a queue."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .client_exports import freshness, parse_timestamp, CLIENTS, filename, instructions, prepared
from .config import find_core, find_mihomo, core_kind, resolve_core_path, validate_core, load_settings
from .initialize import initialize
from .pipeline import execute, status
from .storage import AppError, Paths, read_yaml, write_yaml, read_json, write_json
from .public_sources import GITHUB_POOLS, RECOMMENDED, LARGE_POOLS, PRESETS, selected_pools, github_file_url, source_note
from .fetcher import fetch_all
from .source_plan import summary_text, read_mapping, nonnegative
from .nodes import identity
from .node_library import read_library
from .source_catalog import user_pools, validate_pools, add_pool, repository_of, enabled_urls
from .subscription import create_server
from .tasks import Cancelled, TaskControl, NETWORK_ERRORS
from .progress import TaskProgress, STAGES, TASKS

BG, WHITE, INK, MUTED, BLUE, LINE = "#F3F5F9", "#FFFFFF", "#182337", "#657389", "#275DDE", "#E3E8F0"
DOWNLOADS = {'Mihomo': 'https://github.com/MetaCubeX/mihomo/releases',
             'Clash-Verge-Rev': 'https://github.com/clash-verge-rev/clash-verge-rev/releases',
             'v2rayN': 'https://github.com/2dust/v2rayN/releases'}


def display_time(value):
    if not value: return "尚未记录"
    if isinstance(value, (int, float)): return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try: return parse_timestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError): return str(value)


def open_path(path):
    path = str(Path(path).resolve())
    if os.name == "nt": os.startfile(path)
    elif sys.platform == "darwin": subprocess.Popen(["open", path])
    else: subprocess.Popen(["xdg-open", path])


def open_help(paths):
    candidates = [paths.root / "README.md"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "README.md")
    for path in candidates:
        if path.is_file():
            open_path(path)
            return
    webbrowser.open("https://github.com/Aenica-Rinko/nodeharbor#readme")


class Desktop:
    def __init__(self, root, paths):
        self.root, self.paths = root, paths
        paths.ensure()
        initialize(paths)
        self.events = queue.Queue()
        self.busy = False
        self.control = None
        self.closing = False
        self.server = None
        self.actions = []
        self.page = "overview"
        self.export_client = 'Clash-Verge-Rev'
        self.log_lines = []
        self.task_progress = None
        self.progress_panel = None
        self.search_dialog = None
        root.title("NodeHarbor · 节点港")
        root.geometry("1120x760")
        root.minsize(940, 620)
        root.configure(bg=BG)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK, rowheight=34, borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", background="#EDF1F7", foreground=MUTED, padding=(10, 10), font=("Microsoft YaHei UI", 9))
        style.map("Treeview", background=[("selected", "#E4ECFF")], foreground=[("selected", BLUE)])
        style.configure("TEntry", padding=8)
        style.configure("TCombobox", padding=6)
        style.configure('NodeHarbor.Horizontal.TProgressbar', background=BLUE, troughcolor='#E8EDF5',
                        lightcolor=BLUE, darkcolor=BLUE, bordercolor=LINE)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        sidebar = tk.Frame(root, bg=WHITE, width=208)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="◈  NodeHarbor", bg=WHITE, fg=BLUE, font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=22, pady=(32, 3))
        tk.Label(sidebar, text="节点港  /  本地订阅管理", bg=WHITE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24, pady=(0, 34))
        self.nav = {}
        for key, title in [("overview", "总览"), ("nodes", "免费节点"), ('sources', '公开源'), ("subscriptions", "订阅导出"), ("settings", "设置"), ("logs", "运行日志")]:
            button = tk.Button(sidebar, text=title, anchor="w", relief="flat", bd=0, cursor="hand2", padx=20, pady=13,
                               bg=WHITE, fg=MUTED, command=lambda key=key: self.show(key))
            button.pack(fill="x", padx=12, pady=3)
            self.nav[key] = button
        tk.Label(sidebar, text=f"v{__version__}\n数据保存在这台电脑", justify="left", bg=WHITE, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="bottom", anchor="w", padx=24, pady=28)
        content = tk.Frame(root, bg=BG)
        content.grid(row=0, column=1, sticky="nsew")
        self.canvas = tk.Canvas(content, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.main = tk.Frame(self.canvas, bg=BG, padx=28, pady=24)
        self.content_id = self.canvas.create_window((0, 0), window=self.main, anchor="nw")
        self.main.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resize_content)
        root.bind("<MouseWheel>", self.scroll_content)
        self.state_text = tk.StringVar(value="就绪")
        footer = tk.Frame(root, bg=WHITE, height=35)
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.cancel_button = tk.Button(footer, text="取消任务", command=self.cancel_task, state="disabled",
                                       relief="flat", bg=WHITE, fg=BLUE, padx=16)
        self.cancel_button.pack(side="right", padx=10)
        tk.Label(footer, textvariable=self.state_text, bg=WHITE, fg=MUTED, anchor="w", padx=22, pady=9,
                 font=("Microsoft YaHei UI", 9)).pack(fill="x")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.show("overview")
        self.poll_id = root.after(100, self.poll)

    def label(self, parent, text, size=10, color=INK, bold=False, **kwargs):
        label = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                         font=("Microsoft YaHei UI", size, "bold" if bold else "normal"), **kwargs)
        if kwargs.get('wraplength'):
            maximum = kwargs['wraplength']
            padding = 2 * parent.winfo_pixels(parent.cget('padx'))
            binding = parent.bind('<Configure>', lambda event: label.config(wraplength=min(maximum, max(160, event.width - padding)))
                                  if label.winfo_exists() else None, add='+')
            label.bind('<Destroy>', lambda event: parent.unbind('<Configure>', binding) if parent.winfo_exists() else None)
        return label

    def button(self, parent, title, command, primary=False, task=False):
        def invoke():
            try: command()
            except (AppError, OSError) as error:
                message = str(error) if isinstance(error, AppError) else '无法访问本地文件或目录，请检查权限。'
                messagebox.showerror('操作未完成', message, parent=self.root)
        button = tk.Button(parent, text=title, command=invoke, relief="flat", bd=0, cursor="hand2", padx=18, pady=10,
                           bg=BLUE if primary else "#E8EDF5", fg=WHITE if primary else INK,
                           activebackground="#204FBF" if primary else "#DCE4EF", activeforeground=WHITE if primary else INK)
        if task:
            self.actions.append(button)
            if self.busy: button.config(state="disabled")
        return button

    def show(self, page):
        self.page = page
        self.actions = []
        for child in self.main.winfo_children(): child.destroy()
        for key, button in self.nav.items():
            button.config(bg="#EAF0FF" if key == page else WHITE, fg=BLUE if key == page else MUTED)
        titles = {"overview": ("所有节点，一处管理", "抓取、验证与导出都有记录，订阅保持清晰。"),
                  "nodes": ("免费节点库", "查看节点库中的免费节点。获取与导出分开进行，认证信息不会显示在列表中。"),
                  "subscriptions": ("获取节点，一次导出", "自动生成全部客户端格式，按客户端保存到对应文件夹。"),
                  'sources': ('GitHub 公开来源', '管理内置与自己添加的节点池。添加和启停自动记住；新加入的节点池默认停用。'),
                  "settings": ("应用设置", "配置统一保存在 config 文件夹，修改后按需重新导出。"),
                  "logs": ("运行日志", "查看执行进度与结果，失败不会替换上一份可用订阅。")}
        title, subtitle = titles[page]
        self.label(self.main, title, 24, bold=True).pack(anchor="w", pady=(0, 8))
        self.label(self.main, subtitle, color=MUTED).pack(anchor="w", pady=(0, 26))
        self.progress_panel = None
        if self.task_progress: self.show_task_progress()
        try: getattr(self, "show_" + page)()
        except (AppError, OSError) as error:
            message = str(error) if isinstance(error, AppError) else '无法读取本地文件，请检查目录权限。'
            self.label(self.main, message, color=BLUE, wraplength=740, justify='left').pack(anchor='w', pady=(12, 6))
            self.button(self.main, '打开配置文件夹', lambda: open_path(self.paths.config)).pack(anchor='w')
            self.state_text.set(message)
        self.main.update_idletasks()
        self.resize_content()
        self.canvas.yview_moveto(0)

    def resize_content(self, event=None):
        width = event.width if event else self.canvas.winfo_width()
        height = event.height if event else self.canvas.winfo_height()
        self.canvas.itemconfigure(self.content_id, width=max(width, 1), height=max(height, self.main.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def scroll_content(self, event):
        if self.page not in {"nodes", "logs"}:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

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

    def show_sources(self):
        settings = load_settings(self.paths)
        chosen = settings['fetch']['github_sources']
        self.selected_public_sources = set(RECOMMENDED if chosen is None else chosen)
        self.custom_pools = user_pools(settings['fetch'])
        cached = read_mapping(self.paths.data / 'state/public-sources.json')
        records = cached.get('sources', [])
        states = {s['name']: s for s in records if isinstance(s, dict) and isinstance(s.get('name'), str)} if isinstance(records, list) else {}
        row = tk.Frame(self.main, bg=BG); row.pack(fill='x', pady=(0, 10))
        self.source_selection_hint = tk.StringVar()
        tk.Label(row, textvariable=self.source_selection_hint, bg=BG, fg=BLUE).pack(side='left')
        self.button(row, '搜索 GitHub 节点池', self.open_source_search, task=True).pack(side='right', padx=8)
        table = tk.Frame(self.main, bg=WHITE); table.pack(fill='x')
        self.sources_tree = ttk.Treeview(table, columns=('enabled', 'name', 'status', 'nodes', 'eligible', 'verified'), show='headings', height=6)
        for key, title, width in [('enabled', '使用', 65), ('name', '公开仓库', 235), ('status', '上次下载状态', 135), ('nodes', '解析', 55), ('eligible', '送检', 55), ('verified', '可用', 55)]:
            self.sources_tree.heading(key, text=title); self.sources_tree.column(key, width=width, minwidth=55, stretch=key in {'name', 'status'})
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.sources_tree.yview)
        self.sources_tree.configure(yscrollcommand=scrollbar.set)
        self.sources_tree.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')
        self.sources_tree.bind('<Double-1>', lambda event: self.toggle_public_source())
        self.sources_tree.bind('<<TreeviewSelect>>', lambda event: self.describe_public_source())
        self.public_source_description = tk.StringVar()
        tk.Label(self.main, textvariable=self.public_source_description, wraplength=740, justify='left',
                 anchor='w', bg=BG, fg=MUTED).pack(fill='x', pady=(8, 10))
        buttons = tk.Frame(self.main, bg=BG); buttons.pack(fill='x', before=table, pady=(0, 10))
        self.button(buttons, '启用 / 停用', self.toggle_public_source, task=True).pack(side='left', padx=(0, 8))
        for title in PRESETS:
            self.button(buttons, title, lambda title=title: self.choose_source_preset(title), task=True).pack(side='left', padx=(0, 6))
        self.button(buttons, '重试下载连接', self.test_public_sources, task=True).pack(side='left')
        self.button(self.main, '获取节点', self.fetch_public_sources, True, True).pack(anchor='w', before=table, pady=(0, 10))
        self.label(self.main, f'内置 {len(GITHUB_POOLS)} 个来源，不代表 GitHub 总数。快速优先减少等待，均衡兼顾覆盖，全面适合较长时间检测。',
                   color=MUTED, wraplength=740, justify='left').pack(anchor='w', pady=(10, 14))
        self.label(self.main, '手动添加订阅文件（每行一个 GitHub raw / blob 地址，加入后默认停用）', bold=True).pack(anchor='w', pady=(0, 8))
        self.extra_public_sources_text = tk.Text(self.main, height=3, wrap='word', relief='solid', borderwidth=1,
                                               padx=8, pady=8, font=('Consolas', 10))
        self.extra_public_sources_text.pack(fill='x')
        self.button(self.main, '添加文件到节点池', self.add_manual_sources, task=True).pack(anchor='w', pady=(6, 0))
        self.label(self.main, '解析数是来源内容，送检数是本轮实际分配的候选数，可用数需等待协议检测。来源可能重复；重试下载只测试下载，不改变已有导出。',
                   color=MUTED, wraplength=740, justify='left').pack(anchor='w', pady=(8, 0))
        self.source_budget_value = tk.StringVar(value=str(settings['fetch']['max_candidates']))
        self.per_source_budget_value = tk.StringVar(value=str(settings['fetch']['nodes_per_source']))
        budget = tk.Frame(self.main, bg=BG); budget.pack(fill='x', before=table, pady=(0, 10))
        self.label(budget, '单轮送检上限', color=MUTED).pack(side='left', padx=(0, 8))
        limit_entry = ttk.Entry(budget, textvariable=self.source_budget_value, width=8)
        limit_entry.pack(side='left'); limit_entry.bind('<FocusOut>', lambda _: self.persist_source_choices())
        self.label(budget, '每源新增名额', color=MUTED).pack(side='left', padx=(14, 8))
        per_source_entry = ttk.Entry(budget, textvariable=self.per_source_budget_value, width=8)
        per_source_entry.pack(side='left'); per_source_entry.bind('<FocusOut>', lambda _: self.persist_source_choices())
        self.label(self.main, summary_text(cached.get('summary', {})), color=BLUE, wraplength=740, justify='left').pack(anchor='w', pady=(6, 0))
        self.cached_source_states = states
        self.fill_public_sources()

    def open_source_search(self, repository=None):
        if self.busy: return
        if self.search_dialog and self.search_dialog.exists():
            self.search_dialog.window.lift(); return
        from .source_search_dialog import SourceSearchDialog
        self.search_dialog = SourceSearchDialog(self, repository)

    def fill_public_sources(self):
        selected = self.sources_tree.selection()
        self.sources_tree.delete(*self.sources_tree.get_children())
        for index, (name, url) in enumerate(GITHUB_POOLS):
            state = self.cached_source_states.get(name, {})
            self.sources_tree.insert('', 'end', iid=str(index), values=('✓' if name in self.selected_public_sources else '—', name,
                        state.get('status', '尚未测试'), state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测'))
        self.custom_source_states = {}
        for index, pool in enumerate(self.custom_pools):
            records = [state for name, state in self.cached_source_states.items() if name.startswith(pool['name'] + ' · 自选 ')]
            state = dict(records[0]) if len(records) == 1 else {}
            if len(records) > 1:
                state.update(status='多个订阅文件，见解析与检测统计',
                             nodes=sum(nonnegative(r.get('nodes')) for r in records),
                             eligible=sum(nonnegative(r.get('eligible')) for r in records),
                             verified=sum(nonnegative(r.get('verified')) for r in records) if all(r.get('verified') is not None for r in records) else None)
            self.custom_source_states[pool['name']] = state
            self.sources_tree.insert('', 'end', iid='pool-' + str(index), values=(
                '✓' if pool['enabled'] else '—', pool['name'], state.get('status', '尚未测试' if pool['urls'] else '待选择订阅文件'),
                state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测'))
        selected = [item for item in selected if self.sources_tree.exists(item)]
        if selected: self.sources_tree.selection_set(selected)
        enabled = len(self.selected_public_sources) + sum(pool['enabled'] for pool in self.custom_pools)
        self.source_selection_hint.set(f'启用 {enabled} / {len(GITHUB_POOLS) + len(self.custom_pools)} 个节点池')

    def describe_public_source(self):
        selected = self.sources_tree.selection()
        if not selected: return
        name = self.sources_tree.item(selected[0], 'values')[1]
        state = self.cached_source_states.get(name, self.custom_source_states.get(name, {}))
        text = 'https://github.com/' + name.split(' · ')[0] + ' · ' + source_note(name)
        if state.get('unique') is not None: text += f"\n去重 {state['unique']} · 独有 {state.get('exclusive', 0)} · 送检 {state.get('eligible', 0)}"
        if state.get('last_verified_at'): text += ' · 最近协议检测：' + display_time(state['last_verified_at'])
        if name in LARGE_POOLS: text += ' · 大型来源，检测时间较长'
        if state.get('route'): text += ' · ' + state['route']
        if selected[0].startswith('pool-'):
            pool = self.custom_pools[int(selected[0][5:])]
            text += f" · 自选节点池，已选择 {len(pool['urls'])} 个订阅文件"
        text += '\n最近下载成功：' + display_time(state.get('last_success')) + f" · 连续失败 {state.get('failures', 0)} 次"
        if state.get('next_retry'): text += ' · 下次重试：' + display_time(state['next_retry'])
        attempts = state.get('attempts', [])
        if attempts:
            text += '\n' + '；'.join(f"{a['channel']} / {a['route']}：" +
                     (f"HTTP {a['http_status']}" if a.get('http_status') else NETWORK_ERRORS.get(a.get('error_code'), a.get('result', ''))) for a in attempts)
        self.public_source_description.set(text)

    def toggle_public_source(self):
        if self.busy or not self.sources_tree.selection(): return
        selected = self.sources_tree.selection()[0]
        if selected.startswith('pool-'):
            pool = self.custom_pools[int(selected[5:])]
            if not pool['enabled'] and not pool['urls']:
                self.state_text.set('请先选择订阅文件；添加后再回来手动启用。')
                self.open_source_search(pool)
                return
            pool['enabled'] = not pool['enabled']
        else:
            name, _ = GITHUB_POOLS[int(selected)]
            if name in self.selected_public_sources: self.selected_public_sources.remove(name)
            else: self.selected_public_sources.add(name)
        self.persist_source_choices()

    def choose_public_sources(self, names):
        if self.busy: return
        self.selected_public_sources = set(names)
        self.persist_source_choices()

    def choose_source_preset(self, title):
        if self.busy: return
        names, limit, per_source = PRESETS[title]
        self.source_budget_value.set(str(limit)); self.per_source_budget_value.set(str(per_source))
        self.choose_public_sources(names)

    def public_source_settings(self):
        settings = deepcopy(load_settings(self.paths))
        try:
            limit, per_source = int(self.source_budget_value.get()), int(self.per_source_budget_value.get())
            if not 1 <= limit <= 10000 or not 1 <= per_source <= 2000: raise ValueError()
        except ValueError: raise AppError('送检上限请输入 1–10000，每源名额请输入 1–2000。') from None
        settings['fetch'].update(max_candidates=limit, nodes_per_source=per_source)
        settings['fetch']['github_sources'] = [name for name, _ in GITHUB_POOLS if name in self.selected_public_sources]
        settings['fetch']['node_pools'] = validate_pools(self.custom_pools)
        settings['fetch']['public_sources'] = []
        return settings

    def persist_source_choices(self):
        if self.busy or self.page != 'sources': return False
        try:
            write_yaml(self.paths.config / 'settings.yaml', self.public_source_settings())
            self.fill_public_sources()
            self.state_text.set('来源选择已自动记住。获取节点只更新节点库，导出请到“订阅导出”。')
            return True
        except (AppError, OSError) as error:
            settings = load_settings(self.paths)
            chosen = settings['fetch']['github_sources']
            self.selected_public_sources = set(RECOMMENDED if chosen is None else chosen)
            self.custom_pools = user_pools(settings['fetch'])
            self.fill_public_sources()
            self.state_text.set(str(error) if isinstance(error, AppError) else '无法记住来源选择，请检查配置目录权限。')
            return False

    def add_catalog_repository(self, repository, urls=()):
        if repository['name'].casefold() in {name.casefold() for name, _ in GITHUB_POOLS}:
            message = '该节点池已在内置列表中，沿用内置订阅文件；启用状态保持不变。'
            self.state_text.set(message)
            return message
        settings = load_settings(self.paths)
        pools, added = add_pool(settings['fetch'], repository, urls)
        settings['fetch'].update(node_pools=pools, public_sources=[])
        write_yaml(self.paths.config / 'settings.yaml', settings)
        self.custom_pools = pools
        self.fill_public_sources()
        message = ('已加入 NodeHarbor 节点池，默认停用。选择订阅文件后，请到列表手动启用。' if added else
                   '该节点池已在列表中，已记住所选文件；启用状态保持不变。')
        self.state_text.set(message)
        return message

    def add_manual_sources(self):
        if self.busy: return
        urls = list(dict.fromkeys(github_file_url(line) for line in self.extra_public_sources_text.get('1.0', 'end').splitlines() if line.strip()))
        if not urls: raise AppError('请先填写 GitHub 公开订阅文件地址。')
        settings = load_settings(self.paths)
        for url in urls:
            pools, _ = add_pool(settings['fetch'], {'name': repository_of(url)}, [url])
            settings['fetch'].update(node_pools=pools, public_sources=[])
        write_yaml(self.paths.config / 'settings.yaml', settings)
        self.custom_pools = user_pools(settings['fetch'])
        self.extra_public_sources_text.delete('1.0', 'end')
        self.fill_public_sources()
        self.state_text.set('订阅文件已记住；新加入的节点池默认停用，请手动启用。')

    def test_public_sources(self):
        if self.busy or not self.persist_source_choices(): return
        settings = self.public_source_settings()
        settings['fetch'].update(_cache_folder=str(self.paths.data / 'source-cache'), _force=True)
        self.start_task('test-sources', lambda control: fetch_all(settings,
                        lambda line: self.events.put(('log', line)), control, public_only=True), refresh=False)

    def fetch_public_sources(self):
        if self.busy or not self.persist_source_choices(): return
        self.prepare_export('acquire')

    def show_downloads(self, parent):
        self.label(parent, '官方下载', 13, bold=True).pack(anchor='w', pady=(6, 10))
        row = tk.Frame(parent, bg=parent.cget('bg')); row.pack(anchor='w', pady=(0, 16))
        for client, url in DOWNLOADS.items():
            self.button(row, client, lambda url=url: webbrowser.open(url)).pack(side='left', padx=(0, 8))

    def show_nodes(self):
        tools = tk.Frame(self.main, bg=BG); tools.pack(fill="x", pady=(0, 16))
        self.search = tk.StringVar()
        entry = ttk.Entry(tools, textvariable=self.search)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.button(tools, '刷新列表', self.fill_nodes).pack(side='right')
        self.node_hint = tk.StringVar()
        tk.Label(self.main, textvariable=self.node_hint, bg=BG, fg=BLUE, anchor="w", justify="left",
                 wraplength=760).pack(fill="x", pady=(0, 10))
        self.label(self.main, "输入名称、协议或服务器搜索", color=MUTED).pack(anchor="w", pady=(0, 10))
        frame = tk.Frame(self.main, bg=WHITE); frame.pack(fill="both", expand=True)
        columns = ("name", "protocol", "server", "delay", "quality", "source")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=8)
        for key, label, width in [("name", "节点名称", 180), ("protocol", "协议", 65), ("server", "服务器", 130), ("delay", "延迟 ms", 70), ("quality", "检测结果", 90), ("source", "来源", 180)]:
            self.tree.heading(key, text=label); self.tree.column(key, width=width, minwidth=60, stretch=key in {"name", "server"})
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.node_count = tk.StringVar()
        tk.Label(self.main, textvariable=self.node_count, bg=BG, fg=MUTED).pack(anchor="w", pady=12)
        self.search.trace_add("write", lambda *_: self.fill_nodes(reload=False))
        self.sort_nodes = tk.StringVar(value='延迟优先')
        order = ttk.Combobox(tools, textvariable=self.sort_nodes, values=('延迟优先', '名称', '来源'), state='readonly', width=10)
        order.pack(side='right', padx=(0, 8)); order.bind('<<ComboboxSelected>>', lambda event: self.fill_nodes(reload=False))
        self.fill_nodes()

    def fill_nodes(self, reload=True):
        if reload or not hasattr(self, 'cached_nodes'):
            try:
                self.cached_nodes, self.cached_node_report = read_library(self.paths)
                self.node_hint.set('最近检测：' + display_time(self.cached_node_report.get('verified_at')) + ' · ' + freshness(self.cached_node_report) + '。延迟不是速度，地区标签未经验证。')
            except (AppError, OSError):
                self.cached_nodes, self.cached_node_report = [], {}
                self.node_hint.set('尚无免费节点，请先从 GitHub 公开源获取节点。')
        nodes, report = self.cached_nodes, self.cached_node_report
        verification = report.get('verification', {})
        verification = verification if isinstance(verification, dict) else {}
        delays, results, origins = verification.get('delays_ms', {}), verification.get('quality', {}), report.get('node_sources', {})
        delays = delays if isinstance(delays, dict) else {}
        results = {k: v for k, v in results.items() if isinstance(v, dict)} if isinstance(results, dict) else {}
        origins = {k: [s for s in v if isinstance(s, str)] for k, v in origins.items() if isinstance(v, list)} if isinstance(origins, dict) else {}
        key = lambda n: (nonnegative(delays.get(identity(n)), float('inf')), n['name'])
        if self.sort_nodes.get() == '名称': key = lambda n: n['name'].casefold()
        elif self.sort_nodes.get() == '来源': key = lambda n: ' / '.join(origins.get(identity(n), []))
        nodes = sorted(nodes, key=key)
        query = self.search.get().strip().casefold()
        self.node_rows = {str(i): p for i, p in enumerate(nodes)
                          if query in " ".join(str(p.get(k, "")) for k in ["name", "type", "server", "port"]).casefold()}
        self.tree.delete(*self.tree.get_children())
        for index, p in self.node_rows.items():
            self.tree.insert("", "end", iid=index, values=(p['name'], p['type'], p['server'], delays.get(identity(p), '—'), {'stable': '多次通过', 'basic': '部分轮次通过'}.get(results.get(identity(p), {}).get('quality'), '旧检测结果'), ' / '.join(origins.get(identity(p), [])) or '历史来源未记录'))
        self.node_count.set(f"显示 {len(self.node_rows)} 个节点")

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

    def show_settings(self):
        settings = load_settings(self.paths)
        panel = tk.Frame(self.main, bg=WHITE, padx=24, pady=24); panel.pack(fill="x")
        header = tk.Frame(panel, bg=WHITE); header.pack(fill='x', pady=(0, 8))
        self.label(header, '用于检测节点的客户端', bold=True).pack(side='left')
        self.button(header, '保存设置', self.save_settings, True, True).pack(side='right')
        self.core_client_value = tk.StringVar(value=settings['verification']['core_client'])
        selector = ttk.Combobox(panel, textvariable=self.core_client_value, values=CLIENTS, state='readonly', width=22)
        selector.pack(anchor='w', pady=(0, 10))
        self.core_selector = selector
        self.core_profiles = dict(settings['verification']['core_paths'])
        if not self.core_profiles['Mihomo']: self.core_profiles['Mihomo'] = settings['verification']['mihomo_path']
        self.editing_core_client = self.core_client_value.get()
        self.v2rayn_core_value = tk.StringVar(value=settings['verification']['v2rayn_core'])
        self.core_type_row = tk.Frame(panel, bg=WHITE)
        self.label(self.core_type_row, 'v2rayN 检测核心', color=MUTED).pack(side='left', padx=(0, 10))
        ttk.Combobox(self.core_type_row, textvariable=self.v2rayn_core_value, values=('xray', 'mihomo'),
                     state='readonly', width=12).pack(side='left')
        self.core_hint_value = tk.StringVar()
        self.label(panel, '核心 / 客户端路径（各客户端单独保存）', bold=True).pack(anchor='w', pady=(2, 8))
        row = tk.Frame(panel, bg=WHITE); row.pack(fill="x")
        self.core_path = tk.StringVar(value=self.core_profiles[self.editing_core_client])
        ttk.Entry(row, textvariable=self.core_path).pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.button(row, '选择客户端 / 核心', self.pick_core).pack(side='right')
        self.core_hint_label = tk.Label(panel, textvariable=self.core_hint_value, bg=WHITE, fg=MUTED,
                                      wraplength=660, justify='left', anchor='w')
        self.core_hint_label.pack(fill='x', pady=(8, 8))
        self.button(panel, '检查核心是否可用', self.check_selected_core, task=True).pack(anchor='w', pady=(0, 10))
        selector.bind('<<ComboboxSelected>>', self.change_core_client)
        self.v2rayn_core_value.trace_add('write', lambda *_: self.update_core_hint())
        self.update_core_hint()
        self.label(panel, "本地订阅端口", bold=True).pack(anchor="w", pady=(0, 8))
        self.port_value = tk.StringVar(value=str(settings["subscription"]["port"]))
        ttk.Entry(panel, textvariable=self.port_value, width=15).pack(anchor="w")
        chosen_count = len(selected_pools(settings['fetch'])) + len(enabled_urls(settings['fetch']))
        self.label(panel, f'当前选择 {chosen_count} 个公开来源；获取后去重和检测。可在“公开源”页选择或添加，打开应用不会自动联网。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w')
        self.button(panel, '管理 GitHub 公开源', lambda: self.show('sources')).pack(anchor='w', pady=(6, 0))
        self.label(panel, '下载代理（可选，例如 http://127.0.0.1:7890）', bold=True).pack(anchor='w', pady=(14, 8))
        self.download_proxy_value = tk.StringVar(value=settings['fetch']['proxy'])
        ttk.Entry(panel, textvariable=self.download_proxy_value).pack(fill='x')
        self.label(panel, '仅用于下载订阅；留空使用环境 / 系统代理。网络受限时可使用正在运行的客户端代理端口。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(6, 0))
        self.auto_local_proxy_value = tk.BooleanVar(value=settings['fetch']['auto_local_proxy'])
        tk.Checkbutton(panel, text='公开源连接失败时，自动尝试本机客户端的 HTTP 代理', variable=self.auto_local_proxy_value,
                       bg=WHITE, activebackground=WHITE, fg=INK).pack(anchor='w', pady=(8, 0))
        self.label(panel, '只用于 GitHub 公开源，不修改系统代理。明确填写下载代理时以填写值为准；所有节点来源统一在“公开源”页管理。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(4, 0))
        self.label(panel, 'Mihomo 独立运行的代理端口', bold=True).pack(anchor='w', pady=(18, 8))
        self.mihomo_port_value = tk.StringVar(value=str(settings['exports']['mihomo_port']))
        ttk.Entry(panel, textvariable=self.mihomo_port_value, width=15).pack(anchor='w')
        self.label(panel, '仅写入 Mihomo 的配置，不修改其他客户端或系统代理。', color=MUTED).pack(anchor='w')
        self.label(panel, '导出根目录（留空使用默认位置）', bold=True).pack(anchor='w', pady=(18, 8))
        self.export_directory_value = tk.StringVar(value=settings['exports']['directory'])
        row = tk.Frame(panel, bg=WHITE); row.pack(fill='x')
        ttk.Entry(row, textvariable=self.export_directory_value).pack(side='left', fill='x', expand=True)
        self.button(row, '选择文件夹', self.pick_export_directory, task=True).pack(side='left', padx=(8, 0))
        self.label(panel, '自动按 Mihomo、clash-verge-Rev、v2rayN 分文件夹保存。更换目录时会复制已有导出，原文件保留。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(6, 0))
        self.button(panel, "保存设置", self.save_settings, True, True).pack(anchor="w", pady=(18, 0))
        self.label(self.main, "更多配置", 13, bold=True).pack(anchor="w", pady=(24, 14))
        self.show_downloads(self.main)
        row = tk.Frame(self.main, bg=BG); row.pack(anchor="w")
        self.button(row, "打开配置文件夹", lambda: open_path(self.paths.config)).pack(side="left", padx=(0, 10))
        self.button(row, "使用说明", lambda: open_help(self.paths)).pack(side="left")
        self.show_subscription_tools()

    def show_subscription_tools(self):
        panel = tk.Frame(self.main, bg=WHITE, padx=22, pady=18); panel.pack(fill='x', pady=(0, 12))
        self.label(panel, '本地订阅地址（可选）', 13, bold=True).pack(anchor='w', pady=(0, 8))
        self.label(panel, '文件已自动导出。地址只供同一台电脑使用，更新订阅时需保持 NodeHarbor 运行。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(0, 10))
        row = tk.Frame(panel, bg=WHITE); row.pack(fill='x')
        self.subscription_client_value = tk.StringVar(value=self.export_client)
        ttk.Combobox(row, textvariable=self.subscription_client_value, values=CLIENTS, state='readonly', width=18).pack(side='left')
        self.button(row, '启动服务并复制地址', lambda: self.copy_subscription(self.subscription_client_value.get()), task=True).pack(side='left', padx=8)
        self.button(row, '停止本地服务', self.stop_service).pack(side='left')
        self.service_note = tk.StringVar(value='本地服务运行中。' if self.server else '本地服务未启动。')
        tk.Label(panel, textvariable=self.service_note, bg=WHITE, fg=MUTED).pack(anchor='w')

    def pick_core(self):
        chosen = filedialog.askopenfilename(title='选择客户端主程序或对应的核心文件', filetypes=[("可执行程序", "*.exe"), ("所有文件", "*")])
        if chosen: self.core_path.set(chosen)

    def pick_export_directory(self):
        if self.busy: return
        chosen = filedialog.askdirectory(parent=self.root, title='选择导出根目录', initialdir=str(self.paths.export_root))
        if chosen: self.export_directory_value.set(chosen)

    def change_core_client(self, event=None):
        self.core_profiles[self.editing_core_client] = self.core_path.get().strip()
        self.editing_core_client = self.core_client_value.get()
        self.core_path.set(self.core_profiles[self.editing_core_client])
        self.update_core_hint()

    def update_core_hint(self):
        client = self.core_client_value.get()
        if client == 'v2rayN':
            self.core_type_row.pack(after=self.core_selector, anchor='w', pady=(0, 8))
            self.core_hint_value.set('Xray 请选择 xray.exe；Mihomo 请选择 mihomo.exe。也可选择 v2rayN.exe 自动查找对应核心。sing-box 暂不作为检测后端。')
        else:
            self.core_type_row.pack_forget()
            self.core_hint_value.set('请选择 mihomo.exe / verge-mihomo.exe；选择客户端主程序时会尝试自动定位内置核心。三个客户端路径分别保存，执行前检查身份。')

    def check_selected_core(self):
        if self.busy: return
        settings = deepcopy(load_settings(self.paths))
        self.core_profiles[self.editing_core_client] = self.core_path.get().strip()
        settings['verification'].update(core_client=self.core_client_value.get(), core_paths=dict(self.core_profiles),
                                        v2rayn_core=self.v2rayn_core_value.get())
        self.start_task('check-core', lambda control: self.inspect_core(settings, control), refresh=False)

    def inspect_core(self, settings, control):
        control.emit('core', 0, 1, '定位并检查核心；不会启动客户端代理或修改系统设置')
        core = find_core(self.paths, settings)
        validate_core(core, core_kind(settings), control)
        control.emit('core', 1, 1, f'{core_kind(settings)} 核心检查通过，请保存设置后获取节点。')

    def save_settings(self):
        if self.busy: return
        if self.server:
            messagebox.showinfo("服务运行中", "请先停止本地订阅服务，再修改设置。", parent=self.root); return
        try:
            value = int(self.port_value.get())
            if not 1024 <= value <= 65535: raise ValueError()
            core = self.core_path.get().strip()
            if core and not (Path(core) if Path(core).is_absolute() else self.paths.root / core).is_file():
                raise AppError("选择的核心文件不存在。")
            settings = deepcopy(load_settings(self.paths))
            if self.core_client_value.get() == 'Mihomo': settings["verification"]["mihomo_path"] = core
            self.core_profiles[self.editing_core_client] = core
            settings['verification'].update(core_client=self.core_client_value.get(), core_paths=dict(self.core_profiles),
                                            v2rayn_core=self.v2rayn_core_value.get())
            settings["subscription"]["port"] = value
            settings['exports']['mihomo_port'] = int(self.mihomo_port_value.get())
            if not 1024 <= settings['exports']['mihomo_port'] <= 65535: raise ValueError()
            from urllib.parse import urlsplit
            proxy = self.download_proxy_value.get().strip()
            if proxy:
                try:
                    parsed = urlsplit(proxy)
                    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or not parsed.port: raise ValueError()
                except ValueError: raise AppError('下载代理应为完整的 HTTP/HTTPS 地址与端口，例如 http://127.0.0.1:7890。')
            settings['fetch']['proxy'] = proxy
            settings['fetch']['auto_local_proxy'] = self.auto_local_proxy_value.get()
            directory = self.export_directory_value.get().strip()
            if directory != settings['exports']['directory']:
                from .export_locations import relocate_exports
                relocate_exports(self.paths, directory, settings)
            else:
                write_yaml(self.paths.config / "settings.yaml", settings)
            self.export_client = self.core_client_value.get()
            self.state_text.set("设置已保存")
        except ValueError: messagebox.showerror("端口无效", "请输入 1024–65535 的整数。", parent=self.root)
        except (AppError, OSError) as exc:
            messagebox.showerror('保存失败', str(exc) if isinstance(exc, AppError) else '无法写入或迁移导出，请检查目录权限。', parent=self.root)

    def show_logs(self):
        self.log_widget = tk.Text(self.main, relief="flat", bg=WHITE, fg=INK, padx=18, pady=18,
                                  font=("Consolas", 10), wrap="word", state="normal")
        self.log_widget.pack(fill="both", expand=True)
        path = self.paths.logs / "app.log"
        content = path.read_text(encoding="utf-8")[-30000:] if path.exists() else "尚无运行日志。\n"
        self.log_widget.insert("end", content)
        self.log_widget.see("end"); self.log_widget.config(state="disabled")
        self.button(self.main, "打开日志文件夹", lambda: open_path(self.paths.logs)).pack(anchor="w", pady=(12, 0))

    def run(self, action):
        self.start_task(action, lambda control: execute(self.paths, action,
                        callback=lambda line: self.events.put(('log', line)), control=control))

    def start_task(self, action, operation, refresh=True):
        if self.busy: return
        self.busy = True
        self.task_progress = TaskProgress(action)
        self.refresh_after_task = refresh
        self.control = TaskControl(progress=lambda event: self.events.put(('progress', event)))
        self.cancel_button.config(state="normal")
        for button in self.actions: button.config(state="disabled")
        self.state_text.set(TASKS.get(action, action) + '：准备中')
        if refresh: self.show(self.page)
        else:
            if self.progress_panel and self.progress_panel.winfo_exists(): self.progress_panel.destroy()
            self.show_task_progress()
        def worker():
            try:
                result = operation(self.control)
                self.events.put(("done", result if isinstance(result, str) else "任务完成"))
            except Cancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc) if isinstance(exc, AppError) else f"{type(exc).__name__}：执行失败"))
        threading.Thread(target=worker, name="nodeharbor-worker", daemon=False).start()

    def show_task_progress(self):
        existing = self.main.winfo_children()
        self.progress_panel = tk.Frame(self.main, bg=WHITE, padx=18, pady=14)
        options = {'fill': 'x', 'pady': (0, 16)}
        if len(existing) > 2: options['before'] = existing[2]
        self.progress_panel.pack(**options)
        row = tk.Frame(self.progress_panel, bg=WHITE); row.pack(fill='x')
        self.progress_title = tk.StringVar()
        tk.Label(row, textvariable=self.progress_title, bg=WHITE, fg=INK,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        self.progress_elapsed = tk.StringVar()
        tk.Label(row, textvariable=self.progress_elapsed, bg=WHITE, fg=MUTED).pack(side='right')
        self.progress_cancel_button = self.button(self.progress_panel, '取消当前任务', self.cancel_task)
        self.progress_cancel_button.pack(anchor='e', pady=(6, 0))
        self.progress_steps = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_steps, bg=WHITE, fg=MUTED,
                 anchor='w', wraplength=660, justify='left').pack(fill='x', pady=(8, 5))
        self.progress_bar = ttk.Progressbar(self.progress_panel, style='NodeHarbor.Horizontal.TProgressbar', mode='indeterminate', length=500)
        self.progress_bar_running = False
        self.progress_bar.pack(fill='x', pady=(3, 8))
        self.progress_detail = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_detail, bg=WHITE, fg=BLUE,
                 anchor='w', wraplength=660, justify='left').pack(fill='x')
        self.progress_counts = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_counts, bg=WHITE, fg=MUTED, anchor='w', wraplength=660, justify='left').pack(fill='x', pady=(6, 0))
        self.progress_sources = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_sources, bg=WHITE, fg=MUTED,
                 anchor='w', justify='left', wraplength=660).pack(fill='x', pady=(6, 0))
        self.progress_guidance = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_guidance, bg=WHITE, fg=INK,
                 wraplength=660, justify='left', anchor='w').pack(fill='x', pady=(6, 0))
        self.render_task_progress()

    def render_task_progress(self):
        model = self.task_progress
        if not model or not self.progress_panel or not self.progress_panel.winfo_exists(): return
        state = {'running': '进行中', 'success': '完成', 'failed': '未完成', 'cancelled': '已取消'}[model.status]
        cancelling = self.busy and self.control and self.control.event.is_set()
        if cancelling: state = '正在取消'
        self.progress_cancel_button.config(state='normal' if self.busy and not cancelling else 'disabled',
                                           text='正在取消…' if cancelling else '取消当前任务')
        self.progress_title.set(TASKS.get(model.action, model.action) + ' · ' + state)
        self.progress_elapsed.set(f'已用时 {model.elapsed // 60:02}:{model.elapsed % 60:02}')
        labels = []
        for key, value in model.steps.items():
            mark = '✓' if value.get('finished') else '●' if key == model.stage else '○'
            labels.append(mark + ' ' + STAGES[key])
        self.progress_steps.set('  →  '.join(labels))
        current = model.steps.get(model.stage, {})
        done, total = current.get('completed', 0), current.get('total', 0)
        if total:
            if self.progress_bar_running: self.progress_bar.stop(); self.progress_bar_running = False
            self.progress_bar.configure(mode='determinate', maximum=total, value=done)
        elif model.status == 'running':
            if not self.progress_bar_running:
                self.progress_bar.configure(mode='indeterminate'); self.progress_bar.start(20); self.progress_bar_running = True
        else:
            if self.progress_bar_running: self.progress_bar.stop(); self.progress_bar_running = False
            self.progress_bar.configure(mode='determinate', maximum=1, value=1 if model.status == 'success' else 0)
        self.progress_detail.set(model.message + (f' · {done}/{total}' if total and model.status == 'running' else ''))
        c = model.counters
        self.progress_counts.set(f"解析 {c['parsed']} · 去重 {c['unique']} · 送检 {c['selected']} · TCP 通过 {c['precheck_passed']} · 实测 {c['tested']} · 不兼容 {c['skipped']} · 通过 {c['passed']}（多次通过 {c['stable']}）")
        sources = model.steps.get('download', {}).get('sources', [])
        self.progress_sources.set('\n'.join(f"{source['name']}：{source['status']} · {source['nodes']} 个节点" +
                                  (' · ' + source['route'] if source.get('route') else '') for source in sources[:5]) +
                                  (f'\n另有 {len(sources) - 5} 个来源，可在“公开源”查看全部结果。' if len(sources) > 5 else ''))
        guidance = ('HTTP 和 HTTPS 各检测至少两轮，失败会复试。通过不代表长期稳定或带宽速度，可随时取消。'
                    if model.status == 'running' else
                    '三个客户端的文件已自动导出；到“订阅导出”查看检测时间和校验状态，再打开对应目录导入。'
                    if model.status == 'success' and model.action not in {'check-core', 'test-sources'} else
                    '公开源下载测试完成；尚未检测节点连通性。请启用需要的节点池，再获取节点。' if model.status == 'success' and model.action == 'test-sources' else
                    '核心检查通过。请保存设置，再获取节点。' if model.status == 'success' else
                    '核心相关问题请到设置检查客户端与核心；下载问题请查看各源状态和下载代理。已有订阅未被失败任务替换。')
        if model.acquisition: guidance = summary_text(model.acquisition) + '\n' + guidance
        if model.action == 'search-sources':
            self.progress_counts.set('')
            guidance = '搜索只查找公开仓库与候选文件。加入节点池后默认停用；选择文件并手动启用后，才能获取节点。'
        if model.action in {'acquire', 'verify-library'} and model.status == 'success':
            guidance = '节点库已更新，客户端导出尚未改变。请到“订阅导出”点击“生成 / 更新导出文件”。'
        self.progress_guidance.set(guidance)

    def cancel_task(self):
        if self.busy and self.control:
            self.control.cancel()
            self.cancel_button.config(state="disabled")
            self.state_text.set("正在取消任务并回收进程…")

    def poll(self):
        sources_changed = False
        try:
            for _ in range(100):
                kind, message = self.events.get_nowait()
                if kind == 'progress':
                    self.task_progress.update(message)
                    if message.get('sources'):
                        if self.page == 'sources':
                            self.cached_source_states = {source['name']: source for source in message.get('sources', [])}
                            sources_changed = True
                    self.state_text.set(message.get('message', '') + (f" · {message['completed']}/{message['total']}" if message.get('total') else ''))
                elif kind == 'discovery':
                    dialog, result = message
                    if dialog.exists(): dialog.receive(result)
                elif kind == "log" and self.page == "logs":
                    self.state_text.set(message)
                    self.log_widget.config(state="normal"); self.log_widget.insert("end", message + "\n")
                    self.log_widget.see("end"); self.log_widget.config(state="disabled")
                elif kind in {"done", "error", "cancelled"}:
                    self.task_progress.finish({'done': 'success', 'error': 'failed', 'cancelled': 'cancelled'}[kind], message)
                    self.state_text.set(message)
                    self.busy = False
                    self.control = None
                    self.cancel_button.config(state="disabled")
                    if self.search_dialog and self.search_dialog.exists() and kind != 'done':
                        self.search_dialog.note.set(message)
                    if self.closing:
                        self.close(); return
                    if self.refresh_after_task: self.show(self.page)
                    else:
                        for button in self.actions: button.config(state='normal')
                    if kind == "error": messagebox.showerror("任务未完成", message, parent=self.root)
        except queue.Empty: pass
        if sources_changed and self.page == 'sources': self.fill_public_sources()
        self.render_task_progress()
        if self.search_dialog and self.search_dialog.exists(): self.search_dialog.update_busy()
        self.poll_id = self.root.after(100, self.poll)

    def copy(self, value):
        self.root.clipboard_clear(); self.root.clipboard_append(value)
        self.state_text.set("地址已复制" + ("；请先启动本地服务" if not self.server and "127.0.0.1" in value else ""))

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

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel_task()
            return
        if self.server: self.server.shutdown(); self.server.server_close()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def main(paths=None):
    root = tk.Tk()
    Desktop(root, paths or Paths.discover())
    root.mainloop()
