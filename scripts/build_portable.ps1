<#
.SYNOPSIS
    从仓库自动组装「MIDAS Civil NX 建模向导」便携包，并打成 zip。

.DESCRIPTION
    v2.0.3 交付用打包脚本（幂等：每次重跑先清空输出目录和旧 zip）。

    从仓库（默认本脚本的上级目录）自动组装：
      · 程序 .py          —— 仓库根目录全部 *.py
      · 模型文件\         —— 15 套示例（仓库里该目录被 .gitignore 排除，可能为空，
                             此时自动回退到 -ReferencePackage 指向的上一版便携包）
      · 示例截面\         —— 同上
      · 界面截图\         —— 照 v2.0.2 包里的 5 个文件名，从仓库 docs\*.png 复制
      · 启动器            —— launch_wizard.bat -> 启动建模向导.bat
                             启动建模向导_静默.vbs、修复启动环境.bat
                             scripts\launch_wizard.ps1、scripts\install_python.ps1
      · 使用说明.txt      —— 仓库源文件是 UTF-8，包内转成 GBK(936)（记事本直接打开不乱码）
      · README_使用前必读.txt —— 内置模板，写成 UTF-8 with BOM（与 v2.0.2 包一致）
      · VERSION.txt       —— 版本号

    不打包：__pycache__、*.log、pythonw_path.txt、启动诊断.txt、.git、tests、docs、build、dist、
            venv、旧启动器 安装环境.ps1。

.PARAMETER RepoRoot
    仓库根目录。默认 = 本脚本所在目录的上一级。

.PARAMETER OutRoot
    便携包输出到哪个目录下。默认 F:\新建文件夹。

.PARAMETER Version
    版本号，默认 2.0.3。输出目录名 = MIDAS建模向导_便携版_v<版本>。

.PARAMETER ReferencePackage
    仓库里 模型文件\ / 示例截面\ 为空时，从哪个上一版便携包里取（只读复制，不修改它）。
    默认 F:\新建文件夹\MIDAS建模向导_便携版_v2.0.2。

.PARAMETER ExamplesFrom
    直接指定 模型文件\ 的来源目录（优先于自动判断）。

.PARAMETER SectionsFrom
    直接指定 示例截面\ 的来源目录（优先于自动判断）。

.PARAMETER AllowIncomplete
    开发期开关：启动器文件还没就绪时允许缺文件、只报警告不报错。

.PARAMETER NoZip
    只组装文件夹，不打 zip。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_portable.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -NoZip -AllowIncomplete
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$OutRoot = 'F:\新建文件夹',
    [string]$PackageName = 'MIDAS建模向导_便携版',
    [string]$Version = '2.0.3',
    [string]$ReferencePackage = 'F:\新建文件夹\MIDAS建模向导_便携版_v2.0.2',
    [string]$ExamplesFrom = '',
    [string]$SectionsFrom = '',
    [switch]$AllowIncomplete,
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:ErrorList = New-Object System.Collections.Generic.List[string]

