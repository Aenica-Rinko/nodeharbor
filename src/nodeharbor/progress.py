"""Transient task state for rendering truthful stage progress on the Tk thread."""
import time

STAGES = {'search': '搜索仓库 / 查找文件', 'core': '检查核心', 'download': '下载 / 解析', 'precheck': '连通性预检',
          'protocol': 'HTTP / HTTPS 检测', 'export': '生成导出'}
TASKS = {'refresh-github': '从 GitHub 公开源获取节点', 'refresh': '更新节点', 'verify': '重新检测',
         'acquire': '从 GitHub 公开源获取节点', 'verify-library': '重新检测节点库',
         'export': '生成订阅文件', 'check-core': '检查核心', 'test-sources': '测试公开源下载',
         'search-sources': '搜索 GitHub 节点池'}


class TaskProgress:
    def __init__(self, action):
        self.action = action
        self.started = time.monotonic()
        self.ended = None
        self.status = 'running'
        self.stage = 'search' if action == 'search-sources' else 'download' if action == 'test-sources' else 'export' if action == 'export' else 'core'
        self.message = '正在准备任务'
        self.acquisition = {}
        self.steps = {key: {} for key in (['search'] if action == 'search-sources' else ['download'] if action == 'test-sources' else
                      ['export'] if action == 'export' else ['core'] if action == 'check-core' else
                      ['core', 'download', 'precheck', 'protocol'] if action == 'acquire' else
                      ['core', 'precheck', 'protocol'] if action == 'verify-library' else
                      ['core', 'precheck', 'protocol', 'export'] if action == 'verify' else
                      ['core', 'download', 'precheck', 'protocol', 'export'])}

    def update(self, event):
        stage = event['stage']
        if stage not in STAGES: return
        if stage != self.stage and self.steps.get(self.stage): self.steps[self.stage]['finished'] = True
        self.stage = stage
        self.message = event.get('message', '')
        self.steps[stage] = dict(self.steps.get(stage, {}), **event)
        if event.get('summary'): self.acquisition = dict(event['summary'])
        if event.get('sources') and stage != 'download' and self.steps.get('download'):
            self.steps['download']['sources'] = event['sources']

    def finish(self, status, message):
        self.status, self.message, self.ended = status, message, time.monotonic()
        if status == 'success':
            for value in self.steps.values():
                if value: value['finished'] = True

    @property
    def elapsed(self): return int((self.ended or time.monotonic()) - self.started)

    @property
    def counters(self):
        return {'parsed': self.steps.get('download', {}).get('parsed', 0),
                'prechecked': self.steps.get('precheck', {}).get('completed', 0),
                'unique': self.steps.get('download', {}).get('unique', self.steps.get('precheck', {}).get('candidates', 0)),
                'selected': self.acquisition.get('selected', self.steps.get('precheck', {}).get('candidates', 0)),
                'precheck_passed': self.steps.get('precheck', {}).get('tcp_passed', 0),
                'tested': self.steps.get('protocol', {}).get('tested', 0),
                'skipped': self.steps.get('protocol', {}).get('unsupported', self.steps.get('precheck', {}).get('unsupported', 0)),
                'stable': self.steps.get('protocol', {}).get('stable', 0),
                'passed': self.steps.get('protocol', {}).get('passed', 0)}
