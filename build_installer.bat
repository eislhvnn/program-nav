@echo off
chcp 65001 >nul
rem Build ProgramNav: 1) package exe with PyInstaller  2) compile installer with Inno Setup
cd /d "%~dp0"

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --name "程序导航" ^
  --icon "%~dp0app.ico" --add-data "%~dp0index.html;." --add-data "%~dp0lib\Everything64.dll;lib" ^
  --hidden-import pystray._win32 --distpath "%~dp0build\release" --workpath "%~dp0build\work-release" ^
  --specpath "%~dp0build" "%~dp0main.py"
if errorlevel 1 goto :fail

set "ISCC=%~dp0build\tools\InnoSetup\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
rem installed elsewhere (e.g. D:): read InstallLocation from the uninstall registry entry
rem (reg query prints a blank line and the key name first, so skip=2 lands on the value line)
set "INNO_KEY=Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
if not exist "%ISCC%" for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\Software\WOW6432Node\%INNO_KEY%" /v InstallLocation 2^>nul') do set "ISCC=%%bISCC.exe"
if not exist "%ISCC%" for /f "skip=2 tokens=2,*" %%a in ('reg query "HKCU\Software\%INNO_KEY%" /v InstallLocation 2^>nul') do set "ISCC=%%bISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup not found. Install it from https://jrsoftware.org/isdl.php
  goto :fail
)
echo Using %ISCC%
"%ISCC%" "%~dp0installer.iss"
if errorlevel 1 goto :fail
echo.
echo Done: build\installer
exit /b 0

:fail
echo Build failed.
exit /b 1
