# 用 PyInstaller 把「MIDAS Civil NX 建模向导」打成免安装单文件 exe。
#
# 用法（在仓库根目录，或任意目录都行）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#   powershell ... -File scripts\build_exe.ps1 -Python "C:\path\to\python.exe" -VenvDir "C:\path\to\venv"
#
# 产物：dist\MIDAS建模向导.exe —— 双击即用，目标电脑不需要装 Python。
#
# 关键点：
#   * 用 Python 3.10 打（程序按 3.9+ 语法写，别用 3.14 的新特性）；
#   * 程序纯标准库 + tkinter，没有第三方运行依赖，PyInstaller 自己会带 tcl/tk；
#   * 示例数据（模型文件\ 15 套示例 + 示例截面\ 的 DXF）用 --add-data 打进 exe，
#     首次运行由 midas_wizard.bootstrap_frozen_examples() 复制到 exe 旁边；
#   * 冻结运行时的「程序目录」= exe 所在目录（wiz_common.app_dir()），所有成果/会话都写那里，
#     不会写进临时解包目录 _MEIPASS。
#
# 退出码：0=成功；非 0=失败（会抛错并打印 PyInstaller 日志尾部）。

param(
    [string]$Python = "C:\Midas\.venv-exe\Scripts\python.exe",
    [string]$VenvDir = "C:\Midas\.venv-exe",
    [string]$BasePython = "F:\Python310\python.exe",
    [switch]$SkipInstall,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
Set-Location $root

$exeName = "MIDAS建模向导"
$distExe = Join-Path $root ("dist\" + $exeName + ".exe")
$logPath = Join-Path $root "build\pyinstaller.log"
$stage = Join-Path $root "build\_pyi_payload"

function Write-Step([string]$text) {
    Write-Host ""
    Write-Host ("=== " + $text + " ===") -ForegroundColor Cyan
}

# ---------------------------------------------------------------- 0) 检查输入
Write-Step "0/5 检查仓库里的程序文件和示例数据"
foreach ($name in @("midas_wizard.py", "wiz_common.py")) {
    if (-not (Test-Path (Join-Path $root $name))) {
        throw "仓库不完整：找不到 $name（当前目录 $root）"
    }
}
$modelsSrc = Join-Path $root "模型文件"
$sectSrc = Join-Path $root "示例截面"
if (-not (Test-Path $modelsSrc)) {
    throw "找不到示例数据目录：$modelsSrc（PyInstaller 没东西可打，请先放好 15 套示例）"
}
$modelDirs = @(Get-ChildItem $modelsSrc -Directory)
if ($modelDirs.Count -lt 1) {
    throw "$modelsSrc 里没有示例子目录（预期 15 套）；没有示例也能打包，但请先确认这是不是想要的"
}
if (-not (Test-Path $sectSrc)) {
    throw "找不到示例截面目录：$sectSrc"
}
Write-Host ("模型文件：{0} 个子目录；示例截面：{1} 个文件" -f `
    $modelDirs.Count, @(Get-ChildItem $sectSrc -File).Count)

# ---------------------------------------------------------------- 1) Python
Write-Step "1/5 准备打包用的 Python（3.10）"
New-Item -ItemType Directory -Path (Join-Path $root "build") -Force | Out-Null

function Test-PythonCanWrite([string]$exe, [string]$dir) {
    # 先探一下这个解释器能不能写仓库。极少数受限环境（低完整性级别沙箱）里进程只能写工作区，
    # 用 venv 里的 Python 会 PermissionError；这时自动退回系统 Python（同样 3.10），不换方案。
    if (-not (Test-Path $exe)) { return $false }
    $probe = Join-Path $dir (".pyi_write_probe_" + [guid]::NewGuid().ToString("N") + ".txt")
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $exe -c "import sys; open(sys.argv[1], 'w').write('x')" $probe 2>$null
    $ok = ($LASTEXITCODE -eq 0) -and (Test-Path $probe)
    $ErrorActionPreference = $prev
    if (Test-Path $probe) { Remove-Item $probe -Force -ErrorAction SilentlyContinue }
    return $ok
}

if (-not (Test-Path $Python)) {
    if (-not (Test-Path $BasePython)) {
        throw "找不到打包用的 Python：$Python。请先建 venv：`"$BasePython`" -m venv `"$VenvDir`""
    }
    if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
        Write-Host ("建 venv：" + $VenvDir)
        & $BasePython -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "建 venv 失败（退出码 $LASTEXITCODE）" }
    }
    $Python = Join-Path $VenvDir "Scripts\python.exe"
}
if (-not (Test-PythonCanWrite $Python $root)) {
    Write-Host ("注意：$Python 写不了仓库目录（受限沙箱/目录权限），改用 $BasePython 打包。") -ForegroundColor Yellow
    if (-not (Test-Path $BasePython)) {
        throw "venv 的 Python 写不了仓库目录 $root，备用 Python 也不存在：$BasePython"
    }
    if (-not (Test-PythonCanWrite $BasePython $root)) {
        throw "两个 Python 都写不了仓库目录 $root，请检查目录权限（PyInstaller 必须往 build\、dist\ 写文件）"
    }
    $Python = $BasePython
}
Write-Host ("使用 Python：" + $Python)
$ver = & $Python --version 2>&1
Write-Host ("版本：" + $ver)

