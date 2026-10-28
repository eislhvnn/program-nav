"""程序导航 - 本地后台服务

只用 Python 标准库。数据保存在同目录的 programs.json。
"""
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
FROZEN = getattr(sys, "frozen", False)  # 打包成 exe 后运行
# BASE：数据目录（程序列表、设置、图标缓存……），打包后就是 exe 所在文件夹
# RES：只读资源（页面、Everything 的 DLL），打包后在 exe 旁边的 _internal 里
BASE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", BASE))
DATA_FILE = BASE / "programs.json"
CATS_FILE = BASE / "categories.json"  # 分类列表（顺序 = 显示顺序），空分类也能保留
SETTINGS_FILE = BASE / "settings.json"  # 桌面版的设置（隐藏时是否释放内存、窗口位置等）
PAGE_FILE = RES / "index.html"
URL = f"http://{HOST}:{PORT}/"
on_app_show = None  # 桌面版（app.py）运行时设置：再次启动时用来把已有窗口调出来
on_app_quit = None  # 同上：让正在运行的桌面版退出（升级/测试时用，代替托盘菜单的「退出」）

# 扫描开始菜单快捷方式，解析出真实路径
SCAN_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject WScript.Shell
$dirs = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
          "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
$out = foreach ($d in $dirs) {
  if (Test-Path $d) {
    Get-ChildItem $d -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
      try {
        $t = $shell.CreateShortcut($_.FullName).TargetPath
        if ($t -and $t.ToLower().EndsWith('.exe') -and (Test-Path $t)) {
          [pscustomobject]@{ name = $_.BaseName; path = $t; folder = $_.Directory.Name }
        }
      } catch {}
    }
  }
}
@($out) | ConvertTo-Json -Compress
"""


# Everything 搜索（通过官方 SDK 的 Everything64.dll，需要 Everything 正在运行）
EV_DLL = RES / "lib" / "Everything64.dll"
EV_FILTERS = {
    "program": "ext:exe;bat;cmd;py;pyw;ps1;vbs;ahk;jar;lnk;msc",
    "folder": "folder:",
    "all": "",
}
_ev = None


class EverythingError(Exception):
    pass


def everything():
    global _ev
    if _ev is None:
        import ctypes

        if not EV_DLL.exists():
            raise EverythingError("缺少 lib\\Everything64.dll")
        dll = ctypes.WinDLL(str(EV_DLL))
        dll.Everything_SetSearchW.argtypes = [ctypes.c_wchar_p]
        dll.Everything_GetResultFullPathNameW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32]
        for name in ("Everything_IsFolderResult", "Everything_QueryW"):
            getattr(dll, name).restype = ctypes.c_bool
        _ev = dll
    return _ev


def everything_exe():
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if root and (Path(root) / "Everything" / "Everything.exe").exists():
            return Path(root) / "Everything" / "Everything.exe"
    return None


def everything_query(dll, search, limit):
    dll.Everything_SetSearchW(search)
    dll.Everything_SetMatchPath(False)
    dll.Everything_SetMax(limit)
    dll.Everything_SetSort(1)  # EVERYTHING_SORT_NAME_ASCENDING
    dll.Everything_SetRequestFlags(0x4)  # EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
    if dll.Everything_QueryW(True):
        return True
    if dll.Everything_GetLastError() != 2:  # 2 = EVERYTHING_ERROR_IPC，即没在运行
        raise EverythingError(f"Everything 查询失败（错误码 {dll.Everything_GetLastError()}）")
    return False


def everything_search(query, kind="program", limit=200):
    import ctypes
    import time

    dll = everything()
    search = f"{EV_FILTERS.get(kind, '')} {query}".strip()
    if not everything_query(dll, search, limit):
        exe = everything_exe()
        if not exe:
            raise EverythingError("not_installed")
        # 已安装但没运行：自动在后台启动，等它就绪
        subprocess.Popen([str(exe), "-startup"])
        for _ in range(20):
            time.sleep(0.5)
            if everything_query(dll, search, limit):
                break
        else:
            raise EverythingError("Everything 启动超时，请手动打开一次 Everything")
    buf = ctypes.create_unicode_buffer(32768)
    results = []
    for i in range(dll.Everything_GetNumResults()):
        dll.Everything_GetResultFullPathNameW(i, buf, len(buf))
        results.append({"path": buf.value, "folder": dll.Everything_IsFolderResult(i)})
    return results


# 一键安装 Everything：用程序自带的安装包（没有或签名不对时才去官网下载）→ 校验签名
# → 静默安装（弹一次管理员确认）→ 启动
EV_SITE = "https://www.voidtools.com"
EV_FALLBACK = "/Everything-1.4.1.1032.x64-Setup.exe"
EV_BUNDLED = BASE / "redist" / "Everything-Setup.exe"  # 安装包里自带的官方原版，装完也不删
EV_STALL = 20  # 连续这么多秒没收到数据就算卡住
EV_MAX_TIME = 300  # 整个下载最多 5 分钟
# pct：下载进度 0~100（不知道总大小时为 None）；cancel：页面点了「取消下载」
install_state = {"step": "idle", "msg": "", "pct": None, "cancel": False}


class InstallCancelled(Exception):
    pass


def download_with_progress(url, dest, state=None):
    """分块下载，把进度写进 state（默认是 Everything 安装的状态）；卡住、超时或取消时删掉下了一半的文件"""
    import time
    import urllib.request

    state = install_state if state is None else state
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=EV_STALL) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, start = 0, time.time()
            while chunk := r.read(64 * 1024):  # 超过 EV_STALL 秒没数据会抛 TimeoutError
                if state["cancel"]:
                    raise InstallCancelled
                if time.time() - start > EV_MAX_TIME:
                    raise TimeoutError("total")
                f.write(chunk)
                done += len(chunk)
                mb = f"{done / 1048576:.1f}" + (f" / {total / 1048576:.1f}" if total else "") + " MB"
                state.update(msg=f"正在下载 {dest.name}　{mb}", pct=done * 100 // total if total else None)
        if total and done < total:
            raise ConnectionError("下载不完整，连接中途断开了")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def install_everything():
    import re
    import tempfile
    import urllib.error
    import urllib.request

    def step(s, msg):
        install_state.update(step=s, msg=msg, pct=None)

    def signature(file):
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$s = Get-AuthenticodeSignature -LiteralPath '{file}'; \"$($s.Status)|$($s.SignerCertificate.Subject)\""],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.strip()

    def official(sig):
        return sig.startswith("Valid|") and "voidtools" in sig

    try:
        setup = None
        if EV_BUNDLED.exists():
            step("verify", "正在校验程序自带的安装包…")
            if official(signature(EV_BUNDLED)):
                setup = EV_BUNDLED
        if setup is None:  # 自带的没了（比如被杀毒软件删掉）或者被改过：去官网下载
            step("download", "正在获取最新版本…")
            try:
                page = urllib.request.urlopen(EV_SITE + "/downloads/", timeout=15).read().decode("utf-8", "replace")
                href = re.search(r'href="(/Everything-[\d.]+\.x64-Setup\.exe)"', page).group(1)
            except Exception:
                href = EV_FALLBACK
            setup = Path(tempfile.gettempdir()) / href.lstrip("/")
            step("download", f"正在下载 {setup.name} …")
            try:
                download_with_progress(EV_SITE + href, setup)
            except InstallCancelled:
                return step("error", "已取消下载，下了一半的文件已删除")
            except TimeoutError as e:
                why = "超过 5 分钟还没下完" if str(e) == "total" else f"{EV_STALL} 秒没有收到数据"
                return step("error", f"下载超时：{why}，下了一半的文件已删除。请检查网络后重试")
            except urllib.error.URLError as e:
                return step("error", f"连不上 voidtools 官网（{e.reason}），请检查网络后重试")

            step("verify", "正在校验官方签名…")
            sig = signature(setup)
            if not official(sig):
                setup.unlink(missing_ok=True)
                return step("error", f"安装包签名校验没通过，已删除（{sig or '无签名'}）")

        step("install", "请在弹出的窗口中点「是」，正在安装…")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process -FilePath '{setup}' -ArgumentList '/S' -Verb RunAs -Wait"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if setup != EV_BUNDLED:
            setup.unlink(missing_ok=True)
        exe = everything_exe()
        if r.returncode != 0 or not exe:
            return step("error", "安装被取消或失败。需要在弹出的管理员确认窗口中点「是」。")

        step("start", "安装完成，正在启动 Everything…")
        subprocess.Popen([str(exe), "-startup"])
        step("done", "Everything 已安装并启动")
    except Exception as e:
        step("error", f"安装失败：{e}")


# 程序内更新：从 GitHub 仓库最新的 Release 下载安装包 → 静默安装 → 安装程序自动把它重新打开
# 仓库是公开的，匿名访问 GitHub 就行，不需要登录（匿名每个 IP 每小时 60 次，手动检查更新足够）
GITHUB_REPO = "eislhvnn/program-nav"
VERSION_FILE = RES / "version.txt"
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "0"
update_state = {"step": "idle", "msg": "", "pct": None, "cancel": False}


class UpdateError(Exception):
    pass


def github_get(path):
    import urllib.request

    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "program-nav",
        "X-GitHub-Api-Version": "2022-11-28"})
    return urllib.request.urlopen(req, timeout=15)


def version_key(v):
    import re

    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def latest_release():
    """返回 {latest, newer, notes, date, asset}；失败时抛 UpdateError，消息直接给用户看"""
    import urllib.error

    try:
        rel = json.load(github_get(f"/repos/{GITHUB_REPO}/releases/latest"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise UpdateError("检查得太频繁，被 GitHub 暂时限制了，过一小时再试")
        if e.code == 404:
            raise UpdateError("没有找到发布的版本：仓库里还没有 Release，或者仓库不是公开的")
        raise UpdateError(f"GitHub 返回错误 {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise UpdateError(f"连不上 GitHub（{getattr(e, 'reason', e)}），请检查网络")
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    latest = rel["tag_name"].lstrip("vV")
    return {"latest": latest, "newer": bool(asset) and version_key(latest) > version_key(APP_VERSION),
            "notes": rel.get("body") or "", "date": (rel.get("published_at") or "")[:10], "asset": asset}


def run_update():
    import tempfile
    import urllib.error

    def step(s, msg):
        update_state.update(step=s, msg=msg, pct=None)

    try:
        step("download", "正在获取最新版本…")
        rel = latest_release()
        asset = rel["asset"]
        if not rel["newer"]:
            return step("error", "已经是最新版本，不需要更新")
        setup = Path(tempfile.gettempdir()) / asset["name"]
        try:
            download_with_progress(asset["browser_download_url"], setup, update_state)
        except InstallCancelled:
            return step("error", "已取消下载，下了一半的文件已删除")
        except TimeoutError:
            return step("error", "下载超时，下了一半的文件已删除。请检查网络后重试")
        except urllib.error.URLError as e:
            return step("error", f"下载失败（{e.reason}），请检查网络后重试")
        if setup.stat().st_size != asset["size"]:
            setup.unlink(missing_ok=True)
            return step("error", "下载的文件大小不对，已删除，请重试")

        step("install", f"下载完成，正在安装 {rel['latest']}。程序导航会自动关闭，装好后自动重新打开")
        # 用 cmd 的 start 转一手，让安装程序不在本程序的进程树里：安装程序会结束本程序及其子进程，否则会把自己也结束掉
        subprocess.Popen(["cmd", "/c", "start", "", str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                          "/CLOSEAPPLICATIONS", "/NORESTART", "/RELAUNCH=1"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except UpdateError as e:
        step("error", str(e))
    except Exception as e:
        step("error", f"更新失败：{e}")


# 从文件里读取 Windows 图标，缓存成 PNG
ICON_DIR = BASE / "icons"
_icon_api = None


def icon_api():
    global _icon_api
    if _icon_api is None:
        import ctypes
        from ctypes import wintypes as w

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", w.DWORD),
                        ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                        ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]

        class BITMAP(ctypes.Structure):
            _fields_ = [("bmType", w.LONG), ("bmWidth", w.LONG), ("bmHeight", w.LONG), ("bmWidthBytes", w.LONG),
                        ("bmPlanes", w.WORD), ("bmBitsPixel", w.WORD), ("bmBits", ctypes.c_void_p)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                        ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                        ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
                        ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]

        shell32, user32, gdi32 = ctypes.windll.shell32, ctypes.windll.user32, ctypes.windll.gdi32
        shell32.SHGetFileInfoW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.UINT, w.UINT]
        shell32.SHGetFileInfoW.restype = ctypes.c_size_t
        user32.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.UINT, w.UINT,
                                    ctypes.c_void_p, ctypes.c_void_p, w.UINT]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        ctypes.windll.ole32.CoInitialize(None)
        _icon_api = (ctypes, SHFILEINFOW, ICONINFO, BITMAP, BITMAPINFOHEADER, shell32, user32, gdi32)
    return _icon_api


def write_png(file, width, height, rgba):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    stride = width * 4
    raw = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    file.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def extract_icon(path, out, by_type):
    """by_type=True 时按扩展名取类型图标（文件不存在也行）"""
    ctypes, SHFILEINFOW, ICONINFO, BITMAP, BITMAPINFOHEADER, shell32, user32, gdi32 = icon_api()
    info = SHFILEINFOW()
    flags = 0x100 | (0x10 if by_type else 0)  # SHGFI_ICON | SHGFI_LARGEICON (| SHGFI_USEFILEATTRIBUTES)
    if not shell32.SHGetFileInfoW(path, 0x80, ctypes.byref(info), ctypes.sizeof(info), flags) or not info.hIcon:
        return False
    ii = ICONINFO()
    user32.GetIconInfo(info.hIcon, ctypes.byref(ii))
    hdc = user32.GetDC(None)
    try:
        if not ii.hbmColor:
            return False
        bm = BITMAP()
        gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
        width, height = bm.bmWidth, bm.bmHeight

        def pixels(hbm):
            bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0)  # 自上而下
            buf = ctypes.create_string_buffer(width * height * 4)
            gdi32.GetDIBits(hdc, hbm, 0, height, buf, ctypes.byref(bih), 0)
            return bytearray(buf.raw)

        px = pixels(ii.hbmColor)
        if not any(px[3::4]):  # 老式图标没有透明通道，用遮罩补上
            mask = pixels(ii.hbmMask) if ii.hbmMask else bytearray(len(px))
            px[3::4] = bytes(0 if m else 255 for m in mask[0::4])
        px[0::4], px[2::4] = px[2::4], px[0::4]  # BGRA -> RGBA
        write_png(out, width, height, bytes(px))
        return True
    finally:
        user32.ReleaseDC(None, hdc)
        for h in (ii.hbmColor, ii.hbmMask):
            if h:
                gdi32.DeleteObject(h)
        user32.DestroyIcon(info.hIcon)


# 自定义图标：icons/custom_<条目 id>.png
CUSTOM_ICON_MAX = 4 * 2**20
IMAGE_MAGIC = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif", b"BM": ".bmp", b"\x00\x00\x01\x00": ".ico"}
ICON_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp",
              ".ico": "image/x-icon", ".webp": "image/webp"}


def custom_icon_base(item_id):
    if not item_id or not all(c.isalnum() or c == "-" for c in item_id) or len(item_id) > 64:
        raise ValueError("条目 id 不合法")
    return f"custom_{item_id}"


def save_custom_icon(item_id, data_url):
    """保存图片（dataURL）为图标，返回文件名；能用 Pillow 就统一缩成 128×128 的 PNG"""
    import base64
    import io

    base = custom_icon_base(item_id)
    raw = base64.b64decode(data_url.split(",", 1)[-1])
    if len(raw) > CUSTOM_ICON_MAX:
        raise ValueError("图片太大了（超过 4 MB）")
    # 先确认新图片能用，再删旧的，失败时不丢原来的图标
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)  # 动图只取第一帧
        img = img.convert("RGBA")
        img.thumbnail((128, 128), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG")
        name, body = base + ".png", out.getvalue()
    except ImportError:
        ext = next((e for magic, e in IMAGE_MAGIC.items() if raw.startswith(magic)), None)
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = ".webp"
        if not ext:
            raise ValueError("不支持这种图片格式")
        name, body = base + ext, raw
    except Exception:
        raise ValueError("读不出这张图片，换一张试试")
    delete_custom_icon(item_id)
    ICON_DIR.mkdir(exist_ok=True)
    (ICON_DIR / name).write_bytes(body)
    return name


def delete_custom_icon(item_id):
    base = custom_icon_base(item_id)
    for f in ICON_DIR.glob(base + ".*") if ICON_DIR.exists() else []:
        f.unlink(missing_ok=True)


def icon_name(path):
    import hashlib

    return hashlib.md5(path.lower().encode("utf-8")).hexdigest() + ".png"


def ensure_icons(paths):
    """返回 {路径: 图标文件名}，没缓存的就提取一次"""
    ICON_DIR.mkdir(exist_ok=True)
    for p in paths:
        out = ICON_DIR / icon_name(p)
        if out.exists():
            continue
        try:
            if Path(p).exists():
                extract_icon(p, out, False)
            elif Path(p).suffix and Path(p).suffix.lower() != ".exe":
                # 文件不存在：按扩展名取类型图标（如 .py 的 Python 图标）；不存在的 exe 只会得到通用图标，跳过
                extract_icon(p, out, True)
        except Exception:
            pass
    return {p: icon_name(p) for p in paths if (ICON_DIR / icon_name(p)).exists()}


# 进程检测：列出所有进程的程序路径和命令行
SCRIPT_EXTS = {".py", ".pyw", ".bat", ".cmd", ".ps1", ".ahk", ".vbs", ".jar"}
INTERPRETERS = ("python", "py.exe", "pyw.exe", "cmd.exe", "powershell", "pwsh", "autohotkey",
                "wscript", "cscript", "java")
_proc_api = None


def proc_api():
    global _proc_api
    if _proc_api is None:
        import ctypes
        from ctypes import wintypes as w

        k32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        k32.K32EnumProcesses.argtypes = [ctypes.POINTER(w.DWORD), w.DWORD, ctypes.POINTER(w.DWORD)]
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, w.UINT]
        ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                    w.ULONG, ctypes.POINTER(w.ULONG)]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        _proc_api = (ctypes, w, k32, ntdll)
    return _proc_api


def list_processes():
    """返回 [(pid, 程序路径小写, 命令行小写)]，读不到的进程跳过"""
    ctypes, w, k32, ntdll = proc_api()
    pids = (w.DWORD * 8192)()
    needed = w.DWORD()
    k32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed))
    name = ctypes.create_unicode_buffer(1024)
    result = []
    for pid in pids[: needed.value // ctypes.sizeof(w.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            size = w.DWORD(len(name))
            exe = name.value.lower() if k32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(size)) else ""
            # ProcessCommandLineInformation = 60（Win 8.1+），返回 UNICODE_STRING + 字符串
            cmd, buf_len, ret = "", 2048, w.ULONG()
            for _ in range(2):
                buf = ctypes.create_string_buffer(buf_len)
                status = ntdll.NtQueryInformationProcess(h, 60, buf, buf_len, ctypes.byref(ret))
                if status == 0:
                    length = int.from_bytes(buf.raw[:2], "little")
                    ptr = ctypes.c_void_p.from_buffer(buf, ctypes.sizeof(ctypes.c_void_p)).value
                    cmd = ctypes.wstring_at(ptr, length // 2).lower() if ptr else ""
                    break
                buf_len = ret.value  # 缓冲区不够大，按返回的长度重试
            if exe and pid != os.getpid():  # 不把自己算进去
                result.append((pid, exe, cmd))
        finally:
            k32.CloseHandle(h)
    return result


def proc_index(procs):
    """先把进程整理成索引，每个条目直接查表，不用逐个比对全部进程：
    exe 按路径放进字典；脚本只需要在解释器进程（通常就几个）里找"""
    by_exe, interp = {}, []
    for pid, exe, cmd in procs:
        by_exe.setdefault(exe, []).append(pid)
        if os.path.basename(exe).startswith(INTERPRETERS):
            interp.append((pid, cmd))
    return by_exe, interp


_bat_cache = {}  # bat 路径 → (修改时间, 它启动的程序/脚本)


def bat_targets(path):
    """启动器类的 bat 用 start 拉起别的程序后自己就退出了，进程里找不到它。
    读 bat 的内容，找出里面写到的、确实存在的脚本和 exe（%~dp0 和相对路径按 bat 所在文件夹算）。
    解释器本身（python.exe 之类）不算，否则所有用它跑的脚本都会被算进来"""
    import re

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _bat_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    raw = Path(path).read_bytes()[:65536]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("mbcs", "replace")  # 中文系统下 bat 常是 GBK
    folder = os.path.dirname(path)
    targets = set()
    for line in text.splitlines():
        if line.lstrip(" \t@").lower().startswith(("rem ", "::", "echo")):  # 注释和提示文字里提到的不算
            continue
        line = re.sub(r"%~dp0\\?", lambda _: folder + "\\", line, flags=re.I)
        for quoted, bare in re.findall(r'"([^"]+)"|([^\s"]+)', line):
            token = (quoted or bare).strip()
            ext = os.path.splitext(token)[1].lower()
            if ext != ".exe" and ext not in SCRIPT_EXTS:
                continue
            full = os.path.normpath(os.path.join(folder, token)).lower()
            if (full != path and os.path.isfile(full)
                    and not (ext == ".exe" and os.path.basename(full).startswith(INTERPRETERS))):
                targets.add(full)
    _bat_cache[path] = (mtime, sorted(targets))
    return _bat_cache[path][1]


_lnk_cache = {}  # lnk 路径 → (修改时间, (目标路径, 参数, 起始位置))


def lnk_target(path):
    """直接读 .lnk 文件（格式是公开的 MS-SHLLINK），返回 (目标路径, 参数, 起始位置)，读不出目标时目标为空。
    MSI 的“通告式”快捷方式（部分 Office 等）里没有真实路径，读不出来"""
    import struct

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return "", "", ""
    cached = _lnk_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    target = args = workdir = ""
    try:
        d = Path(path).read_bytes()
        flags = struct.unpack_from("<I", d, 0x14)[0]
        unicode = flags & 0x80
        pos = 0x4C
        idlist = None
        if flags & 0x01:  # HasLinkTargetIDList：先记下来，没有别的路径信息时再用
            n = struct.unpack_from("<H", d, pos)[0]
            idlist = d[pos + 2: pos + 2 + n]
            pos += 2 + n
        if flags & 0x02:  # HasLinkInfo：本地路径 = LocalBasePath + CommonPathSuffix
            size, head, li_flags, _, base_off, _, suffix_off = struct.unpack_from("<7I", d, pos)
            if li_flags & 0x01:
                if head >= 0x24:  # 有 Unicode 版本就用它
                    base_u, suffix_u = struct.unpack_from("<2I", d, pos + 28)
                    target = (_wstr(d, pos + base_u) + _wstr(d, pos + suffix_u))
                else:
                    target = (_astr(d, pos + base_off) + _astr(d, pos + suffix_off))
            pos += size
        strings = []
        for bit in (0x04, 0x08, 0x10, 0x20):  # 名称、相对路径、起始位置、参数
            if flags & bit:
                n = struct.unpack_from("<H", d, pos)[0]
                pos += 2
                raw = d[pos: pos + n * (2 if unicode else 1)]
                pos += len(raw)
                strings.append((bit, raw.decode("utf-16-le" if unicode else "mbcs", "replace")))
            else:
                strings.append((bit, ""))
        s = dict(strings)
        workdir, args = s[0x10], s[0x20]
        if flags & 0x40:  # 图标位置，跳过
            pos += 2 + struct.unpack_from("<H", d, pos)[0] * (2 if unicode else 1)
        while not target and pos + 8 <= len(d):  # 额外数据块：路径里带 %环境变量% 的放在这里
            bsize, sig = struct.unpack_from("<2I", d, pos)
            if bsize < 8:
                break
            if sig == 0xA0000001:
                target = _wstr(d, pos + 8 + 260)
            pos += bsize
        if not target and idlist:  # 只存了 Windows 的项目标识（ID 列表），让系统换算成路径
            import ctypes

            buf = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.shell32.SHGetPathFromIDListW(ctypes.c_char_p(idlist), buf):
                target = buf.value
        if not target and s[0x08]:  # 只剩相对路径（相对快捷方式所在文件夹）
            target = os.path.join(os.path.dirname(path), s[0x08])
        target = os.path.normpath(os.path.expandvars(target)) if target else ""
        workdir = os.path.expandvars(workdir)
    except (OSError, struct.error, ValueError):
        pass
    _lnk_cache[path] = (mtime, (target, args, workdir))
    return _lnk_cache[path][1]


def _wstr(d, pos):
    end = pos
    while d[end: end + 2] != b"\0\0":
        end += 2
    return d[pos:end].decode("utf-16-le", "replace")


def _astr(d, pos):
    return d[pos: d.index(b"\0", pos)].decode("mbcs", "replace")


def lnk_match_paths(path):
    """快捷方式要按哪些路径去匹配进程。目标是解释器（python.exe xxx.py）时按参数里的脚本匹配，
    否则所有用这个解释器跑的程序都会被算进来"""
    import shlex

    target, args, workdir = lnk_target(path)
    if not target:
        return []
    if os.path.basename(target).lower().startswith(INTERPRETERS) and args:
        base = workdir or os.path.dirname(target)
        scripts = []
        for token in shlex.split(args, posix=False):
            token = token.strip('"')
            if os.path.splitext(token)[1].lower() in SCRIPT_EXTS:
                scripts.append(os.path.normpath(os.path.join(base, token)))
        if scripts:
            return scripts
    return [target]


def matching_pids(item, index, _seen=None):
    """这个条目对应的进程 pid 列表"""
    by_exe, interp = index
    path = item["path"].lower()
    ext = os.path.splitext(path)[1]
    if ext == ".exe":
        return by_exe.get(path, [])
    if ext in SCRIPT_EXTS:
        fname = os.path.basename(path)
        needles = (path, f" {fname}", f'"{fname}', f"\\{fname}", f"/{fname}")
        # 只看解释器进程（避免把“用编辑器打开着”算成运行），命令行里有完整路径或文件名即可
        pids = {pid for pid, cmd in interp if any(n in cmd for n in needles)}
        if ext in (".bat", ".cmd"):  # 再加上 bat 启动的程序（可能 bat 自己早就退出了）
            _seen = (_seen or set()) | {path}
            for t in bat_targets(path):
                if t not in _seen:
                    pids.update(matching_pids({"path": t}, index, _seen))
        return sorted(pids)
    if ext == ".lnk":  # 快捷方式：按它指向的程序/脚本匹配
        _seen = (_seen or set()) | {path}
        pids = set()
        for t in lnk_match_paths(path):
            if t.lower() not in _seen:
                pids.update(matching_pids({"path": t}, index, _seen))
        return sorted(pids)
    return []


def running_counts(items):
    """返回 {条目 id: 进程数}，只包含正在运行的"""
    index = proc_index(list_processes())
    counts = {}
    for it in items:
        n = len(matching_pids(it, index))
        if n:
            counts[it["id"]] = n
    return counts


def close_item(item, force):
    """正常关闭：给它的窗口发 WM_CLOSE（和点 ✕ 一样）；强制：TerminateProcess。返回剩余进程数"""
    import time

    ctypes, w, k32, _ = proc_api()
    pids = set(matching_pids(item, proc_index(list_processes())))
    if not pids:
        return 0
    if force:
        for pid in pids:
            h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if h:
                k32.TerminateProcess(h, 1)
                k32.CloseHandle(h)
    else:
        user32 = ctypes.windll.user32
        proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user32.EnumWindows.argtypes = [proto, w.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user32.IsWindowVisible.argtypes = [w.HWND]
        user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]

        def on_window(hwnd, _):
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True

        user32.EnumWindows(proto(on_window), 0)
    # 等它们退出，最多 3 秒
    for _ in range(15):
        time.sleep(0.2)
        left = matching_pids(item, proc_index(list_processes()))
        if not left:
            return 0
    return len(left)


# 「打开位置」：资源管理器窗口是 explorer 自己的进程建的，Windows 防抢焦点会让它开在后面，
# 所以打开后找到那个窗口，主动把它拉到最前面
def explorer_windows():
    import ctypes
    from ctypes import wintypes as w

    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(h, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(260)
        user32.GetClassNameW(h, cls, 64)
        if cls.value == "CabinetWClass":
            user32.GetWindowTextW(h, title, 260)
            found.append((h, title.value))
        return True

    user32.EnumWindows.argtypes = [proto, w.LPARAM]
    user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user32.EnumWindows(proto(cb), 0)
    return found


def force_foreground(hwnd):
    import ctypes
    from ctypes import wintypes as w

    user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    for fn in ("IsIconic", "ShowWindow", "BringWindowToTop", "SetForegroundWindow", "SetWindowPos"):
        getattr(user32, fn).argtypes = [w.HWND] + {"ShowWindow": [ctypes.c_int],
                                                   "SetWindowPos": [w.HWND, ctypes.c_int, ctypes.c_int,
                                                                    ctypes.c_int, ctypes.c_int, w.UINT]}.get(fn, [])
    user32.GetForegroundWindow.restype = w.HWND
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.c_void_p]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # 临时把输入挂到当前前台窗口的线程上，Windows 才允许切换前台
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = k32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    # 万一还是没到前台，至少把它叠到最上面（置顶再取消置顶）
    if user32.GetForegroundWindow() != hwnd:
        flags = 0x0001 | 0x0002 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)


def open_location(p):
    """打开文件所在文件夹并选中它（文件夹就直接打开），返回 False 表示路径不存在"""
    import ctypes
    import time

    if p.is_dir():
        args, title = ["explorer", str(p)], p.name or str(p)
    elif p.exists():
        args, title = f'explorer /select,"{p}"', p.parent.name
    elif p.parent.exists():
        args, title = ["explorer", str(p.parent)], p.parent.name
    else:
        return False
    before = {h for h, _ in explorer_windows()}
    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY：我们在前台时（桌面版）直接放行
    subprocess.Popen(args)

    def bring_front():
        # 等新窗口出来；如果那个文件夹本来就开着，explorer 可能复用旧窗口，就按标题找
        for k in range(40):
            time.sleep(0.1)
            wins = explorer_windows()
            new = [h for h, _ in wins if h not in before]
            same = [h for h, t in wins if t == title] if k >= 15 else []
            if new or same:
                force_foreground((new or same)[0])
                return

    threading.Thread(target=bring_front, daemon=True).start()
    return True


def load_json(file):
    if file.exists():
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return []


def save_json(file, data):
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file)


def load_settings():
    data = load_json(SETTINGS_FILE)
    return data if isinstance(data, dict) else {}


def update_settings(**changes):
    save_json(SETTINGS_FILE, {**load_settings(), **changes})


def load_items():
    return load_json(DATA_FILE)


def save_items(items):
    save_json(DATA_FILE, items)


def browse(kind):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if kind == "folder":
        path = filedialog.askdirectory(parent=root, title="选择文件夹")
    else:
        path = filedialog.askopenfilename(parent=root, title="选择程序或文件")
    root.destroy()
    return os.path.normpath(path) if path else ""


def scan_start_menu():
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", SCAN_SCRIPT],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    seen, items = set(), []
    for d in data:
        key = d["path"].lower()
        if key not in seen:
            seen.add(key)
            items.append(d)
    items.sort(key=lambda d: d["name"].lower())
    return items


class Server(HTTPServer):
    """独占端口：HTTPServer 默认开 SO_REUSEADDR，在 Windows 上会让第二个实例也绑定成功"""
    allow_reuse_address = False

    def server_bind(self):
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def host_ok(self):
        """防 DNS 重绑定：恶意网站把自己的域名解析到 127.0.0.1 后，浏览器会把它当成同源，
        能读到程序列表。这种请求的 Host 是对方的域名，只认我们自己的地址就挡住了"""
        if self.headers.get("Host") in (f"{HOST}:{PORT}", f"localhost:{PORT}"):
            return True
        self.send_json({"error": "forbidden"}, 403)
        return False

    def do_GET(self):
        if not self.host_ok():
            return
        if self.path in ("/", "/index.html"):
            body = PAGE_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/items":
            self.send_json(load_items())
        elif self.path == "/api/cats":
            self.send_json(load_json(CATS_FILE))
        elif self.path.startswith("/icons/"):
            name = self.path[len("/icons/"):].split("?")[0]
            f = ICON_DIR / name
            stem, ext = os.path.splitext(name)
            auto = len(stem) == 32 and ext == ".png" and all(c in "0123456789abcdef" for c in stem)
            custom = stem.startswith("custom_") and ext in ICON_TYPES and all(c.isalnum() or c in "-_" for c in stem)
            if (auto or custom) and f.exists():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ICON_TYPES[ext])
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json({"error": "not found"}, 404)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.host_ok():
            return
        # 只接受本页面发来的请求，防止其他网页调用（浏览器跨站发 POST 时一定会带 Origin）
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") != URL.rstrip("/"):
            return self.send_json({"error": "forbidden"}, 403)
        try:
            data = self.read_json()
            if self.path == "/api/items":
                save_items(data)
                self.send_json({"ok": True})
            elif self.path == "/api/cats":
                save_json(CATS_FILE, [c for c in data if isinstance(c, str) and c.strip()])
                self.send_json({"ok": True})
            elif self.path == "/api/open-folder":
                if not open_location(Path(data["path"])):
                    return self.send_json({"error": "路径不存在"}, 400)
                self.send_json({"ok": True})
            elif self.path == "/api/run":
                p = Path(data["path"])
                if not p.exists():
                    return self.send_json({"error": "路径不存在"}, 400)
                os.startfile(str(p))
                self.send_json({"ok": True})
            elif self.path == "/api/exists":
                self.send_json({p: Path(p).exists() for p in data.get("paths", [])})
            elif self.path == "/api/app/show":
                if not on_app_show:
                    return self.send_json({"error": "not app mode"}, 404)
                threading.Thread(target=on_app_show, daemon=True).start()
                self.send_json({"ok": True})
            elif self.path == "/api/app/quit":
                if not on_app_quit:
                    return self.send_json({"error": "not app mode"}, 404)
                threading.Thread(target=on_app_quit, daemon=True).start()
                self.send_json({"ok": True})
            elif self.path == "/api/running":
                self.send_json(running_counts(load_items()))
            elif self.path == "/api/close":
                item = next((i for i in load_items() if i["id"] == data.get("id")), None)
                if not item:
                    return self.send_json({"error": "找不到这个条目"}, 404)
                self.send_json({"left": close_item(item, bool(data.get("force")))})
            elif self.path == "/api/custom-icon":
                try:
                    if data.get("data"):
                        self.send_json({"file": save_custom_icon(data.get("id"), data["data"])})
                    else:
                        delete_custom_icon(data.get("id"))
                        self.send_json({"ok": True})
                except ValueError as e:
                    self.send_json({"error": str(e)}, 400)
            elif self.path == "/api/icons":
                self.send_json(ensure_icons(data.get("paths", [])))
            elif self.path == "/api/browse":
                self.send_json({"path": browse(data.get("kind", "file"))})
            elif self.path == "/api/scan":
                self.send_json(scan_start_menu())
            elif self.path == "/api/everything":
                try:
                    self.send_json(everything_search(data.get("q", ""), data.get("kind", "program")))
                except EverythingError as e:
                    self.send_json({"error": str(e), "everything": False}, 503)
            elif self.path == "/api/install-everything":
                if install_state["step"] not in ("idle", "done", "error"):
                    return self.send_json(install_state)
                install_state.update(step="download", msg="准备中…", pct=None, cancel=False)
                threading.Thread(target=install_everything, daemon=True).start()
                self.send_json(install_state)
            elif self.path == "/api/install-status":
                self.send_json(install_state)
            elif self.path == "/api/install-cancel":
                install_state["cancel"] = True  # 只在下载阶段有效，下载循环会自己停下
                self.send_json(install_state)
            elif self.path == "/api/version":
                # 源码运行时（开发中）不提供程序内更新，免得把安装版装到正在调试的环境上
                self.send_json({"version": APP_VERSION, "canUpdate": FROZEN, "repo": GITHUB_REPO})
            elif self.path == "/api/update/check":
                try:
                    rel = latest_release()
                except UpdateError as e:
                    return self.send_json({"error": str(e)}, 400)
                asset = rel.pop("asset")  # 里面有下载用的内部信息，页面只需要大小
                self.send_json({**rel, "size": asset["size"] if asset else 0, "current": APP_VERSION})
            elif self.path == "/api/update/start":
                if not FROZEN:
                    return self.send_json({"error": "源码运行中，请用项目文件夹里的「更新.bat」"}, 400)
                if update_state["step"] in ("idle", "error"):
                    update_state.update(step="download", msg="准备中…", pct=None, cancel=False)
                    threading.Thread(target=run_update, daemon=True).start()
                self.send_json(update_state)
            elif self.path == "/api/update/status":
                self.send_json(update_state)
            elif self.path == "/api/update/cancel":
                update_state["cancel"] = True
                self.send_json(update_state)
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:  # 把错误显示到页面上
            self.send_json({"error": str(e)}, 500)


def main():
    try:
        server = Server((HOST, PORT), Handler)
    except OSError:
        # 已经在运行了，直接打开页面
        webbrowser.open(URL)
        return
    threading.Timer(0.5, lambda: webbrowser.open(URL)).start()
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
