#requires -Version 5.1
<#
.SYNOPSIS
    下载并静默安装 Python 3（仅当前用户，不需要管理员），装完真实验证并记录路径。

.DESCRIPTION
    由 scripts\launch_wizard.ps1 在"找不到任何可用 Python"且用户同意后调用。

    1. 依次尝试下载 python.org 官方 3.12.8 安装包；官方站失败时回退国内镜像
       （华为云 mirrors.huaweicloud.com、npmmirror）。
    2. 校验下载文件（大小 > 20 MB 且是 Windows PE 可执行文件），防止把错误页
       当成安装包。
    3. 静默安装到当前用户目录：
       InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1
    4. 在 %LOCALAPPDATA%\Programs\Python 里找 pythonw.exe，用同目录 python.exe
       真跑一次验证（Python >= 3.9 且带 tkinter），成功才写 pythonw_path.txt。
    5. 退出码：0 = 安装成功且已验证；1 = 失败（给出中文手装指引）。

.PARAMETER DryRun
    只打印将要执行的操作（下载源、安装参数、搜索目录），不联网、不安装、不写文件。

.PARAMETER NoPause
    结束时不等待回车（由 launch_wizard.ps1 调用时使用，由它自己负责提示）。
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$script:PowerShellExe = $null

$script:Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $script:Here) { $script:Here = Split-Path -Parent $PSCommandPath }
$script:Root = Split-Path -Parent $script:Here
$script:RememberFile = Join-Path $script:Root 'pythonw_path.txt'

$script:Version = '3.12.8'
$script:InstallerName = 'python-' + $script:Version + '-amd64.exe'
$script:MinimumBytes = 20MB
$script:Sources = @(
    [pscustomobject]@{ Name = 'python.org 官方'; Url = ('https://www.python.org/ftp/python/' + $script:Version + '/' + $script:InstallerName) },
    [pscustomobject]@{ Name = '华为云镜像'; Url = ('https://mirrors.huaweicloud.com/python/' + $script:Version + '/' + $script:InstallerName) },
    [pscustomobject]@{ Name = 'npmmirror 镜像'; Url = ('https://registry.npmmirror.com/-/binary/python/' + $script:Version + '/' + $script:InstallerName) }
)

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::OutputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
} catch { }

function Write-Line {
    param([string]$Text = '', [string]$Color = '')
    if ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
}
function Write-Ok {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Green
}
function Write-Warn {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Yellow
}
function Write-Err {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Red
}
function Pause-IfNeeded {
    if ($NoPause) { return }
    try {
        if ([Console]::IsInputRedirected) { return }
    } catch { }
    try { Read-Host '按回车退出' | Out-Null } catch { }
}

# ---------------------------------------------------------------------------
# 安全拼接路径 / 盘符存在性检查（与 launch_wizard.ps1 相同的加固）
# PS 5.1 在 $ErrorActionPreference='Stop' 时，Join-Path 的父路径若位于不存在的
# 盘符（如被拔掉的 E:）会抛 DriveNotFoundException。对外来路径一律用
# [System.IO.Path]::Combine，只拼字符串、不碰文件系统。
# ---------------------------------------------------------------------------
function Join-SafePath {
    param([string]$Parent, [string]$Child)
    if (-not $Parent) { return [string]$Child }
    if (-not $Child) { return [string]$Parent }
    try { return [System.IO.Path]::Combine($Parent, $Child) } catch { return '' }
}

function Test-PathDriveExists {
    param([string]$Path)
    if (-not $Path) { return $false }
    $m = [regex]::Match($Path, '^([A-Za-z]):')
    if (-not $m.Success) { return $true }
    try {
        return (Test-Path -LiteralPath ($m.Groups[1].Value.ToUpper() + ':\') -PathType Container)
    } catch {
        return $true
    }
}

# ---------------------------------------------------------------------------
# 验证一个 python.exe：真跑一次，退出码 0 才算有效
# ---------------------------------------------------------------------------
function Test-PythonRuntime {
    param(
        [string]$PythonExe,
        [int]$TimeoutSec = 15
    )
    $probe = [pscustomobject]@{ Ok = $false; Version = ''; Tk = ''; Detail = '' }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        $probe.Detail = 'python.exe 不存在'
        return $probe
    }
    $pyCode = 'import sys,tkinter; print(''PYVER=%d.%d.%d'' % sys.version_info[:3]); print(''TKVER=%s'' % tkinter.TkVersion); sys.exit(0 if sys.version_info>=(3,9) else 2)'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = '-c "' + $pyCode + '"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.WorkingDirectory = $script:Root
    try { $psi.StandardOutputEncoding = $utf8NoBom; $psi.StandardErrorEncoding = $utf8NoBom } catch { }
    $proc = $null
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
            try { $proc.Kill() } catch { }
            $probe.Detail = ('超过 {0} 秒没有返回' -f $TimeoutSec)
            return $probe
        }
        $stdout = [string]$outTask.Result
        $stderr = [string]$errTask.Result
        foreach ($line in ($stdout -split "`r?`n")) {
            if ($line -match '^PYVER=(.+)$') { $probe.Version = $Matches[1].Trim() }
            elseif ($line -match '^TKVER=(.+)$') { $probe.Tk = $Matches[1].Trim() }
        }
        if ($proc.ExitCode -eq 0) {
            $probe.Ok = $true
            $probe.Detail = '验证通过'
        } elseif ($proc.ExitCode -eq 2) {
            $probe.Detail = ('Python ' + $probe.Version + ' 低于 3.9')
        } else {
            $first = @($stderr -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
            $msg = '未知错误'
            if ($first.Count -gt 0) { $msg = ([string]$first[0]).Trim() }
            $probe.Detail = ('退出码 ' + [string]$proc.ExitCode + '：' + $msg)
        }
    } catch {
        $probe.Detail = ('无法运行：' + $_.Exception.Message)
    } finally {
        if ($proc) { try { $proc.Dispose() } catch { } }
    }
    return $probe
}

