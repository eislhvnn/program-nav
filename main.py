"""打包成 exe 时的入口：同一个 exe，带 --window 参数时当窗口进程，否则当主进程（托盘 + 后台）"""
import sys

if __name__ == "__main__":
    if "--window" in sys.argv:
        import window
        window.main()
    else:
        import app
        app.main()
