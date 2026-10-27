"""程序导航 - 桌面版主进程：本地服务 + 托盘 + 全局快捷键

窗口在单独的进程里（window.py），需要时才启动。主进程不加载 pywebview/.NET，常驻时很省内存。
「隐藏时释放内存」打开时，关窗口 = 窗口进程退出；关闭时，关窗口只是隐藏，下次瞬间打开。
用法：.venv\\Scripts\\pythonw.exe app.py [--background]
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import winreg
from ctypes import wintypes as w
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # 打包成 exe 后：数据放在 exe 所在文件夹
BASE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
if sys.stderr is None or Path(sys.executable).name.lower() == "pythonw.exe":
    # pythonw 没有控制台，出错信息写到日志里方便排查
    sys.stdout = sys.stderr = open(BASE / "app.log", "a", encoding="utf-8", buffering=1)

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import server  # noqa: E402

TITLE = "程序导航"
ICON_FILE = BASE / "app.ico"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ProgramNav"
# 「启动」文件夹里的快捷方式：安装版会放一个；托盘里取消开机启动时一起删掉
STARTUP_LNK = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup" / "程序导航.lnk"
HOTKEY_TEXT = "Ctrl+Alt+Space"
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_SPACE, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x20, 0x0312
EVENT_SHOW = "Local\\ProgramNav.Show"  # 和 window.py 约定好的两个命名事件
EVENT_QUIT = "Local\\ProgramNav.Quit"

user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
for fn in ("IsIconic", "IsWindowVisible"):
    getattr(user32, fn).argtypes = [w.HWND]
user32.GetForegroundWindow.restype = w.HWND
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
k32.OpenEventW.restype = w.HANDLE
k32.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
k32.SetEvent.argtypes = k32.CloseHandle.argtypes = [w.HANDLE]
k32.OpenProcess.restype = w.HANDLE
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]


def make_icon():
    """画一个四宫格图标：托盘用 PIL 图片，窗口用 .ico"""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), 56, fill=(47, 111, 94, 255))
    for i, (x, y) in enumerate(((56, 56), (140, 56), (56, 140), (140, 140))):
        d.rounded_rectangle((x, y, x + 60, y + 60), 16, fill=(255, 255, 255, 255) if i < 3 else (150, 220, 195, 255))
    img.save(ICON_FILE, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return img


def find_window():
    """找窗口进程的主窗口（隐藏的也算）"""
    found = []
    proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(h, _):
        title, cls = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(h, title, 64)
        user32.GetClassNameW(h, cls, 64)
        if title.value == TITLE and cls.value.startswith("WindowsForms10"):
            found.append(h)
        return True

    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def window_pid(hwnd):
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def signal(name, hwnd):
    """通知某个窗口进程（每个窗口进程的事件名都带着自己的 pid）"""
    h = k32.OpenEventW(0x0002, False, f"{name}.{window_pid(hwnd)}")  # EVENT_MODIFY_STATE
    if h:
        k32.SetEvent(h)
        k32.CloseHandle(h)
    return bool(h)


class App:
    def __init__(self):
        self.tray = None
        self.hotkey_ok = True
        self.spawned_at = 0.0
        self.proc = None
        self.seen = False
        self.lock = threading.Lock()

    def show(self):
        with self.lock:
            h = find_window()
            if h:
                user32.AllowSetForegroundWindow(-1)  # 允许窗口进程把自己切到前台
                signal(EVENT_SHOW, h)
                return
            if self.proc and self.proc.poll() is None:
                if not self.seen and time.time() - self.spawned_at < 15:
                    return  # 窗口进程刚启动、窗口还没建出来（要一两秒），别重复启动
                try:
                    self.proc.wait(3)  # 窗口刚关、进程正在退出：等它退完再开新的
                except subprocess.TimeoutExpired:
                    pass
            self.spawn()

    def spawn(self):
        self.spawned_at, self.seen = time.time(), False
        user32.AllowSetForegroundWindow(-1)
        cmd = [sys.executable, "--window"] if FROZEN else [sys.executable, str(BASE / "window.py")]
        self.proc = subprocess.Popen(cmd, cwd=BASE)

        def watch():  # 记下窗口什么时候真的出来了
            while time.time() - self.spawned_at < 15 and self.proc.poll() is None:
                if find_window():
                    self.seen = True
                    return
                time.sleep(0.2)

        threading.Thread(target=watch, daemon=True).start()

    def hide(self):
        h = find_window()
        if h:
            user32.PostMessageW(h, 0x0010, 0, 0)  # 相当于点 ✕，窗口进程按设置决定退出还是隐藏

    def toggle(self):
        h = find_window()
        if h and user32.IsWindowVisible(h) and user32.GetForegroundWindow() == h and not user32.IsIconic(h):
            self.hide()
        else:
            self.show()

    def quit(self):
        h = find_window()
        if h and signal(EVENT_QUIT, h):
            for _ in range(30):  # 等窗口进程自己退出（会顺便保存窗口位置）
                time.sleep(0.1)
                if not find_window():
                    break
        h = find_window()
        if h:  # 还没退出就强制结束
            hp = k32.OpenProcess(0x0001, False, window_pid(h))
            if hp:
                k32.TerminateProcess(hp, 0)
                k32.CloseHandle(hp)
        if self.proc and self.proc.poll() is None:
            # 窗口进程还在启动、窗口没建出来：连同子进程一起结束（源码版是 venv 转发进程 + 真正的 python）
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.tray.stop()


def hotkey_loop(app):
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE):
        app.hotkey_ok = False
        print("快捷键注册失败，可能被其他程序占用了", HOTKEY_TEXT)
        return
    msg = w.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            app.toggle()


def autostart_cmd():
    # 开机后只进托盘、不弹窗口
    if FROZEN:
        return f'"{sys.executable}" --background'
    return f'"{BASE / ".venv" / "Scripts" / "pythonw.exe"}" "{BASE / "app.py"}" --background'


def autostart_enabled():
    if STARTUP_LNK.exists():  # 安装程序放在「启动」文件夹里的快捷方式
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    """写当前用户的 Run 注册表项（只影响自己的账户）"""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    STARTUP_LNK.unlink(missing_ok=True)  # 两种方式只留注册表一种，避免开机启动两次


def release_on_hide():
    return server.load_settings().get("releaseOnHide", True)


def logged(fn):
    """托盘菜单里的操作出错时记到 app.log，而不是悄悄失败"""
    def run():
        try:
            fn()
        except Exception:
            traceback.print_exc()
    return run


def notify_running_instance():
    """端口被占用：多半是已经开着了，让那个实例把窗口调出来"""
    try:
        req = urllib.request.Request(server.URL + "api/app/show", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        user32.MessageBoxW(None, f"端口 {server.PORT} 被占用了。\n可能是旧版的程序导航还开着，"
                                 "请在任务管理器里结束 pythonw.exe 后再试。", TITLE, 0x30)


def main():
    try:
        httpd = server.Server((server.HOST, server.PORT), server.Handler)
    except OSError:
        return notify_running_instance()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    app = App()
    server.on_app_show = app.show
    server.on_app_quit = app.quit
    image = make_icon()
    threading.Thread(target=hotkey_loop, args=(app,), daemon=True).start()
    app.tray = pystray.Icon("ProgramNav", image, f"{TITLE}（{HOTKEY_TEXT}）", pystray.Menu(
        pystray.MenuItem("打开程序导航", logged(app.show), default=True),
        pystray.MenuItem(lambda item: f"快捷键：{HOTKEY_TEXT}" if app.hotkey_ok else "快捷键被其他程序占用了",
                         None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("隐藏时释放内存（再打开要等 1~2 秒）",
                         logged(lambda: server.update_settings(releaseOnHide=not release_on_hide())),
                         checked=lambda item: release_on_hide()),
        pystray.MenuItem("开机自动启动", logged(lambda: set_autostart(not autostart_enabled())),
                         checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", logged(app.quit)),
    ))
    if "--background" not in sys.argv:
        app.show()
    app.tray.run()  # 一直运行，直到托盘菜单里点「退出」
    httpd.shutdown()
    os._exit(0)  # 不等后台线程（快捷键监听等），立刻退出


if __name__ == "__main__":
    main()
