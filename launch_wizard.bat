@echo off
rem ============================================================
rem  midas-mct-wizard launcher
rem  Double-click this file to start the wizard.
rem  If Python is missing it offers to install it (see scripts\).
rem
rem  NOTE: this file is saved as UTF-8 with a BOM and starts with
rem  chcp 65001 so the Chinese text below displays correctly.
rem  If you edit it, keep it UTF-8 with BOM.
rem ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "SCRIPT=%HERE%midas_wizard.py"

if not exist "%SCRIPT%" (
  echo [错误] 找不到 midas_wizard.py，请把它和本文件放在同一个文件夹里。
  pause
  exit /b 1
)

rem 1) Python installed earlier by our own script
if exist "%HERE%pythonw_path.txt" (
  set /p PYW=<"%HERE%pythonw_path.txt"
  if exist "!PYW!" (
    start "" "!PYW!" "%SCRIPT%"
    exit /b 0
  )
)

rem 2) An existing system Python
where pyw >nul 2>nul
if %ERRORLEVEL%==0 (
  start "" pyw "%SCRIPT%"
  exit /b 0
)
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
  start "" pythonw "%SCRIPT%"
  exit /b 0
)

rem 3) Nothing found - offer to install it
echo.
echo 没有检测到 Python 运行环境。
echo 本程序需要 Python 3（自带 tkinter），安装包约 25 MB，只需装一次。
echo.
set /p ANSWER=现在自动下载安装吗？[Y/N] 
if /i not "%ANSWER%"=="Y" (
  echo.
  echo 请手动安装 Python 3（安装时勾选 "Add python.exe to PATH"），然后重新双击本文件。
  echo 下载地址：https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\install_python.ps1"
if exist "%HERE%pythonw_path.txt" (
  set /p PYW=<"%HERE%pythonw_path.txt"
  if exist "!PYW!" (
    echo 环境就绪，正在启动向导...
    start "" "!PYW!" "%SCRIPT%"
    exit /b 0
  )
)
echo.
echo 环境没有装好。请手动安装 Python 3（勾选 Add python.exe to PATH）后重新双击本文件。
pause