function Write-Head([string]$text) {
    Write-Host ''
    Write-Host ('== ' + $text + ' ==') -ForegroundColor Cyan
}
function Write-Item([string]$text) {
    Write-Host ('   ' + $text)
}
function Write-Warn2([string]$text) {
    Write-Host ('   [警告] ' + $text) -ForegroundColor Yellow
}
function Write-Err2([string]$text) {
    Write-Host ('   [错误] ' + $text) -ForegroundColor Red
}
function Add-BuildError([string]$text) {
    $script:ErrorList.Add($text) | Out-Null
    Write-Err2 $text
}
function Test-RequiredFile([string]$path, [string]$what) {
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $true }
    if ($script:AllowIncomplete) {
        Write-Warn2 ('缺少' + $what + '（' + $path + '）—— -AllowIncomplete 下跳过')
        return $false
    }
    Add-BuildError ('缺少' + $what + '：' + $path)
    return $false
}
function Copy-OneFile([string]$src, [string]$dst, [string]$what) {
    if (-not (Test-RequiredFile $src $what)) { return $false }
    $parent = Split-Path -Parent $dst
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Write-Item ('+ ' + (Split-Path -Leaf $dst))
    return $true
}
function Reset-OutputDirectory([string]$path) {
    if (Test-Path -LiteralPath $path) {
        # F:\新建文件夹 下的旧目录可能带只读属性（压缩/复制留下的），先全清掉
        & attrib.exe -R ($path + '\*.*') /S /D 2>$null | Out-Null
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $path) {
            Start-Sleep -Milliseconds 500
            & cmd.exe /c ('rd /s /q "' + $path + '"') 2>$null | Out-Null
        }
        if (Test-Path -LiteralPath $path) {
            throw ('无法清空输出目录（可能被占用或只读）：' + $path)
        }
        Write-Item ('已清空旧目录：' + $path)
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
function Copy-DirectoryContent([string]$src, [string]$dst, [string]$what) {
    if (-not (Test-Path -LiteralPath $src -PathType Container)) {
        Add-BuildError ('缺少' + $what + '源目录：' + $src)
        return 0
    }
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
    $n = @(Get-ChildItem -LiteralPath $dst -Recurse -File -Force).Count
    Write-Item ('+ ' + $what + '：' + $n + ' 个文件（来自 ' + $src + '）')
    return $n
}
function New-CleanText([string]$text) {
    $t = $text -replace "`r`n", "`n"
    $t = $t -replace "`r", "`n"
    return ($t -replace "`n", "`r`n")
}
function Write-GbkFile([string]$srcUtf8, [string]$dst, [string]$what) {
    $text = [IO.File]::ReadAllText($srcUtf8, [Text.Encoding]::UTF8)
    $text = New-CleanText $text
    $gbk = [Text.Encoding]::GetEncoding(936, [Text.EncoderFallback]::ExceptionFallback, [Text.DecoderFallback]::ExceptionFallback)
    try {
        $bytes = $gbk.GetBytes($text)
    } catch [Text.EncoderFallbackException] {
        Add-BuildError ($what + ' 里有 GBK(936) 无法表示的字符（偏移 ' + $_.Exception.Index + '，字符 "' + $_.Exception.CharUnknown + '"），请改用 GBK 里有的字符')
        return $false
    }
    [IO.File]::WriteAllBytes($dst, $bytes)
    Write-Item ('+ ' + (Split-Path -Leaf $dst) + '（GBK/936，' + $bytes.Length + ' 字节）')
    return $true
}
function Write-Utf8BomFile([string]$path, [string]$text, [string]$what) {
    $text = New-CleanText $text
    # 注意：UTF8Encoding.GetBytes() 不会带 BOM，必须手动拼 GetPreamble()
    $body = (New-Object System.Text.UTF8Encoding($false)).GetBytes($text)
    $preamble = [byte[]](0xEF, 0xBB, 0xBF)
    $bytes = New-Object byte[] ($preamble.Length + $body.Length)
    [Array]::Copy($preamble, 0, $bytes, 0, $preamble.Length)
    [Array]::Copy($body, 0, $bytes, $preamble.Length, $body.Length)
    [IO.File]::WriteAllBytes($path, $bytes)
    Write-Item ('+ ' + (Split-Path -Leaf $path) + '（' + $what + '，' + $bytes.Length + ' 字节）')
}
function Get-HumanSize([double]$bytes) {
    if ($bytes -ge 1MB) { return ('{0:N2} MB' -f ($bytes / 1MB)) }
    if ($bytes -ge 1KB) { return ('{0:N1} KB' -f ($bytes / 1KB)) }
    return ('' + [int]$bytes + ' B')
}
function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
function Test-TextFileShape([string]$path, [string]$what, [switch]$RequireBom, [switch]$RequireAscii) {
    $bytes = [IO.File]::ReadAllBytes($path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)
    if ($RequireBom -and -not $hasBom) {
        Add-BuildError ($what + ' 缺少 UTF-8 BOM')
    }
    if ($RequireAscii) {
        $nonAscii = 0
        foreach ($b in $bytes) { if ($b -gt 127) { $nonAscii++ } }
        if ($nonAscii -gt 0) {
            Add-BuildError ($what + ' 含 ' + $nonAscii + ' 个非 ASCII 字节（应纯 ASCII）')
        }
    }
    $lf = 0
    $crlf = 0
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        if ($bytes[$i] -eq 10) {
            $lf++
            if ($i -gt 0 -and $bytes[$i - 1] -eq 13) { $crlf++ }
        }
    }
    if ($lf -gt 0 -and $lf -ne $crlf) {
        Add-BuildError ($what + ' 换行不是纯 CRLF（LF=' + $lf + '，CRLF=' + $crlf + '）')
    }
    Write-Item ('编码/换行检查通过：' + $what)
}

# ---------------------------------------------------------------- 内置 README 模板
$README_TEMPLATE = @'
MIDAS Civil NX 建模向导 · 便携版 v2.0.3
=========================================================

一句话：**不用学 MIDAS，按步骤填表就能生成桥梁模型的 .mct 文件**，
在 MIDAS Civil NX 里「文件 → 导入 → MCT 命令文件」就能得到完整模型。

本程序是绿色便携的：整个文件夹拷到 U 盘、换台电脑都能用，不写注册表、不联网。

--------------------------------------------------------
一、怎么启动（第一次看这里）
--------------------------------------------------------
两种方式，任选一种：

