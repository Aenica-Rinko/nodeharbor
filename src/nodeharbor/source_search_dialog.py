"""Explicit repository discovery and subscription selection on the Tk thread."""
import tkinter as tk
import webbrowser
from tkinter import ttk

from .config import load_settings
from .github_search import clean_query, discover


class SourceSearchDialog:
    def __init__(self, desktop, repository=None):
        self.desktop = desktop
        self.repositories, self.files = [], []
        self.file_repository = None
        self.window = tk.Toplevel(desktop.root)
        self.window.title('搜索 GitHub 节点池')
        self.window.geometry('900x700')
        self.window.minsize(760, 620)
        self.window.transient(desktop.root)
        self.window.grab_set()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(self.window, padding=18); body.pack(fill='both', expand=True)
        ttk.Label(body, text='搜索仓库 → 加入 NodeHarbor 节点池（默认停用）→ 选择文件 → 回到列表手动启用').pack(anchor='w')
        row = ttk.Frame(body); row.pack(fill='x', pady=10)
        self.query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.entry.bind('<Return>', lambda _: self.search_button.invoke())
        self.search_button = desktop.button(row, '搜索 GitHub', self.search, primary=True)
        self.search_button.pack(side='left')
        self.cancel_button = desktop.button(row, '取消搜索', desktop.cancel_task)
        self.cancel_button.pack(side='left', padx=(8, 0))
        ttk.Label(body, text='搜索关键词示例：免费节点、free v2ray（免费的 V2Ray 节点）、clash subscription').pack(anchor='w', pady=(0, 6))
        self.note = tk.StringVar(value='按最近更新显示最多 20 个匹配仓库；不代表全部节点池，搜索结果尚未验证。')
        ttk.Label(body, textvariable=self.note, wraplength=820).pack(anchor='w', pady=(0, 8))
        self.repo_tree = self.table(body, ('name', 'updated'), [('公开仓库', 570), ('最近推送', 130)], 5)
        self.repo_tree.bind('<<TreeviewSelect>>', self.select_repository)
        self.repo_tree.bind('<Double-1>', lambda _: self.inspect_button.invoke())
        self.description = tk.StringVar()
        ttk.Label(body, textvariable=self.description, wraplength=820).pack(anchor='w', pady=5)
        row = ttk.Frame(body); row.pack(fill='x', pady=(0, 8))
        self.inspect_button = desktop.button(row, '查看候选订阅文件', self.inspect)
        self.inspect_button.pack(side='left')
        self.open_button = desktop.button(row, '打开仓库说明', self.open_repository)
        self.open_button.pack(side='left', padx=8)
        self.join_button = desktop.button(row, '加入 NodeHarbor 节点池', self.join_repository, primary=True)
        self.join_button.pack(side='left')
        self.file_note = tk.StringVar(value='请选择一个仓库查看文件；候选文件仍需下载解析和节点检测。')
        ttk.Label(body, textvariable=self.file_note, wraplength=820).pack(anchor='w', pady=(0, 5))
        self.file_tree = self.table(body, ('path', 'size'), [('候选文件（未检测）', 570), ('大小', 130)], 5, multiple=True)
        self.file_tree.bind('<<TreeviewSelect>>', lambda _: self.update_busy())
        row = ttk.Frame(body); row.pack(fill='x', pady=(10, 0))
        self.add_button = desktop.button(row, '使用选中的订阅文件', self.add, primary=True)
        self.add_button.pack(side='left')
        desktop.button(row, '关闭', self.close).pack(side='right')
        self.update_busy()
        if repository:
            item = dict(repository, description='请查看候选文件并选择要使用的订阅文件。', updated='')
            self.receive({'mode': 'search', 'repositories': [item], 'incomplete': False})
            self.repo_tree.selection_set('0')
        self.entry.focus_set()

    @staticmethod
    def table(parent, columns, headings, height, multiple=False):
        frame = ttk.Frame(parent); frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height,
                            selectmode='extended' if multiple else 'browse')
        for key, (title, width) in zip(columns, headings):
            tree.heading(key, text=title); tree.column(key, width=width, minwidth=90)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        return tree

    def exists(self): return bool(self.window.winfo_exists())

    def selected_repo(self):
        selection = self.repo_tree.selection()
        return self.repositories[int(selection[0])] if selection else None

    def select_repository(self, _=None):
        repo = self.selected_repo()
        self.description.set(repo['description'] if repo else '')
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
            return '搜索完成，请选择仓库查看候选文件。' if mode == 'search' else '候选文件已列出，请选择需要添加的文件。'
        self.note.set('正在搜索 GitHub…' if mode == 'search' else '正在读取仓库文件列表…')
        self.desktop.start_task('search-sources', work, refresh=False)
        self.update_busy()

    def search(self):
        if self.desktop.busy: return
        query = clean_query(self.query.get())
        self.start('search', query)

    def inspect(self):
        if self.desktop.busy: return
        if repo := self.selected_repo(): self.start('files', dict(repo))

    def receive(self, result):
        if not self.exists(): return
        if result['mode'] == 'search':
            self.repositories, self.files = result['repositories'], []
            self.file_repository = None
            self.repo_tree.delete(*self.repo_tree.get_children())
            self.file_tree.delete(*self.file_tree.get_children())
            self.description.set('')
            self.file_note.set('请选择仓库查看候选文件。')
            for index, repo in enumerate(self.repositories):
                self.repo_tree.insert('', 'end', iid=str(index), values=(repo['name'], repo['updated']))
            self.note.set(f'本次列出 {len(self.repositories)} 个匹配仓库（最多 20 个，不代表全部节点池）。' +
                          ('GitHub 返回了部分搜索结果。' if result['incomplete'] else '') +
                          ('未找到匹配仓库，请更换关键词。' if not self.repositories else ''))
        else:
            self.files = result['files']
            self.file_repository = next((dict(r) for r in self.repositories if r['name'] == result['repository']), None)
            self.file_tree.delete(*self.file_tree.get_children())
            for index, file in enumerate(self.files):
                self.file_tree.insert('', 'end', iid=str(index), values=(file['path'], f"{file['size'] / 1024:.1f} KB"))
            self.file_note.set(result['repository'] + f"：找到 {len(self.files)} 个候选文件，尚未验证内容。" +
                               ('仅显示部分文件，可打开仓库说明查找。' if result['limited'] else '') +
                               ('请打开仓库说明，手动复制订阅文件地址。' if not self.files else ''))
            self.note.set('选择文件后点击“使用选中的订阅文件”；新节点池默认停用，请回到列表手动启用。')
        self.update_busy()

    def join_repository(self):
        if self.desktop.busy: return
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
        self.note.set(self.desktop.add_catalog_repository(self.file_repository, urls))

    def update_busy(self):
        if not self.exists(): return
        busy = self.desktop.busy
        self.search_button.config(state='disabled' if busy else 'normal')
        self.entry.config(state='disabled' if busy else 'normal')
        self.inspect_button.config(state='normal' if not busy and self.selected_repo() else 'disabled')
        self.open_button.config(state='normal' if self.selected_repo() else 'disabled')
        self.join_button.config(state='normal' if not busy and self.selected_repo() else 'disabled')
        self.add_button.config(state='normal' if not busy and self.file_tree.selection() else 'disabled')
        cancelling = busy and self.desktop.control and self.desktop.control.event.is_set()
        self.cancel_button.config(state='normal' if busy and not cancelling else 'disabled',
                                  text='正在取消…' if cancelling else '取消搜索')

    def close(self):
        if self.desktop.busy: self.desktop.cancel_task()
        self.window.grab_release()
        self.window.destroy()
