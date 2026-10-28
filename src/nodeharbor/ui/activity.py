"""Activity desktop page behavior."""
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from ..pipeline import execute
from ..storage import AppError
from ..source_plan import summary_text
from ..diagnostic_report import export_report
from ..progress import TaskProgress, STAGES, TASKS
from .common import BLUE, INK, MUTED, WHITE, open_path

class ActivityView:
    def show_logs(self):
        self.button(self.header_actions, '打开日志文件夹', lambda: open_path(self.paths.logs)).pack(side='left', padx=(0, 8))
        self.button(self.header_actions, '导出诊断报告', self.export_diagnostics, primary=True, task=True).pack(side='left')
        self.label(self.main, '诊断报告仅包含状态、统计和匿名来源编号，保存在本机，不自动上传。',
                   color=MUTED, wraplength=740, justify='left').pack(anchor='w', pady=(0, 12))
        frame = tk.Frame(self.main, bg=WHITE); frame.pack(fill='both', expand=True)
        self.log_widget = tk.Text(frame, height=10, relief="flat", bg=WHITE, fg=INK, padx=18, pady=18,
                                  font=("Consolas", 10), wrap="word", state="normal")
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.log_widget.yview)
        self.log_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log_widget.pack(fill="both", expand=True)
        path = self.paths.logs / "app.log"
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(0, 2); stream.seek(max(0, stream.tell() - 60000))
                content = stream.read().decode('utf-8', errors='replace')[-30000:]
        else: content = "尚无运行日志。\n"
        self.log_widget.insert("end", content)
        self.log_widget.see("end"); self.log_widget.config(state="disabled")

    def export_diagnostics(self):
        if self.busy: return
        previous_task = getattr(self, 'last_operational_task', None) or self.task_progress
        def work(control):
            control.emit('diagnostic', 0, 1, '正在生成脱敏诊断报告')
            control.check()
            target = export_report(self.paths, previous_task)
            control.emit('diagnostic', 1, 1, '诊断报告已保存在本机，未上传')
            self.events.put(('diagnostic-saved', target))
            return '脱敏诊断报告已生成，可在日志文件夹的 diagnostics 中查看。'
        self.start_task('diagnostics', work, refresh=False)

    def run(self, action, full_check=False):
        self.start_task(action, lambda control: execute(self.paths, action,
                        callback=lambda line: self.events.put(('log', line)), control=control, full_check=full_check))

    def set_task_actions(self, disabled):
        self.actions = [widget for widget in self.actions if widget.winfo_exists()]
        for widget in self.actions:
            widget.configure(state='disabled' if disabled else 'readonly' if isinstance(widget, ttk.Combobox) else 'normal')

    def start_task(self, action, operation, refresh=True):
        if self.busy: return
        self.busy = True
        self.task_progress = TaskProgress(action)
        if action != 'diagnostics': self.last_operational_task = self.task_progress
        self.refresh_after_task = refresh
        self.control = None
        self.cancel_button.config(state="normal")
        self.set_task_actions(True)
        self.state_text.set(TASKS.get(action, action) + '：准备中')
        if refresh: self.show(self.page)
        else:
            if self.progress_panel and self.progress_panel.winfo_exists(): self.progress_panel.destroy()
            if self.page == 'logs': self.show_task_progress()
        self.render_task_progress()
        try:
            self.control = self.runner.start(operation)
        except (AppError, RuntimeError):
            self.busy = False
            self.task_progress.finish('failed', '后台任务未能启动，请重试。')
            self.cancel_button.config(state='disabled')
            self.set_task_actions(False)
            self.render_task_progress()
            raise AppError('后台任务未能启动，请重试。') from None

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
        actions = tk.Frame(self.progress_panel, bg=WHITE); actions.pack(fill='x', pady=(6, 4))
        self.progress_cancel_button = self.button(actions, '取消当前任务', self.cancel_task)
        self.progress_cancel_button.pack(side='right', padx=8)
        self.progress_details = tk.Frame(self.progress_panel, bg=WHITE)
        self.progress_details_open = self.page == 'logs'
        self.progress_toggle = self.button(actions, '收起详情' if self.progress_details_open else '展开详情', self.toggle_progress_details)
        self.progress_toggle.pack(side='right')
        self.progress_steps = tk.StringVar()
        tk.Label(self.progress_details, textvariable=self.progress_steps, bg=WHITE, fg=MUTED,
                 anchor='w', wraplength=660, justify='left').pack(fill='x', pady=(8, 5))
        self.progress_bar = ttk.Progressbar(self.progress_panel, style='NodeHarbor.Horizontal.TProgressbar', mode='indeterminate', length=500)
        self.progress_bar_running = False
        self.progress_bar.pack(fill='x', pady=(3, 8))
        self.progress_detail = tk.StringVar()
        tk.Label(self.progress_panel, textvariable=self.progress_detail, bg=WHITE, fg=BLUE,
                 anchor='w', wraplength=660, justify='left').pack(fill='x')
        self.progress_counts = tk.StringVar()
        tk.Label(self.progress_details, textvariable=self.progress_counts, bg=WHITE, fg=MUTED, anchor='w', wraplength=660, justify='left').pack(fill='x', pady=(6, 0))
        self.progress_sources = tk.StringVar()
        tk.Label(self.progress_details, textvariable=self.progress_sources, bg=WHITE, fg=MUTED,
                 anchor='w', justify='left', wraplength=660).pack(fill='x', pady=(6, 0))
        self.progress_guidance = tk.StringVar()
        tk.Label(self.progress_details, textvariable=self.progress_guidance, bg=WHITE, fg=INK,
                 wraplength=660, justify='left', anchor='w').pack(fill='x', pady=(6, 0))
        self.progress_recovery = tk.Frame(self.progress_panel, bg=WHITE)
        self.button(self.progress_recovery, '查看来源 / 失败详情', lambda: self.show('sources')).pack(side='left', padx=(0, 8))
        self.button(self.progress_recovery, '检查核心 / 下载代理', lambda: self.show('settings')).pack(side='left', padx=(0, 8))
        self.button(self.progress_recovery, '重试失败来源下载', self.retry_failed_sources).pack(side='left')
        self.flow(self.progress_recovery)
        if self.progress_details_open: self.progress_details.pack(fill='x', pady=(8, 0))
        self.render_task_progress()

    def toggle_progress_details(self):
        self.progress_details_open = not self.progress_details_open
        if self.progress_details_open: self.progress_details.pack(fill='x', pady=(8, 0))
        else: self.progress_details.pack_forget()
        self.progress_toggle.configure(text='收起详情' if self.progress_details_open else '展开详情')

    def render_task_progress(self):
        model = self.task_progress
        if not model: return
        current = model.steps.get(model.stage, {})
        done, total = current.get('completed', 0), current.get('total', 0)
        if model.status == 'running' and not total:
            if not self.footer_progress_running:
                self.footer_progress.configure(mode='indeterminate'); self.footer_progress.start(25)
                self.footer_progress_running = True
        else:
            if self.footer_progress_running: self.footer_progress.stop(); self.footer_progress_running = False
            self.footer_progress.configure(mode='determinate', maximum=total or 1,
                                           value=min(done, total) if total else int(model.status == 'success'))
        if model.status == 'running':
            brief = TASKS.get(model.action, model.action) + ' · ' + STAGES.get(model.stage, '')
            cancelling = self.busy and self.control and self.control.event.is_set()
            self.state_text.set(('正在取消 · ' if cancelling else '') + brief + (f' {done}/{total}' if total else '') + f' · {model.elapsed}s')
        if not self.progress_panel or not self.progress_panel.winfo_exists(): return
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
            self.progress_bar.configure(mode='determinate', maximum=total, value=min(done, total))
        elif model.status == 'running':
            if not self.progress_bar_running:
                self.progress_bar.configure(mode='indeterminate'); self.progress_bar.start(20); self.progress_bar_running = True
        else:
            if self.progress_bar_running: self.progress_bar.stop(); self.progress_bar_running = False
            self.progress_bar.configure(mode='determinate', maximum=1, value=1 if model.status == 'success' else 0)
        brief = STAGES.get(model.stage, '准备中') + (f' {done}/{total}' if total else '')
        if model.stage == 'protocol': brief += f" · 通过 {model.counters['passed']}"
        self.progress_detail.set(brief if model.status == 'running' else model.message)
        if model.status == 'running':
            self.state_text.set(('正在取消 · ' if cancelling else '') + brief + f' · 已用时 {model.elapsed // 60:02}:{model.elapsed % 60:02}')
        if model.status == 'failed' and model.action in {'acquire', 'refresh', 'refresh-github', 'test-sources', 'verify-library', 'check-core'}:
            if not self.progress_recovery.winfo_manager(): self.progress_recovery.pack(fill='x', pady=(10, 0))
        else: self.progress_recovery.pack_forget()
        c = model.counters
        self.progress_counts.set(f"解析 {c['parsed']} · 去重 {c['unique']} · 送检 {c['selected']} · TCP 通过 {c['precheck_passed']} · 实测 {c['tested']} · 不兼容 {c['skipped']} · 通过 {c['passed']}（多次通过 {c['stable']}）")
        if 'fresh_passed' in model.acquisition:
            s = model.acquisition
            self.progress_counts.set(f"解析 {c['parsed']} · 去重 {c['unique']} · 本轮检查 {s.get('checked_this_run', 0)} · 实测 {c['tested']} · 本轮通过 {s['fresh_passed']} · 沿用历史 {s.get('reused', 0)} · 失败退避 {s.get('node_deferred', 0)}")
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
        if model.action == 'diagnostics':
            self.progress_counts.set('')
            guidance = '报告仅保存在本机日志目录的 diagnostics 文件夹，不会自动上传。'
        self.progress_guidance.set(model.message + '\n' + guidance)

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
                    if self.task_progress.action == 'search-sources' and self.search_dialog and self.search_dialog.exists():
                        self.search_dialog.note.set(message.get('message', '正在搜索…'))
                    if message.get('sources'):
                        if self.page == 'sources':
                            self.cached_source_states = {source['name']: source for source in message.get('sources', [])}
                            sources_changed = True
                    self.state_text.set(message.get('message', '') + (f" · {message['completed']}/{message['total']}" if message.get('total') else ''))
                elif kind == 'discovery':
                    dialog, result = message
                    if dialog.exists(): dialog.receive(result)
                elif kind == 'diagnostic-saved':
                    self.last_diagnostic_path = message
                    if not self.closing:
                        messagebox.showinfo('脱敏诊断报告已保存', f'{message}\n\n报告未上传，可自行查看后分享。', parent=self.root)
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
                        self.set_task_actions(False)
                    if kind == "error": messagebox.showerror("任务未完成", message, parent=self.root)
        except queue.Empty: pass
        if sources_changed and self.page == 'sources': self.fill_public_sources()
        self.render_task_progress()
        if self.search_dialog and self.search_dialog.exists(): self.search_dialog.update_busy()
        self.poll_id = self.root.after(100, self.poll)
