# midas-mct-wizard · MIDAS Civil NX 建模向导

一个不用学 MIDAS 就能建桥梁模型的桌面小工具。按 8 个步骤填表，
最后生成一个 `.mct` 命令文件，在 MIDAS Civil NX 里
**文件 → 导入 → MCT 命令文件** 就能得到完整模型。

> **非官方工具。** 与 MIDAS IT 无隶属关系，也未获其认可或赞助。
> "MIDAS"、"MIDAS Civil NX" 是 MIDAS IT 的商标，本项目只是生成它支持的
> MCT 文本格式。

![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![platform](https://img.shields.io/badge/platform-Windows-0078d4)

> 代码质量由 `tests/` 下的单元测试 + `--selftest` 保证，在 Windows / Linux / macOS
> 三平台 CI 上跑（见 `.github/workflows/ci.yml`）。仓库推上去后 CI 徽章会显示在这里。

---

## 这个工具解决什么问题

MIDAS Civil NX 功能很强，但上手要先学一堆概念：节点、单元、材料、截面、
边界组、荷载工况、梁单元荷载…… 对只做常规梁桥/连续梁的人来说，前半小时基本
花在"找菜单"上。

这个向导把这件事变成填表：每一步只问一件事，填错了当场用红字告诉你哪里不对，
填完一键生成 MCT 文件导进 MIDAS。**不需要记命令、不需要点菜单、全程不联网。**

---

## 适配版本（先看这个）

程序生成的是 **MIDAS Civil NX** 的 MCT 命令文件。实测通过的版本：

| 版本 | 情况 |
|---|---|
| **MIDAS CIVIL NX 2025 (v1.1)**，`CVLw.exe` 文件版本 `25.09.16.1001` | ✅ 实测导入通过 |
| MIDAS Civil 2022 及更早 | ⚠️ 未实测。第 8 步勾选「备用写法」会换成旧版语法（`*UNIT` 两字段、`*MATERIAL` 无 `DAMPRATIO`、`*STRUCTYPE` 短格式），可自行尝试 |
| 「直连 MIDAS」三个按钮 | 仅 Civil NX（带 Open API）可用，且要先在 MIDAS 里点 **应用程序 → API 设置 → 连接** |

程序里的 MCT 写法（材料行的 `STANDARD/CODE/DB/USEELAST`、`DBUSER` 截面
`OFFSET` 后那 6 个 `0`、DXF 一般截面的 `*SECT-PSCVALUE`）都是照着上面这个版本
自己导出的原文写的。

---

## 快速开始

### 方式一：双击启动（推荐）

下载仓库后双击 **`launch_wizard.bat`**。它会按顺序找 Python：

1. 之前用本程序装好的 Python
2. 系统里已有的 Python（`pyw` / `pythonw`）
3. 都没有 → **询问你**是否下载安装（约 25 MB，只从 python.org 下载，静默装到当前用户目录，不需要管理员）

普通 Windows 窗口程序，**不用浏览器、不联网**（只有你主动点"直连 MIDAS"或
同意安装 Python 时才会联网）。

### 方式二：命令行

```bat
python midas_wizard.py                 :: 打开界面
python midas_wizard.py --selftest      :: 不打开界面，跑一遍核心算法自检
python midas_wizard.py --emit-mct 工程.json --outdir 输出目录
python midas_wizard.py --version
```

### 环境要求

- Windows 7 以上（主要开发/测试平台）
- Python **3.9+**，自带 `tkinter`
- **零第三方依赖** —— 纯标准库，不用 `pip install` 任何东西

---

## 8 个步骤

左边是步骤栏，右边一次只显示一步。带「（可选）」的不填也能继续，
底部红字会告诉你当前步缺什么。

| 步骤 | 做什么 |
|---|---|
| **1. 支点** | 逐个填 X/Y/Z；也能「批量生成等间距支点」 |
| **2. 单元** | 每个单元连接两个支点；「依次连接相邻支点」自动生成连续梁 |
| **3. 材料** | 选规格（混凝土 C30~C60、钢材 Q235~Q460）自动带出弹模/泊松比/容重；也可用规范数据库或自定义数值 |
| **4. 截面** | 8 种参数化截面 + 变截面 + **CAD 自定义截面（DXF）** |
| **5. 支承** | 节点号 + 勾自由度；「首尾节点设为铰支座」一键两端铰支 |
| **6. 工况与自重** | 给荷载起名字（工况表）+ 定义结构自重；底部还能一键生成 **车道不利布载** |
| **7. 节点/梁单元荷载** | 所有荷载数值都在这一步填 |
| **8. 生成成果** | 写出 `.mct` + 数据 JSON + 数据表，或直连 MIDAS 导入/分析/取结果 |

### 亮点功能

- **CAD 自定义截面**：直接把 DXF 轮廓读进来算面积、形心、Iyy、Izz、扭转常数，
  自动识别孔洞，写成 MIDAS 的**真正多边形一般截面**（`*SECT-PSCVALUE`）。
  支持闭合多段线、`LWPOLYLINE`、经典 `POLYLINE`+`VERTEX`、圆、圆弧、直线串。
- **车道不利布载**：按《公路桥涵设计通用规范》自动生成 5 个最不利布置工况
  （LL-1~LL-5），跨中位置按你的支承位置自动找，不用数节点号。
- **边填边校验**：40 多条检查规则，生成前整体再查一遍，避免写出 MIDAS 导不进去的文件。
- **自动存档**：输入实时存到工作目录，关掉窗口不会丢。

---

## 项目结构

```
midas-mct-wizard/
├─ midas_wizard.py              主程序（单文件，约 3300 行，纯标准库）
├─ launch_wizard.bat            Windows 启动器（UTF-8 with BOM）
├─ scripts/
│   └─ install_python.ps1       首次运行时安装 Python（仅 python.org）
├─ examples/
│   ├─ three-span-beam.json     示例工程（可以用「打开工程」载入）
│   ├─ three-span-beam.mct      示例生成的 MCT
│   ├─ custom-section.mct       自定义（DXF）截面示例
│   └─ box-2000x1500.dxf        示例箱形截面图纸
├─ tests/
│   ├─ test_wizard.py           43 个单元测试（标准库 unittest）
│   └─ test_cross_platform.py   验证没有 winreg 也能 import（CI 跑 Linux/macOS）
├─ LICENSE                      MIT
└─ .github/workflows/ci.yml     CI：3 个平台 × 3 个 Python 版本
```

**为什么是单文件？** 这个程序主打"绿色便携"——整个文件夹拷到 U 盘、换台电脑
双击就能跑。拆成包会破坏这个特性，也会让不懂 Python 的用户没法直接改。
所以逻辑集中在 `midas_wizard.py`，用测试来保证质量，而不是用目录结构。

---

## 跑测试

```bat
python -m unittest discover -s tests -v
python midas_wizard.py --selftest
```

测试只覆盖纯函数（截面特性、DXF 解析、MCT 生成、校验规则、数据模型），
**不启动 Tk、不连 MIDAS、不写工程目录**，所以 Windows / Linux / macOS 和 CI 上都能跑。

重点盯的是"算错了但看起来很正常"的地方，例如：

- 空心箱梁的**扭转常数**：旧写法直接套实心近似 `A⁴/(40·Ip)`，箱梁会差
  **1~3 个数量级**。现在空心走薄壁 Bredt 公式，实测与解析解相差 1~3%。
- CAD 截面画在离原点很远的位置（坐标 317321 这种）不能把惯矩算成垃圾值。
- 工况名在 `*STLDCASE` 和 `*USE-STLD` 里必须写成同一个名字，否则荷载整批丢掉。
- `nan` / `1e999` 这类输入不能把程序搞崩（`pythonw` 下没有控制台，报错看不见）。

---

## 免责声明

- **非官方**：本项目与 MIDAS IT 无任何隶属关系。使用时请遵守你的 MIDAS 许可协议。
- **请务必核对结果**：生成的模型导入 MIDAS 后，请自行核对支点坐标、单元连接、
  材料数据、截面尺寸与特性、支承、荷载工况与数值。工程计算结果请以 MIDAS
  自身算出的为准，本程序只负责"把模型建出来"。
- **扭转常数是近似值**：CAD 自定义截面的 `Ixx` 由轮廓近似算得（空心用薄壁
  Bredt 公式，实心用圣维南近似）；扭转敏感的分析请以 MIDAS 自己算的为准。
  导入后 MIDAS 会按多边形重算，以 MIDAS 的值为准。
- **作者不对任何工程后果负责**，请自行复核。

---

## 参与贡献

欢迎提 Issue 和 PR。请先跑一遍 `python -m unittest discover -s tests` 和
`python midas_wizard.py --selftest`，确保都是绿的。

如果要把程序发给别人，直接把整个文件夹打包即可（绿色便携）；
`models/` 是这个程序运行时自己建的工作目录，不用打包。

## 许可

[MIT](LICENSE)