方式 A：免安装 exe（最省事，推荐）
  如果你的文件夹里还有一个 **「MIDAS建模向导.exe」**（单独发的大文件），
  直接双击它就行 —— 不需要装 Python，也不会被系统里坏掉的 Python 启动器影响。
  第一次打开时它会在 exe 旁边自动生成 模型文件\ 和 示例截面\，以后输入都存在那里。
  只想快点打开程序，就双击这个。

方式 B：便携版（本文件夹）
  1. 双击 **「启动建模向导.bat」**。
     · 如果这台电脑还没装 Python：黑窗口会自动下载安装（约 25 MB，只需一次，
       只装到当前用户目录，不需要管理员权限）；
     · 装好后程序窗口自动出现。
  2. 想要"不闪黑框"的启动方式：双击 **「启动建模向导_静默.vbs」**。
  3. 启动失败 / 报错：双击 **「修复启动环境.bat」**。它会重新检测这台电脑上的
     Python、把检测结果写进「启动诊断.txt」，能自动修就修，修不了会告诉你怎么做。

  ★ v2.0.3 起，启动器不再"看见 pyw 就直接用"，而是把找到的每个 Python 都实际
    试跑一遍（能运行 + 版本 >= 3.9 + 带 tkinter），验证通过才启动。
    这样即使系统里的旧启动器指向一个已经删掉的 Python，也不会再弹
    "Python Launcher is sorry to say ... Unable to create process using" 这种错误。

> 小提示：双击 .bat 时先闪一下黑窗口是正常的 —— 黑窗只是启动器在找 Python，
> 找到后它自己就退出了，紧接着出现的才是程序本体。

--------------------------------------------------------
二、内置 9 条建模流程（右上角「流程」下拉切换）
--------------------------------------------------------
1. 梁桥（一次成桥）—— 8 步，支点/单元/材料/截面(DXF)/支承/工况/荷载；
   可选移动荷载·模态·屈曲（第 06/07 讲）
2. 拱桥 · 施工阶段 —— 拱轴线 + 施工部件 + 施工阶段联合截面 + 收缩徐变
3. 斜拉桥（鱼骨刺·成桥）—— 三跨双塔、单/双索面、索 + 初拉力、塔梁连接
4. 悬索桥 —— 主缆抛物线、吊杆、加劲梁、桥塔、初拉力
5. 施工阶段梁桥 —— 连续刚构（悬臂浇筑）/ 简支变连续
6. 钢管混凝土拱 —— 架钢管 → 灌混凝土 → 联合截面 → 二期（另给一次成拱对比）
7. 平面刚架（自由建模）—— 节点/杆件/荷载自由填；**内置矩阵位移法求解器**，
   不打开 MIDAS 也能直接算杆端力、支反力；含习题 8(a)/(b) 预设
8. 预制 T 梁 + 现浇桥面板 —— 20 m T 梁先架后浇，施工阶段联合截面，
   自动给内力分配/应力、与一次成型对比
9. 两跨钢管混凝土（湿重对比）—— 一次生成两套 MCT，对比两种湿重模拟方式

每条流程都是「填数值 → 立刻校验 → 下一步」，填错了底部红字会拦住；
截面页面都支持参数化截面和导入 CAD（DXF）自定义截面。

--------------------------------------------------------
三、生成成果 / 导入 MIDAS
--------------------------------------------------------
最后一步点「生成成果」，会写出一套文件（同一文件夹里）：
  · <名字>.mct            —— 导入 MIDAS 用（GBK 编码）
  · <名字>_数据表.txt      —— 填过的数据汇总
  · <名字>_数据.json       —— 可再打开继续改
  · 导入说明.txt           —— 这个模型怎么来的、导入后核对什么
MIDAS Civil NX 里：文件 → 导入 → MIDAS/Civil MCT 文件 → 选 .mct。

--------------------------------------------------------
四、示例工程（模型文件\ 文件夹）
--------------------------------------------------------
程序目录\模型文件\ 里每个示例一个文件夹（.mct + 数据表 + 导入说明），共 15 个，例如：
  示例_平面刚架_习题8         示例_预制T梁20m_联合截面
  示例_两跨CFST_湿重对比      示例_斜拉桥220m_鱼骨刺成桥
  示例_悬索桥                 示例_连续刚构60+100+60
  示例_简支变连续2x20         示例_钢管混凝土拱50m
  示例_拱桥100m_模态与稳定    示例_简支梁10m_移动荷载与模态 …
「示例截面\」里是 DXF 自定义截面的图纸示例。

--------------------------------------------------------
五、常见问题
--------------------------------------------------------
Q：双击后黑窗闪一下就没后续？
A：先看有没有出现「MIDAS Civil NX 建模向导」窗口；没有就再双击一次 bat，
   按提示装 Python。也可以双击「修复启动环境.bat」让它重新检查一遍。

