"""Nodes desktop page behavior."""
import tkinter as tk
from tkinter import ttk
from ..client_exports import freshness
from ..storage import AppError
from ..source_plan import nonnegative
from ..nodes import identity
from ..node_library import read_library
from .common import BG, BLUE, MUTED, WHITE, display_time

class NodesPage:
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
