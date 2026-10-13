"""Windowed entry point for the Windows application bundle."""
from nodeharbor.desktop import main
import sys


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-installation":
        from nodeharbor.install_check import check_installation
        raise SystemExit(check_installation(sys.argv[2]))
    try:
        main()
    except Exception as error:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("NodeHarbor 启动失败", f"{type(error).__name__}\n请完整解压发布包；若问题仍存在，请使用 Console 程序检查环境。")
        root.destroy()
        raise SystemExit(1)
