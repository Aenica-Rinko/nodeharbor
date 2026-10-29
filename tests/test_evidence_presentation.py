from nodeharbor.ui.presentation import source_evidence, node_evidence


def test_download_without_verification_is_not_recommended():
    assert '暂无本轮可用性结论' in source_evidence({'status': '下载成功', 'nodes': 200})
    assert '暂无本轮可用性结论' in source_evidence({'eligible': 0, 'verified': 0})


def test_source_explains_reuse_and_overlap_without_claiming_unique_success():
    text = source_evidence({'eligible': 10, 'verified': 4, 'reused': 3, 'unique': 100, 'exclusive': 20})
    assert '沿用历史 3 个' in text and '重复 80/100' in text
    assert '尚不等于独有可用节点' in text
    assert '全部节点均不可用' in source_evidence({'eligible': 10, 'verified': 0})


def test_node_preserves_unknown_and_historical_evidence():
    assert '轮数未记录' in node_evidence({}, {})
    text = node_evidence({'reused': True, 'rounds': 2, 'successful_rounds': 1}, {'recent': [True, False, True]})
    assert '本轮未重新探测' in text and '1/2 轮通过' in text and '2/3 轮通过' in text


def test_input_actions_preserves_buttons_when_resizing():
    import tkinter as tk
    import pytest
    from nodeharbor.ui.common import input_actions
    try: root = tk.Tk()
    except tk.TclError: pytest.skip('Display unavailable')
    try:
        entry, buttons = input_actions(root, tk.StringVar(), lambda parent, text, command: tk.Button(parent, text=text, command=command), [('Choose file', lambda: None, {})])
        for width in (600, 220, 600):
            root.geometry(f'{width}x240'); root.update()
            button = buttons[0]
            assert button.winfo_width() >= button.winfo_reqwidth()
            assert entry.winfo_width() > 30
            assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    finally: root.destroy()
