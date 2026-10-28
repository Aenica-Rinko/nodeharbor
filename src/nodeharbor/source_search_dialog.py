"""Explicit repository discovery and subscription selection on the Tk thread."""
import tkinter as tk
import webbrowser
from tkinter import ttk

from .config import load_settings
from .github_search import clean_query, discover
from .ui.common import BG, INK, MUTED, WHITE


class SourceSearchDialog:
    def __init__(self, desktop, repository=None, replace=False):
        self.desktop = desktop
        self.replacing = dict(repository) if replace and repository else None
        self.repositories, self.files = [], []
        self.cached_files = {}
        self.file_repository = None
        self.window = tk.Toplevel(desktop.root)
        self.window.title('搜索节点池')
        scale = max(1, float(desktop.root.tk.call('tk', 'scaling')) / (96 / 72))
        width = min(round(1100 * scale), desktop.root.winfo_screenwidth() - 40)
        height = min(round(700 * scale), desktop.root.winfo_screenheight() - 80)
        compact = height < 620 * scale
        self.window.geometry(f'{width}x{height}')
        self.window.minsize(min(round(900 * scale), width), min(round(600 * scale), height))
        self.window.configure(bg=BG)
        self.window.transient(desktop.root)
        self.window.grab_set()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        body = tk.Frame(self.window, bg=BG, padx=20, pady=18); body.pack(fill='both', expand=True)
        row = tk.Frame(body, bg=BG); row.pack(fill='x')
        self.search_kind = tk.StringVar(value='github')
        self.mode_buttons = []
        for title, mode in [('GitHub 公开源', 'github'), ('Cloudflare 托管订阅', 'cloudflare')]:
            button = tk.Radiobutton(row, text=title, variable=self.search_kind, value=mode,
                                    command=self.change_mode, bg=BG, fg=INK, activebackground=BG,
                                    selectcolor=WHITE, padx=8, pady=6)
            button.pack(side='left', padx=(0, 16)); self.mode_buttons.append(button)
        self.scope = tk.StringVar()
        self.message(body, self.scope, height=1 if compact else 2).pack(fill='x', pady=(6, 8))
        row = tk.Frame(body, bg=BG); row.pack(fill='x', pady=(0, 12))
        self.query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.entry.bind('<Return>', lambda _: self.search_button.invoke())
        self.search_button = desktop.button(row, '搜索节点池', self.search, primary=True)
        self.search_button.pack(side='left')
        self.cancel_button = desktop.button(row, '取消搜索', desktop.cancel_task)
        self.cancel_button.pack(side='left', padx=(8, 0))
        self.note = tk.StringVar(value='输入关键词开始搜索，例如 free v2ray（免费的 V2Ray 节点）。搜索结果尚未验证。')
        self.message(body, self.note, height=1 if compact else 2).pack(fill='x', pady=(0, 8))
        panes = ttk.Panedwindow(body, orient='horizontal'); panes.pack(fill='both', expand=True)
        left = tk.Frame(panes, bg=WHITE, padx=12, pady=12)
        right = tk.Frame(panes, bg=WHITE, padx=12, pady=12)
        panes.add(left, weight=2); panes.add(right, weight=3)
        if not compact: desktop.label(left, '1  公开出处', bold=True).pack(anchor='w', pady=(0, 8))
        self.repo_tree = self.table(left, ('name', 'updated'), [('公开仓库', 230), ('最近推送', 105)], 5)
        self.repo_tree.bind('<<TreeviewSelect>>', self.select_repository)
        self.repo_tree.bind('<Double-1>', lambda _: self.inspect_button.invoke())
        self.description = tk.StringVar()
        description_label = self.message(left, self.description, height=1 if compact else 2)
        description_label.pack(fill='x', pady=8)
        row = tk.Frame(left, bg=WHITE); row.pack(fill='x')
        self.inspect_button = desktop.button(row, '查看订阅文件', self.inspect)
        self.inspect_button.pack(side='left')
        self.open_button = desktop.button(row, '打开仓库说明', self.open_repository)
        self.open_button.pack(side='left', padx=(8, 0))
        self.join_button = desktop.button(row, '加入节点池', self.join_repository, primary=True)
        self.join_button.pack(side='left')
        desktop.flow(row)
        row.pack_configure(side='bottom', before=self.repo_tree.master)
        description_label.pack_configure(side='bottom', before=self.repo_tree.master)
        if not compact: desktop.label(right, '2  候选订阅', bold=True).pack(anchor='w', pady=(0, 8))
        self.file_tree = self.table(right, ('path', 'kind', 'size'), [('候选文件 / 公开链接', 290), ('托管类型', 160), ('大小', 70)], 5, multiple=True)
        self.file_tree.bind('<<TreeviewSelect>>', lambda _: self.update_busy())
        self.file_note = tk.StringVar(value='请选择一个仓库查看文件；候选文件仍需下载解析和节点检测。')
        file_note_label = self.message(right, self.file_note, height=1 if compact else 2)
        file_note_label.pack(fill='x', pady=8)
        row = tk.Frame(right, bg=WHITE); row.pack(fill='x')
        self.add_button = desktop.button(row, '替换选中订阅' if self.replacing else '添加选中订阅', self.add, primary=True)
        self.add_button.pack(side='left')
        self.preview_button = desktop.button(row, '预览选中订阅', self.preview)
        self.preview_button.pack(side='left', padx=8)
        desktop.flow(row)
        row.pack_configure(side='bottom', before=self.file_tree.master)
        file_note_label.pack_configure(side='bottom', before=self.file_tree.master)
        row = tk.Frame(body, bg=BG); row.pack(side='bottom', before=panes, fill='x', pady=(12, 0))
        desktop.label(row, '加入后默认停用；启用并获取节点后，到“订阅导出”生成文件。', color=MUTED,
                      wraplength=650, justify='left', anchor='w').pack(side='left', fill='x', expand=True)
        desktop.button(row, '关闭', self.close).pack(side='right', padx=(8, 0))
        self.set_scope()
        self.update_busy()
        if repository:
            item = dict(repository, description='请查看候选文件并选择要使用的订阅文件。', updated='')
            self.receive({'mode': 'search', 'repositories': [item], 'incomplete': False})
            self.repo_tree.selection_set('0')
            if self.replacing:
                self.note.set('替换模式：仅替换该来源的文件。文件变化后自动停用，需回到列表手动启用。')
        self.entry.focus_set()

    @staticmethod
    def message(parent, variable, height=2):
        """Bound remote descriptions and status text without hiding long messages."""
        frame = tk.Frame(parent, bg=parent.cget('bg'))
        text = tk.Text(frame, height=height, width=1, wrap='word', relief='flat',
                       bg=parent.cget('bg'), fg=MUTED, state='disabled', takefocus=True)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        def update(*_):
            text.configure(state='normal'); text.delete('1.0', 'end')
            text.insert('1.0', variable.get()); text.configure(state='disabled')
        trace = variable.trace_add('write', update)
        frame.bind('<Destroy>', lambda event: variable.trace_remove('write', trace) if event.widget == frame else None)
        update()
        return frame

    @staticmethod
    def table(parent, columns, headings, height, multiple=False):
        frame = ttk.Frame(parent); frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height,
                            selectmode='extended' if multiple else 'browse')
        for key, (title, width) in zip(columns, headings):
            tree.heading(key, text=title); tree.column(key, width=width, minwidth=90)
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew'); scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        return tree

    def set_scope(self):
        self.scope.set('搜索公开 GitHub 仓库，最多显示 20 个结果。关键词示例：免费节点、free v2ray、clash subscription。'
                       if self.search_kind.get() == 'github' else
                       '从 GitHub 公开说明查找 workers.dev / pages.dev 订阅；每次最多检查 6 个仓库。留空默认搜索 vless，可改为 clash、免费节点等。不是 Cloudflare 官方节点目录。')

    def clear_results(self):
        self.repositories, self.files, self.cached_files = [], [], {}
        self.file_repository = None
        self.repo_tree.delete(*self.repo_tree.get_children())
        self.file_tree.delete(*self.file_tree.get_children())
        self.description.set('')
        self.file_note.set('请选择左侧来源，查看候选订阅；预览只解析，不检测节点可用性。')

    def change_mode(self):
        self.clear_results()
        self.set_scope()
        self.note.set('点击“搜索节点池”开始查找。新加入的订阅默认停用。')
        self.update_busy()

    def exists(self): return bool(self.window.winfo_exists())

    def selected_repo(self):
        selection = self.repo_tree.selection()
        return self.repositories[int(selection[0])] if selection else None

    def select_repository(self, _=None):
        repo = self.selected_repo()
        self.description.set((repo.get('kind', '订阅候选 / 待确认') + ' · ' + repo['description']) if repo else '')
        if repo and repo['name'] in self.cached_files:
            self.receive({'mode': 'files', 'repository': repo['name'],
                          'files': self.cached_files[repo['name']], 'limited': False})
        elif self.file_repository and (not repo or self.file_repository['name'] != repo['name']):
            self.files, self.file_repository = [], None
            self.file_tree.delete(*self.file_tree.get_children())
            self.file_note.set('点击“查看订阅文件”读取当前仓库。')
        self.update_busy()

    def open_repository(self):
        if repo := self.selected_repo(): webbrowser.open('https://github.com/' + repo['name'])

    def start(self, mode, value):
        if self.desktop.busy: return
        options = load_settings(self.desktop.paths)['fetch']
        def work(control):
            result = discover(mode, value, options, control)
            control.check()
            self.desktop.events.put(('discovery', (self, result)))
            return '搜索完成，请查看候选订阅及搜索范围提示。' if mode.startswith('search') else '解析预览完成，节点可用性尚未检测。' if mode == 'preview' else '候选文件已列出，请选择需要添加的文件。'
        if mode.startswith('search'): self.clear_results()
        self.note.set('正在搜索公开来源…' if mode.startswith('search') else '正在下载并解析订阅，最多读取 2 MB…' if mode == 'preview' else '正在读取仓库文件列表和说明中的公开链接…')
        self.desktop.start_task('search-sources', work, refresh=False)
        self.update_busy()

    def search(self):
        if self.desktop.busy: return
        cloudflare = self.search_kind.get() == 'cloudflare'
        query = self.query.get().strip()
        if query or not cloudflare: query = clean_query(query)
        self.start('search-cloudflare' if cloudflare else 'search', query)

    def inspect(self):
        if self.desktop.busy: return
        if repo := self.selected_repo():
            if repo['name'] in self.cached_files: self.select_repository()
            else: self.start('files', dict(repo))

    def preview(self):
        if self.desktop.busy: return
        selected = self.file_tree.selection()
        if selected: self.start('preview', self.files[int(selected[0])]['url'])

    def receive(self, result):
        if not self.exists(): return
        if result['mode'] == 'preview':
            protocols = '、'.join(f'{key} {count}' for key, count in result.get('protocols', {}).items())
            self.file_note.set(result.get('warning') or f"预览解析到 {result['count']} 个节点（{protocols}）。仅解析，尚未检测可用性；不会加入节点库或导出。")
            self.note.set('订阅出处：' + ('https://github.com/' + self.file_repository['name'] if self.file_repository else '公开链接'))
        elif result['mode'] in ('search', 'search-cloudflare'):
            self.repositories, self.files = result['repositories'], []
            self.file_repository = None
            self.cached_files = result.get('files_by_repository', {})
            self.repo_tree.delete(*self.repo_tree.get_children())
            self.file_tree.delete(*self.file_tree.get_children())
            self.description.set('')
            self.file_note.set('请选择仓库查看候选文件。')
            for index, repo in enumerate(self.repositories):
                self.repo_tree.insert('', 'end', iid=str(index), values=(repo['name'], repo['updated']))
            self.note.set(f'本次列出 {len(self.repositories)} 个匹配仓库（最多 20 个，不代表全部节点池）。' +
                          ('GitHub 返回了部分搜索结果。' if result['incomplete'] else '') +
                          ('未找到匹配仓库，请更换关键词。' if not self.repositories else ''))
            if result['mode'] == 'search-cloudflare':
                total = sum(len(items) for items in self.cached_files.values())
                self.note.set(f"已读取 {result['checked']} 个公开仓库说明，找到 {total} 个 Cloudflare 托管候选链接（未检测）。" +
                              ' '.join(result.get('warnings', [])) +
                              ('本次未找到符合地址规则的公开候选链接，可更换关键词；这不代表没有此类来源。' if not total else '选择左侧出处查看链接。'))
        else:
            self.files = result['files']
            self.file_repository = next((dict(r) for r in self.repositories if r['name'] == result['repository']), None)
            self.file_tree.delete(*self.file_tree.get_children())
            for index, file in enumerate(self.files):
                from .public_links import link_kind
                self.file_tree.insert('', 'end', iid=str(index), values=(file['path'], link_kind(file['url']), f"{file['size'] / 1024:.1f} KB" if file['size'] else '未知'))
            self.file_note.set(result['repository'] + f"：找到 {len(self.files)} 个候选文件，尚未验证内容。" +
                               ('仅显示部分文件，可打开仓库说明查找。' if result['limited'] else '') +
                               ('请打开仓库说明，手动复制订阅文件地址。' if not self.files else '') + result.get('readme_warning', ''))
            if self.replacing:
                self.note.set('选择文件后点击“替换为选中的订阅文件”，原文件列表将被替换，文件变化后需手动启用。')
        self.update_busy()

    def join_repository(self):
        if self.desktop.busy or self.replacing: return
        repo = self.selected_repo()
        if not repo: return
        urls = []
        if self.file_repository and self.file_repository['name'] == repo['name']:
            urls = [self.files[int(i)]['url'] for i in self.file_tree.selection()]
        self.note.set(self.desktop.add_catalog_repository(repo, urls))

    def add(self):
        if self.desktop.busy or not self.file_repository: return
        urls = [self.files[int(i)]['url'] for i in self.file_tree.selection()]
        if not urls: return
        if self.replacing:
            if self.file_repository['name'].casefold() != self.replacing['name'].casefold(): return
            self.note.set(self.desktop.replace_catalog_files(self.replacing, urls))
            self.replacing = next(dict(p) for p in self.desktop.custom_pools if p['name'].casefold() == self.replacing['name'].casefold())
        else:
            self.note.set(self.desktop.add_catalog_repository(self.file_repository, urls))

    def update_busy(self):
        if not self.exists(): return
        busy = self.desktop.busy
        self.search_button.config(state='disabled' if busy or self.replacing else 'normal')
        self.entry.config(state='disabled' if busy or self.replacing else 'normal')
        for button in self.mode_buttons: button.config(state='disabled' if busy or self.replacing else 'normal')
        self.inspect_button.config(state='normal' if not busy and self.selected_repo() else 'disabled')
        self.open_button.config(state='normal' if self.selected_repo() else 'disabled')
        self.join_button.config(state='normal' if not busy and self.selected_repo() and not self.replacing else 'disabled')
        self.add_button.config(state='normal' if not busy and self.file_tree.selection() else 'disabled')
        self.preview_button.config(state='normal' if not busy and len(self.file_tree.selection()) == 1 else 'disabled')
        cancelling = busy and self.desktop.control and self.desktop.control.event.is_set()
        self.cancel_button.config(state='normal' if busy and not cancelling else 'disabled',
                                  text='正在取消…' if cancelling else '取消搜索')

    def close(self):
        if self.desktop.busy: self.desktop.cancel_task()
        self.window.grab_release()
        self.window.destroy()
