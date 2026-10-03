# Download and install Python 3 for the current user (no admin needed).
#
# Called by launch_wizard.bat only after the user agrees.
# Downloads ONLY from python.org over HTTPS. There are no third-party mirrors
# here on purpose: a public repo must not silently install an interpreter from
# an untrusted host.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

$version = "3.12.8"
$url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
$exe = Join-Path $env:TEMP "python-$version-amd64.exe"

$ok = $false
try {
  Write-Host ("  正在下载：" + $url)
  Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing -TimeoutSec 900
  if ((Get-Item $exe).Length -gt 5MB) { $ok = $true }
} catch {
  Write-Host ("  下载失败：" + $_.Exception.Message)
}

if (-not $ok) {
  Write-Host ""
  Write-Host "下载失败（多半是网络问题）。请手动安装："
  Write-Host "  1. 打开 https://www.python.org/downloads/windows/"
  Write-Host "  2. 下载 Python 3.9 或更高版本的 Windows installer (64-bit)"
  Write-Host "  3. 安装时务必勾选 Add python.exe to PATH"
  Write-Host "  4. 装完重新双击 launch_wizard.bat"
  Read-Host "按回车退出"
  exit 1
}

Write-Host "  正在静默安装到当前用户目录（不改系统设置、不需要管理员）..."
Start-Process -FilePath $exe `
  -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", `
                "Include_tcltk=1", "Include_test=0", "Include_doc=0" -Wait
Remove-Item $exe -Force -ErrorAction SilentlyContinue

$cands = @()
$cands += (Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter pythonw.exe -Recurse -ErrorAction SilentlyContinue |
           Select-Object -ExpandProperty FullName)
$cands += "C:\Python312\pythonw.exe"
$found = $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $found) {
  Write-Host "安装似乎完成了，但没找到 pythonw.exe。请重启电脑后再双击启动器。"
  Read-Host "按回车退出"
  exit 1
}

Set-Content -Path (Join-Path $root "pythonw_path.txt") -Value $found -Encoding ASCII
Write-Host ("  安装成功：" + $found)
exit 0
