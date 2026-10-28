**中文** | [English](README.en.md)

# 程序导航

Windows 上的程序启动器：把常用的软件、自己写的脚本、常去的文件夹集中放在一个页面里，按快捷键随时呼出，双击就能运行。

## 功能

- **一个页面管理所有程序**：支持 exe、快捷方式（.lnk）、文件夹，以及 Python、bat、cmd、PowerShell、AutoHotkey、VBS、Java 等脚本
- **三种添加方式**
  - 全盘搜索添加：输入文件名，用 [Everything](https://www.voidtools.com/) 秒搜整台电脑
  - 从开始菜单导入：一次性导入已安装的软件
  - 手动填写路径
- **分类和收藏**：自建分类（右键可重命名或删除），常用的标 ★ 收藏
- **运行状态**：正在运行的程序显示绿点，「● 运行中」里能一键正常关闭或强制结束。bat 启动器和快捷方式也能识别出它们实际启动的程序
- **搜索**：按名称、路径、备注、分类筛选
- **三种视图**：导航、图标、详细
- **自定义图标**：选图片、把图片拖进编辑窗口，或者直接 Ctrl+V 粘贴
- **常驻托盘、很省内存**：开启「隐藏时释放内存」后，挂在托盘里只占约 23 MB
- **程序内更新**：从 GitHub Releases 下载新版本并自动安装，不用登录，程序列表不受影响

## 安装

1. 到 [Releases](https://github.com/eislhvnn/program-nav/releases/latest) 下载 `ProgramNav-Setup-x.x.x.exe`
2. 运行安装程序。如果电脑上没有 Everything，可以勾选一起安装（安装包里自带官方安装程序，不用联网）
3. 装好后会创建桌面快捷方式，并默认开机自动启动

## 使用

| 操作 | 方法 |
| --- | --- |
| 呼出 / 隐藏窗口 | `Ctrl + Alt + Space`，或点托盘图标 |
| 添加程序 | 点「＋ 添加」，选一种添加方式 |
| 运行 | 双击程序 |
| 更多操作 | 右键程序：复制路径、打开位置、收藏、编辑、删除 |
| 搜索 | 按 `/` 聚焦搜索框，`Esc` 清空 |
| 管理分类 | 点左边的「＋ 新建分类」；右键分类可重命名或删除 |
| 检查更新 | 左下角「⚙ 设置」→「检查更新」 |

托盘右键菜单还可以设置：

- 隐藏时释放内存（再次打开要等 1~2 秒）
- 开机自动启动
- 退出

## 数据存放位置

程序列表、分类、设置都保存在安装目录（`%LOCALAPPDATA%\Programs\程序导航`）下的 `programs.json`、`categories.json`、`settings.json` 里，只存在本机，不会上传。

## 从源码运行

需要 Python 3 和 Windows。

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw.exe app.py
```

打包安装程序需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)，运行 `build_installer.bat`，生成的安装包在 `build\installer\` 里。

## 致谢

- 全盘搜索由 [Everything](https://www.voidtools.com/)（voidtools）提供，许可证见 `licenses/`
- 安装程序的中文翻译来自 [Inno-Setup-Chinese-Simplified-Translation](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)