Q：报「Python Launcher is sorry to say ... Unable to create process using ...」？
A：这不是本程序自己的报错，是你电脑上 Windows 自带的 pyw 启动器坏了
   （它记着的那个 Python 已经被删除或者挪走了）。按顺序做：
   1) 双击 **「修复启动环境.bat」** —— 它会跳过坏掉的 pyw，重新找到能用的 Python
      （找不到就自动装一个），整个过程写进同目录的「启动诊断.txt」；
   2) 如果还不行，**改用免安装 exe**（MIDAS建模向导.exe，双击即用，完全不用 Python）；
   3) 还是搞不定，就把程序文件夹里的 **「启动诊断.txt」** 发回来 —— 里面记录了
      这台电脑的 Windows 版本、pythonw_path.txt 内容、所有候选 Python 及验证结果，
      看一眼就知道卡在哪。

Q：怎么知道程序版本 / 能不能用？
A：在文件夹里打开命令行运行
     python midas_wizard.py --version     （看到 2.0.3）
     python midas_wizard.py --selftest    （9 条流程自检，应该 0 失败）

Q：打开了两个程序窗口？
A：双击了两次。程序没有单实例限制，关掉多余那个即可，数据以最后保存的为准。

Q：导入 MIDAS 报错？
A：把 MIDAS 命令行窗口里的**行号**记下来，对照 .mct 第几行；也可以看同目录的
   「导入说明.txt」。常见原因：截面尺寸没填全、荷载全 0、编码选错（中文 Windows 用 GBK）。

Q：截面导入后看不见 / 特别大？
A：自定义截面的「图纸单位」选错了（mm 当 m 会差 1000 倍），回截面那步改单位重算。

--------------------------------------------------------
六、版本与来源
--------------------------------------------------------
· 版本：2.0.3（2026-10-31）
· 本次更新只修启动（启动器先验证再启动 + 新增修复启动环境.bat/启动诊断.txt），
  程序功能和生成的模型与 2.0.2 完全一致。
· 项目主页（开源，MIT 许可）：https://github.com/shi-zhong111/midas-mct-wizard
· 非官方工具：与 MIDAS IT 无隶属关系；生成的模型导入 MIDAS 后请自行核对
  支点、单元、材料、截面、支承、荷载，工程结果以 MIDAS 自己算的为准。
· 本机未做 MIDAS 真机导入验证的新流程（移动荷载/模态/屈曲、平面刚架端部释放、
  两跨 CFST 湿重对比）属于首次实测项，导入报错请按上面的行号方法反馈。
'@

# ---------------------------------------------------------------- 开跑
# PS 5.1 里 param 默认值中 $PSScriptRoot 可能是空的，放到脚本正文里取
if (-not $RepoRoot) {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir) {
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    $RepoRoot = Split-Path -Parent $scriptDir
}
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$pkgName = $PackageName + '_v' + $Version
$outDir = Join-Path $OutRoot $pkgName
$zipPath = Join-Path $OutRoot ($pkgName + '.zip')

Write-Head ('组装 MIDAS建模向导 便携包 v' + $Version)
Write-Item ('仓库：' + $repo)
Write-Item ('输出：' + $outDir)

if (-not (Test-Path -LiteralPath (Join-Path $repo 'midas_wizard.py'))) {
    throw ('这不是仓库根目录（找不到 midas_wizard.py）：' + $repo)
}

# ---- 1) 清空输出目录（幂等） -------------------------------------------
Reset-OutputDirectory $outDir

# ---- 2) 程序 .py（仓库根，排除子目录） ----------------------------------
Write-Head '1/7 程序 .py'
$pyFiles = @(Get-ChildItem -LiteralPath $repo -File -Filter '*.py' |
    Where-Object { $_.Extension -eq '.py' } |
    Sort-Object Name)
foreach ($f in $pyFiles) {
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $outDir $f.Name) -Force
}
Write-Item ('+ ' + $pyFiles.Count + ' 个 .py：' + (($pyFiles | ForEach-Object { $_.Name }) -join ', '))
if ($pyFiles.Count -lt 15) {
    Add-BuildError ('程序 .py 只有 ' + $pyFiles.Count + ' 个，明显不够（应 >= 15）')
}

