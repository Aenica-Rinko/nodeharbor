"""Cancellation, task deadlines and ownership of bounded child processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .storage import AppError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(AppError):
    pass


class DeadlineExceeded(AppError):
    pass


class TaskControl:
    def __init__(self, timeout=None, progress=None):
        self.event = threading.Event()
        self.deadline = time.monotonic() + timeout if timeout is not None else None
        self.progress = progress

    def emit(self, stage, completed=0, total=0, message='', **details):
        if self.progress:
            from copy import deepcopy
            self.progress(deepcopy(dict(stage=stage, completed=completed, total=total, message=message, **details)))

    def limit(self, seconds):
        deadline = time.monotonic() + seconds
        self.deadline = min(self.deadline, deadline) if self.deadline is not None else deadline

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set(): raise Cancelled("任务已取消；已发布的版本保持有效。")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise DeadlineExceeded("任务超过总时限；已发布的版本保持有效。")

    def communicate(self, process, payload=None, timeout=30):
        """Drain both pipes; stop only the process owned by this call on every exit."""
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise DeadlineExceeded("当前步骤超过时限。")
                try:
                    result = process.communicate(input=payload, timeout=min(0.1, remaining))
                    self.check()
                    return result
                except subprocess.TimeoutExpired:
                    payload = None
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream: stream.close()


def worker_command():
    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).with_name("NodeHarbor.Console.exe")
        if not worker.is_file():
            raise AppError("后台程序缺失，请完整解压 Windows 发布包后重新运行。")
        return [str(worker), "--network-worker"]
    return [sys.executable, "-B", "-m", "nodeharbor.network_worker"]


def network_job(operation, payload, control=None, timeout=30):
    """Isolate DNS and streaming I/O so even slow-drip responses can be cancelled."""
    control = control or TaskControl()
    control.check()
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        source = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    with subprocess.Popen(worker_command(),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=NO_WINDOW, env=env) as process:
        output, _ = control.communicate(process, json.dumps({"operation": operation, "payload": payload}).encode(), timeout)
        if process.returncode: raise AppError("网络工作进程失败。")
    try:
        result = json.loads(output)
        if not result["ok"]:
            code = result.get('error_code', 'network')
            raise NetworkFailure(code if code in NETWORK_ERRORS else 'network', result.get('attempts', []))
        return result["result"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError("网络工作进程返回无效数据。") from exc


class NetworkPool:
    """Reusable, owned subprocesses; hard cancellation still interrupts DNS/I/O."""
    def __init__(self, workers, control):
        self.control = control
        self.slots = queue.Queue()
        self.workers = [None] * min(max(workers, 1), 24)
        for i in range(len(self.workers)): self.slots.put(i)

    def __enter__(self): return self

    def _start(self):
        env = os.environ.copy()
        if not getattr(sys, 'frozen', False):
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
        process = subprocess.Popen(worker_command() + ['--persistent'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=NO_WINDOW, env=env)
        replies = queue.Queue()
        def read():
            try:
                for line in process.stdout: replies.put(line)
            finally: replies.put(None)
        reader = threading.Thread(target=read, daemon=True); reader.start()
        return process, replies, reader

    @staticmethod
    def _stop(worker):
        if not worker: return
        process, _, reader = worker
        if process.poll() is None:
            process.terminate()
            try: process.wait(2)
            except subprocess.TimeoutExpired: process.kill(); process.wait(2)
        reader.join(2)
        for stream in (process.stdin, process.stdout):
            if stream: stream.close()

    def request(self, operation, payload, timeout=30, on_progress=None):
        while True:
            self.control.check()
            try: slot = self.slots.get(timeout=0.1); break
            except queue.Empty: pass
        try:
            if self.workers[slot] is None: self.workers[slot] = self._start()
            process, replies, _ = self.workers[slot]
            deadline = time.monotonic() + timeout
            process.stdin.write(json.dumps({'operation': operation, 'payload': payload}).encode() + b'\n')
            process.stdin.flush()
            while True:
                self.control.check()
                if time.monotonic() >= deadline: raise DeadlineExceeded('节点检测超过时限。')
                try: line = replies.get(timeout=0.1)
                except queue.Empty: pass
                else:
                    if line is None: raise AppError('网络工作进程提前退出。')
                    result = json.loads(line)
                    if 'event' in result:
                        if on_progress: on_progress(result['event'])
                        continue
                    break
            if not result['ok']:
                code = result.get('error_code', 'network')
                raise NetworkFailure(code if code in NETWORK_ERRORS else 'network', result.get('attempts', []))
            return result['result']
        except NetworkFailure: raise
        except BaseException:
            self._stop(self.workers[slot]); self.workers[slot] = None
            raise
        finally: self.slots.put(slot)

    def __exit__(self, *args):
        for worker in self.workers: self._stop(worker)


NETWORK_ERRORS = {'timeout': '下载或请求超时', 'proxy': '下载代理连接失败，请检查代理设置',
                  'tls': 'HTTPS 证书校验失败', 'http': '订阅服务器返回 HTTP 错误',
                  'connection': '网络或 DNS 连接失败', 'format': '响应格式无法解析',
                  'oversize': '订阅响应超过大小限制', 'network': '网络请求或响应解析失败'}
NETWORK_ERRORS.update(dns='DNS 解析失败', empty='源可连接，但没有可解析的节点')


class NetworkFailure(AppError):
    def __init__(self, code, attempts=None):
        self.code = code
        self.attempts = attempts or []
        super().__init__(NETWORK_ERRORS[code])


def parallel_map(function, values, workers, control):
    """Wait for owned jobs to unwind before releasing locks or closing the UI."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {}
    try:
        for index, value in enumerate(values):
            control.check()
            futures[pool.submit(function, value)] = index
        results = [None] * len(futures)
        for future in as_completed(futures):
            control.check()
            results[futures[future]] = future.result()
        return results
    except BaseException:
        control.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
