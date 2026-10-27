@echo off
rem One-click update: pull latest code, rebuild, install silently, restart (see update.ps1)
rem Pass -Force to rebuild and reinstall even when there is no new commit
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
pause
