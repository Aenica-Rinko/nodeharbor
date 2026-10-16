"""Sources desktop page behavior."""
import tkinter as tk
from copy import deepcopy
from tkinter import messagebox, ttk
from ..config import load_settings
from ..storage import AppError, app_lock, write_yaml
from ..public_sources import GITHUB_POOLS, RECOMMENDED, LARGE_POOLS, PRESETS, github_file_url, source_note
from ..fetcher import fetch_all
from ..source_plan import summary_text, read_mapping
from ..source_catalog import user_pools, validate_pools, add_pool, repository_of, remove_pool, replace_pool, update_catalog, pool_health
from ..tasks import NETWORK_ERRORS
from .common import BG, BLUE, MUTED, WHITE, display_time

class SourcesPage:
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
        maintenance = tk.Frame(self.main, bg=BG); maintenance.pack(fill='x', before=table, pady=(0, 10))
        self.button(maintenance, '编辑自选订阅文件', self.edit_public_source, task=True).pack(side='left', padx=(0, 8))
        self.button(maintenance, '删除自选来源', self.remove_public_source, task=True).pack(side='left')
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
        self.source_health = read_mapping(self.paths.data / 'source-cache/health.json')
        self.fill_public_sources()

    def open_source_search(self, repository=None, replace=False):
        if self.busy: return
        if self.search_dialog and self.search_dialog.exists():
            self.search_dialog.window.lift(); return
        from ..source_search_dialog import SourceSearchDialog
        self.search_dialog = SourceSearchDialog(self, repository, replace=replace)

    def fill_public_sources(self):
        selected = self.sources_tree.selection()
        self.sources_tree.delete(*self.sources_tree.get_children())
        for index, (name, url) in enumerate(GITHUB_POOLS):
            state = self.cached_source_states.get(name, {})
            self.sources_tree.insert('', 'end', iid=str(index), values=('✓' if name in self.selected_public_sources else '—', name,
                        state.get('status', '尚未测试'), state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测'))
        self.custom_source_states = {}
        health = dict(getattr(self, 'source_health', {}))
        for record in self.cached_source_states.values():
            if isinstance(record, dict) and record.get('key'): health[record['key']] = record
        for index, pool in enumerate(self.custom_pools):
            state, records = pool_health(pool, health)
            state['files'] = records
            self.custom_source_states[pool['name']] = state
            self.sources_tree.insert('', 'end', iid='pool-' + str(index), values=(
                '✓' if pool['enabled'] else '—', pool['name'], state.get('status', '尚未测试' if pool['urls'] else '待选择订阅文件'),
                state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测'))
        selected = [item for item in selected if self.sources_tree.exists(item)]
        if selected: self.sources_tree.selection_set(selected)
        enabled = len(self.selected_public_sources) + sum(pool['enabled'] for pool in self.custom_pools)
        self.source_selection_hint.set(f'启用 {enabled} / {len(GITHUB_POOLS) + len(self.custom_pools)} 个节点池')
        if selected: self.describe_public_source()
        else: self.public_source_description.set('选择来源查看最近成功时间、失败原因和各文件状态。内置来源可停用；自选来源可编辑或删除。')

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
            for number, (url, record) in enumerate(zip(pool['urls'], state.get('files', [])), 1):
                text += f"\n文件 {number}：{url}\n  {record.get('status', '尚未测试')} · 最近下载成功：{display_time(record.get('last_success'))}"
                if record.get('failure_reason'): text += ' · 原因：' + str(record['failure_reason'])
                if record.get('next_retry'): text += ' · 下次重试：' + display_time(record['next_retry'])
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
            with app_lock(self.paths):
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
        added = []
        def change(options):
            pools, new = add_pool(options, repository, urls)
            added.append(new)
            return pools
        pools = update_catalog(self.paths, change)
        self.custom_pools = pools
        self.fill_public_sources()
        message = ('已加入 NodeHarbor 节点池，默认停用。选择订阅文件后，请到列表手动启用。' if added[0] else
                   '该节点池已在列表中，已记住所选文件；启用状态保持不变。')
        self.state_text.set(message)
        return message

    def add_manual_sources(self):
        if self.busy: return
        urls = list(dict.fromkeys(github_file_url(line) for line in self.extra_public_sources_text.get('1.0', 'end').splitlines() if line.strip()))
        if not urls: raise AppError('请先填写 GitHub 公开订阅文件地址。')
        def change(options):
            options = deepcopy(options)
            for url in urls:
                pools, _ = add_pool(options, {'name': repository_of(url)}, [url])
                options.update(node_pools=pools, public_sources=[])
            return user_pools(options)
        self.custom_pools = update_catalog(self.paths, change)
        self.extra_public_sources_text.delete('1.0', 'end')
        self.fill_public_sources()
        self.state_text.set('订阅文件已记住；新加入的节点池默认停用，请手动启用。')

    def selected_custom_pool(self):
        selected = self.sources_tree.selection()
        if not selected: raise AppError('请先在列表中选择一个自选来源。')
        if not selected[0].startswith('pool-'): raise AppError('内置来源请使用启用 / 停用；编辑和删除仅适用于自己添加的来源。')
        return deepcopy(self.custom_pools[int(selected[0][5:])])

    def remove_public_source(self):
        if self.busy: return
        pool = self.selected_custom_pool()
        if not messagebox.askyesno('删除自选来源', f"从来源列表移除 {pool['name']}？\n已有节点和导出文件保留。以后可重新添加。", parent=self.root): return
        self.custom_pools = update_catalog(self.paths, lambda options: remove_pool(options, pool['name']))
        self.sources_tree.selection_remove(*self.sources_tree.selection())
        self.fill_public_sources()
        self.state_text.set('自选来源已删除；已有节点和导出文件保留。')

    def replace_catalog_files(self, pool, urls):
        self.custom_pools = update_catalog(self.paths, lambda options: replace_pool(options, pool['name'], urls, expected=pool))
        self.fill_public_sources()
        message = '订阅文件已保存；文件有变化的来源已停用，请检查后手动启用。'
        self.state_text.set(message)
        return message

    def edit_public_source(self):
        if self.busy: return
        pool = self.selected_custom_pool()
        window = tk.Toplevel(self.root)
        window.title('编辑自选订阅文件')
        window.geometry('760x450'); window.minsize(620, 350)
        window.transient(self.root); window.grab_set()
        body = ttk.Frame(window, padding=18); body.pack(fill='both', expand=True)
        ttk.Label(body, text=pool['name']).pack(anchor='w')
        ttk.Label(body, text='每行一个属于该仓库的 GitHub 文件地址。可以替换或移除失效文件。\n文件改变后自动停用，需回到列表手动启用；清空后保留来源供以后选择文件。', wraplength=680).pack(anchor='w', pady=10)
        editor = tk.Text(body, wrap='word', height=10)
        editor.pack(fill='both', expand=True); editor.insert('1.0', '\n'.join(pool['urls']))
        error = tk.StringVar()
        ttk.Label(body, textvariable=error, wraplength=680).pack(anchor='w', pady=8)
        def close(): window.grab_release(); window.destroy()
        def save():
            try:
                self.replace_catalog_files(pool, [line.strip() for line in editor.get('1.0', 'end').splitlines() if line.strip()])
                close()
            except (AppError, OSError) as exc:
                error.set(str(exc) if isinstance(exc, AppError) else '无法保存，请检查配置目录权限。')
        def search():
            close(); self.open_source_search(pool, replace=True)
        row = ttk.Frame(body); row.pack(fill='x')
        self.button(row, '保存文件列表', save, primary=True).pack(side='left')
        self.button(row, '重新搜索候选文件', search).pack(side='left', padx=8)
        self.button(row, '取消', close).pack(side='right')
        window.protocol('WM_DELETE_WINDOW', close)

    def test_public_sources(self):
        if self.busy or not self.persist_source_choices(): return
        settings = self.public_source_settings()
        settings['fetch'].update(_cache_folder=str(self.paths.data / 'source-cache'), _force=True)
        def work(control):
            with app_lock(self.paths):
                return fetch_all(settings, lambda line: self.events.put(('log', line)), control, public_only=True)
        self.start_task('test-sources', work, refresh=False)

    def fetch_public_sources(self):
        if self.busy or not self.persist_source_choices(): return
        self.prepare_export('acquire')