# ---- 3) 模型文件 / 示例截面（仓库为空则回退参考包） --------------------
Write-Head '2/7 模型文件 / 示例截面'
$examplesSrc = ''
if ($ExamplesFrom) {
    $examplesSrc = $ExamplesFrom
} elseif ((Test-Path -LiteralPath (Join-Path $repo '模型文件') -PathType Container) -and
          (@(Get-ChildItem -LiteralPath (Join-Path $repo '模型文件') -Directory -Force)).Count -gt 0) {
    $examplesSrc = Join-Path $repo '模型文件'
} else {
    $refModels = Join-Path $ReferencePackage '模型文件'
    if (Test-Path -LiteralPath $refModels -PathType Container) {
        $examplesSrc = $refModels
        Write-Warn2 ('仓库内 模型文件\ 为空（.gitignore 排除），改用参考包：' + $refModels)
    } else {
        Add-BuildError ('仓库内 模型文件\ 为空，且参考包也没有：' + $refModels)
    }
}
$nModels = 0
if ($examplesSrc) {
    $nModels = Copy-DirectoryContent $examplesSrc (Join-Path $outDir '模型文件') '模型文件'
    # 开发机的运行时会话文件不进包（里面有本机输出路径）
    $staleSession = Join-Path $outDir '模型文件\_wizard_session.json'
    if (Test-Path -LiteralPath $staleSession) {
        Remove-Item -LiteralPath $staleSession -Force
        Write-Item '已排除开发机会话文件：模型文件\_wizard_session.json'
    }
    $modelOutDir = Join-Path $outDir '模型文件'
    $exampleDirs = @(Get-ChildItem -LiteralPath $modelOutDir -Directory -Force)
    if ($exampleDirs.Count -lt 15) {
        Add-BuildError ('模型文件\ 里只有 ' + $exampleDirs.Count + ' 个示例文件夹（应 >= 15）')
    }
    $thinDirs = @($exampleDirs | Where-Object {
        @(Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force).Count -lt 3
    })
    if ($thinDirs.Count -gt 0) {
        Add-BuildError ('这些示例文件夹里不足 3 个文件：' + (($thinDirs | ForEach-Object { $_.Name }) -join ', '))
    }
    Write-Item ('示例文件夹：' + $exampleDirs.Count + ' 套（每套 >= 3 个文件）')
    $nModels = @(Get-ChildItem -LiteralPath $modelOutDir -Recurse -File -Force).Count
}

$sectionsSrc = ''
if ($SectionsFrom) {
    $sectionsSrc = $SectionsFrom
} elseif ((Test-Path -LiteralPath (Join-Path $repo '示例截面') -PathType Container) -and
          (@(Get-ChildItem -LiteralPath (Join-Path $repo '示例截面') -File -Force -Recurse)).Count -gt 0) {
    $sectionsSrc = Join-Path $repo '示例截面'
} else {
    $refSections = Join-Path $ReferencePackage '示例截面'
    if (Test-Path -LiteralPath $refSections -PathType Container) {
        $sectionsSrc = $refSections
        Write-Warn2 ('仓库内 示例截面\ 为空，改用参考包：' + $refSections)
    } else {
        Add-BuildError ('找不到 示例截面\（仓库和参考包都没有）')
    }
}
$nSections = 0
if ($sectionsSrc) {
    $nSections = Copy-DirectoryContent $sectionsSrc (Join-Path $outDir '示例截面') '示例截面'
}
if ($nModels -lt 20) {
    Add-BuildError ('模型文件\ 只有 ' + $nModels + ' 个文件，明显不对（应 >= 60）')
}

# ---- 4) 界面截图（名字照 v2.0.2 包） ------------------------------------
Write-Head '3/7 界面截图'
$shotMap = [ordered]@{
    'flow-cfst2.png'      = 'flow-cfst2.png'
    'flow-frame.png'      = 'flow-frame.png'
    'flow-tbeam.png'      = 'flow-tbeam.png'
    'import-mct.png'      = 'import-mct.png'
    'step6-loadcases.png' = 'step6-loadcases.png'
}
$shotsDir = Join-Path $outDir '界面截图'
New-Item -ItemType Directory -Path $shotsDir -Force | Out-Null
$docsDir = Join-Path $repo 'docs'
foreach ($pair in $shotMap.GetEnumerator()) {
    $srcShot = Join-Path $docsDir $pair.Value
    Copy-OneFile $srcShot (Join-Path $shotsDir $pair.Key) ('界面截图 ' + $pair.Key) | Out-Null
}

