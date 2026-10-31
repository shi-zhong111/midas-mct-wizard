@echo off
rem ============================================================
rem  MIDAS Wizard launcher (double-click me)
rem
rem  This file is only a thin shell. ALL Python detection,
rem  verification, installation and launching logic lives in
rem  scripts\launch_wizard.ps1.
rem
rem  Why: the old launcher trusted pyw/pythonw on PATH and could
rem  trigger the Windows "Python Launcher is sorry to say ...
rem  Unable to create process using ..." dialog when the Python
rem  it pointed at had been moved or deleted. The new chain only
rem  uses an interpreter that has actually been executed and
rem  verified (Python >= 3.9 + tkinter), and never runs pyw.exe.
rem
rem  NOTE: keep this file as UTF-8 WITH BOM + CRLF line endings,
rem  otherwise cmd.exe mis-parses it and the Chinese text breaks.
rem  CI checks the BOM and CRLF of this file.
rem ============================================================
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PS1=%HERE%scripts\launch_wizard.ps1"

if not exist "%HERE%midas_wizard.py" (
  echo [错误] 找不到 midas_wizard.py，请把它和本文件放在同一个文件夹里。
  pause
  exit /b 1
)

if not exist "%PS1%" (
  echo [错误] 缺少启动脚本 scripts\launch_wizard.ps1
  echo 请重新解压便携版的全部文件，不要只复制其中的单个文件。
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Interactive
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo 启动失败（退出码 %RC%）。
  echo 请双击"修复启动环境.bat"，它会生成 启动诊断.txt 并尝试修复。
  pause
)
exit /b %RC%
