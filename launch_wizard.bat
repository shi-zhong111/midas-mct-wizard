@echo off
rem ============================================================
rem  midas-mct-wizard launcher  (double-click me)
rem
rem  Finds a Python 3 with tkinter and starts midas_wizard.py with
rem  pythonw.exe (no console window). If none is found it offers to
rem  install one - see scripts\install_python.ps1.
rem
rem  Order of search:
rem    0) pythonw_path.txt written by a previous run
rem    1) pyw / pythonw          (on PATH)
rem    2) the py launcher, incl. cmd /c py -3.12            (PATH)
rem    3) %LOCALAPPDATA%\Programs\Python\Python3*\pythonw.exe
rem       C:\Python3*\pythonw.exe
rem       %ProgramFiles%\Python3*\pythonw.exe
rem  Step 3 matters because the python.org installer does NOT put
rem  Python on PATH unless "Add python.exe to PATH" is ticked, and
rem  in that case pyw/pythonw are not found even though Python is
rem  installed correctly.
rem
rem  NOTE: saved as UTF-8 with BOM + chcp 65001 so the Chinese text
rem  below displays correctly. Keep that encoding if you edit it.
rem ============================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "SCRIPT=%HERE%midas_wizard.py"
set "PYW="

if not exist "%SCRIPT%" (
  echo [错误] 找不到 midas_wizard.py，请把它和本文件放在同一个文件夹里。
  pause
  exit /b 1
)

rem ---- 0) remembered from a previous successful start --------------------
if exist "%HERE%pythonw_path.txt" (
  set /p PYW=<"%HERE%pythonw_path.txt"
  if defined PYW if not exist "!PYW!" set "PYW="
)

rem ---- 1) on PATH --------------------------------------------------------
if not defined PYW (
  where pyw >nul 2>nul && set "PYW=pyw"
)
if not defined PYW (
  where pythonw >nul 2>nul && set "PYW=pythonw"
)

rem ---- 2) the py launcher (installer always adds it, even without PATH) --
if not defined PYW (
  where py >nul 2>nul && set "PYW=py"
)

rem ---- 3) default install locations the installer uses -------------------
if not defined PYW (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python"
    "C:"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
  ) do (
    if not defined PYW (
      for /f "delims=" %%P in ('dir /b /o-n "%%~D\Python3*" 2^>nul') do (
        if not defined PYW if exist "%%~D\%%P\pythonw.exe" set "PYW=%%~D\%%P\pythonw.exe"
      )
    )
  )
)

rem ---- launch -----------------------------------------------------------
if defined PYW (
  echo 正在启动建模向导...
  start "" "!PYW!" "%SCRIPT%"
  exit /b 0
)

rem ---- nothing found: offer to install ----------------------------------
echo.
echo 没有检测到 Python 运行环境。
echo 本程序需要 Python 3（自带 tkinter），安装包约 25 MB，只需装一次。
echo.
set /p ANSWER=现在自动下载安装吗？[Y/N] 
if /i not "%ANSWER%"=="Y" (
  echo.
  echo 请手动安装 Python 3，然后重新双击本文件。
  echo 下载地址：https://www.python.org/downloads/windows/
  echo 安装时建议勾选 "Add python.exe to PATH"（不勾也能用，程序会自己去常见目录里找）。
  pause
  exit /b 1
)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\install_python.ps1"
if exist "%HERE%pythonw_path.txt" (
  set /p PYW=<"%HERE%pythonw_path.txt"
  if defined PYW if exist "!PYW!" (
    echo 环境就绪，正在启动向导...
    start "" "!PYW!" "%SCRIPT%"
    exit /b 0
  )
)
echo.
echo 环境没有装好。请手动安装 Python 3 后重新双击本文件。
pause
