"""Tk-independent worker lifecycle. Only queue events cross the UI boundary."""
import threading

from .storage import AppError
from .tasks import Cancelled, TaskControl


class TaskRunner:
    def __init__(self, events):
        self.events = events
        self.thread = None

    def start(self, operation):
        if self.thread and self.thread.is_alive():
            raise AppError('另一项任务尚未结束。')
        control = TaskControl(progress=lambda event: self.events.put(('progress', event)))
        self.thread = threading.Thread(target=self._work, args=(operation, control),
                                       name='nodeharbor-worker', daemon=False)
        self.thread.start()
        return control

    def _work(self, operation, control):
        try:
            result = operation(control)
            self.events.put(('done', result if isinstance(result, str) else '任务完成'))
        except Cancelled as error:
            self.events.put(('cancelled', str(error)))
        except Exception as error:
            self.events.put(('error', str(error) if isinstance(error, AppError) else f'{type(error).__name__}：执行失败'))
