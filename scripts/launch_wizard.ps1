#requires -Version 5.1
<#
.SYNOPSIS
    MIDAS 建模向导 —— 唯一启动 / 检测逻辑（Windows 便携版）

.DESCRIPTION
    原则：先验证，再启动。任何一个候选 Python 都必须"真的能跑、版本 >= 3.9、
    带 tkinter"才会被使用；否则跳过继续找。

    绝不直接执行系统的 py.exe / pyw.exe 启动器。pyw 在目标 Python 已被删除或
    移动时，会弹出 Windows「Python Launcher is sorry to say ... Unable to create
    process using ...」错误框，且不会返回错误码。py.exe 只允许用 -0p 参数枚举
    已安装的解释器（该操作不会启动 Python，失败也不会弹窗）。

    检测顺序（每个候选都要真跑一次验证，带 15 秒超时）：
      0) pythonw_path.txt（上次验证通过的路径，本次仍重新验证）
      1) 程序目录\python\pythonw.exe（为将来内置运行时预留）
      2) PATH 里的 python.exe / pythonw.exe（跳过 \WindowsApps\ 商店假别名）
      3) py -0p 枚举出的解释器路径（只解析输出，绝不执行 pyw）
      4) 注册表 HKCU/HKLM Software\Python\PythonCore\<版本>\InstallPath
      5) %LOCALAPPDATA%\Programs\Python\Python3*、C:\Python3*、
         %ProgramFiles%\Python3*、%ProgramFiles(x86)%\Python3*

    全都没找到时：交互模式询问是否自动安装（默认 Y，调用
    scripts\install_python.ps1）；静默模式改为弹出一个可见控制台窗口重试，
    让用户能看到安装进度。

.PARAMETER Interactive
    允许交互：提示是否自动安装、失败时给出提示。launch_wizard.bat 使用。

.PARAMETER Silent
    静默模式（无控制台窗口），启动建模向导_静默.vbs 使用。没找到 Python 时
    不弹询问框，而是用可见窗口重新调用自己，由用户决定是否安装。

.PARAMETER DryRun
    只检测，不启动、不写任何文件、不安装。用于排查环境（不会出现任何弹窗）。

.PARAMETER Diagnose
    详细列出所有候选与验证结果，并把报告写入程序目录的 启动诊断.txt；
    之后照常修复 / 安装 / 启动。

.PARAMETER NoInstall
    不允许自动下载安装 Python，只给出手动安装指引。

.PARAMETER Hold
    脚本结束时等待回车再退出。静默模式转可见窗口重试时使用，防止窗口一闪而过。

.PARAMETER SkipSources
    仅供诊断 / 测试使用：逗号分隔的来源名，跳过对应检测来源。
    可选值：remembered, bundled, path, py0p, registry, defaultdirs