# ---- 5) 启动器 ---------------------------------------------------------
Write-Head '4/7 启动器'
Copy-OneFile (Join-Path $repo 'launch_wizard.bat') (Join-Path $outDir '启动建模向导.bat') '启动器 launch_wizard.bat' | Out-Null
Copy-OneFile (Join-Path $repo '启动建模向导_静默.vbs') (Join-Path $outDir '启动建模向导_静默.vbs') '静默启动器 vbs' | Out-Null
Copy-OneFile (Join-Path $repo '修复启动环境.bat') (Join-Path $outDir '修复启动环境.bat') '修复启动环境.bat' | Out-Null
Copy-OneFile (Join-Path $repo 'scripts\launch_wizard.ps1') (Join-Path $outDir 'scripts\launch_wizard.ps1') 'scripts\launch_wizard.ps1' | Out-Null
Copy-OneFile (Join-Path $repo 'scripts\install_python.ps1') (Join-Path $outDir 'scripts\install_python.ps1') 'scripts\install_python.ps1' | Out-Null
if (-not $AllowIncomplete) {
    # 旧版启动器绝不能混进来
    $oldLauncher = Join-Path $outDir '安装环境.ps1'
    if (Test-Path -LiteralPath $oldLauncher) {
        Add-BuildError '包里混进了旧启动器 安装环境.ps1'
    }

    # 启动器必须"只改名、不改内容"：与仓库源文件逐字节一致
    $launcherChecks = @(
        @{ Out = '启动建模向导.bat';        Src = (Join-Path $repo 'launch_wizard.bat') },
        @{ Out = '启动建模向导_静默.vbs';  Src = (Join-Path $repo '启动建模向导_静默.vbs') },
        @{ Out = '修复启动环境.bat';        Src = (Join-Path $repo '修复启动环境.bat') },
        @{ Out = 'scripts\launch_wizard.ps1';  Src = (Join-Path $repo 'scripts\launch_wizard.ps1') },
        @{ Out = 'scripts\install_python.ps1'; Src = (Join-Path $repo 'scripts\install_python.ps1') }
    )
    foreach ($c in $launcherChecks) {
        $outFile = Join-Path $outDir $c.Out
        if (-not (Test-Path -LiteralPath $outFile -PathType Leaf)) { continue }
        if (-not (Test-Path -LiteralPath $c.Src -PathType Leaf)) {
            Add-BuildError ('仓库里找不到启动器源文件：' + $c.Src)
            continue
        }
        if ((Get-Sha256 $outFile) -ne (Get-Sha256 $c.Src)) {
            Add-BuildError ('包内 ' + $c.Out + ' 与仓库源文件不逐字节一致（只允许改名）')
        } else {
            Write-Item ('逐字节一致：' + $c.Out)
        }
    }

    # 编码 / 换行形态（CI 老坑：bat 丢 BOM 或变 LF 会"双击没反应"）
    $shapeChecks = @(
        @{ Rel = '启动建模向导.bat';        Bom = $true;  Ascii = $false },
        @{ Rel = '修复启动环境.bat';        Bom = $true;  Ascii = $false },
        @{ Rel = '启动建模向导_静默.vbs';  Bom = $false; Ascii = $true  },
        @{ Rel = 'scripts\launch_wizard.ps1';  Bom = $true;  Ascii = $false },
        @{ Rel = 'scripts\install_python.ps1'; Bom = $true;  Ascii = $false }
    )
    foreach ($s in $shapeChecks) {
        $p = Join-Path $outDir $s.Rel
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { continue }
        Test-TextFileShape $p $s.Rel -RequireBom:$s.Bom -RequireAscii:$s.Ascii
    }
}

# ---- 6) 文档 / 版本号 ---------------------------------------------------
Write-Head '5/7 文档'
$srcManual = Join-Path $repo '使用说明.txt'
if (Test-Path -LiteralPath $srcManual -PathType Leaf) {
    Write-GbkFile $srcManual (Join-Path $outDir '使用说明.txt') '使用说明.txt' | Out-Null
} else {
    Add-BuildError ('缺少 使用说明.txt：' + $srcManual)
}
Write-Utf8BomFile (Join-Path $outDir 'README_使用前必读.txt') $README_TEMPLATE 'UTF-8 BOM'
$versionText = '2.0.3' + "`r`n" +
    'MIDAS建模向导 便携版' + "`r`n" +
    '打包时间：' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "`r`n" +
    '说明：本次更新只修启动；程序功能与 2.0.2 相同。' + "`r`n"
Write-Utf8BomFile (Join-Path $outDir 'VERSION.txt') $versionText 'UTF-8 BOM'

# ---- 7) 清理只读属性 + 自检 --------------------------------------------
Write-Head '6/7 打包自检'
Get-ChildItem -LiteralPath $outDir -Recurse -File -Force | ForEach-Object {
    if ($_.IsReadOnly) { $_.IsReadOnly = $false }
}

$allFiles = @(Get-ChildItem -LiteralPath $outDir -Recurse -File -Force | Sort-Object FullName)

# 7.1 禁带文件 / 目录
$badDirPattern = '(^|\\)(__pycache__|tests|docs|build|dist|\.git|\.venv[^\\]*)(\\|$)'
$badFilePattern = '^(安装环境\.ps1|pythonw_path\.txt|启动诊断\.txt)$'
foreach ($f in $allFiles) {
    $rel = $f.FullName.Substring($outDir.Length + 1)
    if ($rel -match $badDirPattern) {
        Add-BuildError ('不该打包的目录：' + $rel)
    }
    if ($f.Name -match $badFilePattern) {
        Add-BuildError ('不该打包的文件：' + $rel)
    }
    if ($f.Extension -ieq '.log') {
        Add-BuildError ('不该打包的日志：' + $rel)
    }
}