# ---------------------------------------------------------------------------
# 找新装好的 pythonw.exe 并真实验证
# ---------------------------------------------------------------------------
function Find-InstalledPythonw {
    $candidates = New-Object System.Collections.ArrayList
    $searchDirs = New-Object System.Collections.ArrayList
    if ($env:LOCALAPPDATA) {
        $localPython = Join-SafePath -Parent $env:LOCALAPPDATA -Child 'Programs\Python'
        if ($localPython) { [void]$searchDirs.Add($localPython) }
    }
    [void]$searchDirs.Add('C:\')
    if ($env:ProgramFiles) { [void]$searchDirs.Add($env:ProgramFiles) }
    $pf86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($pf86) { [void]$searchDirs.Add($pf86) }

    $dirs = New-Object System.Collections.ArrayList
    foreach ($base in $searchDirs) {
        try {
            if (-not $base) { continue }
            if (-not (Test-PathDriveExists -Path $base)) { continue }
            if (-not (Test-Path -LiteralPath $base -PathType Container)) { continue }
            Get-ChildItem -LiteralPath $base -Directory -ErrorAction Stop |
                Where-Object { $_.Name -match '^Python3' } |
                ForEach-Object { [void]$dirs.Add($_) }
        } catch { }
    }
    # 版本从高到低
    $dirs = @($dirs | Sort-Object -Property @{ Expression = { $_.Name } } -Descending)
    foreach ($d in $dirs) {
        try {
            $pyw = Join-SafePath -Parent $d.FullName -Child 'pythonw.exe'
            if ($pyw -and (Test-Path -LiteralPath $pyw -PathType Leaf)) {
                [void]$candidates.Add([pscustomobject]@{ PythonwExe = $pyw; Dir = $d.FullName })
            }
        } catch { continue }
    }

    foreach ($c in $candidates) {
        try {
            $py = Join-SafePath -Parent $c.Dir -Child 'python.exe'
            if (-not $py) { continue }
            if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { continue }
            $probe = Test-PythonRuntime -PythonExe $py -TimeoutSec 20
            if ($probe.Ok) {
                return [pscustomobject]@{
                    Found = $true
                    PythonwExe = $c.PythonwExe
                    PythonExe = $py
                    Version = $probe.Version
                    Tk = $probe.Tk
                    CheckedCount = $candidates.Count
                }
            } else {
                Write-Warn ('  跳过 ' + $c.PythonwExe + '：' + $probe.Detail)
            }
        } catch { continue }
    }
    return [pscustomobject]@{
        Found = $false
        PythonwExe = ''
        PythonExe = ''
        Version = ''
        Tk = ''
        CheckedCount = $candidates.Count
    }
}

function Write-ManualGuide {
    Write-Line ''
    Write-Line '请手动安装 Python 3（3.9 或更高版本）：'
    Write-Line '  1. 打开 https://www.python.org/downloads/windows/'
    Write-Line '     （国内网络可用镜像：https://mirrors.huaweicloud.com/python/3.12.8/ ）'
    Write-Line '  2. 下载 Windows installer (64-bit) 并运行'
    Write-Line '  3. 安装时务必勾选 "Add python.exe to PATH"'
    Write-Line '  4. 装完后重新双击"修复启动环境.bat"'
}

function Save-RememberedPath {
    param([string]$PythonwExe)
    try {
        $enc = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($script:RememberFile, $PythonwExe, $enc)
        return $true
    } catch {
        Write-Warn ('无法写入 pythonw_path.txt：' + $_.Exception.Message)
        return $false
    }
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' MIDAS 建模向导 —— 自动安装 Python 3（仅当前用户）' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan

if ($DryRun) {
    Write-Line ''
    Write-Line '[DryRun] 只检查，不联网下载、不安装、不写任何文件。'
    Write-Line ''
    Write-Line '将要依次尝试的下载地址：'
    $i = 0
    foreach ($s in $script:Sources) {
        $i++
        Write-Line ('  ' + [string]$i + '. ' + $s.Name)
        Write-Line ('     ' + $s.Url)
    }
    Write-Line ''
    Write-Line ('安装包校验：大小 > ' + [string]($script:MinimumBytes / 1MB) + ' MB，且文件头为 MZ（Windows 可执行文件）')
    Write-Line ('安装参数：/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_test=0 Include_doc=0')
    Write-Line ''
    Write-Line '安装后搜索 pythonw.exe 的目录：%LOCALAPPDATA%\Programs\Python、C:\、%ProgramFiles%、%ProgramFiles(x86)%'
    $found = Find-InstalledPythonw
    if ($found.Found) {
        Write-Line ('当前机器已有可用 Python（仅检测，不修改）：' + $found.PythonwExe + '（Python ' + $found.Version + '）')
    } else {
        Write-Line '当前机器没有找到已安装且验证通过的 Python（正常，装完后才会出现）。'
    }
    Write-Line ''
    Write-Line '[DryRun] 结束（未做任何修改）。'
    exit 0
}

$installerPath = Join-Path ([System.IO.Path]::GetTempPath()) $script:InstallerName
$downloaded = $false
$lastError = ''

# TLS 1.2（老系统默认可能没开）
try {
    $currentProtocols = [Net.ServicePointManager]::SecurityProtocol
    [Net.ServicePointManager]::SecurityProtocol = $currentProtocols -bor [Net.SecurityProtocolType]::Tls12
} catch { }

foreach ($src in $script:Sources) {
    Write-Line ''
    Write-Line ('正在从 ' + $src.Name + ' 下载：')
    Write-Line ('  ' + $src.Url)
    try {
        if (Test-Path -LiteralPath $installerPath) { Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue }
        $ProgressPreference = 'Continue'
        Invoke-WebRequest -Uri $src.Url -OutFile $installerPath -UseBasicParsing -TimeoutSec 600
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) { throw '下载后文件不存在' }
        $len = (Get-Item -LiteralPath $installerPath -ErrorAction Stop).Length
        if ($len -lt $script:MinimumBytes) { throw ('文件太小（' + [string]$len + ' 字节），可能下载到的是错误页') }
        $fs = [System.IO.File]::OpenRead($installerPath)
        try {
            $head = New-Object byte[] 2
            [void]$fs.Read($head, 0, 2)
        } finally {
            $fs.Close()
        }
        if ($head[0] -ne 0x4D -or $head[1] -ne 0x5A) { throw '文件头不是 MZ，不是有效的 Windows 安装包' }
        Write-Ok ('  下载成功（' + [string][math]::Round($len / 1MB, 1) + ' MB）')
        $downloaded = $true
        break
    } catch {
        $lastError = $_.Exception.Message
        Write-Warn ('  下载失败：' + $lastError)
        Write-Warn '  换下一个下载源...'
    }
}

if (-not $downloaded) {
    Write-Err ''
    Write-Err '下载失败（多半是网络问题，或所有下载源都不可用）。'
    Write-Line ('最后错误：' + $lastError)
    Write-ManualGuide
    Pause-IfNeeded
    exit 1
}

Write-Line ''
Write-Line '正在静默安装到当前用户目录（不需要管理员，不会修改系统设置）...'
$installerRc = $null
try {
    $proc = Start-Process -FilePath $installerPath -Wait -PassThru -ArgumentList @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_launcher=1',
        'Include_tcltk=1',
        'Include_test=0',
        'Include_doc=0'
    )
    $installerRc = $proc.ExitCode
    Write-Line ('  安装程序退出码：' + [string]$installerRc)
} catch {
    Write-Warn ('  运行安装程序失败：' + $_.Exception.Message)
}
try { Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue } catch { }

Write-Line ''
Write-Line '正在查找并验证刚装好的 Python...'
$found = Find-InstalledPythonw
if (-not $found.Found) {
    Write-Err ''
    Write-Err '安装结束，但没有找到可用的 pythonw.exe（需要 Python >= 3.9 且带 tkinter）。'
    if ($installerRc -ne $null) { Write-Line ('安装程序退出码：' + [string]$installerRc) }
    Write-Line '如果安装程序被安全软件拦截，请把 Python 加入白名单后再试。'
    Write-Line '也可以手动安装（见下面的指引）。'
    Write-ManualGuide
    Pause-IfNeeded
    exit 1
}

$saved = Save-RememberedPath -PythonwExe $found.PythonwExe
Write-Ok ''
Write-Ok ('安装成功！已验证可用：' + $found.PythonwExe)
Write-Line ('  Python ' + $found.Version + '，tkinter ' + $found.Tk)
if ($saved) {
    Write-Line ('  已记录到 pythonw_path.txt：' + $script:RememberFile)
} else {
    Write-Warn '  未能写入 pythonw_path.txt，下次启动会自动重新检测（不影响使用）。'
}
exit 0
