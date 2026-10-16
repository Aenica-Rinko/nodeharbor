"""Tk application shell: navigation, layout and shared widget helpers."""
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from . import __version__
from .initialize import initialize
from .storage import AppError, Paths
from .task_runner import TaskRunner
from .ui.common import BG, BLUE, INK, LINE, MUTED, WHITE, open_path

from .ui.sources import SourcesPage
from .ui.nodes import NodesPage
from .ui.exports import ExportsPage
from .ui.settings import SettingsPage
from .ui.activity import ActivityView
from .ui.overview import OverviewPage


class Desktop(SourcesPage, NodesPage, ExportsPage, SettingsPage, ActivityView, OverviewPage):
    def __init__(self, root, paths):
        self.root, self.paths = root, paths
        paths.ensure()
        initialize(paths)
        self.events = queue.Queue()
        self.runner = TaskRunner(self.events)
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


    def copy(self, value):
        self.root.clipboard_clear(); self.root.clipboard_append(value)
        self.state_text.set("地址已复制" + ("；请先启动本地服务" if not self.server and "127.0.0.1" in value else ""))


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
