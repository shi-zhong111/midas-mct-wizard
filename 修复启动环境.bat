@echo off
rem ============================================================
rem  MIDAS 建模向导 - 修复启动环境 (one-click repair)
rem
rem  Double-click this file when the wizard does not start, or
rem  when you see a "Python Launcher is sorry to say ..." dialog.
rem
rem  It writes 启动诊断.txt next to the program, then fixes the
rem  environment (or offers to install Python) and launches the
rem  wizard. Keep this window open until it finishes.
rem
rem  NOTE: keep this file as UTF-8 WITH BOM + CRLF.
rem ============================================================
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PS1=%HERE%scripts\launch_wizard.ps1"
title MIDAS 建模向导 - 修复启动环境

echo ============================================================
echo  MIDAS 建模向导 - 修复启动环境
echo ============================================================
echo  本工具会依次做：
echo    1. 检查系统里的 Python 环境（逐个真实运行验证）
echo    2. 生成诊断报告  启动诊断.txt
echo    3. 必要时自动下载安装 Python 3
echo    4. 启动建模向导
echo ============================================================
echo.

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

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Diagnose -Interactive
set "RC=%ERRORLEVEL%"

echo.
if exist "%HERE%启动诊断.txt" (
  echo 诊断报告已生成：%HERE%启动诊断.txt
) else (
  echo 未能生成 启动诊断.txt
)
echo 退出码：%RC%
echo.
echo 如果仍然启动不了，请把 启动诊断.txt 发回给作者。
pause
exit /b %RC%