#>
[CmdletBinding()]
param(
    [switch]$Interactive,
    [switch]$Silent,
    [switch]$DryRun,
    [switch]$Diagnose,
    [switch]$NoInstall,
    [switch]$Hold,
    [string]$SkipSources = ''
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# 基础路径 / 全局状态
# ---------------------------------------------------------------------------
$script:ScriptPath = $PSCommandPath
if (-not $script:ScriptPath) { $script:ScriptPath = $MyInvocation.MyCommand.Path }
$script:ScriptDir = Split-Path -Parent $script:ScriptPath
$script:Root = Split-Path -Parent $script:ScriptDir
$script:Wizard = Join-Path $script:Root 'midas_wizard.py'
$script:RememberFile = Join-Path $script:Root 'pythonw_path.txt'
$script:DiagFile = Join-Path $script:Root '启动诊断.txt'
$script:Installer = Join-Path $script:ScriptDir 'install_python.ps1'
$script:SystemRoot = $env:SystemRoot
if (-not $script:SystemRoot) { $script:SystemRoot = 'C:\Windows' }
$script:PowerShellExe = Join-Path $script:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script:Actions = New-Object System.Collections.ArrayList

# 控制台用 UTF-8 输出（launch_wizard.bat 里已 chcp 65001）
try {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
} catch {
    # 无控制台（例如 vbs 隐藏窗口）时忽略
}

# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------
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
function Add-Action {
    param([string]$Text)
    [void]$script:Actions.Add($Text)
}

function Write-Banner {
    if (-not ($Interactive -or $Diagnose)) { return }
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ' MIDAS 建模向导 启动器' -ForegroundColor Cyan
    Write-Host ' （先验证 Python 环境，再启动程序；不会再碰到 Python Launcher 弹窗）' -ForegroundColor DarkGray
    Write-Host '============================================================' -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# 安全拼接路径 + 盘符存在性检查
#
# 为什么必须这么写：Windows PowerShell 5.1 在 $ErrorActionPreference='Stop' 时，
# Join-Path 如果父路径所在盘符不存在（典型场景：Python 原来装在 E: 盘，U 盘拔了、
# 移动硬盘换了盘符、或当初的 Python 目录已被删掉），会抛
#   DriveNotFoundException: Cannot find drive. A drive with the name 'E' does not exist.
# 非终止错误被升级成终止错误，会把整个启动器打崩（rc=9，一行都启动不了）。
# [System.IO.Path]::Combine 只做字符串拼接、不访问文件系统，永远不会因此抛异常。
# ---------------------------------------------------------------------------
function Join-SafePath {
    param([string]$Parent, [string]$Child)
    if (-not $Parent) { return [string]$Child }
    if (-not $Child) { return [string]$Parent }
    try { return [System.IO.Path]::Combine($Parent, $Child) } catch { return '' }
}

# 路径所在盘符是否真的存在（相对路径 / UNC 路径不做盘符判断，直接视为 True）
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
# 隐藏运行一条 cmd 命令，把它的输出（原始字节）落到临时文件再自动解码。
# 用来执行 "py -0p"：只枚举解释器，不会启动任何 Python，也不会弹窗。
# ---------------------------------------------------------------------------
function Invoke-CmdCapture {
    param(
        [string]$CommandLine,
        [int]$TimeoutSec = 15
    )
    $result = [pscustomobject]@{
        Text = ''
        ExitCode = $null
        TimedOut = $false
        Error = ''
    }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('midas_cap_' + [guid]::NewGuid().ToString('N') + '.txt')
    $proc = $null
    try {
        $cmdExe = Join-Path $script:SystemRoot 'System32\cmd.exe'
        if (-not (Test-Path -LiteralPath $cmdExe -PathType Leaf)) { $cmdExe = 'cmd.exe' }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $cmdExe
        # cmd /c ""<命令>" > "<临时文件>" 2>&1" —— 双引号包裹让带空格的路径也安全
        $psi.Arguments = '/d /c "' + $CommandLine + ' > "' + $tmp + '" 2>&1"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.WorkingDirectory = [System.IO.Path]::GetTempPath()
        $proc = [System.Diagnostics.Process]::Start($psi)
        if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
            $result.TimedOut = $true
            $result.Error = ('超过 {0} 秒未返回，已终止' -f $TimeoutSec)
            try { $proc.Kill() } catch { }
            try { [void]$proc.WaitForExit(2000) } catch { }
        } else {
            $result.ExitCode = $proc.ExitCode
        }
        if (Test-Path -LiteralPath $tmp -PathType Leaf) {
            $bytes = [System.IO.File]::ReadAllBytes($tmp)
            $text = $null
            try {
                $text = ([System.Text.UTF8Encoding]::new($false, $true)).GetString($bytes)
            } catch {
                # 不是合法 UTF-8（例如控制台代码页 936 / GBK），按 GBK 解
                try { $text = ([System.Text.Encoding]::GetEncoding(936)).GetString($bytes) } catch { $text = '' }
            }
            $result.Text = $text
        }
    } catch {
        $result.Error = $_.Exception.Message
    } finally {
        if ($proc) { try { $proc.Dispose() } catch { } }
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    return $result
}

# ---------------------------------------------------------------------------
# 单个候选 Python 的验证：同目录 python.exe 真跑一次，退出码 0 才算有效
# ---------------------------------------------------------------------------
function Invoke-PythonProbe {
    param(
        [string]$PythonExe,
        [string]$WorkDir,
        [int]$TimeoutSec = 15
    )
    $probe = [pscustomobject]@{
        Ok = $false
        ExitCode = $null
        TimedOut = $false
        Version = ''
        Tk = ''
        StdOut = ''
        StdErr = ''
        Detail = ''
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        $probe.Detail = 'python.exe 不存在'
        return $probe
    }
    # 这段代码只会 import + 打印版本，不会创建窗口
    $pyCode = 'import sys,tkinter; print(''PYVER=%d.%d.%d'' % sys.version_info[:3]); print(''TKVER=%s'' % tkinter.TkVersion); sys.exit(0 if sys.version_info>=(3,9) else 2)'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = '-c "' + $pyCode + '"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.WorkingDirectory = $WorkDir
    try {
        $probeEnc = [System.Text.UTF8Encoding]::new($false)
        $psi.StandardOutputEncoding = $probeEnc
        $psi.StandardErrorEncoding = $probeEnc
    } catch { }
    $proc = $null
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        $exited = $proc.WaitForExit($TimeoutSec * 1000)
        if (-not $exited) {
            $probe.TimedOut = $true
            $probe.Detail = ('超过 {0} 秒没有返回，已终止' -f $TimeoutSec)
            try { $proc.Kill() } catch { }
            try { [void]$proc.WaitForExit(2000) } catch { }
            return $probe
        }
        $probe.StdOut = [string]$outTask.Result
        $probe.StdErr = [string]$errTask.Result
        $probe.ExitCode = $proc.ExitCode
    } catch {
        $probe.Detail = ('无法运行：' + $_.Exception.Message)
        return $probe
    } finally {
        if ($proc) { try { $proc.Dispose() } catch { } }
    }

    foreach ($line in ($probe.StdOut -split "`r?`n")) {
        if ($line -match '^PYVER=(.+)$') { $probe.Version = $Matches[1].Trim() }
        elseif ($line -match '^TKVER=(.+)$') { $probe.Tk = $Matches[1].Trim() }
    }
    if ($probe.ExitCode -eq 0) {
        $probe.Ok = $true
        $probe.Detail = '验证通过（可运行 + 版本 >= 3.9 + tkinter 正常）'
    } elseif ($probe.ExitCode -eq 2) {
        $verText = $probe.Version
        if (-not $verText) { $verText = '未知' }
        $probe.Detail = ('版本 ' + $verText + ' 低于 3.9')
    } else {
        $errLine = ''
        $lines = @($probe.StdErr -split "`r?`n" | Where-Object { $_.Trim() })
        if ($lines.Count -gt 0) { $errLine = [string]$lines[0] }
        if (-not $errLine) {
            $errLine = $probe.Detail
            if (-not $errLine) { $errLine = '未知错误（可能缺少 tkinter 或解释器损坏）' }
        }
        $errLine = $errLine.Trim()
        if ($errLine.Length -gt 160) { $errLine = $errLine.Substring(0, 160) + '...' }
        $probe.Detail = ('退出码 ' + [string]$probe.ExitCode + '：' + $errLine)
    }
    return $probe
}

# ---------------------------------------------------------------------------
# 验证一个候选路径（python.exe 或 pythonw.exe 均可）
# ---------------------------------------------------------------------------
function Test-PythonwCandidate {
    param(
        [string]$Path,
        [string]$Source,
        [string]$WorkDir,
        [int]$TimeoutSec = 15
    )
    $r = [pscustomobject]@{
        Source = $Source
        Requested = $Path
        PythonExe = ''
        PythonwExe = ''
        Valid = $false
        Version = ''
        Tk = ''
        Detail = ''
    }
    # 整段兜底：任何一个坏路径（盘符不存在、路径含非法字符、权限异常、注册表乱码……）
    # 都只能让"这一个候选"无效，绝不能打断整轮扫描。
    try {
        if (-not $Path) { $r.Detail = '空路径'; return $r }
        $p = $Path.Trim().Trim('"').Trim()
        if (-not $p) { $r.Detail = '空路径'; return $r }
        if ($p.Contains('%')) {
            try { $p = [System.Environment]::ExpandEnvironmentVariables($p) } catch { }
        }
        $r.Requested = $p

        # 商店假别名：C:\Users\xxx\AppData\Local\Microsoft\WindowsApps\python.exe
        if ($p -match '\\WindowsApps\\') {
            $r.Detail = 'Microsoft Store 应用别名（不是真正的 Python），跳过'
            return $r
        }

        # 绝不把 py.exe / pyw.exe 当解释器执行（pyw 失败会弹窗）
        $leaf = ''
        try { $leaf = Split-Path -Leaf $p } catch { $leaf = '' }
        if ($leaf -ieq 'pyw.exe' -or $leaf -ieq 'py.exe') {
            $r.Detail = '禁止直接执行 py.exe / pyw.exe 启动器（失败会弹 Python Launcher 错误框），跳过'
            return $r
        }

        # 注意：这里的 Join-Path 全部换成 Join-SafePath —— 外来路径的父目录可能
        # 在一个已经不存在的盘符上，Join-Path 会抛 DriveNotFoundException。
        if ($leaf -ieq 'pythonw.exe') {
            $pyw = $p
            $py = Join-SafePath -Parent (Split-Path -Parent $p) -Child 'python.exe'
        } elseif ($leaf -ieq 'python.exe') {
            $py = $p
            $pyw = Join-SafePath -Parent (Split-Path -Parent $p) -Child 'pythonw.exe'
        } else {
            $r.Detail = '不是 python.exe / pythonw.exe 路径'
            return $r
        }

        if (-not $py -or -not $pyw) {
            $r.Detail = '路径无法解析，跳过'
            return $r
        }
        if (-not [System.IO.Path]::IsPathRooted($py)) { $py = Join-SafePath -Parent $WorkDir -Child $py }
        if (-not [System.IO.Path]::IsPathRooted($pyw)) { $pyw = Join-SafePath -Parent $WorkDir -Child $pyw }
        try {
            $py = [System.IO.Path]::GetFullPath($py)
            $pyw = [System.IO.Path]::GetFullPath($pyw)
        } catch { }
        $r.PythonExe = $py
        $r.PythonwExe = $pyw

        # 盘符不存在（U 盘被拔 / Python 装在别的机器才有的 E: 盘）→ 明确跳过
        if (-not (Test-PathDriveExists -Path $py)) {
            $drive = [regex]::Match($py, '^([A-Za-z]):').Groups[1].Value.ToUpper()
            $r.Detail = ('路径所在盘符 ' + $drive + ': 不存在（磁盘/U 盘未挂载或盘符已变），跳过')
            return $r
        }
        $pyDir = ''
        try { $pyDir = Split-Path -Parent $py } catch { $pyDir = '' }
        if ($pyDir -and -not (Test-Path -LiteralPath $pyDir -PathType Container)) {
            $r.Detail = '路径所在目录不存在（Python 可能已被移动或删除），跳过'
            return $r
        }

        if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
            $r.Detail = '同目录缺少 python.exe'
            return $r
        }
        try {
            if ((Get-Item -LiteralPath $py -ErrorAction Stop).Length -eq 0) {
                $r.Detail = 'python.exe 是 0 字节空壳（常见于 Microsoft Store 应用别名），跳过'
                return $r
            }
        } catch { }
        if (-not (Test-Path -LiteralPath $pyw -PathType Leaf)) {
            $r.Detail = '同目录缺少 pythonw.exe'
            return $r
        }

        $probe = Invoke-PythonProbe -PythonExe $py -WorkDir $WorkDir -TimeoutSec $TimeoutSec
        $r.Version = $probe.Version
        $r.Tk = $probe.Tk
        $r.Valid = $probe.Ok
        $r.Detail = $probe.Detail
        return $r
    } catch {
        $r.Valid = $false
        $r.Detail = '候选路径异常，跳过：' + $_.Exception.Message
        return $r
    }
}

