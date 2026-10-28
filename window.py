"""程序导航的窗口进程：由 app.py 按需启动，只负责显示窗口

关窗口时看设置：「隐藏时释放内存」打开 → 整个进程退出，WebView2 和 .NET 占的内存全部还给系统；
关闭 → 只是隐藏，下次瞬间打开。app.py 通过两个命名事件通知这里：显示窗口 / 退出。
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
if sys.stderr is None or Path(sys.executable).name.lower() == "pythonw.exe":
    sys.stdout = sys.stderr = open(BASE / "app.log", "a", encoding="utf-8", buffering=1)

import webview  # noqa: E402

import server  # noqa: E402

TITLE = "程序导航"
APP_ID = "ProgramNav.Desktop"
ICON_FILE = BASE / "app.ico"
EVENT_SHOW = "Local\\ProgramNav.Show"
EVENT_QUIT = "Local\\ProgramNav.Quit"
EVENT_PREVIEW = "Local\\ProgramNav.Preview"

user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.IsIconic.argtypes = user32.IsWindowVisible.argtypes = [w.HWND]
user32.GetForegroundWindow.restype = w.HWND
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.LoadImageW.restype = w.HANDLE
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
k32.CreateEventW.restype = w.HANDLE
k32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
k32.WaitForMultipleObjects.argtypes = [w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD]


class Win:
    def __init__(self):
        self.window = None
        self.quitting = False

    def hwnd(self):
        try:
            return self.window.native.Handle.ToInt64()
        except Exception:
            return None

    def set_icon(self):
        h = self.hwnd()
        if not h:
            return
        for kind, size in ((1, 32), (0, 16)):  # ICON_BIG / ICON_SMALL
            hicon = user32.LoadImageW(None, str(ICON_FILE), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if hicon:
                user32.SendMessageW(h, 0x0080, kind, hicon)  # WM_SETICON

    def save_bounds(self):
        try:
            win = self.window
            h = self.hwnd()
            if h and not user32.IsIconic(h):
                server.update_settings(bounds={"x": win.x, "y": win.y, "w": win.width, "h": win.height})
        except Exception:
            pass

    def on_closing(self):
        self.save_bounds()
        if self.quitting or server.load_settings().get("releaseOnHide", True):
            # 真的关掉：进程退出，内存全部释放。不等 WebView2 慢慢收尾，窗口消失后马上结束进程
            threading.Timer(0.3, os._exit, (0,)).start()
            return True
        threading.Thread(target=self.window.hide, daemon=True).start()
        return False  # 只隐藏

    def show(self):
        h = self.hwnd()
        if h and user32.IsIconic(h):
            self.window.restore()
        self.window.show()
        if h:
            server.force_foreground(h)

    def listen(self):
        """等 app.py 的通知：显示窗口 / 退出 / 打开窗口预览。事件名带上自己的 pid，旧窗口进程抢不走发给新窗口的通知"""
        pid = os.getpid()
        handles = (w.HANDLE * 3)(k32.CreateEventW(None, False, False, f"{EVENT_SHOW}.{pid}"),
                                 k32.CreateEventW(None, False, False, f"{EVENT_QUIT}.{pid}"),
                                 k32.CreateEventW(None, False, False, f"{EVENT_PREVIEW}.{pid}"))
        while True:
            r = k32.WaitForMultipleObjects(3, handles, False, 0xFFFFFFFF)
            if r == 0:
                self.show()
            elif r == 1:
                self.quitting = True
                self.window.destroy()
                return
            elif r == 2:
                h = self.hwnd()
                in_front = h and user32.IsWindowVisible(h) and not user32.IsIconic(h) \
                    and user32.GetForegroundWindow() == h
                self.show()
                try:  # 已经在最前面并且停在预览页 → 页面会把窗口收起来
                    self.window.evaluate_js(f"window.onAppGoto && window.onAppGoto('preview', {str(bool(in_front)).lower()})")
                except Exception:
                    pass

    def watch_backend(self):
        """主进程（后台服务）没了就自己退出，免得留下一个连不上后台的孤儿窗口"""
        import socket

        misses = 0
        while True:
            time.sleep(3)
            try:
                socket.create_connection((server.HOST, server.PORT), timeout=1).close()
                misses = 0
            except OSError:
                misses += 1
                if misses >= 2:
                    os._exit(0)

    def watch_visibility(self):
        """窗口隐藏/最小化时告诉页面，页面就暂停每 3 秒的进程检测"""
        last = None
        while True:
            time.sleep(0.5)
            h = self.hwnd()
            if not h:
                continue
            hidden = not user32.IsWindowVisible(h) or bool(user32.IsIconic(h))
            if hidden != last:
                last = hidden
                try:
                    self.window.evaluate_js(f"window.__appHidden = {str(hidden).lower()};"
                                            "if (!window.__appHidden && window.onAppShow) window.onAppShow()")
                except Exception:
                    last = None  # 页面还没加载好，下次再试


def fit_bounds(b):
    """窗口不能比屏幕可用区域大；保存的位置跑到屏幕外（比如换了显示器）就重新居中"""
    r = w.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA（不含任务栏）
    sw, sh = r.right - r.left, r.bottom - r.top
    width, height = min(b.get("w", 1280), sw), min(b.get("h", 820), sh)
    x, y = b.get("x"), b.get("y")
    if x is None or y is None or x < r.left or y < r.top or x + width > r.right or y + height > r.bottom:
        x, y = r.left + (sw - width) // 2, r.top + (sh - height) // 2
    return x, y, width, height


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    x, y, width, height = fit_bounds(server.load_settings().get("bounds") or {})
    app = Win()
    page = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--page=")), "")
    app.window = webview.create_window(
        TITLE, server.URL + (f"?page={page}" if page.isalnum() else ""), width=width, height=height, x=x, y=y,
        min_size=(720, 480), background_color="#f6f8fa",
    )
    app.window.events.closing += app.on_closing
    app.window.events.shown += app.set_icon
    threading.Thread(target=app.listen, daemon=True).start()
    threading.Thread(target=app.watch_visibility, daemon=True).start()
    threading.Thread(target=app.watch_backend, daemon=True).start()
    # 保留 localStorage（记住视图选择等）
    webview.start(private_mode=False, storage_path=str(BASE / ".webview"))
    # 窗口关了就立刻结束进程：正常退出时 Python/.NET 的清理要好几秒，这期间旧进程还占着内存，
    # 还会抢走发给新窗口的「显示」通知。数据都由主进程保存，这里没什么要清理的
    os._exit(0)


if __name__ == "__main__":
    main()
