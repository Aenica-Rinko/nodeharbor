"""Shared desktop styles and OS helpers."""
import os
import sys
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime
from ..client_exports import parse_timestamp

BG, WHITE, INK, MUTED, BLUE, LINE = "#F5F7FB", "#FFFFFF", "#172B4D", "#57657A", "#2859D8", "#DFE5EF"
DOWNLOADS = {'Mihomo': 'https://github.com/MetaCubeX/mihomo/releases',
             'Clash-Verge-Rev': 'https://github.com/clash-verge-rev/clash-verge-rev/releases',
             'v2rayN': 'https://github.com/2dust/v2rayN/releases'}


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