# ---------------------------------------------------------------- 2) PyInstaller
Write-Step "2/5 检查 / 安装 PyInstaller"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -c "import PyInstaller" *> $null
$hasPyi = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEap
if ($hasPyi) {
    $pyiVer = (& $Python -m PyInstaller --version 2>&1 | Select-Object -Last 1)
    Write-Host ("已安装 PyInstaller " + $pyiVer)
} elseif ($SkipInstall) {
    throw "没装 PyInstaller 且指定了 -SkipInstall"
} else {
    Write-Host "安装 PyInstaller（先官方源，失败再试清华镜像）…"
    $ErrorActionPreference = "Continue"
    & $Python -m pip install --disable-pip-version-check pyinstaller
    $pipOk = ($LASTEXITCODE -eq 0)
    if (-not $pipOk) {
        Write-Host "官方源失败，改用清华镜像…"
        & $Python -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
        $pipOk = ($LASTEXITCODE -eq 0)
    }
    $ErrorActionPreference = $prevEap
    if (-not $pipOk) { throw "PyInstaller 安装失败" }
    $pyiVer = (& $Python -m PyInstaller --version 2>&1 | Select-Object -Last 1)
    Write-Host ("已安装 PyInstaller " + $pyiVer)
}

# ---------------------------------------------------------------- 3) 干净的打包数据
Write-Step "3/5 整理要打进 exe 的示例数据（不带开发机的会话/日志）"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item $modelsSrc -Destination (Join-Path $stage "模型文件") -Recurse -Force
Copy-Item $sectSrc -Destination (Join-Path $stage "示例截面") -Recurse -Force
# 开发机的运行时会话、日志、缓存不随 exe 发出去（用户首次运行会生成自己的）
Get-ChildItem (Join-Path $stage "模型文件") -Recurse -File -Include "_wizard_session.json", "*.log", "*.tmp" |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$stageModels = @(Get-ChildItem (Join-Path $stage "模型文件") -Directory).Count
Write-Host ("打包数据：模型文件 {0} 个子目录，示例截面 {1} 个文件" -f `
    $stageModels, @(Get-ChildItem (Join-Path $stage "示例截面") -File).Count)

# ---------------------------------------------------------------- 4) PyInstaller
Write-Step "4/5 PyInstaller 打包（单文件 + windowed，约 1~2 分钟）"
New-Item -ItemType Directory -Path (Join-Path $root "build") -Force | Out-Null
if (Test-Path $logPath) { Remove-Item $logPath -Force }

# 动态 import 的流程插件必须显式 --hidden-import，PyInstaller 静态分析看不到 importlib 的字符串。
$hidden = @()
foreach ($f in (Get-ChildItem $root -File -Filter "*.py" | Sort-Object Name)) {
    $mod = [IO.Path]::GetFileNameWithoutExtension($f.Name)
    if ($mod -in @("midas_wizard", "setup", "build")) { continue }
    $hidden += @("--hidden-import", $mod)
}
Write-Host ("hidden-import：" + (($hidden | Where-Object { $_ -ne "--hidden-import" }) -join ", "))

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", $exeName,
    "--distpath", "dist",
    "--workpath", "build",
    "--specpath", "build",
    "--add-data", ((Join-Path $stage "模型文件") + ";模型文件"),
    "--add-data", ((Join-Path $stage "示例截面") + ";示例截面"),
    "--exclude-module", "tests",
    "--log-level", "INFO"
) + $hidden + @("midas_wizard.py")

# 注意：不要用 $args（PowerShell 自动变量，赋值不生效，会把参数全丢掉 → pyinstaller 只打 usage）。
$ErrorActionPreference = "Continue"     # 让原生命令的 stderr 不抛 NativeCommandError，先落盘再看退出码
& $Python @pyArgs *> $logPath
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if (Test-Path $logPath) { Get-Content $logPath -Tail 15 }
if ($code -ne 0) {
    Write-Host "PyInstaller 失败，日志尾部：" -ForegroundColor Red
    if (Test-Path $logPath) { Get-Content $logPath -Tail 40 }
    throw "PyInstaller 打包失败（退出码 $code），完整日志：$logPath"
}
if (-not (Test-Path $distExe)) {
    throw "PyInstaller 报成功但找不到产物：$distExe（看日志 $logPath）"
}

# ---------------------------------------------------------------- 5) 产物 + 冒烟测试
Write-Step "5/5 产物信息"
$item = Get-Item $distExe
$hash = (Get-FileHash $distExe -Algorithm SHA256).Hash
Write-Host ("exe：" + $item.FullName)
Write-Host ("大小：" + ("{0:N2} MB" -f ($item.Length / 1MB)) + "（" + $item.Length + " 字节）")
Write-Host ("SHA256：" + $hash)
Write-Host ("修改时间：" + $item.LastWriteTime)

if (-not $SkipSmokeTest) {
    Write-Step "冒烟测试：exe --selftest（windowed 无控制台，只看退出码）"
    $p = Start-Process -FilePath $distExe -ArgumentList "--selftest" -Wait -PassThru
    Write-Host ("--selftest 退出码：" + $p.ExitCode)
    if ($p.ExitCode -ne 0) {
        throw "exe 自检失败（退出码 $($p.ExitCode)）：打包出来的程序不能正常跑"
    }
}

Write-Host ""
Write-Host "打包成功 ✔" -ForegroundColor Green
Write-Host ("产物：" + $distExe)
Write-Host ("SHA256：" + $hash)
