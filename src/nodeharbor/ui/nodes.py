"""Nodes desktop page behavior."""
import tkinter as tk
from tkinter import ttk
from ..client_exports import freshness
from ..storage import AppError
from ..source_plan import nonnegative
from ..nodes import identity
from ..node_library import read_library
from ..node_history import stability_text, stability_order, read_history, recent_results
from ..public_links import HOSTING_TYPES, UNKNOWN_HOSTING, node_hosting
from datetime import datetime, timezone
from .common import BG, BLUE, MUTED, WHITE, display_time
from .presentation import quality_label, node_evidence

class NodesPage:
    def show_nodes(self):
        self.button(self.header_actions, '刷新列表', self.fill_nodes).pack(side='left', padx=(0, 8))
        self.button(self.header_actions, '重新检测', lambda: self.run('verify-library'), primary=True, task=True).pack(side='left')
        tools = tk.Frame(self.main, bg=BG); tools.pack(fill="x", pady=(0, 16))
        self.search = tk.StringVar()
        self.label(tools, '搜索节点', color=MUTED).pack(side='left', padx=(0, 8))
        entry = ttk.Entry(tools, textvariable=self.search)
        self.button(tools, '更多筛选', self.toggle_node_filters).pack(side='right')
        self.node_hint = tk.StringVar()
        tk.Label(self.main, textvariable=self.node_hint, bg=BG, fg=BLUE, anchor="w", justify="left",
                 wraplength=760).pack(fill="x", pady=(0, 10))
        filters = tk.Frame(self.main, bg=BG); filters.pack(fill='x', pady=(0, 12))
        self.node_extra_filters = tk.Frame(self.main, bg=BG)
        self.node_filters = {}
        self.node_filter_boxes = {}
        for key, title, values, width in [('hosting', '订阅托管', ('全部', *HOSTING_TYPES, UNKNOWN_HOSTING), 25), ('protocol', '协议', ('全部',), 10), ('quality', '结果', ('全部', '多次通过', '部分轮次通过', '沿用历史结果', '最近检测失败', '旧检测结果'), 14), ('source', '来源', ('全部',), 22)]:
            group = tk.Frame(self.node_extra_filters if key in ('source', 'protocol') else filters, bg=BG); group.pack(side='left')
            self.label(group, title, color=MUTED).pack(side='left', padx=(0, 6))
            variable = tk.StringVar(value='全部'); self.node_filters[key] = variable
            box = ttk.Combobox(group, textvariable=variable, values=values, width=width, state='readonly')
            self.node_filter_boxes[key] = box
            box.pack(side='left', padx=(0, 10))
            box.bind('<<ComboboxSelected>>', lambda _: self.fill_nodes(reload=False))
        self.flow(filters)
        self.flow(self.node_extra_filters)
        self.node_extra_filters.pack_forget()
        frame = tk.Frame(self.main, bg=WHITE); frame.pack(fill="both", expand=True)
        self.node_table_frame = frame
        columns = ("name", "protocol", "server", "delay", "quality", "source", "stability", "last_passed", "failures", "hosting")
        self.tree = ttk.Treeview(frame, columns=columns, displaycolumns=('name', 'protocol', 'quality', 'delay', 'hosting'), show="headings", selectmode="browse", height=6)
        self.tree.heading('hosting', text='订阅托管类型'); self.tree.column('hosting', width=220, minwidth=100)
        for key, label, width in [("name", "节点名称", 180), ("protocol", "协议", 65), ("server", "服务器", 130), ("delay", "延迟 ms", 70), ("quality", "检测结果", 90), ("source", "来源", 180)]:
            self.tree.heading(key, text=label); self.tree.column(key, width=width, minwidth=60, stretch=key in {"name", "server"})
        for key, label, width in [('stability', '最近 20 轮稳定性', 220), ('last_passed', '最后通过时间', 160), ('failures', '连续失败', 80)]:
            self.tree.heading(key, text=label); self.tree.column(key, width=width, minwidth=60)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        frame.grid_columnconfigure(0, weight=1); frame.grid_rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky='nsew'); scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.node_count = tk.StringVar()
        tk.Label(self.main, textvariable=self.node_count, bg=BG, fg=MUTED).pack(anchor="w", pady=12)
        detail = tk.Frame(self.main, bg=WHITE, padx=14, pady=10); detail.pack(fill='x')
        self.label(detail, '节点详情', bold=True).pack(anchor='w', pady=(0, 6))
        self.node_detail = tk.Text(detail, height=3, wrap='word', bg=WHITE, fg=MUTED, relief='flat', state='disabled')
        scrollbar = ttk.Scrollbar(detail, orient='vertical', command=self.node_detail.yview)
        self.node_detail.configure(yscrollcommand=scrollbar.set)
        self.node_detail.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.describe_node())
        self.search.trace_add("write", lambda *_: self.fill_nodes(reload=False))
        self.sort_nodes = tk.StringVar(value='稳定性优先')
        order = ttk.Combobox(tools, textvariable=self.sort_nodes, values=('稳定性优先', '延迟优先', '名称', '来源'), state='readonly', width=10)
        order.pack(side='right', padx=(0, 8)); order.bind('<<ComboboxSelected>>', lambda event: self.fill_nodes(reload=False))
        # Reserve action and sort widths before the search field takes remaining space.
        entry.pack(side='left', fill='x', expand=True, padx=(0, 12))
        self.fill_nodes()

    def toggle_node_filters(self):
        if self.node_extra_filters.winfo_manager(): self.node_extra_filters.pack_forget()
        else: self.node_extra_filters.pack(before=self.node_table_frame, fill='x', pady=(0, 12))

    def describe_node(self):
        selection = self.tree.selection()
        if selection:
            row = self.tree.item(selection[0], 'values')
            text = (f'{row[0]} · {row[1]} · {row[4]}\n服务器：{row[2]} · 最后通过：{row[7]} · 连续失败：{row[8]}\n'
                    f'跨轮记录：{row[6]}\n公开出处：{row[5]}\n订阅托管：{row[9]}（不代表节点出口）')
            node = self.node_rows.get(selection[0])
            if node:
                key = identity(node)
                checks = self.cached_node_report.get('verification', {})
                quality = checks.get('quality', {}) if isinstance(checks, dict) else {}
                quality = quality if isinstance(quality, dict) else {}
                stored = self.cached_node_report.get('stability', {})
                history = dict(stored) if isinstance(stored, dict) else {}
                history.update(getattr(self, 'cached_node_history', {}))
                text += '\n\n检测依据：\n' + node_evidence(quality.get(key), history.get(key))
        else: text = '选择节点查看服务器、公开出处和跨轮检测记录。认证信息不会显示。'
        self.node_detail.configure(state='normal'); self.node_detail.delete('1.0', 'end')
        self.node_detail.insert('1.0', text); self.node_detail.configure(state='disabled')

    def fill_nodes(self, reload=True):
        selected = self.tree.selection()
        selected_node = getattr(self, 'node_rows', {}).get(selected[0]) if selected else None
        selected_id = identity(selected_node) if selected_node else None
        if reload or not hasattr(self, 'cached_nodes'):
            try:
                self.cached_nodes, self.cached_node_report = read_library(self.paths)
                self.cached_node_history = read_history(self.paths)
                self.node_hint.set('最近检测：' + display_time(self.cached_node_report.get('verified_at')) + ' · ' + freshness(self.cached_node_report) + '。延迟不是速度，地区标签未经验证。')
            except (AppError, OSError):
                self.cached_nodes, self.cached_node_report, self.cached_node_history = [], {}, {}
                self.node_hint.set('尚无免费节点，请先从已启用的公开源获取节点。')
        if not self.cached_nodes: self.node_hint.set('节点库为空。请在“公开源”启用来源并获取节点。')
        nodes, report = self.cached_nodes, self.cached_node_report
        hosting = node_hosting(report)
        verification = report.get('verification', {})
        verification = verification if isinstance(verification, dict) else {}
        delays, results, origins = verification.get('delays_ms', {}), verification.get('quality', {}), report.get('node_sources', {})
        delays = delays if isinstance(delays, dict) else {}
        results = {k: v for k, v in results.items() if isinstance(v, dict)} if isinstance(results, dict) else {}
        origins = {k: [s for s in v if isinstance(s, str)] for k, v in origins.items() if isinstance(v, list)} if isinstance(origins, dict) else {}
        key = lambda n: (nonnegative(delays.get(identity(n)), float('inf')), n['name'])
        stability = report.get('stability', {})
        stability = {k: v for k, v in stability.items() if isinstance(v, dict)} if isinstance(stability, dict) else {}
        stability.update(getattr(self, 'cached_node_history', {}))
        for node_id, item in stability.items():
            if recent_results(item)[-1:] == [False]: results[node_id] = {'quality': 'failed'}
        if self.sort_nodes.get() == '稳定性优先': key = lambda n: (stability_order(stability.get(identity(n), {})), nonnegative(delays.get(identity(n)), float('inf')), n['name'])
        if self.sort_nodes.get() == '名称': key = lambda n: n['name'].casefold()
        elif self.sort_nodes.get() == '来源': key = lambda n: ' / '.join(origins.get(identity(n), []))
        if reload:
            for name, values in [('protocol', sorted({n['type'] for n in nodes})), ('source', sorted({s for names in origins.values() for s in names} | ({'历史来源未记录'} if any(not origins.get(identity(n)) for n in nodes) else set())))]:
                self.node_filter_boxes[name].configure(values=['全部'] + values)
                if self.node_filters[name].get() not in ['全部'] + values: self.node_filters[name].set('全部')
        nodes = sorted(nodes, key=key)
        query = self.search.get().strip().casefold()
        self.node_rows = {str(i): p for i, p in enumerate(nodes)
                          if query in " ".join(str(p.get(k, "")) for k in ["name", "type", "server", "port"]).casefold()
                          and self.node_filters['protocol'].get() in ('全部', p['type'])
                          and self.node_filters['quality'].get() in ('全部', quality_label(results.get(identity(p), {})))
                          and (self.node_filters['hosting'].get() == '全部' or self.node_filters['hosting'].get() in (hosting.get(identity(p)) or [UNKNOWN_HOSTING]))
                          and (self.node_filters['source'].get() == '全部' or self.node_filters['source'].get() in (origins.get(identity(p)) or ['历史来源未记录']))}
        self.tree.delete(*self.tree.get_children())
        for index, p in self.node_rows.items():
            item = stability.get(identity(p), {})
            stamp = nonnegative(item.get('last_passed'))
            last_passed = display_time(datetime.fromtimestamp(stamp, timezone.utc).isoformat()) if 0 < stamp < 253402300800 else '尚无记录'
            self.tree.insert("", "end", iid=index, values=(p['name'], p['type'], p['server'], delays.get(identity(p), '—'), quality_label(results.get(identity(p), {})), ' / '.join(origins.get(identity(p), [])) or '历史来源未记录', stability_text(item), last_passed, item.get('failures', '—'), ' / '.join(hosting.get(identity(p)) or [UNKNOWN_HOSTING])))
        self.node_count.set(f"显示 {len(self.node_rows)} / {len(nodes)} 个节点")
        for index, node in self.node_rows.items():
            if identity(node) == selected_id:
                self.tree.selection_set(index); break
        self.describe_node()
