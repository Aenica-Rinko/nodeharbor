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
from ..public_links import HOSTING_TYPES, pool_hosting
from .common import BG, BLUE, MUTED, WHITE, display_time, input_actions
from .presentation import source_matches, needs_attention, source_evidence
import webbrowser

class SourcesPage:
    def show_sources(self):
        settings = load_settings(self.paths)
        chosen = settings['fetch']['github_sources']
        self.selected_public_sources = set(RECOMMENDED if chosen is None else chosen)
        self.custom_pools = user_pools(settings['fetch'])
        cached = read_mapping(self.paths.data / 'state/public-sources.json')
        records = cached.get('sources', [])
        states = {s['name']: s for s in records if isinstance(s, dict) and isinstance(s.get('name'), str)} if isinstance(records, list) else {}
        self.source_selection_hint = tk.StringVar()
        self.source_query = tk.StringVar()
        self.source_filter = tk.StringVar(value='全部')
        row = tk.Frame(self.main, bg=BG); row.pack(fill='x', pady=(0, 10))
        self.button(self.header_actions, '搜索节点池', self.open_source_search, task=True).pack(side='left', padx=(0, 8))
        self.button(self.header_actions, '获取节点', self.fetch_public_sources, True, True).pack(side='left')
        self.label(row, '来源搜索', color=MUTED).pack(anchor='w', pady=(0, 4))
        input_actions(row, self.source_query, self.button, [('更多选项', self.toggle_source_advanced, {})], width=18)
        filters = tk.Frame(self.main, bg=BG); filters.pack(fill='x', pady=(0, 12))
        category = ttk.Combobox(filters, textvariable=self.source_filter, values=('全部', '已启用', '未启用', '需处理'), state='readonly', width=9)
        category.pack(side='left')
        category.bind('<<ComboboxSelected>>', lambda _: self.fill_public_sources())
        self.source_advanced = tk.Frame(self.main, bg=WHITE, padx=16, pady=12)
        preset_row = tk.Frame(self.source_advanced, bg=WHITE); preset_row.pack(fill='x', pady=(0, 10))
        self.label(preset_row, '内置方案', color=MUTED).pack(side='left', padx=(0, 8))
        self.source_preset = tk.StringVar(value='自定义')
        preset = ttk.Combobox(preset_row, textvariable=self.source_preset, values=tuple(PRESETS), state='readonly', width=10)
        preset.pack(side='left')
        preset.bind('<<ComboboxSelected>>', lambda _: self.choose_source_preset(self.source_preset.get()))
        self.actions.append(preset)
        if self.busy: preset.configure(state='disabled')
        tk.Label(filters, textvariable=self.source_selection_hint, bg=BG, fg=MUTED).pack(side='right')
        hosting_row = filters
        self.label(hosting_row, '托管类型', color=MUTED).pack(side='left', padx=(12, 6))
        self.source_hosting = tk.StringVar(value='全部')
        hosting_box = ttk.Combobox(hosting_row, textvariable=self.source_hosting,
                                  values=('全部', *HOSTING_TYPES, '待选择文件'), state='readonly', width=22)
        hosting_box.pack(side='left')
        hosting_box.bind('<<ComboboxSelected>>', lambda _: self.fill_public_sources())
        self.flow(filters)
        self.source_body = tk.Frame(self.main, bg=BG); self.source_body.pack(fill='both', expand=True)
        self.source_body.grid_rowconfigure(0, weight=1)
        self.source_body.grid_columnconfigure(0, weight=1)
        table = tk.Frame(self.source_body, bg=WHITE)
        self.source_table = table
        table.grid(row=0, column=0, sticky='nsew')
        self.sources_tree = ttk.Treeview(table, columns=('enabled', 'name', 'status', 'nodes', 'eligible', 'verified', 'hosting'),
                                        displaycolumns=('enabled', 'name', 'hosting', 'status', 'verified'), show='headings', selectmode='browse', height=6)
        for key, title, width in [('enabled', '启用', 65), ('name', '公开仓库', 220), ('status', '下载状态', 145), ('nodes', '解析', 55), ('eligible', '送检', 55), ('verified', '通过', 60)]:
            self.sources_tree.heading(key, text=title); self.sources_tree.column(key, width=width, minwidth=50, stretch=key in {'name', 'status'})
        self.sources_tree.heading('hosting', text='托管类型'); self.sources_tree.column('hosting', width=180, minwidth=100)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.sources_tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.sources_tree.xview)
        self.sources_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        self.sources_tree.grid(row=0, column=0, sticky='nsew'); scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.sources_tree.bind('<ButtonRelease-1>', self.source_click)
        self.sources_tree.bind('<space>', lambda _: self.toggle_public_source())
        self.sources_tree.bind('<<TreeviewSelect>>', lambda _: self.describe_public_source())
        self.source_query.trace_add('write', lambda *_: self.fill_public_sources())
        self.public_source_description = tk.StringVar()
        self.source_detail = tk.Frame(self.source_body, bg=WHITE, padx=16, pady=12)
        self.source_detail.grid(row=1, column=0, sticky='nsew', pady=(10, 0))
        detail_actions = tk.Frame(self.source_detail, bg=WHITE); detail_actions.pack(fill='x')
        self.label(detail_actions, '选择详情', bold=True).pack(side='left')
        self.button(detail_actions, '打开仓库', self.open_selected_repository).pack(side='right')
        self.source_menu = tk.Menu(self.source_detail, tearoff=False)
        self.source_menu.add_command(label='启用 / 停用', command=self.toggle_public_source)
        self.source_menu.add_command(label='编辑订阅文件', command=lambda: self.source_maintenance('edit'))
        self.source_menu.add_command(label='删除自选来源', command=lambda: self.source_maintenance('remove'))
        self.button(detail_actions, '维护来源 ▾', self.show_source_menu, task=True).pack(side='right', padx=8)
        detail_body = tk.Frame(self.source_detail, bg=WHITE); detail_body.pack(fill='x', pady=(8, 0))
        self.source_detail_text = tk.Text(detail_body, width=22, height=3, wrap='word', bg=WHITE, fg=MUTED, relief='flat', state='disabled')
        detail_scroll = ttk.Scrollbar(detail_body, orient='vertical', command=self.source_detail_text.yview)
        self.source_detail_text.configure(yscrollcommand=detail_scroll.set)
        self.source_detail_text.pack(side='left', fill='both', expand=True); detail_scroll.pack(side='right', fill='y')
        self.public_source_description.trace_add('write', lambda *_: self.update_source_detail())
        tools = tk.Frame(self.source_advanced, bg=WHITE); tools.pack(fill='x', pady=(0, 10))
        self.button(tools, '重试失败来源下载', self.retry_failed_sources, task=True).pack(side='left', padx=(0, 8))
        self.flow(tools)
        self.source_budget_value = tk.StringVar(value=str(settings['fetch']['max_candidates']))
        self.per_source_budget_value = tk.StringVar(value=str(settings['fetch']['nodes_per_source']))
        budget = tk.Frame(self.source_advanced, bg=WHITE); budget.pack(fill='x')
        for title, variable in [('单轮送检上限', self.source_budget_value), ('每源新增名额', self.per_source_budget_value)]:
            self.label(budget, title, color=MUTED).pack(side='left', padx=(0, 8))
            entry = ttk.Entry(budget, textvariable=variable, width=7); entry.pack(side='left', padx=(0, 12))
            entry.bind('<FocusOut>', lambda _: self.persist_source_choices())
        self.button(self.source_advanced, '测试全部启用来源下载', self.test_public_sources, task=True).pack(anchor='w', pady=8)
        self.button(self.source_advanced, '获取并完整检测（忽略历史复用 / 退避）', lambda: self.fetch_public_sources(full_check=True), task=True).pack(anchor='w', pady=8)
        self.label(self.source_advanced, '手动添加：每行一个 GitHub raw / blob 文件地址，新来源默认停用。', color=MUTED, wraplength=760, justify='left').pack(anchor='w')
        self.extra_public_sources_text = tk.Text(self.source_advanced, height=3, wrap='word', relief='solid', borderwidth=1, padx=8, pady=8)
        self.extra_public_sources_text.pack(fill='x', pady=8)
        self.button(self.source_advanced, '添加文件到节点池', self.add_manual_sources, task=True).pack(anchor='w')
        self.label(self.main, f'内置 {len(GITHUB_POOLS)} 个来源，不代表 GitHub 总数。下载成功后仍需检测节点；通过数不代表长期稳定性。',
                   color=MUTED, wraplength=760, justify='left').pack(anchor='w', pady=(10, 0))
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
            if self.source_hosting.get() not in ('全部', 'GitHub 文件'): continue
            if not source_matches(name, name in self.selected_public_sources, state, self.source_query.get(), self.source_filter.get()): continue
            self.sources_tree.insert('', 'end', iid=str(index), values=('☑' if name in self.selected_public_sources else '☐', name,
                        state.get('status', '尚未测试'), state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测', 'GitHub 文件'))
        self.custom_source_states = {}
        health = dict(getattr(self, 'source_health', {}))
        for record in self.cached_source_states.values():
            if isinstance(record, dict) and record.get('key'): health[record['key']] = record
        for index, pool in enumerate(self.custom_pools):
            state, records = pool_health(pool, health)
            state['files'] = records
            self.custom_source_states[pool['name']] = state
            kinds = pool_hosting(pool['urls']) or ['待选择文件']
            if self.source_hosting.get() != '全部' and self.source_hosting.get() not in kinds: continue
            if not source_matches(pool['name'], pool['enabled'], state, self.source_query.get(), self.source_filter.get(), bool(pool['urls'])): continue
            self.sources_tree.insert('', 'end', iid='pool-' + str(index), values=(
                '☑' if pool['enabled'] else '☐', pool['name'], state.get('status', '尚未测试' if pool['urls'] else '待选择订阅文件'),
                state.get('nodes', '—'), state.get('eligible', '—'), state.get('verified') if state.get('verified') is not None else '未检测', ' / '.join(kinds)))
        selected = [item for item in selected if self.sources_tree.exists(item)]
        if selected: self.sources_tree.selection_set(selected)
        enabled = len(self.selected_public_sources) + sum(pool['enabled'] for pool in self.custom_pools)
        self.source_selection_hint.set(f'启用 {enabled} / {len(GITHUB_POOLS) + len(self.custom_pools)} · 显示 {len(self.sources_tree.get_children())}')
        for title, (names, limit, per_source) in PRESETS.items():
            if set(names) == self.selected_public_sources and str(limit) == self.source_budget_value.get() and str(per_source) == self.per_source_budget_value.get():
                self.source_preset.set(title); break
        else: self.source_preset.set('自定义')
        if selected: self.describe_public_source()
        else: self.public_source_description.set('选择来源查看最近成功时间、失败原因和各文件状态。内置来源可停用；自选来源可编辑或删除。')

    def describe_public_source(self):
        selected = self.sources_tree.selection()
        if not selected: return
        name = self.sources_tree.item(selected[0], 'values')[1]
        state = self.custom_source_states.get(name, {}) if selected[0].startswith('pool-') else self.cached_source_states.get(name, {})
        text = 'https://github.com/' + name.split(' · ')[0] + ' · ' + source_note(name)
        text += f"\n解析 {state.get('nodes', '—')} · 送检 {state.get('eligible', '—')} · 通过 {state.get('verified', '未检测')}"
        if state.get('unique') is not None: text += f"\n去重 {state['unique']} · 独有 {state.get('exclusive', 0)} · 送检 {state.get('eligible', 0)}"
        if state.get('last_verified_at'): text += ' · 最近协议检测：' + display_time(state['last_verified_at'])
        if name in LARGE_POOLS: text += ' · 大型来源，检测时间较长'
        if state.get('route'): text += ' · ' + state['route']
        if selected[0].startswith('pool-'):
            pool = self.custom_pools[int(selected[0][5:])]
            text += f" · 自选节点池，已选择 {len(pool['urls'])} 个订阅文件"
            for number, (url, record) in enumerate(zip(pool['urls'], state.get('files', [])), 1):
                from ..public_links import link_kind
                text += '\n来源类型：' + link_kind(url)
                text += f"\n文件 {number}：{url}\n  {record.get('status', '尚未测试')} · 最近下载成功：{display_time(record.get('last_success'))}"
                if record.get('failure_reason'): text += ' · 原因：' + str(record['failure_reason'])
                if record.get('next_retry'): text += ' · 下次重试：' + display_time(record['next_retry'])
        text += '\n最近下载成功：' + display_time(state.get('last_success')) + f" · 连续失败 {state.get('failures', 0)} 次"
        if selected[0].startswith('pool-'):
            for number, record in enumerate(state.get('files', []), 1):
                text += f'\n文件 {number} 的贡献依据：' + source_evidence(record)
        else:
            text += '\n贡献依据：' + source_evidence(state)
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
                   '该节点池已在列表中；新增文件后会停用，请检查文件并手动启用。')
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
        ttk.Label(body, text='每行一个该仓库的文件地址，或维护者在仓库说明中公开分享的 HTTPS 订阅链接。\n文件改变后自动停用，需回到列表手动启用；清空后保留来源供以后选择文件。', wraplength=680).pack(anchor='w', pady=10)
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

    def fetch_public_sources(self, full_check=False):
        if self.busy or not self.persist_source_choices(): return
        if full_check: self.run('acquire', full_check=True)
        else: self.prepare_export('acquire')

    def toggle_source_advanced(self):
        if self.source_advanced.winfo_manager(): self.source_advanced.pack_forget()
        else: self.source_advanced.pack(before=self.source_body, fill='x', pady=(0, 12))

    def update_source_detail(self):
        self.source_detail_text.configure(state='normal')
        self.source_detail_text.delete('1.0', 'end')
        self.source_detail_text.insert('1.0', self.public_source_description.get())
        self.source_detail_text.configure(state='disabled')

    def source_click(self, event):
        if self.sources_tree.identify_region(event.x, event.y) != 'cell': return
        if self.sources_tree.identify_column(event.x) == '#1':
            row = self.sources_tree.identify_row(event.y)
            if row:
                self.sources_tree.selection_set(row)
                self.toggle_public_source()

    def open_selected_repository(self):
        selected = self.sources_tree.selection()
        if not selected:
            self.state_text.set('请先选择一个来源。'); return
        item = selected[0]
        name = self.custom_pools[int(item[5:])]['name'] if item.startswith('pool-') else GITHUB_POOLS[int(item)][0]
        webbrowser.open('https://github.com/' + name.split(' · ')[0])

    def show_source_menu(self):
        selected = self.sources_tree.selection()
        if not selected:
            self.state_text.set('请先选择一个来源。'); return
        custom = selected[0].startswith('pool-')
        for index in (1, 2): self.source_menu.entryconfigure(index, state='normal' if custom else 'disabled')
        try: self.source_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally: self.source_menu.grab_release()

    def source_maintenance(self, action):
        try:
            if action == 'edit': self.edit_public_source()
            else: self.remove_public_source()
        except (AppError, OSError) as error:
            messagebox.showerror('操作未完成', str(error) if isinstance(error, AppError) else '无法更新来源，请检查目录权限。', parent=self.root)

    def retry_failed_sources(self):
        if self.busy: return
        # Use file health keys, not displayed repository labels: custom pools may
        # contain a mix of failed and successful files.
        settings = load_settings(self.paths)
        from ..source_catalog import enabled_urls
        from ..public_sources import selected_pools
        from ..source_plan import source_key
        health = read_mapping(self.paths.data / 'source-cache/health.json')
        urls = [url for _, url in selected_pools(settings['fetch'])] + enabled_urls(settings['fetch'])
        keys = {source_key(url) for url in urls if needs_attention(health.get(source_key(url), {}))}
        if not keys:
            self.state_text.set('当前启用的来源没有下载失败记录。需选择文件或协议检测失败的问题，请查看来源详情。'); return
        settings['fetch'].update(_cache_folder=str(self.paths.data / 'source-cache'), _force=True, _only_source_keys=sorted(keys))
        def work(control):
            with app_lock(self.paths):
                return fetch_all(settings, lambda line: self.events.put(('log', line)), control, public_only=True)
        self.start_task('test-sources', work)