# 7.2 禁带字符串（启动器/文档里不许出现写死的开发机路径和旧启动器名）
$forbiddenText = @('F:\python', 'E:\新建文件夹', '安装环境.ps1')
$textExts = @('.py', '.txt', '.md', '.bat', '.vbs', '.ps1', '.mct', '.json', '.yml', '.yaml', '.cfg', '.ini', '.csv')
$gbkSafe = [Text.Encoding]::GetEncoding(936)
foreach ($f in $allFiles) {
    if ($textExts -notcontains $f.Extension.ToLowerInvariant()) { continue }
    $raw = [IO.File]::ReadAllBytes($f.FullName)
    $asUtf8 = [Text.Encoding]::UTF8.GetString($raw)
    $asGbk = $gbkSafe.GetString($raw)
    foreach ($pat in $forbiddenText) {
        if ($asUtf8.Contains($pat) -or $asGbk.Contains($pat)) {
            Add-BuildError ('文件内容含禁用字符串 "' + $pat + '"：' + $f.FullName.Substring($outDir.Length + 1))
        }
    }
}

# 7.3 必备文件
$required = @(
    'midas_wizard.py',
    '使用说明.txt',
    'README_使用前必读.txt',
    'VERSION.txt',
    '启动建模向导.bat',
    '启动建模向导_静默.vbs',
    '修复启动环境.bat',
    'scripts\launch_wizard.ps1',
    'scripts\install_python.ps1'
)
foreach ($r in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $outDir $r) -PathType Leaf)) {
        if ($AllowIncomplete) {
            Write-Warn2 ('自检：缺少 ' + $r + '（-AllowIncomplete 下仅警告）')
        } else {
            Add-BuildError ('自检：缺少必备文件 ' + $r)
        }
    }
}

# 7.3b 文档编码回读验证（GBK / BOM 这类坑必须打包时拦住）
$replacementChar = [string]([char]0xFFFD)
$readmeOut = Join-Path $outDir 'README_使用前必读.txt'
if (Test-Path -LiteralPath $readmeOut) {
    $rb = [IO.File]::ReadAllBytes($readmeOut)
    if (-not ($rb.Length -ge 3 -and $rb[0] -eq 239 -and $rb[1] -eq 187 -and $rb[2] -eq 191)) {
        Add-BuildError 'README_使用前必读.txt 缺少 UTF-8 BOM（v2.0.2 包是 UTF-8 with BOM）'
    }
    $readmeText = [Text.Encoding]::UTF8.GetString($rb)
    if ($readmeText.Contains($replacementChar)) { Add-BuildError 'README_使用前必读.txt 按 UTF-8 解码出现替换字符' }
    if (-not $readmeText.Contains('v2.0.3')) { Add-BuildError 'README_使用前必读.txt 里没有版本号 v2.0.3' }
    Write-Item '文档编码检查通过：README_使用前必读.txt（UTF-8 BOM，中文正常）'
}
$versionOut = Join-Path $outDir 'VERSION.txt'
if (Test-Path -LiteralPath $versionOut) {
    $vb = [IO.File]::ReadAllBytes($versionOut)
    if (-not ($vb.Length -ge 3 -and $vb[0] -eq 239 -and $vb[1] -eq 187 -and $vb[2] -eq 191)) {
        Add-BuildError 'VERSION.txt 缺少 UTF-8 BOM'
    }
    Write-Item '文档编码检查通过：VERSION.txt（UTF-8 BOM）'
}
$manualOut = Join-Path $outDir '使用说明.txt'
if (Test-Path -LiteralPath $manualOut) {
    $mb = [IO.File]::ReadAllBytes($manualOut)
    $gbkDec = [Text.Encoding]::GetEncoding(936)
    $manText = $gbkDec.GetString($mb)
    if ($manText.Contains($replacementChar)) { Add-BuildError '使用说明.txt 按 GBK(936) 解码出现替换字符' }
    if (-not $manText.Contains('怎么启动')) { Add-BuildError '使用说明.txt 按 GBK(936) 解码后看不到中文正文' }
    if (-not $manText.Contains('修复启动环境.bat')) { Add-BuildError '使用说明.txt 里没有 v2.0.3 的启动 FAQ' }
    Write-Item '文档编码检查通过：使用说明.txt（GBK/936 回读正常）'
}

