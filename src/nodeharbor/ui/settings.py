"""Settings desktop page behavior."""
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from ..client_exports import CLIENTS
from ..config import find_core, core_kind, validate_core, load_settings, validate_settings
from ..storage import AppError, app_lock, write_yaml
from ..public_sources import selected_pools
from ..source_catalog import enabled_urls
from .common import BG, INK, MUTED, WHITE, open_help, open_path, input_actions

class SettingsPage:
    def show_settings(self):
        settings = load_settings(self.paths)
        self.settings_snapshot = deepcopy(settings)
        panel = tk.Frame(self.main, bg=WHITE, padx=24, pady=24); panel.pack(fill="x")
        header = tk.Frame(panel, bg=WHITE); header.pack(fill='x', pady=(0, 8))
        self.label(header, '用于检测节点的客户端', bold=True).pack(side='left')
        self.button(self.header_actions, '保存设置', self.save_settings, True, True).pack(side='right')
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
        input_actions(row, self.core_path, self.button, [('选择客户端 / 核心', self.pick_core, {})])
        self.label(panel, '已有客户端：选择其主程序，应用会在安装目录查找核心。没有客户端：使用下方官方下载。选择后点击“检查核心是否可用”，成功后保存设置。',
                   color=MUTED, wraplength=660, justify='left').pack(fill='x', pady=(8, 0))
        self.core_hint_label = tk.Label(panel, textvariable=self.core_hint_value, bg=WHITE, fg=MUTED,
                                      wraplength=660, justify='left', anchor='w')
        self.core_hint_label.pack(fill='x', pady=(8, 8))
        self.button(panel, '检查核心是否可用', self.check_selected_core, task=True).pack(anchor='w', pady=(0, 10))
        selector.bind('<<ComboboxSelected>>', self.change_core_client)
        self.v2rayn_core_value.trace_add('write', lambda *_: self.update_core_hint())
        self.update_core_hint()
        panel = self.settings_section('下载与来源')
        chosen_count = len(selected_pools(settings['fetch'])) + len(enabled_urls(settings['fetch']))
        self.label(panel, f'当前选择 {chosen_count} 个公开来源；获取后去重和检测。可在“公开源”页选择或添加，打开应用不会自动联网。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w')
        self.button(panel, '管理公开源', lambda: self.show('sources')).pack(anchor='w', pady=(6, 0))
        self.label(panel, '下载代理（可选，例如 http://127.0.0.1:7890）', bold=True).pack(anchor='w', pady=(14, 8))
        self.download_proxy_value = tk.StringVar(value=settings['fetch']['proxy'])
        ttk.Entry(panel, textvariable=self.download_proxy_value).pack(fill='x')
        self.label(panel, '用于公开源搜索和订阅下载；留空使用环境 / 系统代理。网络受限时可使用正在运行的客户端代理端口。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(6, 0))
        self.auto_local_proxy_value = tk.BooleanVar(value=settings['fetch']['auto_local_proxy'])
        tk.Checkbutton(panel, text='公开源连接失败时，自动尝试本机客户端的 HTTP 代理', variable=self.auto_local_proxy_value,
                       bg=WHITE, activebackground=WHITE, fg=INK).pack(anchor='w', pady=(8, 0))
        self.label(panel, '仅影响本应用下载请求，不修改系统代理。明确填写下载代理时以填写值为准。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(4, 0))
        panel = self.settings_section('导出与本地订阅')
        self.label(panel, "本地订阅端口", bold=True).pack(anchor="w", pady=(0, 8))
        self.port_value = tk.StringVar(value=str(settings["subscription"]["port"]))
        ttk.Entry(panel, textvariable=self.port_value, width=15).pack(anchor="w")
        self.label(panel, 'Mihomo 独立运行的代理端口', bold=True).pack(anchor='w', pady=(18, 8))
        self.mihomo_port_value = tk.StringVar(value=str(settings['exports']['mihomo_port']))
        ttk.Entry(panel, textvariable=self.mihomo_port_value, width=15).pack(anchor='w')
        self.label(panel, '仅写入 Mihomo 的配置，不修改其他客户端或系统代理。', color=MUTED).pack(anchor='w')
        self.label(panel, '导出根目录（留空使用默认位置）', bold=True).pack(anchor='w', pady=(18, 8))
        self.export_directory_value = tk.StringVar(value=settings['exports']['directory'])
        row = tk.Frame(panel, bg=WHITE); row.pack(fill='x')
        input_actions(row, self.export_directory_value, self.button, [('选择文件夹', self.pick_export_directory, {'task': True})])
        self.label(panel, '自动按 Mihomo、clash-verge-Rev、v2rayN 分文件夹保存。更换目录时会复制已有导出，原文件保留。',
                   color=MUTED, wraplength=700, justify='left').pack(anchor='w', pady=(6, 0))
        self.label(self.main, "更多配置", 13, bold=True).pack(anchor="w", pady=(24, 14))
        self.show_downloads(self.main)
        row = tk.Frame(self.main, bg=BG); row.pack(anchor="w")
        self.button(row, "打开配置文件夹", lambda: open_path(self.paths.config)).pack(side="left", padx=(0, 10))
        self.button(row, "使用说明", lambda: open_help(self.paths)).pack(side="left")
        self.button(self.main, '导出脱敏诊断报告', self.export_diagnostics, task=True).pack(anchor='w', pady=(12, 12))
        self.show_subscription_tools()

    def settings_section(self, title):
        panel = tk.Frame(self.main, bg=WHITE, padx=24, pady=20)
        panel.pack(fill='x', pady=(12, 0))
        self.label(panel, title, 13, bold=True).pack(anchor='w', pady=(0, 12))
        return panel

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
            original = load_settings(self.paths)
            if original != self.settings_snapshot:
                raise AppError('配置已被另一项操作修改，请重新打开设置后再保存。')
            settings = deepcopy(original)
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
            settings = validate_settings(self.paths, settings)
            directory = self.export_directory_value.get().strip()
            if directory != settings['exports']['directory']:
                from ..export_locations import relocate_exports
                relocate_exports(self.paths, directory, settings, expected=original)
            else:
                with app_lock(self.paths):
                    if load_settings(self.paths) != original:
                        raise AppError('配置已被另一项操作修改，请重新打开设置后再保存。')
                    write_yaml(self.paths.config / "settings.yaml", settings)
            self.export_client = self.core_client_value.get()
            self.settings_snapshot = load_settings(self.paths)
            self.state_text.set("设置已保存")
        except ValueError: messagebox.showerror("端口无效", "请输入 1024–65535 的整数。", parent=self.root)
        except (AppError, OSError) as exc:
            messagebox.showerror('保存失败', str(exc) if isinstance(exc, AppError) else '无法写入或迁移导出，请检查目录权限。', parent=self.root)