# ---------------------------------------------------------------------------
# 候选收集
# ---------------------------------------------------------------------------
function Add-CandidateTo {
    param($List, $Seen, [string]$Path, [string]$Source)
    if (-not $Path) { return }
    $p = $Path.Trim().Trim('"').Trim()
    if (-not $p) { return }
    $key = $p.ToLowerInvariant()
    if ($Seen.ContainsKey($key)) { return }
    $Seen[$key] = $true
    [void]$List.Add([pscustomobject]@{ Path = $p; Source = $Source })
}

function Get-PythonCandidateList {
    param(
        [string]$Root,
        [string]$RememberFile,
        [string]$SkipSources = ''
    )
    $skip = @{}
    foreach ($s in ($SkipSources -split ',')) {
        $t = $s.Trim().ToLowerInvariant()
        if ($t) { $skip[$t] = $true }
    }
    $list = New-Object System.Collections.ArrayList
    $seen = @{}
    $info = [pscustomobject]@{
        RememberedExists = $false
        RememberedText = ''
        PyLauncher = ''
        PyLauncherExists = $false
        PywLauncher = ''
        PywLauncherExists = $false
        Py0pText = ''
        Py0pExit = $null
        Py0pTimedOut = $false
        Py0pError = ''
        PathEntries = @()
    }

    # 1) 上次记住的路径（仍然要重新验证）
    if (-not $skip.ContainsKey('remembered')) {
        if (Test-Path -LiteralPath $RememberFile -PathType Leaf) {
            $info.RememberedExists = $true
            try {
                $text = [System.IO.File]::ReadAllText($RememberFile, [System.Text.UTF8Encoding]::new($false))
                $info.RememberedText = $text.Trim()
                $first = ''
                foreach ($ln in ($text -split "`r?`n")) {
                    if ($ln.Trim()) { $first = $ln.Trim(); break }
                }
                if ($first) { Add-CandidateTo $list $seen $first 'pythonw_path.txt（上次记住的路径）' }
            } catch {
                $info.RememberedText = '（读取失败：' + $_.Exception.Message + '）'
            }
        }
    }

    # 2) 程序目录下自带的运行时（为将来内置 Python 预留）
    if (-not $skip.ContainsKey('bundled')) {
        try {
            $bundled = Join-SafePath -Parent $Root -Child 'python\pythonw.exe'
            if ($bundled -and (Test-Path -LiteralPath $bundled -PathType Leaf)) {
                Add-CandidateTo $list $seen $bundled '程序目录\python\（内置运行时）'
            }
        } catch { }
    }

    # 3) PATH 里的 python.exe / pythonw.exe
    #    注意：PATH 里常有指向"已拔掉的 U 盘/移动硬盘"的残留条目（例如盘符已经不在的旧目录），
    #    这些条目必须能安全跳过 —— 这里一律用 Join-SafePath，永远不执行 Join-Path。
    if (-not $skip.ContainsKey('path')) {
        $entries = @($env:PATH -split ';')
        $info.PathEntries = $entries
        foreach ($entryRaw in $entries) {
            try {
                $entry = $entryRaw.Trim().Trim('"').Trim()
                if (-not $entry) { continue }
                if ($entry -notmatch '^[A-Za-z]:\\' -and $entry -notmatch '^\\\\') { continue }
                if ($entry -match '\\WindowsApps\\?$') { continue }
                if (-not (Test-PathDriveExists -Path $entry)) {
                    # 盘符不存在的 PATH 残留：作为一个"坏候选"写进诊断，然后继续下一个
                    $ghost = Join-SafePath -Parent $entry -Child 'python.exe'
                    if ($ghost) { Add-CandidateTo $list $seen $ghost ('PATH（盘符不存在）：' + $entry) }
                    continue
                }
                $pyw = Join-SafePath -Parent $entry -Child 'pythonw.exe'
                $py = Join-SafePath -Parent $entry -Child 'python.exe'
                if (-not $pyw -or -not $py) { continue }
                if (Test-Path -LiteralPath $pyw -PathType Leaf) {
                    Add-CandidateTo $list $seen $pyw ('PATH：' + $entry)
                } elseif (Test-Path -LiteralPath $py -PathType Leaf) {
                    Add-CandidateTo $list $seen $py ('PATH：' + $entry)
                }
            } catch {
                continue
            }
        }
    }

    # 4) py -0p 枚举（安全：只读列表，不执行 pyw、不启动解释器）
    if (-not $skip.ContainsKey('py0p')) {
        $pyExe = ''
        try {
            $cmd = Get-Command -Name 'py' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd -and $cmd.Source) { $pyExe = [string]$cmd.Source }
        } catch { }
        if (-not $pyExe) {
            $candidate = Join-Path $script:SystemRoot 'py.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $pyExe = $candidate }
        }
        $info.PyLauncher = $pyExe
        $info.PyLauncherExists = ($pyExe -ne '' -and (Test-Path -LiteralPath $pyExe -PathType Leaf))
        $pywExe = Join-Path $script:SystemRoot 'pyw.exe'
        if (Test-Path -LiteralPath $pywExe -PathType Leaf) {
            $info.PywLauncher = $pywExe
            $info.PywLauncherExists = $true
        }
        if ($info.PyLauncherExists) {
            $cap = Invoke-CmdCapture -CommandLine ('"' + $pyExe + '" -0p') -TimeoutSec 15
            $info.Py0pText = $cap.Text
            $info.Py0pExit = $cap.ExitCode
            $info.Py0pTimedOut = $cap.TimedOut
            $info.Py0pError = $cap.Error
            foreach ($line in ($cap.Text -split "`r?`n")) {
                if ($line -match '^\s*-V:([0-9][^\s]*)(?:\s+\*)?\s+(.+?)\s*$') {
                    $ver = $Matches[1]
                    $pth = $Matches[2]
                    Add-CandidateTo $list $seen $pth ('py -0p：' + $ver)
                }
            }
        }
    }

    # 5) 注册表安装路径
    if (-not $skip.ContainsKey('registry')) {
        $regFound = New-Object System.Collections.ArrayList
        $specs = @(
            @{ Hive = [Microsoft.Win32.RegistryHive]::CurrentUser; View = [Microsoft.Win32.RegistryView]::Default; Label = 'HKCU' },
            @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine; View = [Microsoft.Win32.RegistryView]::Default; Label = 'HKLM' },
            @{ Hive = [Microsoft.Win32.RegistryHive]::LocalMachine; View = [Microsoft.Win32.RegistryView]::Registry32; Label = 'HKLM(WOW64)' }
        )
        foreach ($spec in $specs) {
            $baseKey = $null
            try {
                $baseKey = [Microsoft.Win32.RegistryKey]::OpenBaseKey($spec.Hive, $spec.View)
                $coreKey = $baseKey.OpenSubKey('Software\Python\PythonCore')
                if ($coreKey) {
                    foreach ($name in $coreKey.GetSubKeyNames()) {
                        $m = [regex]::Match($name, '^([0-9]+\.[0-9]+)')
                        if (-not $m.Success) { continue }
                        $ver = [version]$m.Groups[1].Value
                        $ipKey = $coreKey.OpenSubKey($name + '\InstallPath')
                        if (-not $ipKey) { continue }
                        $dir = [string]$ipKey.GetValue('')
                        if (-not $dir) {
                            $exePath = [string]$ipKey.GetValue('ExecutablePath')
                            if ($exePath) { $dir = Split-Path -Parent $exePath }
                        }
                        $ipKey.Close()
                        if ($dir) {
                            [void]$regFound.Add([pscustomobject]@{ Version = $ver; Dir = $dir; Label = ($spec.Label + ' ' + $name) })
                        }
                    }
                    $coreKey.Close()
                }
            } catch {
                # 注册表读不到不算错误
            } finally {
                if ($baseKey) { try { $baseKey.Close() } catch { } }
            }
        }
        $regFound = @($regFound | Sort-Object -Property Version -Descending)
        foreach ($rg in $regFound) {
            try {
                # 注册表是"外来数据"：InstallPath 可能指到不存在的 E: 盘，
                # 这里必须用 Join-SafePath，否则整个启动器会崩（rc=9）。
                $pyw = Join-SafePath -Parent $rg.Dir -Child 'pythonw.exe'
                if ($pyw) {
                    Add-CandidateTo $list $seen $pyw ('注册表 ' + $rg.Label + '：' + $rg.Dir)
                }
            } catch { continue }
        }
    }

    # 6) 常见默认安装目录
    if (-not $skip.ContainsKey('defaultdirs')) {
        $bases = New-Object System.Collections.ArrayList
        if ($env:LOCALAPPDATA) {
            [void]$bases.Add([pscustomobject]@{ Path = (Join-SafePath -Parent $env:LOCALAPPDATA -Child 'Programs\Python'); Label = '%LOCALAPPDATA%\Programs\Python' })
        }
        [void]$bases.Add([pscustomobject]@{ Path = 'C:\'; Label = 'C:\' })
        if ($env:ProgramFiles) {
            [void]$bases.Add([pscustomobject]@{ Path = $env:ProgramFiles; Label = '%ProgramFiles%' })
        }
        $pf86 = [System.Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
        if ($pf86) {
            [void]$bases.Add([pscustomobject]@{ Path = $pf86; Label = '%ProgramFiles(x86)%' })
        }
        foreach ($b in $bases) {
            try {
                if (-not $b.Path) { continue }
                if (-not (Test-PathDriveExists -Path $b.Path)) { continue }
                if (-not (Test-Path -LiteralPath $b.Path -PathType Container)) { continue }
                $dirs = @()
                $dirs = @(Get-ChildItem -LiteralPath $b.Path -Directory -ErrorAction Stop |
                          Where-Object { $_.Name -match '^Python3' } |
                          Sort-Object -Property Name -Descending)
                foreach ($d in $dirs) {
                    $pyw = Join-SafePath -Parent $d.FullName -Child 'pythonw.exe'
                    if ($pyw) { Add-CandidateTo $list $seen $pyw ('默认安装目录 ' + $b.Label) }
                }
            } catch { continue }
        }
    }

    return [pscustomobject]@{
        Candidates = @($list)
        Info = $info
    }
}

# ---------------------------------------------------------------------------
# 候选枚举的"总兜底"包装：即使枚举阶段出现任何意外异常（例如某个外部数据源
# 抛了我们没预料到的错误），也只会得到"0 个候选"，继续走安装引导流程，
# 绝不会让整个启动器以 rc=9 崩溃。
# ---------------------------------------------------------------------------
function New-EmptyCandidateInfo {
    return [pscustomobject]@{
        RememberedExists = $false
        RememberedText = '（候选枚举异常，未能读取）'
        PyLauncher = ''
        PyLauncherExists = $false
        PywLauncher = ''
        PywLauncherExists = $false
        Py0pText = ''
        Py0pExit = $null
        Py0pTimedOut = $false
        Py0pError = ''
        PathEntries = @()
    }
}

function Get-PythonCandidateListSafe {
    param(
        [string]$Root,
        [string]$RememberFile,
        [string]$SkipSources = ''
    )
    try {
        return (Get-PythonCandidateList -Root $Root -RememberFile $RememberFile -SkipSources $SkipSources)
    } catch {
        try { Write-Warn ('候选枚举出现异常（已忽略，继续下一个来源）：' + $_.Exception.Message) } catch { }
        return [pscustomobject]@{
            Candidates = @()
            Info = (New-EmptyCandidateInfo)
        }
    }
}

# ---------------------------------------------------------------------------
# 启动建模向导（pythonw.exe + 2 秒存活检测）
# ---------------------------------------------------------------------------
function Start-WizardProcess {
    param(
        [string]$PythonwExe,
        [string]$WizardFile,
        [string]$WorkDir
    )
    $result = [pscustomobject]@{
        Started = $false
        Id = 0
        HasExited = $false
        ExitCode = $null
        Error = ''
    }
    try {
        $quotedWizard = '"' + $WizardFile + '"'
        $proc = Start-Process -FilePath $PythonwExe -ArgumentList $quotedWizard -WorkingDirectory $WorkDir -PassThru
        $result.Started = $true
        $result.Id = $proc.Id
        Start-Sleep -Milliseconds 2000
        $proc.Refresh()
        if ($proc.HasExited) {
            $result.HasExited = $true
            $result.ExitCode = $proc.ExitCode
        }
    } catch {
        $result.Error = $_.Exception.Message
    }
    return $result
}

# ---------------------------------------------------------------------------
# 写 pythonw_path.txt（UTF-8 无 BOM，单行绝对路径）——DryRun 时不写
# ---------------------------------------------------------------------------
function Save-RememberedPath {
    param([string]$PythonwExe)
    $old = ''
    try {
        if (Test-Path -LiteralPath $script:RememberFile -PathType Leaf) {
            $old = ([System.IO.File]::ReadAllText($script:RememberFile, [System.Text.UTF8Encoding]::new($false))).Trim()
        }
    } catch { }
    try {
        $enc = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($script:RememberFile, $PythonwExe, $enc)
        return ($old -ne $PythonwExe)
    } catch {
        Add-Action ('写 pythonw_path.txt 失败：' + $_.Exception.Message)
        Write-Warn ('提示：无法写入 pythonw_path.txt（' + $_.Exception.Message + '），本次仍会启动。')
        return $false
    }
}

# ---------------------------------------------------------------------------
# 启动诊断报告
# ---------------------------------------------------------------------------
function Get-CandidateReportLine {
    param($Result, [int]$Index)
    $state = '无效'
    if ($Result.Valid) { $state = '有效' }
    $ver = $Result.Version
    if (-not $ver) { $ver = '-' }
    $tk = $Result.Tk
    if (-not $tk) { $tk = '-' }
    return ('{0,2}) 来源: {1} | pythonw: {2} | 结果: {3} | Python {4} / tkinter {5} | {6}' -f `
            $Index, $Result.Source, $Result.PythonwExe, $state, $ver, $tk, $Result.Detail)
}

function Format-DiagnosticReport {
    param(
        [string]$ModeText,
        $EnumInfo,
        [object[]]$Results,
        [string]$FinalChoice = '（尚未选择）',
        [string]$InstallNote = '（未执行安装）',
        [string]$LaunchNote = '（未启动）'
    )
    $L = @()
    $L += '============================================================'
    $L += ' MIDAS 建模向导 —— 启动诊断报告'
    $L += ' （本文件由 修复启动环境.bat / launch_wizard.ps1 -Diagnose 自动生成）'
    $L += '============================================================'
    $L += ('生成时间: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    $L += ('主机名: ' + $env:COMPUTERNAME + '    用户名: ' + $env:USERNAME)
    $osVersionString = ''
    try { $osVersionString = [System.Environment]::OSVersion.VersionString } catch { }
    $osDisplay = ''
    $osBuild = ''
    try {
        $cv = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
        if ($cv.DisplayVersion) { $osDisplay = [string]$cv.DisplayVersion }
        if ($cv.CurrentBuildNumber) { $osBuild = [string]$cv.CurrentBuildNumber }
        if ($cv.UBR) { $osBuild = $osBuild + '.' + [string]$cv.UBR }
    } catch { }
    $L += ('操作系统: ' + $osVersionString)
    if ($osDisplay -or $osBuild) { $L += ('Windows 版本: ' + $osDisplay + '  Build ' + $osBuild) }
    $L += ('64 位系统: ' + [string][System.Environment]::Is64BitOperatingSystem + '    64 位进程: ' + [string][System.Environment]::Is64BitProcess)
    $L += ('PowerShell: ' + $PSVersionTable.PSVersion.ToString() + '    当前模式: ' + $ModeText)
    $L += ('程序目录: ' + $script:Root)
    $L += ('启动脚本: ' + $script:ScriptPath)
    if (Test-Path -LiteralPath $script:Wizard -PathType Leaf) {
        $wizSize = 0
        try { $wizSize = (Get-Item -LiteralPath $script:Wizard -ErrorAction Stop).Length } catch { }
        $L += ('midas_wizard.py: 存在（' + [string]$wizSize + ' 字节）')
    } else {
        $L += 'midas_wizard.py: 不存在！'
    }
    $L += ''
    $L += '[一] pythonw_path.txt（上次记住的 Python 路径）'
    $L += ('文件: ' + $script:RememberFile)
    if ($EnumInfo.RememberedExists) {
        $L += '存在: 是'
        $L += ('内容: ' + $EnumInfo.RememberedText)
    } else {
        $L += '存在: 否（第一次运行，或还没成功启动过）'
    }
    $L += ''
    $L += '[二] 系统 py / pyw 启动器'
    $L += ('py.exe:  ' + $EnumInfo.PyLauncher)
    $L += ('py.exe 存在: ' + [string]$EnumInfo.PyLauncherExists)
    $L += ('pyw.exe: ' + $EnumInfo.PywLauncher)
    $L += ('pyw.exe 存在: ' + [string]$EnumInfo.PywLauncherExists)
    $L += '注意: 本程序绝不直接执行 pyw.exe / py.exe 启动器（目标 Python 损坏时会弹'
    $L += '      「Python Launcher is sorry to say ... Unable to create process using ...」'
    $L += '      错误框）。py.exe 只用于 -0p 枚举已安装解释器。'
    $L += ''
    $L += '[三] py -0p 输出（只枚举，不启动 Python）'
    if (-not $EnumInfo.PyLauncherExists) {
        $L += '（系统里没有 py.exe，跳过）'
    } elseif ($EnumInfo.Py0pTimedOut) {
        $L += '（超时未返回）'
    } else {
        $L += ('退出码: ' + [string]$EnumInfo.Py0pExit)
        foreach ($ln in ($EnumInfo.Py0pText -split "`r?`n")) { $L += ('    ' + $ln) }
        if ($EnumInfo.Py0pError) { $L += ('错误: ' + $EnumInfo.Py0pError) }
    }
    $L += ''
    $L += '[四] PATH 相关项'
    $pathEntries = @($EnumInfo.PathEntries)
    $pyPathEntries = @($pathEntries | Where-Object { $_ -match '(?i)python' })
    $L += ('PATH 共 ' + [string]$pathEntries.Count + ' 项，其中含 python 的 ' + [string]$pyPathEntries.Count + ' 项：')
    foreach ($e in $pyPathEntries) { $L += ('    ' + $e) }
    $L += '完整 PATH（按顺序）：'
    $i = 0
    foreach ($e in $pathEntries) {
        $i++
        $L += ('    ' + [string]$i + '. ' + $e)
    }
    $L += ''
    $L += '[五] 候选 Python 及验证结果（每个候选都用同目录 python.exe 真跑验证）'
    $L += '验证命令: python.exe -c "import sys,tkinter; sys.exit(0 if sys.version_info>=(3,9) else 2)"'
    $L += '验证要求: 退出码 0（能运行 + Python >= 3.9 + 带 tkinter），同目录还必须有 pythonw.exe'
    if ($Results.Count -eq 0) {
        $L += '（没有找到任何候选路径）'
    } else {
        $ci = 0
        foreach ($r in $Results) {
            $ci++
            $L += (Get-CandidateReportLine -Result $r -Index $ci)
        }
    }
    $L += ''
    $L += '[六] 最终选择'
    $L += ('    ' + $FinalChoice)
    $L += ''
    $L += '[七] 安装 / 修复动作'
    $L += ('    ' + $InstallNote)
    foreach ($a in $script:Actions) { $L += ('    ' + $a) }
    $L += ''
    $L += '[八] 启动结果'
    $L += ('    ' + $LaunchNote)
    $L += ''
    $L += '[九] 怎么办'
    $L += '    * 如果上面有"有效"的候选但仍启动失败：把本文件发回给作者。'
    $L += '    * 如果全都没有效：双击"修复启动环境.bat"，按提示自动安装 Python 3。'
    $L += '    * 也可以手动安装 Python 3.9+（python.org），安装时勾选 Add python.exe to PATH。'
    $L += '    * 本程序不会修改任何系统设置；安装 Python 只装到当前用户目录。'
    $L += '============================================================'
    return ($L -join "`r`n")
}

function Write-DiagnosticReport {
    param(
        [string]$ModeText,
        $EnumInfo,
        [object[]]$Results,
        [string]$FinalChoice = '（尚未选择）',
        [string]$InstallNote = '（未执行安装）',
        [string]$LaunchNote = '（未启动）'
    )
    if ($DryRun) { return $false }
    try {
        $text = Format-DiagnosticReport -ModeText $ModeText -EnumInfo $EnumInfo -Results $Results `
            -FinalChoice $FinalChoice -InstallNote $InstallNote -LaunchNote $LaunchNote
        $enc = [System.Text.UTF8Encoding]::new($true)
        [System.IO.File]::WriteAllText($script:DiagFile, $text, $enc)
        return $true
    } catch {
        Write-Warn ('提示：写 启动诊断.txt 失败：' + $_.Exception.Message)
        return $false
    }
}

function Write-CandidateLine {
    param([int]$Index, [int]$Total, $Result)
    Write-Host ('  [{0}/{1}] {2}' -f $Index, $Total, $Result.Source) -ForegroundColor Gray
    Write-Host ('        候选: ' + $Result.PythonwExe) -ForegroundColor Gray
    if ($Result.Valid) {
        Write-Host ('        结果: 有效 —— Python ' + $Result.Version + ' / tkinter ' + $Result.Tk) -ForegroundColor Green
    } else {
        Write-Host ('        结果: 跳过 —— ' + $Result.Detail) -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 静默模式失败后：用可见窗口重新调用自己
# ---------------------------------------------------------------------------
function Restart-Visible {
    param([string[]]$ExtraArgs = @())
    if (-not $script:PowerShellExe -or -not (Test-Path -LiteralPath $script:PowerShellExe -PathType Leaf)) {
        $script:PowerShellExe = 'powershell.exe'
    }
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $script:ScriptPath + '"'))
    foreach ($a in $ExtraArgs) { $argList += $a }
    try {
        Add-Action ('静默模式失败，已用可见窗口重新启动：' + ($argList -join ' '))
        Start-Process -FilePath $script:PowerShellExe -ArgumentList $argList -WorkingDirectory $script:Root | Out-Null
    } catch {
        Write-Err ('无法打开可见窗口：' + $_.Exception.Message)
    }
}

function Write-ManualGuide {
    Write-Line ''
    Write-Line '请手动安装 Python 3（3.9 或更高版本），然后重新双击启动器：'
    Write-Line '  1. 打开 https://www.python.org/downloads/windows/'
    Write-Line '     （国内网络可用镜像：https://mirrors.huaweicloud.com/python/3.12.8/ ）'
    Write-Line '  2. 下载 Windows installer (64-bit)'
    Write-Line '  3. 安装时务必勾选 "Add python.exe to PATH"，其它默认即可'
    Write-Line '  4. 装完后双击"修复启动环境.bat"重新检测'
}

# ---------------------------------------------------------------------------
# 运行自动安装脚本（前台、可见进度）
# ---------------------------------------------------------------------------
function Invoke-Installer {
    if (-not (Test-Path -LiteralPath $script:Installer -PathType Leaf)) {
        Add-Action '安装脚本 scripts\install_python.ps1 不存在，跳过自动安装'
        Write-Err '缺少安装脚本 scripts\install_python.ps1，无法自动安装。'
        return 1
    }
    Write-Line ''
    Write-Line '正在调用 scripts\install_python.ps1 下载并安装 Python 3.12...' -Color Gray
    if (-not $script:PowerShellExe -or -not (Test-Path -LiteralPath $script:PowerShellExe -PathType Leaf)) {
        $script:PowerShellExe = 'powershell.exe'
    }
    $rc = 1
    try {
        & $script:PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $script:Installer -NoPause
        $rc = $LASTEXITCODE
    } catch {
        Add-Action ('调用安装脚本出错：' + $_.Exception.Message)
        Write-Err ('调用安装脚本出错：' + $_.Exception.Message)
        return 1
    }
    Add-Action ('安装脚本退出码: ' + [string]$rc)
    return $rc
}

# ---------------------------------------------------------------------------
# 首次有效候选的公共启动处理
# ---------------------------------------------------------------------------
function Start-And-Report {
    param(
        $Valid,
        $EnumInfo,
        [object[]]$Results,
        [string]$ModeText,
        [string]$InstallNote = '（未执行安装）'
    )
    $changed = Save-RememberedPath -PythonwExe $Valid.PythonwExe
    if ($changed) {
        Add-Action ('已更新 pythonw_path.txt：' + $Valid.PythonwExe)
        if ($Interactive -or $Diagnose) {
            Write-Line ('已记录 Python 路径到 pythonw_path.txt：' + $Valid.PythonwExe) -Color DarkGray
        }
    }
    $finalChoice = $Valid.PythonwExe + '（Python ' + $Valid.Version + '，tkinter ' + $Valid.Tk + '，来源：' + $Valid.Source + '）'
    if ($Diagnose) {
        $written = Write-DiagnosticReport -ModeText $ModeText -EnumInfo $EnumInfo -Results $Results `
            -FinalChoice $finalChoice -InstallNote $InstallNote -LaunchNote '（准备启动）'
        if ($written -and ($Interactive -or $Diagnose)) {
            Write-Line ('诊断报告已写入：' + $script:DiagFile) -Color DarkGray
        }
    }
    if ($Interactive -or $Diagnose) {
        Write-Line ''
        Write-Line '正在启动建模向导...'
    }
    $launch = Start-WizardProcess -PythonwExe $Valid.PythonwExe -WizardFile $script:Wizard -WorkDir $script:Root
    if ($launch.HasExited -or -not $launch.Started) {
        $errText = '启动失败：pythonw 进程在 2 秒内退出了'
        if ($launch.ExitCode -ne $null) { $errText = $errText + '（退出码 ' + [string]$launch.ExitCode + '）' }
        if ($launch.Error) { $errText = '启动失败：' + $launch.Error }
        Write-Err ''
        Write-Err $errText
        Write-Err '请查看程序目录下的 启动诊断.txt，或双击“修复启动环境.bat”。'
        Write-DiagnosticReport -ModeText $ModeText -EnumInfo $EnumInfo -Results $Results `
            -FinalChoice $finalChoice -InstallNote $InstallNote -LaunchNote $errText | Out-Null
        if (-not $Interactive) {
            Restart-Visible -ExtraArgs @('-Diagnose', '-Interactive', '-Hold')
            return 0
        }
        if ($Hold) { Read-Host '按回车退出' | Out-Null }
        return 3
    }
    if ($Diagnose) {
        Write-DiagnosticReport -ModeText $ModeText -EnumInfo $EnumInfo -Results $Results `
            -FinalChoice $finalChoice -InstallNote $InstallNote -LaunchNote ('已启动，PID ' + [string]$launch.Id) | Out-Null
    }
    if ($Interactive -or $Diagnose) {
        Write-Ok ''
        Write-Ok ('建模向导已启动（PID ' + [string]$launch.Id + '）。本窗口可以关闭。')
    }
    return 0
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
function Invoke-Main {
    Write-Banner
    $interactiveMode = $true
    if ($Silent.IsPresent -and -not $Interactive.IsPresent) { $interactiveMode = $false }

    $modeParts = @()
    if ($interactiveMode) { $modeParts += '-Interactive' } else { $modeParts += '-Silent' }
    if ($DryRun) { $modeParts += '-DryRun' }
    if ($Diagnose) { $modeParts += '-Diagnose' }
    if ($NoInstall) { $modeParts += '-NoInstall' }
    $modeText = $modeParts -join ' '

    if (-not (Test-Path -LiteralPath $script:Wizard -PathType Leaf)) {
        Write-Err ('找不到 midas_wizard.py：' + $script:Wizard)
        Write-Line '请把本启动器和 midas_wizard.py 放在同一个文件夹里！'
        if ($Hold) { Read-Host '按回车退出' | Out-Null }
        return 2
    }

    $enum = Get-PythonCandidateListSafe -Root $script:Root -RememberFile $script:RememberFile -SkipSources $SkipSources
    $candidates = @($enum.Candidates)
    if ($Diagnose -or $DryRun) {
        Write-Line ('检测到 ' + [string]$candidates.Count + ' 个候选 Python，逐个验证（每个最多 15 秒）...')
    } elseif ($interactiveMode) {
        Write-Line '正在检测 Python 运行环境...'
    }

    $results = New-Object System.Collections.ArrayList
    $valid = $null
    $idx = 0
    foreach ($c in $candidates) {
        $idx++
        # 单候选兜底：任何意外异常只影响这一个候选，绝不打崩整轮扫描
        try {
            $r = Test-PythonwCandidate -Path $c.Path -Source $c.Source -WorkDir $script:Root -TimeoutSec 15
        } catch {
            $r = [pscustomobject]@{
                Source = $c.Source; Requested = $c.Path; PythonExe = ''; PythonwExe = ''
                Valid = $false; Version = ''; Tk = ''
                Detail = ('候选校验异常，跳过：' + $_.Exception.Message)
            }
        }
        [void]$results.Add($r)
        if ($Diagnose) { Write-CandidateLine -Index $idx -Total $candidates.Count -Result $r }
        if ($r.Valid -and -not $valid) {
            $valid = $r
            if (-not $Diagnose) { break }
        }
    }
    $resultsArray = @($results)

    if ($valid) {
        if ($interactiveMode -or $Diagnose) {
            Write-Ok ('找到可用 Python：' + $valid.PythonwExe)
            Write-Line ('    版本 Python ' + $valid.Version + '，tkinter ' + $valid.Tk + '（来源：' + $valid.Source + '）')
        }
    } else {
        if ($interactiveMode -or $Diagnose -or $DryRun) {
            Write-Warn '没有找到可用的 Python 3（要求：能运行、版本 >= 3.9、带 tkinter）。'
        }
    }

    # ---- DryRun：只检测 ----
    if ($DryRun) {
        if ($valid) {
            Write-Line ('[DryRun] 将使用：' + $valid.PythonwExe + '（不启动、不写文件）')
            $finalChoice = $valid.PythonwExe + '（Python ' + $valid.Version + '，tkinter ' + $valid.Tk + '）'
        } else {
            $finalChoice = '（没有有效候选）'
            Write-Line '[DryRun] 未找到有效 Python（不安装、不启动、不写文件）。'
        }
        if ($Diagnose) {
            Write-Line ''
            $reportText = Format-DiagnosticReport -ModeText $modeText -EnumInfo $enum.Info -Results $resultsArray `
                -FinalChoice $finalChoice -InstallNote '（DryRun：不安装）' -LaunchNote '（DryRun：不启动、不写文件）'
            Write-Line $reportText
        }
        if ($valid) { return 0 }
        return 1
    }

    # ---- 找到：写路径 + 启动 ----
    if ($valid) {
        return (Start-And-Report -Valid $valid -EnumInfo $enum.Info -Results $resultsArray -ModeText $modeText)
    }

    # ---- 没找到 ----
    if ($Diagnose) {
        $written = Write-DiagnosticReport -ModeText $modeText -EnumInfo $enum.Info -Results $resultsArray `
            -FinalChoice '（没有有效候选）' -InstallNote '（尚未执行安装）' -LaunchNote '（尚未启动）'
        if ($written) { Write-Line ('诊断报告已写入：' + $script:DiagFile) -Color DarkGray }
    }

    if (-not $interactiveMode) {
        # 静默模式：改为可见窗口，让用户看到安装提示和进度
        $extra = @('-Diagnose', '-Interactive', '-Hold')
        if ($NoInstall) { $extra += '-NoInstall' }
        Restart-Visible -ExtraArgs $extra
        return 0
    }

    if ($NoInstall) {
        Write-Line ''
        Write-Warn '已指定 -NoInstall：不自动安装 Python。'
        Write-ManualGuide
        if ($Hold) { Read-Host '按回车退出' | Out-Null }
        return 1
    }

    if (-not (Test-Path -LiteralPath $script:Installer -PathType Leaf)) {
        Write-Err '缺少安装脚本 scripts\install_python.ps1，无法自动安装。'
        Write-ManualGuide
        return 1
    }

    Write-Line ''
    $answer = Read-Host '是否自动下载并安装 Python 3.12（约 25 MB，只需装一次）？[Y/n]'
    $answer = ([string]$answer).Trim()
    if ($answer -and ($answer -notmatch '^[Yy]')) {
        Write-Line ''
        Write-Warn '已选择不自动安装。'
        Write-ManualGuide
        if ($Hold) { Read-Host '按回车退出' | Out-Null }
        return 1
    }

    $installRc = Invoke-Installer
    $installNote = '已执行自动安装（退出码 ' + [string]$installRc + '）'

    # 安装后复验：重新完整检测一遍（安装脚本会写 pythonw_path.txt，也扫默认目录 / 注册表）
    Write-Line ''
    Write-Line '安装脚本已结束，重新检测 Python 环境...' -Color Gray
    $enum2 = Get-PythonCandidateListSafe -Root $script:Root -RememberFile $script:RememberFile -SkipSources ''
    $results2 = New-Object System.Collections.ArrayList
    $valid2 = $null
    foreach ($c in @($enum2.Candidates)) {
        try {
            $r2 = Test-PythonwCandidate -Path $c.Path -Source $c.Source -WorkDir $script:Root -TimeoutSec 15
        } catch {
            $r2 = [pscustomobject]@{
                Source = $c.Source; Requested = $c.Path; PythonExe = ''; PythonwExe = ''
                Valid = $false; Version = ''; Tk = ''
                Detail = ('候选校验异常，跳过：' + $_.Exception.Message)
            }
        }
        [void]$results2.Add($r2)
        if ($r2.Valid -and -not $valid2) { $valid2 = $r2; break }
    }
    if ($valid2) {
        Write-Ok ('安装成功，找到可用 Python：' + $valid2.PythonwExe)
        return (Start-And-Report -Valid $valid2 -EnumInfo $enum2.Info -Results @($results2) -ModeText $modeText -InstallNote $installNote)
    }

    Write-Err ''
    Write-Err '安装结束，但仍然没有检测到可用的 Python 3。'
    Write-ManualGuide
    Write-DiagnosticReport -ModeText $modeText -EnumInfo $enum2.Info -Results @($results2) `
        -FinalChoice '（安装后仍没有有效候选）' -InstallNote $installNote -LaunchNote '（未启动）' | Out-Null
    if ($Hold) { Read-Host '按回车退出' | Out-Null }
    return 1
}

# ---------------------------------------------------------------------------
# 入口
#   约定退出码（供 launch_wizard.bat 判断）：
#     0 = 成功（已启动 / DryRun 找到有效 Python）
#     1 = 失败（没找到 Python / 用户放弃安装 / DryRun 没找到）
#     2 = 程序文件缺失（midas_wizard.py 不在旁边）
#     3 = 找到了 Python 但 pythonw 启动后 2 秒内退出
#   任何情况下都不返回 9：单个候选/来源的异常只跳过它自己，不能让整个启动器崩溃。
# ---------------------------------------------------------------------------
$script:ExitCode = 1
try {
    $script:ExitCode = Invoke-Main
} catch {
    Write-Err ''
    Write-Err ('启动器内部错误：' + $_.Exception.Message)
    try { Write-Err ('出错位置：' + $_.InvocationInfo.PositionMessage) } catch { }
    if ($Hold) { try { Read-Host '按回车退出' | Out-Null } catch { } }
    $script:ExitCode = 1
}
exit $script:ExitCode