# 7.4 统计
$folderBytes = ($allFiles | Measure-Object -Property Length -Sum).Sum
if (-not $folderBytes) { $folderBytes = 0 }
$nPy = @($allFiles | Where-Object { $_.Extension -ieq '.py' }).Count
$nModelsOut = @($allFiles | Where-Object { $_.FullName.StartsWith((Join-Path $outDir '模型文件') + '\') }).Count
$nShots = @($allFiles | Where-Object { $_.FullName.StartsWith((Join-Path $outDir '界面截图') + '\') }).Count
$nSectionsOut = @($allFiles | Where-Object { $_.FullName.StartsWith((Join-Path $outDir '示例截面') + '\') }).Count
Write-Item ('总文件数：' + $allFiles.Count + '（程序 .py ' + $nPy + ' / 模型文件 ' + $nModelsOut + ' / 界面截图 ' + $nShots + ' / 示例截面 ' + $nSectionsOut + '）')
Write-Item ('文件夹大小：' + (Get-HumanSize $folderBytes))
if ($allFiles.Count -lt 95 -and -not $AllowIncomplete) {
    Add-BuildError ('包内文件数偏少：' + $allFiles.Count + '（应 >= 95，v2.0.2 是 107）')
}

# ---- 8) 打 zip ---------------------------------------------------------
if (-not $NoZip) {
    Write-Head '7/7 打 zip'
    if (Test-Path -LiteralPath $zipPath) {
        & attrib.exe -R $zipPath 2>$null | Out-Null
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # 注意：这里**不要**传 entryNameEncoding 参数。PS 5.1 / .NET Framework 的 5 参数重载
    # 虽然按 UTF-8 写文件名，但不会置 zip 的 UTF-8 标志位（bit 11），Windows 资源管理器
    # 解压时会把中文名解成乱码；4 参数默认重载会正确置位（和 v2.0.2 参考包一致）。
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $outDir, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true)
    $zipInfo = Get-Item -LiteralPath $zipPath
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    # 校验 zip 根目录名、条目数、UTF-8 标志位
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entryCount = @($zip.Entries | Where-Object { $_.FullName -notmatch '[\\/]$' }).Count
        $rootName = ($zip.Entries[0].FullName -split '[\\/]')[0]
    } finally {
        $zip.Dispose()
    }
    $zipBytes = [IO.File]::ReadAllBytes($zipPath)
    $cdOffset = -1
    for ($i = 0; $i -lt $zipBytes.Length - 3; $i++) {
        if ($zipBytes[$i] -eq 0x50 -and $zipBytes[$i + 1] -eq 0x4B -and
            $zipBytes[$i + 2] -eq 0x01 -and $zipBytes[$i + 3] -eq 0x02) {
            $cdOffset = $i
            break
        }
    }
    $utf8Flag = $false
    if ($cdOffset -ge 0) {
        $flags = $zipBytes[$cdOffset + 8] + $zipBytes[$cdOffset + 9] * 256
        $utf8Flag = [bool]($flags -band 0x0800)
    }
    if (-not $utf8Flag) {
        Add-BuildError 'zip 条目没有置 UTF-8 标志位（bit 11），Windows 解压会中文乱码'
    }
    if ($rootName -ne $pkgName) {
        Add-BuildError ('zip 根目录名不对：' + $rootName + '（应为 ' + $pkgName + '）')
    }
    if ($entryCount -ne $allFiles.Count) {
        Add-BuildError ('zip 文件条目数 ' + $entryCount + ' 与文件夹文件数 ' + $allFiles.Count + ' 不一致')
    }
    Write-Item ('zip：' + $zipPath)
    Write-Item ('zip 大小：' + (Get-HumanSize $zipInfo.Length))
    Write-Item ('zip 内文件数：' + $entryCount + '，根目录：' + $rootName)
    Write-Item ('SHA256：' + $zipHash)
    $script:ResultZip = $zipPath
    $script:ResultZipSize = $zipInfo.Length
} else {
    Write-Head '7/7 打 zip（-NoZip，已跳过）'
}

# ---- 9) 总结 -----------------------------------------------------------
Write-Head '打包结果'
Write-Item ('目录：' + $outDir)
Write-Item ('文件数：' + $allFiles.Count + '，大小：' + (Get-HumanSize $folderBytes))
if (-not $NoZip) {
    Write-Item ('zip：' + $script:ResultZip + '（' + (Get-HumanSize $script:ResultZipSize) + '）')
}
if ($script:ErrorList.Count -gt 0) {
    Write-Host ''
    Write-Host ('打包失败：' + $script:ErrorList.Count + ' 个问题') -ForegroundColor Red
    foreach ($e in $script:ErrorList) { Write-Host ('  - ' + $e) -ForegroundColor Red }
    exit 1
}
Write-Host ''
Write-Host '打包完成，自检全部通过。' -ForegroundColor Green
exit 0
