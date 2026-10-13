"""Desktop entry point without a console window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
try:
    from nodeharbor.desktop import main
    main()
except Exception as exc:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw()
    messagebox.showerror("NodeHarbor 启动失败", f"{type(exc).__name__}\n请先运行 scripts/windows/setup.cmd 安装依赖。")
    root.destroy()
