[中文](README.md) | **English**

# Program Nav (程序导航)

A program launcher for Windows. Keep your everyday apps, your own scripts and frequently used folders on one page, bring it up with a hotkey at any time, and double-click to run.

> The interface is currently in Chinese only.

## Features

- **One page for everything**: exe files, shortcuts (.lnk), folders, and scripts such as Python, bat, cmd, PowerShell, AutoHotkey, VBS and Java
- **Three ways to add programs**
  - Search the whole disk: type a file name and find it instantly with [Everything](https://www.voidtools.com/)
  - Import from the Start menu: add your installed apps in one go
  - Enter a path by hand
- **Categories and favorites**: create your own categories (right-click to rename or delete) and star ★ the ones you use most
- **Running status**: running programs show a green dot, and the "● Running" view lets you close or force-kill them in one click. Bat launchers and shortcuts are matched to the programs they actually start
- **Window preview**: thumbnails of the open windows of your listed programs; click one to switch to it
- **Hide taskbar buttons** per program, so they don't take up taskbar space; switch to them from the window preview. Buttons come back when Program Nav quits
- **Custom hotkeys** for showing the main window and for jumping straight to the window preview, set in Settings
- **Search** by name, path, note or category
- **Three views**: Nav, Icons, Details
- **Custom icons**: pick an image, drag one into the edit dialog, or paste it with Ctrl+V
- **Lives in the tray, light on memory**: with "Release memory when hidden" on, it uses only about 23 MB in the tray
- **In-app updates**: downloads and installs new versions from GitHub Releases. No login needed, and your program list is kept

## Install

1. Download `ProgramNav-Setup-x.x.x.exe` from [Releases](https://github.com/eislhvnn/program-nav/releases/latest)
2. Run the installer. If Everything is not installed, you can tick the option to install it too (the official installer is bundled, so no internet is needed)
3. The installer creates a desktop shortcut and, by default, starts the app when Windows starts

## Usage

| Action | How |
| --- | --- |
| Show / hide the window | `Ctrl + Alt + Space`, or click the tray icon |
| Window preview | Click "▦ 窗口预览" (Window preview) on the left, or press your own hotkey |
| Hide a taskbar button | Right-click a program → "隐藏任务栏按钮" (Hide taskbar button); do it again to restore |
| Change hotkeys | "⚙ 设置" (Settings) → "快捷键" (Hotkeys): click a button, then press the new combination |
| Add a program | Click "＋ 添加" (Add) and choose a method |
| Run | Double-click a program |
| More actions | Right-click a program: copy path, open location, favorite, edit, delete |
| Search | Press `/` to focus the search box, `Esc` to clear it |
| Manage categories | Click "＋ 新建分类" (New category) on the left; right-click a category to rename or delete it |
| Check for updates | "⚙ 设置" (Settings) at the bottom left → "检查更新" (Check for updates) |

The tray icon's right-click menu also lets you set:

- Release memory when hidden (reopening then takes 1–2 seconds)
- Start with Windows
- Quit

## Where your data is stored

Your program list, categories and settings are stored in `programs.json`, `categories.json` and `settings.json` in the install folder (`%LOCALAPPDATA%\Programs\程序导航`). They stay on your computer and are never uploaded.

## Run from source

Requires Python 3 and Windows.

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw.exe app.py
```

To build the installer you need [Inno Setup 6](https://jrsoftware.org/isinfo.php). Run `build_installer.bat`; the installer is written to `build\installer\`.

## Credits

- Disk-wide search is powered by [Everything](https://www.voidtools.com/) (voidtools); see `licenses/` for its license
- The installer's Simplified Chinese translation comes from [Inno-Setup-Chinese-Simplified-Translation](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)
