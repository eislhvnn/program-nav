@echo off
rem Publish a new version: build the installer and upload it as a GitHub Release (see publish.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1"
pause
