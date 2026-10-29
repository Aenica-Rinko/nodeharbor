"""Shared desktop styles and OS helpers."""
import os
import sys
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime
from ..client_exports import parse_timestamp
import tkinter as tk
from tkinter import ttk

BG, WHITE, INK, MUTED, BLUE, LINE = "#F5F7FB", "#FFFFFF", "#172B4D", "#57657A", "#2859D8", "#DFE5EF"
DOWNLOADS = {'Mihomo': 'https://github.com/MetaCubeX/mihomo/releases',
             'Clash-Verge-Rev': 'https://github.com/clash-verge-rev/clash-verge-rev/releases',
             'v2rayN': 'https://github.com/2dust/v2rayN/releases'}


def input_actions(parent, variable, make_button, actions, width=20):
    """Keep actions intact; move them below the input when horizontal space is scarce."""
    row = tk.Frame(parent, bg=parent.cget('bg'))
    row.pack(fill='x', expand=True)
    entry = ttk.Entry(row, textvariable=variable, width=width)
    buttons = [make_button(row, title, command, **options) for title, command, options in actions]
    row.grid_columnconfigure(0, weight=1)
    previous = None
    def layout(event):
        nonlocal previous
        required = sum(b.winfo_reqwidth() + 8 for b in buttons)
        compact = event.width < required + 160
        if compact == previous: return
        previous = compact
        for child in (entry, *buttons): child.grid_forget()
        if compact:
            entry.grid(row=0, column=0, sticky='ew', pady=(0, 6))
            for i, button in enumerate(buttons):
                button.grid(row=i + 1, column=0, sticky='w', pady=(0, 4))
        else:
            entry.grid(row=0, column=0, sticky='ew')
            for i, button in enumerate(buttons):
                button.grid(row=0, column=i + 1, padx=(8, 0))
    row.bind('<Configure>', layout)
    return entry, buttons


def display_time(value):
    if not value: return "尚未记录"
    try:
        if type(value) in (int, float): return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return parse_timestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError): return '时间记录无效'


def open_path(path):
    path = str(Path(path).resolve())
    if os.name == "nt": os.startfile(path)
    elif sys.platform == "darwin": subprocess.Popen(["open", path])
    else: subprocess.Popen(["xdg-open", path])


def open_help(paths):
    candidates = [paths.root / "README.md"]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).parent / "README.md")
    for path in candidates:
        if path.is_file():
            open_path(path)
            return
    webbrowser.open("https://github.com/Aenica-Rinko/nodeharbor#readme")


