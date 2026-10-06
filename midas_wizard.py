# -*- coding: utf-8 -*-
"""
MIDAS Civil NX 建模向导  —  本地桌面程序（Python + Tkinter，不依赖浏览器）

用途：按步骤输入桥梁/梁模型的支点、单元、材料、截面、支承、荷载，
      最后生成一个 .mct 命令文件，在 MIDAS Civil NX 里
      「文件 → 导入 → MCT 命令文件」即可生成模型。

MCT 命令的字段顺序依据 MIDAS 官方《MCT Command Quick Reference》
(manual.midasuser.com, midas Civil 900 → Appendix → MCT Command Shell)。

非官方工具，与 MIDAS IT 无隶属关系。https://github.com/
"""

__version__ = "1.0.0"

import argparse
import datetime
import json
import math
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "MIDAS Civil NX 建模向导"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# 便携：程序文件夹就是自己的所在目录，整个文件夹拷到哪都跟着走。
# 但如果程序装在只读位置（Program Files、只读网络盘、macOS 的 .app 里），
# 就不能往自己旁边写东西 —— 这时自动退到用户目录，程序照样能用。
_WORK_SUBDIR = "models"
_FALLBACK_BASE = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") \
    or os.path.join(os.path.expanduser("~"), ".local", "share")


def _pick_work_dir():
    """优先用程序旁边的 models/（便携）；那儿写不了就换用户目录。"""
    preferred = os.path.join(APP_DIR, _WORK_SUBDIR)
    for cand in (preferred, os.path.join(_FALLBACK_BASE, "midas-mct-wizard", _WORK_SUBDIR)):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return cand
        except OSError:
            continue
    return tempfile.gettempdir()


WORK_DIR = _pick_work_dir()
# 结果文件夹已取消：MIDAS 的结果统一放进当前工程文件夹（和 .mct 放一起）


def find_midas_exe():
    """找 MIDAS Civil NX 的主程序。每台机器装的位置不一样，这里逐个试，不写死。"""
    import glob
    try:
        import winreg
    except ImportError:                      # 非 Windows：没有注册表，跳过这一步
        winreg = None
    cands = []
    try:                                     # 1) 注册表里 MIDAS 自己记的安装位置
        if winreg is None:
            raise OSError
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\MIDAS") as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                for val in ("InstallDir", "InstallPath", "Path", "Home"):
                    try:
                        p = str(winreg.QueryValueEx(winreg.OpenKey(k, sub), val)[0]).strip()
                    except OSError:
                        continue
                    if p:
                        cands.append(os.path.join(p, "CVLw.exe"))
    except OSError:
        pass
    for drive in ("C:", "D:", "E:", "F:", "G:"):   # 2) 常见安装位置
        for sub in (r"\Program Files\MIDAS\MIDAS CIVIL NX", r"\Program Files (x86)\MIDAS\MIDAS CIVIL NX",
                    r"\MIDAS\MIDAS CIVIL NX"):
            cands.append(drive + sub + r"\CVLw.exe")
    cands.append(os.path.join(APP_DIR, "CVLw.exe"))
    for p in cands:
        if os.path.isfile(p):
            return p
    for hit in glob.glob(os.path.join(APP_DIR, "..", "**", "CVLw.exe"), recursive=True):   # 3) 程序旁边
        return os.path.abspath(hit)
    return ""


MIDAS_EXE = find_midas_exe()
SESSION_FILE = os.path.join(WORK_DIR, "_wizard_session.json")

# --------------------------------------------------------------------------
# 常量表
# --------------------------------------------------------------------------

SHAPES = [
    ("SB", "实心矩形", [("H", "高"), ("B", "宽")]),
    ("SR", "实心圆", [("D", "直径")]),
    ("L", "角钢", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf", "翼缘厚")]),
    ("T", "T 形", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf", "翼缘厚")]),
    ("C", "槽钢", [("H", "高"), ("B1", "上宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"), ("B2", "下宽"), ("tf2", "下翼厚")]),
    ("H", "工字钢", [("H", "高"), ("B1", "上宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"),
                     ("B2", "下宽"), ("tf2", "下翼厚"), ("r1", "圆角 1"), ("r2", "圆角 2")]),
    ("B", "箱形", [("H", "高"), ("B", "宽"), ("tw", "腹板厚"), ("tf1", "上翼厚"), ("C", "倒角"), ("tf2", "下翼厚")]),
    ("P", "圆管", [("D", "外径"), ("tw", "壁厚")]),
]
SHAPE_BY_CODE = {s[0]: s for s in SHAPES}
CAD_SHAPE = "CAD"
TS_SHAPE = "TS"            # 变截面（引用两端截面，沿梁高线性渐变）
DIM_SLOTS = 10             # DBUSER 截面固定写 10 个数值槽位（真实导出为 24 字段定长）

# 材料规格库：名称 -> (类别, 弹性模量 kN/m², 泊松比, 容重 kN/m³, 线膨胀系数 1/℃)
# 混凝土按 JTG 3362-18 弹性模量（×10⁴ MPa）；钢材 E=2.06×10⁵ MPa，容重按 MIDAS 默认 76.98 kN/m³
MATERIAL_SPECS = {
    "C30": ("CONC", "3.00e7", "0.2", "25", "0.00001"),
    "C35": ("CONC", "3.15e7", "0.2", "25", "0.00001"),
    "C40": ("CONC", "3.25e7", "0.2", "25", "0.00001"),
    "C45": ("CONC", "3.35e7", "0.2", "25", "0.00001"),
    "C50": ("CONC", "3.45e7", "0.2", "25", "0.00001"),
    "C55": ("CONC", "3.55e7", "0.2", "25", "0.00001"),
    "C60": ("CONC", "3.60e7", "0.2", "25", "0.00001"),
    "Q235": ("STEEL", "2.06e8", "0.3", "76.98", "0.000012"),
    "Q345": ("STEEL", "2.06e8", "0.3", "76.98", "0.000012"),
    "Q390": ("STEEL", "2.06e8", "0.3", "76.98", "0.000012"),
    "Q420": ("STEEL", "2.06e8", "0.3", "76.98", "0.000012"),
    "Q460": ("STEEL", "2.06e8", "0.3", "76.98", "0.000012"),
    "自定义": ("USER", "", "", "", ""),
}
CONC_SPECS = [k for k, v in MATERIAL_SPECS.items() if v[0] == "CONC"]
STEEL_SPECS = [k for k, v in MATERIAL_SPECS.items() if v[0] == "STEEL"]

FORCE_UNITS = ["KN", "N", "KGF", "TONF", "LBF", "KIPS"]
DIST_UNITS = ["M", "CM", "MM", "FT", "IN"]
HEAT_UNITS = ["KJ", "J", "CAL", "KCAL", "BTU"]
TEMPER_UNITS = ["C", "F"]
STYPES = [("0", "0 · 三维（3D）"), ("1", "1 · 二维 X-Z 平面"), ("2", "2 · 二维 Y-Z 平面"), ("3", "3 · 二维 X-Y 平面")]
LC_TYPES = ["D", "L", "W", "E", "T", "S", "R", "IL", "EP", "B", "CR", "SH", "PS", "ER", "USER"]
OFFSETS = ["CC", "CT", "CB", "LC", "RC", "LT", "RT", "LB", "RB"]
ELEM_TYPES = ["BEAM", "TRUSS"]
MAT_TYPES = [("CONC", "混凝土"), ("STEEL", "钢材"), ("USER", "用户自定义")]
MAT_STANDARDS = ["JTG3362-18(RC)", "JTG04(RC)", "GB(RC)", "GB10(RC)", "ASTM(RC)", "EN(RC)", "BS(RC)", "JIS(RC)", "KS(RC)"]
BEAMLOAD_TYPES = ["UNILOAD", "CONLOAD", "UNIMOMENT", "CONMOMENT"]
DIRECTIONS = ["GZ", "GY", "GX", "LZ", "LY", "LX"]
# MIDAS 读取 MCT 的编码随版本而异：中文 Windows 下先试本地 ANSI（GBK），不行再换 UTF-8。
ENCODINGS = [("GBK（中文 ANSI，推荐）", "gbk"), ("UTF-8", "utf-8"), ("UTF-8 带 BOM", "utf-8-sig")]


def default_model():
    return {
        "project": {"name": "", "force": "KN", "dist": "M", "heat": "KJ", "temp_unit": "C",
                    "styp": "1", "smas": "1", "grav": "9.806", "temper": "0", "compat": False,
                    "outdir": "", "alt_mct": False},
        "nodes": [{"x": "0", "y": "0", "z": "0", "note": ""},
                  {"x": "30", "y": "0", "z": "0", "note": ""}],
        "elements": [{"type": "BEAM", "i": "1", "j": "2", "mat": "1", "sect": "1", "angle": "0"}],
        "materials": [{"name": "C50", "type": "CONC", "mode": "spec", "spec": "C50",
                       "standard": "JTG3362-18(RC)", "dbname": "C50",
                       "elast": "3.45e7", "poisn": "0.2", "den": "25", "thermal": "0.00001"}],
        "sections": [{"name": "GIRDER", "shape": "SB", "dims": ["2.0", "1.5"], "offset": "CC",
                      "shear": "YES", "warp": "NO", "file": "", "unit": "0.001", "props": None}],
        "supports": [{"nodes": "1", "dx": True, "dy": True, "dz": True, "rx": False, "ry": False, "rz": False, "group": ""}],
        "loadcases": [{"name": "DL", "type": "D", "desc": ""}, {"name": "LL", "type": "L", "desc": ""}],
        "selfweight": {"on": True, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""},
        "nodalloads": [],
        "beamloads": [{"lc": "DL", "elems": "1", "cmd": "BEAM", "type": "UNILOAD", "dir": "GZ", "proj": "NO",
                       "d1": "0", "p1": "-25", "d2": "1", "p2": "-25", "d3": "0", "p3": "0", "d4": "0", "p4": "0",
                       "group": ""}],
    }


# --------------------------------------------------------------------------
# 数值 / MCT 生成
# --------------------------------------------------------------------------

def as_float(v, default=0.0):
    """宽松转 float：任何不是有限数的输入都退回 default。

    nan / inf / 1e999 以前会被 float() 收下，然后在 int() 那一步炸掉
    （OverflowError 不是 ValueError，接不住），窗口上什么都看不见。
    """
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def is_num(v):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return False
    return str(v).strip() != "" and math.isfinite(f)


def num(v):
    f = as_float(v, None)
    if f is None:
        return "0"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def numg(v, sig=6):
    """截面特性用：6 位有效数字，免得写出 0.39612500000000017 这种浮点噪声。"""
    f = as_float(v, None)
    if f is None:
        return "0"
    return "%.*g" % (sig, f)


def txt(v):
    return "" if v is None else str(v)


def _squash(s):
    """去掉 MCT 字段分隔符（逗号）和换行/制表符，并把连续空格压成一个。"""
    for ch in (",", "\r", "\n", "\t"):
        s = s.replace(ch, " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def aascii(v, fallback=""):
    """边界组名专用：*CONSTRAINT 的组名里出现中文会导致整份导入失败（实测）。"""
    return _squash("".join(ch for ch in txt(v) if ord(ch) < 128)) or fallback


def aname(v, fallback=""):
    """MCT 里写名称用：只清掉会破坏字段结构的东西，中文原样保留。

    必须清掉的只有「逗号」——MCT 用逗号分隔字段，名称里带逗号会让整行错列；
    换行/制表符同理。**非 ASCII（中文）不用清**：作者实测中文名能正常导入
    （仓库里 examples/custom-section.mct 的截面名就是「主梁」，导入通过）。
    MCT 文件按 GBK 写出去，中文能正常显示。

    早先的版本会把非 ASCII 全部删掉，结果是用户填「自重」「二期」这种中文工况名
    全被抹成兜底名，几个工况撞成同一个 —— 那是错的，已经改回来。
    """
    return _squash(txt(v)) or fallback


STYPE_NAMES = {"0": "三维 (3D)", "1": "二维 X-Z 平面", "2": "二维 Y-Z 平面", "3": "二维 X-Y 平面", "4": "三维 (Z 向旋转约束)"}
MAT_TYPE_NAMES = {"CONC": "混凝土", "STEEL": "钢材", "USER": "用户自定义"}
MAT_MODES = [("spec", "规格自动填数"), ("db", "规范数据库"), ("user", "自定义数值")]
MAT_MODE_NAMES = {k: v for k, v in MAT_MODES}
MAT_MODE_CODES = {v: k for k, v in MAT_MODES}
MAT_TYPE_CODES = {v: k for k, v in MAT_TYPE_NAMES.items()}


def empty_model():
    """真正的空模型：所有清单都是空的，从头开始填。"""
    m = default_model()
    for k in ("nodes", "elements", "materials", "sections", "supports",
              "loadcases", "nodalloads", "beamloads"):
        m[k] = []
    m["selfweight"] = {"on": True, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
    return m


def ensure_model(data):
    """把外面读进来的 JSON 补齐成一个完整模型。

    打开旧工程、旧版本存档或手改过的 JSON 时，缺哪个键就补默认值 —— 以前直接
    把读到的 dict 换上就用，少一个键后面 render() 就 KeyError，整个界面卡死。
    """
    m = default_model()
    if not isinstance(data, dict):
        return m
    # 每一行还必须是 dict：手改坏的 JSON 里可能是 null / 数字 / 字符串，
    # 光把列表补出来不够，访问 row.get() 一样会 AttributeError。
    for key, dflt in m.items():
        if isinstance(dflt, list) and isinstance(data.get(key), list):
            data = dict(data)
            data[key] = [row for row in data[key] if isinstance(row, dict)]
    for key, dflt in m.items():
        if key not in data:
            continue
        val = data[key]
        if isinstance(dflt, list):
            m[key] = val if isinstance(val, list) else dflt
        elif isinstance(dflt, dict):
            if isinstance(val, dict):
                merged = dict(dflt)
                merged.update(val)          # 旧版本多出来的键保留，缺的键用默认值补
                m[key] = merged
        else:
            m[key] = val
    return m


STEP_CLEAR_KEYS = {
    "nodes": ("nodes",), "elements": ("elements",), "materials": ("materials",),
    "sections": ("sections", "sec_assign"), "supports": ("supports",), "loadcases": ("loadcases",),
    "loads": ("nodalloads", "beamloads"),
}


def build_data_report(m):
    """把向导里的全部输入整理成一份人可读的数据表（存档 / 核对用）。"""
    p = m["project"]
    L = []
    add = L.append
    add("=" * 78)
    add("MIDAS Civil NX 建模向导 —— 模型数据表")
    add("=" * 78)
    add("项目名称：%s" % (txt(p.get("name")).strip() or "（未命名）"))
    add("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("单位制：力 %s，长度 %s" % (p.get("force", "KN"), p.get("dist", "M")))
    add("结构类型：%s      重力加速度：%s      初始温度：%s"
        % (STYPE_NAMES.get(str(p.get("styp")), str(p.get("styp"))), p.get("grav", "9.806"), p.get("temper", "0")))
    add("")

    add("-" * 78)
    add("一、支点（节点）  共 %d 个" % len(m["nodes"]))
    add("-" * 78)
    add("  编号            X            Y            Z   备注")
    for i, nd in enumerate(m["nodes"], start=1):
        add("  %4d  %12s %12s %12s   %s" % (i, num(nd.get("x")), num(nd.get("y")), num(nd.get("z")), txt(nd.get("note"))))
    add("")

    add("-" * 78)
    add("二、单元  共 %d 个" % len(m["elements"]))
    add("-" * 78)
    add("  编号   类型    i 节点   j 节点   材料号   截面号   β角")
    for i, el in enumerate(m["elements"], start=1):
        add("  %4d  %6s  %7s  %7s  %7s  %7s  %6s"
            % (i, el.get("type", "BEAM"), num(el.get("i")), num(el.get("j")),
               num(el.get("mat")), num(el.get("sect")), num(el.get("angle"))))
    add("")

    add("-" * 78)
    add("三、材料  共 %d 种" % len(m["materials"]))
    add("-" * 78)
    for i, mat in enumerate(m["materials"], start=1):
        spec = txt(mat.get("spec"))
        add("  %d) %-14s %s%s" % (i, txt(mat.get("name")), MAT_TYPE_NAMES.get(mat.get("type"), txt(mat.get("type"))),
                                  ("    规格：" + spec) if spec and spec != "自定义" else "    （自定义数据）"))
        add("       弹性模量 E = %s      泊松比 ν = %s      容重 = %s      线膨胀系数 = %s"
            % (num(mat.get("elast")), num(mat.get("poisn")), num(mat.get("den")), num(mat.get("thermal"))))
    add("")

    add("-" * 78)
    add("四、截面  共 %d 个" % len(m["sections"]))
    add("-" * 78)
    for i, sec in enumerate(m["sections"], start=1):
        code = shape_code_of(sec)
        add("  %d) %s" % (i, txt(sec.get("name"))))
        if code == CAD_SHAPE:
            pr = sec.get("props") or {}
            add("       类型：自定义（CAD 文件）    文件：%s" % txt(sec.get("file")))
            if pr:
                add("       面积 A = %s m²      形心 y = %s m, z = %s m"
                    % (numg(pr.get("A")), numg(pr.get("Cy")), numg(pr.get("Cz"))))
                add("       Iyy = %s m⁴      Izz = %s m⁴      Ixx(扭转,近似) = %s m⁴"
                    % (numg(pr.get("Iyy")), numg(pr.get("Izz")), numg(pr.get("Ixx"))))
                add("       外周长 = %s m      内周长 = %s m      轮廓 %s 个 / 孔洞 %s 个"
                    % (numg(pr.get("PERI_OUT")), numg(pr.get("PERI_IN")), pr.get("N_LOOP"), pr.get("N_HOLE")))
            else:
                add("       （尚未解析出轮廓）")
        else:
            if code == TS_SHAPE:
                for key, lab in (("i_sect", "i 端"), ("j_sect", "j 端")):
                    v = sec.get(key)
                    if not is_num(v) or not (1 <= int(float(v)) <= len(m["sections"])):
                        add("       变截面 %d 的「%s截面号」要填 1~%d。" % (i, lab, len(m["sections"])))
                    elif int(float(v)) == i:
                        add("       变截面 %d 不能引用自己。" % i)
            shape = SHAPE_BY_CODE.get(code, SHAPES[0])
            dims = sec.get("dims", [])
            ds = "，".join("%s=%s" % (shape[2][k][0], num(dims[k]) if k < len(dims) else "0")
                           for k in range(len(shape[2])))
            add("       类型：%s（%s）    %s m" % (shape[1], code, ds))
        add("       偏心点 = %s      剪切变形 = %s      翘曲效应 = %s"
            % (sec.get("offset", "CC"), sec.get("shear", "YES"), sec.get("warp", "NO")))
    add("")

    add("-" * 78)
    add("五、支承（边界条件）  共 %d 条" % len(m["supports"]))
    add("-" * 78)
    add("  节点号            Dx Dy Dz Rx Ry Rz   边界组")
    for sp in m["supports"]:
        code = "".join(" 1" if sp.get(k) else " 0" for k in ("dx", "dy", "dz", "rx", "ry", "rz"))
        add("  %-16s %s   %s" % (txt(sp.get("nodes")), code, txt(sp.get("group"))))
    add("")

    add("-" * 78)
    add("六、荷载工况  共 %d 个" % len(m["loadcases"]))
    add("-" * 78)
    for i, lc in enumerate(m["loadcases"], start=1):
        add("  %d) %-12s 类型 %-6s %s" % (i, txt(lc.get("name")), txt(lc.get("type")), txt(lc.get("desc"))))
    add("")
    sw = m["selfweight"]
    add("  自重：%s" % ("计入" if sw.get("on") else "不计入"))
    if sw.get("on"):
        add("        工况 %s，系数 X=%s Y=%s Z=%s，荷载组 %s"
            % (txt(sw.get("lc")), num(sw.get("x")), num(sw.get("y")), num(sw.get("z")), txt(sw.get("group"))))
    add("")

    add("-" * 78)
    add("七、节点荷载  共 %d 条" % len(m["nodalloads"]))
    add("-" * 78)
    if not m["nodalloads"]:
        add("  （无）")
    for nl in m["nodalloads"]:
        add("  工况 %-10s 节点 %-8s FX=%-10s FY=%-10s FZ=%-10s MX=%-10s MY=%-10s MZ=%-10s 组=%s"
            % (txt(nl.get("lc")), txt(nl.get("node")), num(nl.get("fx")), num(nl.get("fy")), num(nl.get("fz")),
               num(nl.get("mx")), num(nl.get("my")), num(nl.get("mz")), aname(nl.get("group"))))
    add("")

    add("-" * 78)
    add("八、梁单元荷载  共 %d 条" % len(m["beamloads"]))
    add("-" * 78)
    if not m["beamloads"]:
        add("  （无）")
    for bl in m["beamloads"]:
        add("  工况 %-10s 单元 %-12s %s/%s 方向 %s  投影 %s"
            % (txt(bl.get("lc")), txt(bl.get("elems")), txt(bl.get("cmd")), txt(bl.get("type")),
               txt(bl.get("dir")), txt(bl.get("proj"))))
        add("        D = %s, %s, %s, %s      P = %s, %s, %s, %s      组=%s"
            % (num(bl.get("d1")), num(bl.get("d2")), num(bl.get("d3")), num(bl.get("d4")),
               num(bl.get("p1")), num(bl.get("p2")), num(bl.get("p3")), num(bl.get("p4")), txt(bl.get("group"))))
    add("")
    add("=" * 78)
    add("说明：本表是向导输入的存档，导入 MIDAS 用的是同目录下的 .mct 文件。")
    add("=" * 78)
    return "\r\n".join(L) + "\r\n"


_STATUS_CACHE = {"t": 0.0, "val": None}
_STATUS_TTL = 5.0          # 秒：第 8 步每次重绘都查一遍太浪费（要起 tasklist 进程）


def midas_status(force=False):
    """查一下 MIDAS 是否在运行、API 是否已连接（读注册表里的连接信息）。

    结果缓存 5 秒：第 8 步的复选框一动就会重绘，以前每次都去起一个 tasklist 进程。
    """
    import time as _time
    now = _time.monotonic()
    if not force and _STATUS_CACHE["val"] is not None and now - _STATUS_CACHE["t"] < _STATUS_TTL:
        return _STATUS_CACHE["val"]
    running = False
    try:
        import subprocess as _sp
        out = _sp.run(["tasklist", "/FI", "IMAGENAME eq CVLw.exe", "/NH"],
                      capture_output=True, text=True, timeout=8,
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0)).stdout
        running = "CVLw.exe" in out
    except Exception:
        running = False
    info = None
    try:
        import winreg
    except ImportError:
        return running, None
    for country in ("CH", "US"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\MIDAS\CVLwNX_%s\CONNECTION" % country, 0, winreg.KEY_READ)
            uri = str(winreg.QueryValueEx(key, "URI")[0])
            port = str(winreg.QueryValueEx(key, "PORT")[0])
            try:
                keyv = str(winreg.QueryValueEx(key, "Key")[0])
            except OSError:
                keyv = ""
            info = (uri, port, keyv)
            break
        except (OSError, ImportError):
            continue
    _STATUS_CACHE["t"] = now
    _STATUS_CACHE["val"] = (running, info)
    return running, info


def read_api_conn():
    """读 MIDAS API 连接信息（在 MIDAS 里 Apps → API Settings 连接后才有）。"""
    try:
        import winreg
    except ImportError:
        return None
    for country in ("CH", "US"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\MIDAS\CVLwNX_%s\CONNECTION" % country, 0, winreg.KEY_READ)
            uri = str(winreg.QueryValueEx(key, "URI")[0]).strip()
            port = str(winreg.QueryValueEx(key, "PORT")[0]).strip()
            try:
                apikey = str(winreg.QueryValueEx(key, "Key")[0]).strip()
            except OSError:
                apikey = ""
            if uri and port:
                return uri, port, apikey
        except OSError:
            continue
    return None


def api_call(method, path, body=None, timeout=120):
    """调用 MIDAS 本地 API。返回 (状态码, 返回内容) 或 (None, 错误文字)。"""
    import ssl
    import urllib.error
    import urllib.request

    conn = read_api_conn()
    if not conn:
        return None, "MIDAS 的 API 还没连接：请在 MIDAS 里「应用程序 → API Settings → 连接」。"
    uri, port, apikey = conn
    # 这个请求带着 MIDAS 的 API Key，只允许发给本机 —— 注册表里的地址被改过时宁可失败。
    if uri.split("//")[-1].split(":")[0].strip("[]") not in ("127.0.0.1", "localhost", "::1"):
        return None, ("MIDAS API 地址不是本机（%s），出于安全考虑已拒绝发送 API Key。\n"
                      "请在 MIDAS 里重新连接 API 设置。" % uri)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    last = "未知错误"
    for scheme in ("https", "http"):
        url = "%s://%s:%s/civil%s" % (scheme, uri, port, path)
        req = urllib.request.Request(
            url, data=payload, method=method,
            headers={"Content-Type": "application/json", "MAPI-Key": apikey})
        try:
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                text = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, (json.loads(text) if text.strip() else {})
                except ValueError:
                    return resp.status, {"raw": text[:2000]}
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                return exc.code, {"error": str(exc)}
        except Exception as exc:                     # 连不上就换一种协议再试
            last = "%s（%s）" % (exc, url)
    return None, last


def api_error_text(resp):
    """把 API 返回里的错误信息抠出来，没有就返回空串。"""
    if not isinstance(resp, dict):
        return ""
    err = resp.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    msg = resp.get("message")
    return str(msg) if msg else ""


def safe_name(name, fallback="midas-model"):
    base = txt(name).strip() or fallback
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, "_")
    return base.strip()


def write_bundle(m, out_dir, prefix, enc, include_compat=False):
    """把一个工程写成一套成果：.mct 模型 + 数据 json + 数据表 + 导入说明。"""
    os.makedirs(out_dir, exist_ok=True)
    name = safe_name(prefix)
    files = []

    mct_path = os.path.join(out_dir, name + ".mct")
    with open(mct_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(build_mct(m))
    files.append(("MIDAS 模型命令文件（导入用）", mct_path))

    json_path = os.path.join(out_dir, name + "_数据.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
    files.append(("向导数据（可再打开继续改）", json_path))

    rep_path = os.path.join(out_dir, name + "_数据表.txt")
    with open(rep_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(build_data_report(m))
    files.append(("数据表（人可读，核对/存档）", rep_path))

    readme_path = os.path.join(out_dir, "导入说明.txt")
    with open(readme_path, "w", encoding=enc, errors="replace", newline="") as fh:
        fh.write(
            "本文件夹是「MIDAS Civil NX 建模向导」为项目「%s」生成的成果。\r\n\r\n"
            "1) %s.mct\r\n"
            "   MIDAS 模型命令文件。在 MIDAS Civil NX 里：文件 → 导入 → MCT 命令文件，选中它即可生成模型。\r\n"
            "   也可以打开记事本复制里面的文字，粘贴到 MIDAS 的「工具 → 命令窗口」后点运行。\r\n\r\n"
            "2) %s_数据.json\r\n"
            "   向导里的全部输入。在程序里点「打开工程」选它，就能接着改。\r\n\r\n"
            "3) %s_数据表.txt\r\n"
            "   把支点、单元、材料、截面、支承、荷载列成表格，导入后照着核对。\r\n\r\n"
            "4) 结果\r\n"
            "   在 MIDAS 里跑完分析后，把结果文件（比如结果表格导出的 txt/csv）放到本文件夹，\r\n"
            "   并在程序第 8 步点「读入 MIDAS 结果」，程序会把结果一并归档在这里。\r\n"
            % (txt(m["project"].get("name")) or "（未命名）", name, name, name))
    files.append(("导入说明", readme_path))

    if include_compat:
        m2 = json.loads(json.dumps(m))
        m2["project"]["compat"] = not bool(m["project"].get("compat"))
        alt = os.path.join(out_dir, name + "（备用写法）.mct")
        with open(alt, "w", encoding=enc, errors="replace", newline="") as fh:
            fh.write(build_mct(m2))
        files.append(("备用写法的 MCT（标准版导入报错时试它）", alt))

    return files


# --------------------------------------------------------------------------
# 自定义截面：读 DXF（CAD 导出的 ASCII 图纸），取闭合轮廓并算截面特性
#   约定（"固定格式"）：截面轮廓画在 CAD 的 XY 平面上，存成 DXF；
#   最外层轮廓 + 内部孔洞都画成闭合多段线（LWPOLYLINE/POLYLINE），
#   直线 LINE 串成的闭合环、圆 CIRCLE、圆弧 ARC 也认。
#   换算：DXF 的 x -> 截面 y，DXF 的 y -> 截面 z，再按所选单位缩放。
# --------------------------------------------------------------------------

def _dist(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def _perimeter(pts):
    n = len(pts)
    return sum(_dist(pts[i], pts[(i + 1) % n]) for i in range(n))


def _point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xin = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < xin:
                inside = not inside
    return inside


def _loop_terms(pts):
    """返回 (A, Sy, Sz, Iyy, Izz)：面积、一次矩、关于原点的二次矩。"""
    n = len(pts)
    a2 = sy = sz = iyy = izz = 0.0
    for i in range(n):
        y0, z0 = pts[i]
        y1, z1 = pts[(i + 1) % n]
        cr = y0 * z1 - y1 * z0
        a2 += cr
        sy += (y0 + y1) * cr
        sz += (z0 + z1) * cr
        iyy += (z0 * z0 + z0 * z1 + z1 * z1) * cr
        izz += (y0 * y0 + y0 * y1 + y1 * y1) * cr
    return a2 / 2.0, sy / 6.0, sz / 6.0, iyy / 12.0, izz / 12.0


def compute_section_props(loops):
    """由若干闭合环（含孔洞）算出截面特性；失败返回 None。

    注意：CAD 里画截面常常随手画在图纸中间（坐标可能是 317321 这种大数）。
    大数相减会把惯性矩算成 1e5 这种垃圾值，写进 MCT 还会被 6 位有效数字抹平、
    让多边形退化成重复点 —— MIDAS 就报「截面尺寸输入有错误」。
    所以这里先整体平移，再积分，最后把形心加回去；写 MCT 的环一律以形心为原点。
    """
    loops = [[[float(p[0]), float(p[1])] for p in lp] for lp in loops if len(lp) >= 3]
    if not loops:
        return None
    # 坐标本身是 nan / inf，或者跨度大到乘平方就溢出的（1e308 这种），直接判失败。
    # 必须先拦：后面 _perimeter() 里的 (dx)**2 会抛 OverflowError，
    # 那不是 ValueError，一路穿到界面上只会显示一句看不懂的报错。
    for lp in loops:
        for p in lp:
            if not (math.isfinite(p[0]) and math.isfinite(p[1])):
                return None
    _span = max(max(p[0] for p in lp) - min(p[0] for p in lp) for lp in loops)
    _span = max(_span, max(max(p[1] for p in lp) - min(p[1] for p in lp) for lp in loops))
    if not math.isfinite(_span) or _span > 1e150:
        return None
    # 去掉相邻重复点（含首尾重复）与面积可忽略的退化环
    clean = []
    for lp in loops:
        pts = []
        for p in lp:
            if not pts or abs(p[0] - pts[-1][0]) > 1e-9 or abs(p[1] - pts[-1][1]) > 1e-9:
                pts.append(p)
        if len(pts) >= 3 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
            pts = pts[:-1]
        if len(pts) >= 3 and abs(_loop_terms(pts)[0]) > 1e-12:
            clean.append(pts)
    loops = clean
    if not loops:
        return None

    _ox = min(p[0] for lp in loops for p in lp)
    _oz = min(p[1] for lp in loops for p in lp)
    loops = [[[p[0] - _ox, p[1] - _oz] for p in lp] for lp in loops]

    depth = []
    for i, lp in enumerate(loops):
        d = 0
        for j, other in enumerate(loops):
            if i != j and _point_in_poly(lp[0], other):
                d += 1
        depth.append(d)

    A = Sy = Sz = Iyy = Izz = 0.0
    peri_out = peri_in = 0.0
    ys, zs = [], []
    for lp, d in zip(loops, depth):
        a, sy, sz, iyy, izz = _loop_terms(lp)
        sign = 1.0 if d % 2 == 0 else -1.0
        if (a > 0) != (sign > 0):
            a, sy, sz, iyy, izz = -a, -sy, -sz, -iyy, -izz
        A += a
        Sy += sy
        Sz += sz
        Iyy += iyy
        Izz += izz
        if sign > 0:
            peri_out += _perimeter(lp)
            ys.extend(p[0] for p in lp)
            zs.extend(p[1] for p in lp)
        else:
            peri_in += _perimeter(lp)
    if A <= 1e-12 or not all(math.isfinite(v) for v in (A, Sy, Sz, Iyy, Izz)):
        return None

    cy = Sy / A
    cz = Sz / A
    iyy_c = Iyy - A * cz * cz          # 关于形心，对应 MIDAS 的 Iyy
    izz_c = Izz - A * cy * cy          # 对应 MIDAS 的 Izz
    ip = iyy_c + izz_c
    n_hole = sum(1 for d in depth if d % 2 == 1)
    j_hollow = n_hole > 0
    if A <= 1e-12 or ip <= 1e-12:
        j_approx = 0.0
    elif j_hollow:
        # 空心闭口截面（箱梁）用薄壁 Bredt 公式 J = 4·Am²·t/Sm，直接用孔洞环推壁中线：
        #   A_cav, P_cav 取所有孔洞的面积/周长（各自平移到形心，否则平行轴定理算错）
        #   t = 壁面积 / 孔洞周长      —— 等效壁厚
        #   Sm = P_cav + 2·t          —— 壁中线周长（矩形环上是精确的）
        #   Am = A_cav + 0.5·t·P_cav  —— 壁中线围成的面积
        # 旧版本对空心截面也套实心近似 A⁴/(40·Ip)，箱梁会差 1~3 个数量级，
        # 而箱梁恰恰是本程序自定义截面的主用场景。
        a_cav = p_cav = 0.0
        for lp, d in zip(loops, depth):
            if d % 2 == 1:
                a_cav += abs(_loop_terms(lp)[0])
                p_cav += _perimeter(lp)
        if p_cav > 1e-12 and a_cav > 1e-12:
            t_eff = A / p_cav                       # 等效壁厚（均匀壁厚时就是真实壁厚）
            s_mean = p_cav + 4.0 * t_eff            # 壁中线周长（矩形环上精确）
            a_mean = a_cav + 0.5 * t_eff * p_cav    # 壁中线围成的面积
            j_approx = 4.0 * a_mean * a_mean * t_eff / s_mean
        else:
            j_approx = (A ** 4) / (40.0 * ip)
    else:
        # 实心截面：圣维南近似 J ≈ A⁴/(40·Ip)（圆截面误差约 1.5%、方截面约 7%）
        j_approx = (A ** 4) / (40.0 * ip)
    return {
        "A": A,
        "Cy": cy + _ox,                # 回到原图坐标，只为了让界面能显示「画在哪儿」
        "Cz": cz + _oz,
        "Iyy": iyy_c,
        "Izz": izz_c,
        "Ixx": j_approx,
        # 剪切面积：矩形取 5/6·A。以前只有 *SECT-PSCVALUE 那条路自己算，
        # *SECTION VALUE 那条路读不到就写成 0 —— 两条路必须一致。
        "ASy": A * 5.0 / 6.0,
        "ASz": A * 5.0 / 6.0,
        "CyP": max(ys) - cy,
        "CyM": cy - min(ys),
        "CzP": max(zs) - cz,
        "CzM": cz - min(zs),
        "PERI_OUT": peri_out,
        "PERI_IN": peri_in,
        "N_LOOP": len(loops),
        "N_HOLE": n_hole,
        # J 的算法标记：BREDT = 空心薄壁公式，STVENANT = 实心近似。界面会照这个提示。
        "J_METHOD": "BREDT" if j_hollow else "STVENANT",
        "J_APPROX": True,
        # 写 *SECT-PSCVALUE 的 OPOLY / IPOLY 用（偶数层=外轮廓，奇数层=孔洞）。
        # 这里已经把形心平移到原点：MIDAS 的一般截面就是这么存的，
        # 坐标也变小了，不会被 6 位有效数字抹成重复点。
        "OUTER_LOOPS": [[[round(p[0] - cy, 9), round(p[1] - cz, 9)] for p in lp]
                        for lp, d in zip(loops, depth) if d % 2 == 0],
        "HOLE_LOOPS": [[[round(p[0] - cy, 9), round(p[1] - cz, 9)] for p in lp]
                       for lp, d in zip(loops, depth) if d % 2 == 1],
        "DRAW_OFFSET": [round(_ox, 6), round(_oz, 6)],   # 原图里画在哪儿（显示/排查用）
    }


def suggest_unit(sec):
    """面积明显不对时，按面积反推这张图应该选哪个图纸单位，返回单位名（如 "m 米"）。

    面积随单位比例的平方变化：A ∝ scale²。用当前单位算出的面积反推需要的比例，
    再挑最接近的那个单位。
    """
    try:
        cur = float(sec.get("unit") or "0.001")
        a = float((sec.get("props") or {}).get("A") or 0)
    except (TypeError, ValueError):
        return ""
    if a <= 0 or cur <= 0:
        return ""
    need = cur * (1.0 / a) ** 0.5          # 想要 1 m² 上下
    return min(UNIT_SCALES, key=lambda u: abs(float(u[1]) / need - 1.0))[0]


def unit_advice(sec):
    """面积明显不像梁截面时，给人一句能照做的建议。"""
    _sug = suggest_unit(sec)
    _cur = {u[1]: u[0] for u in UNIT_SCALES}.get(txt(sec.get("unit")).strip(), "")
    if _sug and _cur and _sug == _cur:
        return ("现在选的单位（%s）其实就是对的，只是这个面积还是按老单位算出来的 —— "
                "点一下「重新计算」重算一遍。" % _cur)
    if _sug:
        return "「图纸单位」多半选错了（这张图按「%s」算才合理），改过来程序会立刻重算。" % _sug
    return "请检查第 4 步的「图纸单位」是不是选错了。"


def loop_area(lp):
    """多边形的有向面积（鞋带公式）×2：正=逆时针。"""
    s = 0.0
    n = len(lp)
    for i in range(n):
        x1, y1 = lp[i][0], lp[i][1]
        x2, y2 = lp[(i + 1) % n][0], lp[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return s


def orient_loop(lp, ccw=True):
    """按 MIDAS 的习惯整理环的方向：外轮廓逆时针(CCW)、孔洞顺时针(CW)。

    注意：顶点里不要重复第一点（实测重复首点会让 MIDAS 报「截面尺寸输入有错误」），
    相邻的重复点也一并去掉。
    """
    pts = []
    for p in lp:
        q = [float(p[0]), float(p[1])]
        if not pts or abs(q[0] - pts[-1][0]) > 1e-9 or abs(q[1] - pts[-1][1]) > 1e-9:
            pts.append(q)
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
        pts = pts[:-1]
    a = loop_area(pts)
    if (a < 0 and ccw) or (a > 0 and not ccw):
        pts.reverse()
    return pts


def _tess_circle(cx, cy, r, n=72):
    import math
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _tess_arc(cx, cy, r, a1, a2, n=48):
    import math
    if a2 < a1:
        a2 += 360.0
    return [(cx + r * math.cos(math.radians(a1 + (a2 - a1) * i / n)),
             cy + r * math.sin(math.radians(a1 + (a2 - a1) * i / n))) for i in range(n + 1)]


def _chain_segments(segs, tol):
    """把开口线段首尾相接成闭合环。返回 (闭合环列表, 没用上的线段数)。"""
    segs = [list(s) for s in segs if len(s) >= 2]
    total = len(segs)
    used = 0
    loops = []
    while segs:
        cur = segs.pop(0)
        used += 1
        joined = True
        while joined and not (len(cur) > 2 and _dist(cur[0], cur[-1]) <= tol):
            joined = False
            for i, s in enumerate(segs):
                if _dist(cur[-1], s[0]) <= tol:
                    cur.extend(s[1:])
                elif _dist(cur[-1], s[-1]) <= tol:
                    cur.extend(list(reversed(s))[1:])
                elif _dist(cur[0], s[-1]) <= tol:
                    cur = s[:-1] + cur
                elif _dist(cur[0], s[0]) <= tol:
                    cur = list(reversed(s))[:-1] + cur
                else:
                    continue
                segs.pop(i)
                used += 1
                joined = True
                break
        if len(cur) >= 4 and _dist(cur[0], cur[-1]) <= tol:
            loops.append(cur[:-1])
        elif len(cur) < 2:
            used -= 1
    # 没能围成环的段数：以前用 total - used 算，而 used 是按「弹出了几段」记的，
    # 结果几乎总是 0，界面上那句「有 N 段线没能围成闭合环」永远不会出现（等于静默丢图）。
    return loops, len(segs)


def read_dxf_loops(path, unit_scale=1.0):
    """读 DXF，返回 (闭合环列表, 提示信息列表)。"""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError as exc:
            return [], ["读不了文件：%s" % exc]
    if raw is None:
        return [], ["dxf 编码无法识别（既不是 UTF-8 也不是 GBK）"]

    lines = raw.splitlines()
    pairs = []
    i = 0
    while i + 1 < len(lines):
        code = lines[i].strip()
        val = lines[i + 1].strip()
        i += 2
        try:
            pairs.append((int(code), val))
        except ValueError:
            continue

    seg, in_ent = [], False
    for k, (code, val) in enumerate(pairs):
        if code == 0 and val == "SECTION":
            nxt = pairs[k + 1] if k + 1 < len(pairs) else (0, "")
            in_ent = (nxt[0] == 2 and nxt[1] == "ENTITIES")
            continue
        if code == 0 and val == "ENDSEC":
            in_ent = False
            continue
        if in_ent:
            seg.append((code, val))

    closed, open_segs, notes = [], [], []
    state = {"type": None, "pts": [], "flags": 0, "num": None}

    def flush():
        t, pts, fl, nu = state["type"], state["pts"], state["flags"], state["num"]
        if t in ("LWPOLYLINE", "POLYLINE") and 0 < len(pts) < 3:
            msg = ("有一条多段线只读到 %d 个顶点，已忽略"
                   "（描图时请用「多段线」一笔画完轮廓，别用两三个点）" % len(pts))
            if msg not in notes:
                notes.append(msg)
        if t in ("LWPOLYLINE", "POLYLINE") and len(pts) >= 3:
            (closed if (fl & 1) else open_segs).append(pts)
        elif t == "CIRCLE" and nu and nu[2] > 0:
            closed.append(_tess_circle(nu[0], nu[1], nu[2]))
        elif t == "ARC" and nu and nu[2] > 0:
            open_segs.append(_tess_arc(nu[0], nu[1], nu[2], nu[3], nu[4]))
        elif t == "LINE" and nu and len(nu) >= 4:
            open_segs.append([(nu[0], nu[1]), (nu[2], nu[3])])
        elif t in ("SPLINE", "ELLIPSE", "INSERT", "SOLID", "HATCH", "3DFACE"):
            msg = "%s 暂不支持，已忽略（请把轮廓炸开成多段线 / 直线 / 圆弧）" % t
            if msg not in notes:
                notes.append(msg)
        state["type"], state["pts"], state["flags"], state["num"] = None, [], 0, None

    for code, val in seg:
        if code == 0:
            # 经典 DXF（R12 及更早）的 POLYLINE 把顶点放在后面的 VERTEX 实体里：
            #   POLYLINE ... 66=1 70=1 / VERTEX x y / VERTEX x y / ... / SEQEND
            # 所以整个 POLYLINE 段落必须按一个实体处理 —— 中途遇到 VERTEX 不能 flush，
            # 收尾要等 SEQEND（或下一个不是 VERTEX 的实体）。
            if state["type"] == "POLYLINE" and val in ("VERTEX", "SEQEND"):
                state["num"] = [0.0, 0.0, 0.0, 0.0, 0.0]
                continue
            flush()
            state["type"] = val
            state["num"] = [0.0, 0.0, 0.0, 0.0, 0.0]
            continue
        t = state["type"]
        try:
            f = float(val)
        except ValueError:
            continue
        if t in ("LWPOLYLINE", "POLYLINE"):
            if code == 10:
                state["pts"].append([f, 0.0])
            elif code == 20 and state["pts"]:
                state["pts"][-1][1] = f
            elif code == 70:
                state["flags"] |= int(f)
        elif t == "CIRCLE":
            if code == 10:
                state["num"][0] = f
            elif code == 20:
                state["num"][1] = f
            elif code == 40:
                state["num"][2] = f
        elif t == "ARC":
            idx = {10: 0, 20: 1, 40: 2, 50: 3, 51: 4}.get(code)
            if idx is not None:
                state["num"][idx] = f
        elif t == "LINE":
            idx = {10: 0, 20: 1, 11: 2, 21: 3}.get(code)
            if idx is not None:
                state["num"][idx] = f
    flush()

    if open_segs:
        xs = [p[0] for s in open_segs for p in s]
        ys = [p[1] for s in open_segs for p in s]
        diag = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5 if xs else 1.0
        tol = max(diag * 1e-6, 1e-9)
        chained, leftover = _chain_segments(open_segs, tol)
        if chained:
            notes.append("由直线/圆弧串成了 %d 个闭合环" % len(chained))
            closed.extend(chained)
        if leftover:
            notes.append("有 %d 段线没能围成闭合环，已忽略——请确认轮廓是闭合的" % leftover)

    loops = []
    for lp in closed:
        pts = [(p[0] * unit_scale, p[1] * unit_scale) for p in lp]
        if len(pts) >= 3:
            loops.append(pts)
    return loops, notes


UNIT_SCALES = [("mm 毫米", "0.001"), ("cm 厘米", "0.01"), ("dm 分米", "0.1"), ("m 米", "1"),
               ("in 英寸", "0.0254"), ("ft 英尺", "0.3048")]


def shape_code_of(sec):
    """从截面记录里取出形状代码（下拉框显示的是 'SB · 实心矩形' 这种文本）。"""
    raw = txt(sec.get("shape", "SB"))
    code = raw.split("·")[0].strip().split(" ")[0].strip()
    if code in (CAD_SHAPE, TS_SHAPE) or code in SHAPE_BY_CODE:
        return code
    return "SB"


def build_mct(m):
    p = m["project"]
    compat = bool(p.get("compat"))
    L = []
    add = L.append

    add("; ================================================================")
    add("; MIDAS Civil NX — MCT 命令文件")
    add("; 由「MIDAS 建模向导」生成  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if txt(p.get("name")).strip():
        add("; 项目名称：" + txt(p["name"]).strip())
    add("; 导入：MIDAS Civil NX → 文件 → 导入 → MCT 命令文件")
    add("; ================================================================")
    add("")

    add("*UNIT    ; Unit System")
    if compat:
        # Civil 2022 及更早：*UNIT 只有力/长度两个字段，多写会报错
        add("; FORCE, LENGTH")
        add("   %s, %s" % (p.get("force", "KN"), p.get("dist", "M")))
    else:
        add("; FORCE, LENGTH, HEAT, TEMPER")
        add("   %s, %s, %s, %s" % (p.get("force", "KN"), p.get("dist", "M"),
                                   p.get("heat", "KJ"), p.get("temp_unit", "C")))
    add("")

    add("*STRUCTYPE    ; Structure Type")
    if compat:
        # Civil 2022 及更早的字段更少：iSTYP, iMASS, iSMAS, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB。
        # 这行是照实测能导入的旧版 .mct 原文写的（见 samples/）。
        add("; iSTYP, iMASS, iSMAS, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB")
        add("   %s, 1, %s, %s, %s, NO, NO" % (p.get("styp", "1"), p.get("smas", "1"),
                                               num(p.get("grav", 9.806)), num(p.get("temper", 0))))
    else:
        add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
        add("   %s, 1, 1, YES, NO, %s, %s, NO, NO, NO" % (p.get("styp", "1"),
                                                          num(p.get("grav", 9.806)), num(p.get("temper", 0))))
    add("")

    if m["materials"]:
        add("*MATERIAL    ; Material")
        if compat:
            add("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, [DATA1]")
            add("; [DATA1] : 1, DB, NAME  /  2, ELAST, POISN, THERMAL, DEN, MASS")
        else:
            add("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]")
            add("; [DATA1] : 1, STANDARD, CODE, DB, USEELAST, ELAST  /  2, ELAST, POISN, THERMAL, DEN, MASS")
        for i, mat in enumerate(m["materials"], start=1):
            head = "   %d, %s, %s, 0, 0, " % (i, mat.get("type", "CONC"),
                                              aname(mat.get("name"), "MAT%d" % i))
            # PLAST 必须占一个空字段（就是这里两个逗号之间什么都不写）！
            # 少了它整行会往前串一位，MIDAS 会连报三个错：
            #   「TUNIT值有错误」「必须输入是或否」「整数值错误」
            # 因为 TUNIT 收到了 C、bMASS 收到了 NO、DAMPRATIO 收到了 0.05。
            # 这个空字段照抄自 MIDAS Civil NX 自己导出的原文（见 examples/）。
            # Civil 2022 及更早没有 DAMPRATIO 字段；NX 才有，默认阻尼比 0.05。
            tail_head = head + (", C, NO, " if compat else ", C, NO, 0.05, ")
            if mat.get("mode") == "db":
                add(tail_head + "1, %s, , %s, NO, 0" % (txt(mat.get("standard")), txt(mat.get("dbname"))))
            else:
                add(tail_head + "2, %s, %s, %s, %s%s" % (num(mat.get("elast")), num(mat.get("poisn")),
                                                         num(mat.get("thermal")), num(mat.get("den")),
                                                         ", 0"))
        add("")

    # DXF 自定义截面走 *SECT-PSCVALUE（MIDAS 里叫「PSC 数值 / 一般截面」）——
    # MIDAS 没有 *SECT-GENERAL 这个命令；多边形截面只能由 *SECT-PSCVALUE 定义，
    # 它不进 *SECTION 表。格式照 MIDAS Civil NX 自己导出的原文写。
    _cad = [(i, s) for i, s in enumerate(m["sections"], start=1) if shape_code_of(s) == CAD_SHAPE]
    _val_fallback = []          # 外轮廓不唯一的 CAD 截面：*SECT-PSCVALUE 放不下，退回「数值截面」
    if _cad:
        add("*SECT-PSCVALUE    ; PSC Value, General Section（DXF 自定义截面）")
        add("; SECT=iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, bBU, bEQ, SNAME2(PSC VAL) ; 1st line")
        add(";      AREA, ASy, ASz, Ixx, Iyy, Izz                                    ; 2nd line")
        add(";      CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz          ; 3rd line")
        add(";      Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4                                   ; 4th line")
        add(";      HT, BT, T1, T2                                                   ; 5th line(PSC)")
        add(";      bSHEARCHK, Z1, Z3, bAUTO_QY1, QY1, ..., TOR, bAUTO_SHR1, SHR1, ... ; 6th line(PSC)")
        add(";      OPOLY=X1, Y1, X2, Y2, ...  外轮廓（逆时针；首点不要重复）")
        add(";      IPOLY=X1, Y1, X2, Y2, ...  孔洞（顺时针），有几个写几行")
        for i, sec in _cad:
            name = aname(sec.get("name"), "SEC%d" % i)
            off = sec.get("offset", "CC")
            pr = sec.get("props") or {}
            _outs = [orient_loop(lp, True) for lp in (pr.get("OUTER_LOOPS") or [])
                     if len(lp) >= 3 and abs(loop_area(lp)) > 1e-12]
            _ins = [orient_loop(lp, False) for lp in (pr.get("HOLE_LOOPS") or [])
                    if len(lp) >= 3 and abs(loop_area(lp)) > 1e-12]
            if len(_outs) != 1:
                # 0 个或 2 个以上独立外轮廓：*SECT-PSCVALUE 只认 1 个外轮廓，改用数值截面兜底
                _val_fallback.append((i, sec))
                continue
            try:
                _A = float(pr.get("A") or 0); _iyy = float(pr.get("Iyy") or 0)
                _izz = float(pr.get("Izz") or 0); _ixx = float(pr.get("Ixx") or 0)
                _cy = float(pr.get("Cy") or 0); _cz = float(pr.get("Cz") or 0)
                _cyp = float(pr.get("CyP") or 0); _cym = float(pr.get("CyM") or 0)
                _czp = float(pr.get("CzP") or 0); _czm = float(pr.get("CzM") or 0)
                _po = float(pr.get("PERI_OUT") or 0); _pi = float(pr.get("PERI_IN") or 0)
                _uyy = _A * 5.0 / 6.0; _uzz = _A * 5.0 / 6.0     # 剪切面积：按矩形取 5/6·A
            except (TypeError, ValueError):
                _A = _iyy = _izz = _ixx = _cy = _cz = 0.0
                _cyp = _cym = _czp = _czm = _po = _pi = _uyy = _uzz = 0.0
            add(" SECT=%4d, PSC       , %-17s , %s, 0, 0, 0, 0, 0, 0, %s, %s, VALU, NO, NO, "
                % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO")))
            add("        %s, %s, %s, %s, %s, %s" % (numg(_A), numg(_uyy), numg(_uzz),
                                                     numg(_ixx), numg(_iyy), numg(_izz)))
            add("        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                % (numg(_cyp), numg(_cym), numg(_czp), numg(_czm),
                   numg(_A * _czp / 8.0), numg(_A * _cyp / 8.0),      # QyB, QzB（矩形近似）
                   numg(_po), numg(_pi), numg(_cyp), numg(_czp)))     # 末两个照 MIDAS 自己的写法给极值
            add("        %s, %s, %s, %s, %s, %s, %s, %s"
                % (numg(-_cym), numg(_cyp), numg(_cyp), numg(-_cym),
                   numg(_czp), numg(_czp), numg(-_czm), numg(-_czm)))
            add("        0.1, 0.1, 0.1, 0.1")            # HT, BT, T1, T2（PSC 设计用，先给默认值）
            add("        YES, 0, 0, YES, , YES, , YES, , 0, YES, , YES, , YES, ")
            for _lp in _outs:
                add("       OPOLY=" + ", ".join(numg(v) for p in _lp for v in (p[0], p[1])))
            for _lp in _ins:
                add("       IPOLY=" + ", ".join(numg(v) for p in _lp for v in (p[0], p[1])))
        add("")

    _std = sorted([(i, s) for i, s in enumerate(m["sections"], start=1)
                   if s.get("shape") != CAD_SHAPE] + _val_fallback, key=lambda t: t[0])
    if _std:
        add("*SECTION    ; Section Properties")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, [DATA1], [DATA2]      ; 1st line - DB/USER")
        add("; iSEC, TYPE, SNAME, [OFFSET], bSD, bWE, SHAPE, BLT, D1, ..., D8, iCEL ; 1st line - VALUE")
        add(";       AREA, ASy, ASz, Ixx, Iyy, Izz                                 ; 2nd line")
        add(";       CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz       ; 3rd line")
        add(";       Y1, Y2, Y3, Y4, Z1, Z2, Z3, Z4                                ; 4th line")
        for i, sec in _std:
            name = aname(sec.get("name"), "SEC%d" % i)
            off = sec.get("offset", "CC")
            if shape_code_of(sec) == TS_SHAPE:
                _secs = m["sections"]
                try:
                    _ia = int(float(sec.get("i_sect") or 0)) - 1
                    _ja = int(float(sec.get("j_sect") or 0)) - 1
                except (TypeError, ValueError):
                    _ia = _ja = -1
                _si = _secs[_ia] if 0 <= _ia < len(_secs) else None
                _sj = _secs[_ja] if 0 <= _ja < len(_secs) else None
                _ci = shape_code_of(_si) if _si else "SB"

                def _pad8(o):
                    if not o:
                        return ["0"] * 8
                    _c = shape_code_of(o)
                    _n = len(SHAPE_BY_CODE.get(_c, SHAPES[0])[2])
                    _d = [num(o.get("dims", [])[k]) if k < len(o.get("dims", [])) else "0" for k in range(_n)]
                    return (_d + ["0"] * 8)[:8]

                add("   %d, TAPERED, %s, %s, 0, 0, 0, 0, 0, 0, 0, 0, %s, %s, NO, %s, 1, 1, USER"
                    % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"), _ci))
                _di, _dj = _pad8(_si), _pad8(_sj)
                if sec.get("mirror"):
                    _di, _dj = _dj, _di
                add("      %s, %s" % (", ".join(_di), ", ".join(_dj)))
                continue
            head = "   %d, %s, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, " % (
                i, "VALUE" if shape_code_of(sec) == CAD_SHAPE else "DBUSER",
                name, off, sec.get("shear", "YES"), sec.get("warp", "NO"))
            if shape_code_of(sec) == CAD_SHAPE:
                pr = sec.get("props") or {}
                # VALUE 型（数值截面）—— 字段按 MIDAS Civil NX 自己导出的写法（共 23 个）：
                #   iSEC, TYPE, SNAME, OFFSET, 0,0,0,0,0,0, bSD, bWE, SHAPE, BUILT, D1..D8, iCEL
                # 注意 OFFSET 后面那 6 个 0 不能省：少了 MIDAS 会把这行整条丢掉（导入返回 200
                # 但截面根本不建，或者报「参数数量过多/截面尺寸输入有错误」）。
                try:
                    _h = float(pr.get("CzP") or 0) + float(pr.get("CzM") or 0)
                    _b = float(pr.get("CyP") or 0) + float(pr.get("CyM") or 0)
                except (TypeError, ValueError):
                    _h = _b = 0.0
                add("   %d, VALUE, %s, %s, 0, 0, 0, 0, 0, 0, %s, %s, SB, BUILT, %s, %s, 0, 0, 0, 0, 0, 0, 0"
                    % (i, name, off, sec.get("shear", "YES"), sec.get("warp", "NO"),
                       numg(_h), numg(_b)))
                add("   %s, %s, %s, %s, %s, %s" % (
                    numg(pr.get("A")), numg(pr.get("ASy")), numg(pr.get("ASz")),
                    numg(pr.get("Ixx")), numg(pr.get("Iyy")), numg(pr.get("Izz"))))
                add("   %s, %s, %s, %s, 0, 0, %s, %s, %s, %s" % (
                    numg(pr.get("CyP")), numg(pr.get("CyM")), numg(pr.get("CzP")), numg(pr.get("CzM")),
                    numg(pr.get("PERI_OUT")), numg(pr.get("PERI_IN")), numg(pr.get("Cy")), numg(pr.get("Cz"))))
                # 第 4 行：Y1..Y4, Z1..Z4, Zyy, Zzz —— 共 10 个值。
                # Y/Z 是截面的 4 个角点，MIDAS 的顺序是 (-CyM,+CzP) (+CyP,+CzP) (+CyP,-CzM) (-CyM,-CzM)。
                try:
                    _cyp = float(pr.get("CyP") or 0); _cym = float(pr.get("CyM") or 0)
                    _czp = float(pr.get("CzP") or 0); _czm = float(pr.get("CzM") or 0)
                    _iyy = float(pr.get("Iyy") or 0); _izz = float(pr.get("Izz") or 0)
                except (TypeError, ValueError):
                    _cyp = _cym = _czp = _czm = _iyy = _izz = 0.0
                add("   %s, %s, %s, %s, %s, %s, %s, %s, %s, %s" % (
                    numg(-_cym), numg(_cyp), numg(_cyp), numg(-_cym),
                    numg(_czp), numg(_czp), numg(-_czm), numg(-_czm),
                    numg(_iyy / _cyp if _cyp else 0), numg(_izz / _czp if _czp else 0)))
            else:
                code = sec.get("shape", "SB")
                shape = SHAPE_BY_CODE.get(code, SHAPES[0])
                dims = [num(sec.get("dims", [])[k]) if k < len(sec.get("dims", [])) else "0"
                        for k in range(len(shape[2]))]
                vals = dims + ["0"] * max(0, DIM_SLOTS - len(dims))
                add(head + "%s, 2, %s" % (code, ", ".join(vals)))
        add("")

    if m["nodes"]:
        add("*NODE    ; Nodes")
        add("; iNO, X, Y, Z")
        for i, nd in enumerate(m["nodes"], start=1):
            add("   %d, %s, %s, %s" % (i, num(nd.get("x")), num(nd.get("y")), num(nd.get("z"))))
        add("")

    if m["elements"]:
        add("*ELEMENT    ; Elements")
        add("; iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB, EXVAL         ; Frame Element")
        _assign = m.get("sec_assign") or []

        def _sect_for(_e, _default):
            for _a in _assign:
                try:
                    _f = int(float(_a.get("from") or 0))
                    _t = int(float(_a.get("to") or 0))
                    _s = int(float(_a.get("sect") or 0))
                except (TypeError, ValueError):
                    continue
                if _f <= _e <= _t and _s > 0:
                    return str(_s)
            return _default

        for i, el in enumerate(m["elements"], start=1):
            add("   %d, %s, %s, %s, %s, %s, %s, 0"
                % (i, el.get("type", "BEAM"), num(el.get("mat")), _sect_for(i, num(el.get("sect"))),
                   num(el.get("i")), num(el.get("j")), num(el.get("angle"))))
        add("")

    sup = [s for s in m["supports"] if txt(s.get("nodes")).strip()]
    if sup:
        add("*CONSTRAINT    ; Supports")
        add("; NODE_LIST, CONST(Dx,Dy,Dz,Rx,Ry,Rz), GROUP")
        for s in sup:
            code = "".join("1" if s.get(k) else "0" for k in ("dx", "dy", "dz", "rx", "ry", "rz"))
            ids = " ".join([t for t in txt(s.get("nodes")).replace(",", " ").split() if t])
            add("   %s, %s, %s" % (ids, code, aascii(s.get("group"))))
        add("")

    if m["loadcases"]:
        add("*STLDCASE    ; Static Load Cases")
        add("; LCNAME, LCTYPE, DESC")
        for lc in m["loadcases"]:
            add("   %s, %s, %s" % (aname(lc.get("name"), "LC"), lc.get("type", "D"), aname(lc.get("desc"))))
        add("")

    def lcname(raw):
        """把荷载引用的工况名换算成 *STLDCASE 里实际写出去的那个名字。

        两边必须一致：*STLDCASE 会去掉非 ASCII，而 *USE-STLD 以前直接用原名，
        名字里只要有中文（或逗号）对不上，MIDAS 就把这些荷载整批丢掉。
        """
        want = txt(raw).strip()
        if not want:
            return ""
        for _lc in m["loadcases"]:
            if txt(_lc.get("name")).strip() == want:
                return aname(want, "LC")
        for _lc in m["loadcases"]:
            if aname(_lc.get("name"), "LC") == aname(want, "LC"):
                return aname(want, "LC")
        return aname(want, "LC")

    sw = m["selfweight"]
    if sw.get("on") and txt(sw.get("lc")).strip():
        add("*USE-STLD, " + lcname(sw["lc"]))
        add("; *SELFWEIGHT, X, Y, Z, GROUP")
        add("*SELFWEIGHT    ; Self Weight")
        add("   %s, %s, %s, %s" % (num(sw.get("x")), num(sw.get("y")),
                                   num(sw.get("z")), aname(sw.get("group"))))
        add("")

    for nl in m["nodalloads"]:
        add("*USE-STLD, " + lcname(nl.get("lc")))
        add("*CONLOAD    ; Nodal Loads")
        add("; NODE_LIST, FX, FY, FZ, MX, MY, MZ, GROUP")
        add("   %s, %s, %s, %s, %s, %s, %s, %s"
            % (txt(nl.get("node")), num(nl.get("fx")), num(nl.get("fy")), num(nl.get("fz")),
               num(nl.get("mx")), num(nl.get("my")), num(nl.get("mz")), aname(nl.get("group"))))
        add("")

    for bl in m["beamloads"]:
        add("*USE-STLD, " + lcname(bl.get("lc")))
        add("*BEAMLOAD    ; Element Beam Loads")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, D1, P1, D2, P2, D3, P3, D4, P4, GROUP")
        add("   %s, %s, %s, %s, %s, NO, LY, , , , %s, %s, %s, %s, %s, %s, %s, %s, , NO, 0, 0, NO, %s, 0, NO"
            % (txt(bl.get("elems")), bl.get("cmd", "BEAM"), bl.get("type", "UNILOAD"), bl.get("dir", "GZ"),
               bl.get("proj", "NO"), num(bl.get("d1")), num(bl.get("p1")), num(bl.get("d2")), num(bl.get("p2")),
               num(bl.get("d3")), num(bl.get("p3")), num(bl.get("d4")), num(bl.get("p4")),
               aascii(bl.get("group"))))
        add("")

    add("*ENDDATA")
    # 兜底：MIDAS 遇到字段里的非 ASCII 会整份放弃导入，这里把非注释行的非 ASCII 全部去掉
    return "\r\n".join(L) + "\r\n"


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

ADVISORY_PREFIX = "名称里有中文"    # 只提醒、不拦人的提示（中文名 MIDAS 其实认，实测过）


def issues_for(step_id, m):
    out = []
    nn = len(m["nodes"])
    if step_id == "loadcases":
        _seen = {}
        if not m["loadcases"]:
            out.append("至少需要一个荷载工况。")
        for _i, _lc in enumerate(m["loadcases"], start=1):
            _nm = txt(_lc.get("name")).strip()
            if not _nm:
                out.append("第 %d 个工况没有填名称。" % _i)
            elif _nm in _seen:
                out.append("工况名「%s」重复了（第 %d 个和第 %d 个）。MIDAS 不允许重名，请改成不同的名字。" % (_nm, _seen[_nm], _i))
            else:
                _seen[_nm] = _i
            # 中文名原样写进 MCT（实测可导入），所以这里只按原名查重就够了。
            # 但名称里不能有逗号：MCT 用逗号分字段，带了会让整行错列。
            if _nm and "," in _nm:
                out.append("工况名「%s」里有逗号。MCT 用逗号分隔字段，写出去会让整行错位 —— "
                           "请把逗号改成空格或其他字符。" % _nm)
        for _i, _b in enumerate(m["beamloads"], start=1):
            _lc = txt(_b.get("lc")).strip()
            if not _lc:
                out.append("第 %d 条梁单元荷载没有选工况（会生成空的 *USE-STLD，MIDAS 报错）。" % _i)
        for _i, _n in enumerate(m["nodalloads"], start=1):
            _lc = txt(_n.get("lc")).strip()
            if not _lc:
                out.append("第 %d 条节点荷载没有选工况（会生成空的 *USE-STLD，MIDAS 报错）。" % _i)
        if m.get("selfweight", {}).get("on"):
            _sw = txt(m["selfweight"].get("lc")).strip()
            if not _sw:
                out.append("自重没有选工况（会生成空的 *USE-STLD，MIDAS 报错）。")
            elif _sw not in _seen:
                out.append("自重的工况「%s」不存在。工况表里现有：%s —— 要填工况的名称（D/L 是类型）。"
                           % (_sw, "、".join(list(_seen.keys())) or "（还没有工况）"))
        for _lab, _rows, _key, _zh in (("梁单元荷载", m["beamloads"], "elems", "单元范围"),
                                       ("节点荷载", m["nodalloads"], "node", "节点号")):
            for _i, _r in enumerate(_rows, start=1):
                if not txt(_r.get(_key)).strip():
                    out.append("%s %d 没有填%s。" % (_lab, _i, _zh))
        return out
    if step_id == "loads":
        _ne = len(m["elements"])
        for _i, _b in enumerate(m["beamloads"], start=1):
            _v = txt(_b.get("elems")).strip().replace(",", " ")
            _nums = []
            for _tok in _v.split():
                if "to" in _tok.lower():
                    _a, _, _b2 = _tok.lower().partition("to")
                    if is_num(_a) and is_num(_b2):
                        _nums += [int(float(_a)), int(float(_b2))]
                elif _tok.isdigit():
                    _nums.append(int(_tok))
            if _nums and (max(_nums) > _ne or min(_nums) < 1):
                out.append("第 %d 条梁单元荷载的单元范围超出（现有 1~%d 号单元）。" % (_i, _ne))
        for _i, _n in enumerate(m["nodalloads"], start=1):
            _v = txt(_n.get("node")).strip()
            if _v and _v.isdigit() and not (1 <= int(_v) <= nn):
                out.append("第 %d 条节点荷载的节点号 %s 超出范围（现有 1~%d 号支点）。" % (_i, _v, nn))
    if step_id == "supports":
        if not m["supports"]:
            out.append("至少要有一个支承，否则结构不稳定、MIDAS 算不了。")
        for _i, _sp in enumerate(m["supports"], start=1):
            if not txt(_sp.get("nodes")).strip():
                out.append("支承 %d 没有填节点号。" % _i)
    if step_id == "elements":
        _dup = {}
        for _i, _el in enumerate(m["elements"], start=1):
            if is_num(_el.get("i")) and is_num(_el.get("j")):
                _key = tuple(sorted((int(float(_el["i"])), int(float(_el["j"])))))
                if _key in _dup:
                    out.append("单元 %d 与单元 %d 连接的是同样的两个支点（重复单元）。" % (_i, _dup[_key]))
                else:
                    _dup[_key] = _i
    if step_id == "sections":
        _ne = len(m["elements"])
        _ns = len(m["sections"])
        for _i, _a in enumerate(m.get("sec_assign") or [], start=1):
            if not (is_num(_a.get("from")) and is_num(_a.get("to")) and is_num(_a.get("sect"))):
                out.append("分段 %d 的起始/终止单元号和截面号都要填数字。" % _i)
                continue
            _f = int(float(_a["from"])); _t = int(float(_a["to"])); _s = int(float(_a["sect"]))
            if _f < 1 or _t > _ne or _f > _t:
                out.append("分段 %d 的单元范围 %d~%d 越界（现有 1~%d 号单元，且起始不能大于终止）。"
                           % (_i, _f, _t, _ne))
            if _s < 1 or _s > _ns:
                out.append("分段 %d 指定的截面号 %d 越界（现有 1~%d 号截面）。" % (_i, _s, _ns))
    if step_id == "materials":
        for _i, _mat in enumerate(m["materials"], start=1):
            if is_num(_mat.get("poisn")) and not (0.0 <= float(_mat["poisn"]) < 0.5):
                out.append("材料 %d 的泊松比应在 0 ~ 0.5 之间。" % _i)
            if is_num(_mat.get("den")) and float(_mat["den"]) <= 0:
                out.append("材料 %d 的容重应大于 0。" % _i)
            # 材料名原样写出去（中文可导入），只挡逗号
            _nm = txt(_mat.get("name")).strip()
            if _nm and "," in _nm:
                out.append("材料名「%s」里有逗号，会让 MCT 整行错位，请改掉。" % _nm)
    if step_id == "sections":
        for _i, _sec in enumerate(m["sections"], start=1):
            _nm = txt(_sec.get("name")).strip()
            if _nm and "," in _nm:
                out.append("截面名「%s」里有逗号，会让 MCT 整行错位，请改掉。" % _nm)
    if step_id == "nodes":
        if nn == 0:
            out.append("至少需要一个支点。")
        for i, nd in enumerate(m["nodes"], start=1):
            if not (is_num(nd.get("x")) and is_num(nd.get("y")) and is_num(nd.get("z"))):
                out.append("支点 %d 的 X/Y/Z 必须是数字。" % i)
    elif step_id == "elements":
        if not m["elements"]:
            out.append("至少需要一个单元。")
        for i, el in enumerate(m["elements"], start=1):
            if not (is_num(el.get("i")) and is_num(el.get("j"))):
                out.append("单元 %d 的 i / j 节点号必须是数字。" % i)
                continue
            if float(el["i"]) == float(el["j"]):
                out.append("单元 %d 的 i 与 j 不能是同一个节点。" % i)
            if float(el["i"]) > nn or float(el["j"]) > nn:
                out.append("单元 %d 引用了不存在的支点（当前共 %d 个）。" % (i, nn))
            if m["materials"] and (not is_num(el.get("mat")) or float(el["mat"]) > len(m["materials"]) or float(el["mat"]) < 1):
                out.append("单元 %d 的材料号超出范围（当前共 %d 种材料）。" % (i, len(m["materials"])))
            if m["sections"] and (not is_num(el.get("sect")) or float(el["sect"]) > len(m["sections"]) or float(el["sect"]) < 1):
                out.append("单元 %d 的截面号超出范围（当前共 %d 个截面）。" % (i, len(m["sections"])))
    elif step_id == "materials":
        if not m["materials"]:
            out.append("至少需要一种材料。")
        for i, mat in enumerate(m["materials"], start=1):
            if not txt(mat.get("name")).strip():
                out.append("材料 %d 需要名称。" % i)
            if mat.get("mode") == "db":
                if not txt(mat.get("dbname")).strip():
                    out.append("材料 %d 需要填写数据库中的名称。" % i)
            elif not is_num(mat.get("elast")) or float(mat["elast"]) <= 0:
                out.append("材料 %d 的弹性模量必须大于 0。" % i)
    elif step_id == "sections":
        if not m["sections"]:
            out.append("至少需要一个截面。")
        for i, sec in enumerate(m["sections"], start=1):
            if not txt(sec.get("name")).strip():
                out.append("截面 %d 需要名称。" % i)
            code = shape_code_of(sec)
            if code == CAD_SHAPE:
                if not sec.get("props"):
                    out.append("截面 %d 是自定义截面：请选择 CAD 文件并解析成功后再继续。" % i)
                else:
                    try:
                        _a = float((sec.get("props") or {}).get("A") or 0)
                    except (TypeError, ValueError):
                        _a = 0.0
                    if not (0.005 <= _a <= 50):
                        out.append("截面 %d（DXF 自定义截面）算出来的面积是 %.6g m²，跟梁截面差得太远 —— %s"
                                   % (i, _a, unit_advice(sec)))
                continue
            if code == TS_SHAPE:
                for key, lab in (("i_sect", "i 端"), ("j_sect", "j 端")):
                    v = sec.get(key)
                    if not is_num(v) or not (1 <= int(float(v)) <= len(m["sections"])):
                        out.append("变截面 %d 的「%s截面号」要填 1~%d。" % (i, lab, len(m["sections"])))
                    elif int(float(v)) == i:
                        out.append("变截面 %d 不能引用自己。" % i)
                continue
            shape = SHAPE_BY_CODE.get(code, SHAPES[0])
            dims = sec.get("dims", [])
            for k, d in enumerate(shape[2]):
                v = dims[k] if k < len(dims) else ""
                if not is_num(v) or float(v) <= 0:
                    out.append("截面 %d（%s）的尺寸「%s」必须大于 0 —— 留空会被写成 0，"
                               "MIDAS 导入时就报「截面尺寸输入有错误」。"
                               % (i, shape[1], d[1]))
    elif step_id == "supports":
        for i, s in enumerate(m["supports"], start=1):
            ids = [t for t in txt(s.get("nodes")).replace(",", " ").split() if t]
            if not ids:
                out.append("支承 %d 需要填写节点号。" % i)
            for t in ids:
                if not is_num(t) or float(t) > nn:
                    out.append("支承 %d 引用了不存在的节点 %s。" % (i, t))
            if not any(s.get(k) for k in ("dx", "dy", "dz", "rx", "ry", "rz")):
                out.append("支承 %d 至少要约束一个自由度。" % i)
    elif step_id == "loads":
        names = [txt(lc.get("name")).strip() for lc in m["loadcases"]]
        for i, nl in enumerate(m["nodalloads"], start=1):
            if txt(nl.get("lc")) not in names:
                out.append("节点荷载 %d 的工况「%s」不存在。" % (i, txt(nl.get("lc"))))
            if not txt(nl.get("node")).strip():
                out.append("节点荷载 %d 需要节点号。" % i)
            # 六个分量全是 0 = 没加荷载，MIDAS 导入时报「荷载值输入有错误」（命令行指向 *CONLOAD 那一行）
            if not any(is_num(nl.get(k)) and float(nl[k]) != 0.0
                       for k in ("fx", "fy", "fz", "mx", "my", "mz")):
                out.append("节点荷载 %d 的 6 个分量全是 0（等于没加荷载），MIDAS 导入时会报"
                           "「荷载值输入有错误」。填上实际数值，或者把这一条删掉。" % i)
        for i, bl in enumerate(m["beamloads"], start=1):
            if txt(bl.get("lc")) not in names:
                out.append("梁单元荷载 %d 的工况「%s」不存在。" % (i, txt(bl.get("lc"))))
            if not txt(bl.get("elems")).strip():
                out.append("梁单元荷载 %d 需要单元号。" % i)
            if not any(is_num(bl.get(k)) and float(bl[k]) != 0.0
                       for k in ("p1", "p2", "p3", "p4")):
                out.append("梁单元荷载 %d 的荷载值全是 0（等于没加荷载），MIDAS 导入时会报"
                           "「荷载值输入有错误」。填上实际数值，或者把这一条删掉。" % i)
    return out


STEP_IDS = ["nodes", "elements", "materials", "sections", "supports", "loadcases", "loads", "export"]
STEP_TITLES = [
    "支点（节点坐标）",
    "单元",
    "材料",
    "截面",
    "支承（边界条件）",
    "荷载工况与自重",
    "节点荷载 / 梁单元荷载",
    "生成 MCT 文件",
]
STEP_HINTS = [
    "逐个输入支点：第 1 个支点、第 2 个支点…… 编号按输入顺序自动生成。一般 X 为桥轴方向，Y 为横桥向，Z 为竖向。",
    "每个单元连接两个支点。类型一般用 BEAM（梁单元）；只受轴力时用 TRUSS。材料号、截面号对应后面两步里的编号。",
    "可以按规范数据库取值（如 JTG3362-18 的 C50），也可以直接填弹性模量、泊松比、容重和线膨胀系数。单位与顶部单位制一致。",
    "选择截面形状后按顺序填写尺寸（单位 m）。偏心点一般用 CC（形心）；剪切变形默认「考虑」。",
    "勾选要约束的自由度：铰支座勾 Dx/Dy/Dz，固定支座再勾 Rx/Ry/Rz。一行可以写多个节点号，用空格或逗号分隔。",
    "先定义荷载工况（例如 DL 恒载、LL 活载），再指定哪些工况计入结构自重。自重沿 -Z 取 -1 表示向下。",
    "第 6 步的工况只是名字，荷载值在这一步加（自重除外，它在第 6 步已经算好了）。"
    "字段含义点上面的「▶ 怎么填这一步」展开看。",
    "确认无误后保存 .mct 文件，在 MIDAS Civil NX 中「文件 → 导入 → MCT 命令文件」选择它；也可以复制文本粘贴到「工具 → 命令窗口」运行。",
]


def is_advisory(msg):
    """只提醒、不拦人的那类提示（中文名 MIDAS 其实认，实测过），返回 True。"""
    return msg.startswith(ADVISORY_PREFIX)


def all_issues(m):
    """8 个步骤的检查一次全跑，生成 / 导入之前用它兜底。

    逐步点「确定，下一步」时每一步都会拦一次；但左侧步骤栏可以直接跳到第 8 步，
    跳过去就不会再查前面几步，于是「尺寸没填全的截面」也可能被写成 .mct——
    MIDAS 导入时就报「截面尺寸输入有错误 / 运行错误: 命令行 27」。
    """
    out = []
    for _i, _sid in enumerate(STEP_IDS, start=1):
        if _sid == "export":
            continue
        for _msg in issues_for(_sid, m):
            if is_advisory(_msg):
                continue
            out.append("第 %d 步（%s）：%s" % (_i, STEP_TITLES[_i - 1], _msg))
    return out


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------

class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x740")
        self.minsize(940, 620)

        self.model = default_model()
        self.step = 0
        self.saved_path = None
        self.bundle_files = None
        self.result_files = None

        self._init_style()
        self._build_top()
        self._build_middle()
        self._build_bottom()

        self.load_session()
        self.render()

    # ---------------- 样式 ----------------
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Opt.TLabel", foreground="#8a8a8a")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("H1.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Err.TLabel", foreground="#c0392b")
        style.configure("Ok.TLabel", foreground="#2d7d46")
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

    # ---------------- 顶部：项目与单位 ----------------
    def _build_top(self):
        bar = ttk.Frame(self, padding=(12, 10, 12, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text=APP_TITLE, style="H1.TLabel").grid(row=0, column=0, columnspan=8, sticky="w")
        ttk.Label(bar, text="一次只填一件事：填完当前步骤点「确定，下一步」。标有（可选）的项目不填也能继续。",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=8, sticky="w", pady=(2, 8))

        self.v_project = tk.StringVar()
        self.v_force = tk.StringVar(value=self.model["project"]["force"])
        self.v_dist = tk.StringVar(value=self.model["project"]["dist"])
        self.v_styp = tk.StringVar(value=self.model["project"]["styp"])

        def bind(var, key):
            var.trace_add("write", lambda *_: self.model["project"].__setitem__(key, var.get()))

        bind(self.v_project, "name")
        bind(self.v_force, "force")
        bind(self.v_dist, "dist")

        ttk.Label(bar, text="项目名称（可选）").grid(row=2, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.v_project, width=24).grid(row=3, column=0, sticky="w", padx=(0, 14))
        ttk.Label(bar, text="力单位").grid(row=2, column=1, sticky="w")
        ttk.Combobox(bar, textvariable=self.v_force, values=FORCE_UNITS, width=8, state="readonly").grid(row=3, column=1, sticky="w", padx=(0, 14))
        ttk.Label(bar, text="长度单位").grid(row=2, column=2, sticky="w")
        ttk.Combobox(bar, textvariable=self.v_dist, values=DIST_UNITS, width=8, state="readonly").grid(row=3, column=2, sticky="w", padx=(0, 14))
        ttk.Label(bar, text="结构类型").grid(row=2, column=3, sticky="w")
        ttk.Combobox(bar, textvariable=self.v_styp, values=[s[1] for s in STYPES], width=22, state="readonly").grid(row=3, column=3, sticky="w")
        self.v_styp.set(dict(STYPES)[self.model["project"]["styp"]])
        self.v_styp.trace_add("write", lambda *_: self.model["project"].__setitem__("styp", self.v_styp.get().split(" ")[0]))

        ttk.Separator(self).pack(fill="x", pady=(8, 0))

    # ---------------- 中部：步骤列表 + 表单 ----------------
    def _build_middle(self):
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True)

        left = ttk.Frame(mid, padding=(10, 10, 6, 10))
        left.pack(side="left", fill="y")
        ttk.Label(left, text="步骤", style="Title.TLabel").pack(anchor="w", pady=(0, 6))
        self.step_list = tk.Listbox(left, width=22, activestyle="none", exportselection=False,
                                    font=("Microsoft YaHei UI", 9), highlightthickness=1)
        self.step_list.pack(fill="y", expand=True)
        self.step_list.bind("<<ListboxSelect>>", self._on_pick_step)

        right = ttk.Frame(mid, padding=(6, 10, 12, 6))
        right.pack(side="left", fill="both", expand=True)

        self.v_title = tk.StringVar()
        self.v_hint = tk.StringVar()
        ttk.Label(right, textvariable=self.v_title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(right, textvariable=self.v_hint, style="Hint.TLabel", wraplength=760,
                  justify="left").pack(anchor="w", pady=(2, 8))

        wrap = ttk.Frame(right)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, highlightthickness=0)
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self._hbar_shown = False
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=self.hbar.set)
        vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.form = ttk.Frame(self.canvas)
        self._form_w = 0
        self.form_id = self.canvas.create_window((0, 0), window=self.form, anchor="nw")
        self.form.bind("<Configure>", self._sync_canvas)
        self.canvas.bind("<Configure>", self._sync_canvas)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.canvas.bind("<Shift-MouseWheel>",
                         lambda e: self.canvas.xview_scroll(int(-1 * (e.delta / 120)), "units"))

    def _sync_canvas(self, _event=None):
        """表单比画布宽时（窗口窄、或系统把字号放大），靠横向滚动条把它滚出来。

        不这么干的话，表格最后一列（各页面的「删除」按钮）会被画布直接裁掉、点都点不到。
        """
        try:
            need = self.form.winfo_reqwidth()
            cur = self.canvas.winfo_width()
        except tk.TclError:
            return
        want = max(cur, need)
        if abs(want - getattr(self, "_form_w", 0)) > 1:
            self._form_w = want
            self.canvas.itemconfigure(self.form_id, width=want)
        # 只有内容真的超出画布时才把横向滚动条露出来
        if need > cur + 1:
            if not self._hbar_shown:
                self.hbar.pack(side="bottom", fill="x", before=self.canvas)   # 必须排在画布前面，否则被挤成一条缝
                self._hbar_shown = True
        elif self._hbar_shown:
            self.hbar.pack_forget()
            self._hbar_shown = False
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ---------------- 底部 ----------------
    def _build_bottom(self):
        ttk.Separator(self).pack(fill="x")
        foot = ttk.Frame(self, padding=(12, 8))
        foot.pack(fill="x")

        self.v_status = tk.StringVar()
        self.lbl_status = ttk.Label(foot, textvariable=self.v_status, style="Hint.TLabel", wraplength=640, justify="left")
        self.lbl_status.pack(side="left", fill="x", expand=True)

        ttk.Button(foot, text="清空全部", command=self.on_reset).pack(side="right", padx=(6, 0))
        ttk.Button(foot, text="清空本步", command=self.on_clear_step).pack(side="right", padx=(6, 0))
        self.btn_next = ttk.Button(foot, text="确定，下一步", command=self.on_next)
        self.btn_next.pack(side="right", padx=(6, 0))
        self.btn_prev = ttk.Button(foot, text="上一步", command=self.on_prev)
        self.btn_prev.pack(side="right")

    # ---------------- 工具 ----------------
    def svar(self, container, key, initial=""):
        v = tk.StringVar(value=txt(initial))
        v.trace_add("write", lambda *_: container.__setitem__(key, v.get()))
        return v

    def bvar(self, container, key, initial=False):
        v = tk.BooleanVar(value=bool(initial))
        v.trace_add("write", lambda *_: container.__setitem__(key, v.get()))
        return v

    def dim_var(self, sec, k, initial=""):
        """截面尺寸变量：直接写回 sec['dims'][k]，长度不够时自动补齐。"""
        v = tk.StringVar(value=txt(initial))

        def write(*_):
            dims = sec.setdefault("dims", [])
            while len(dims) <= k:
                dims.append("")
            dims[k] = v.get()

        v.trace_add("write", write)
        return v

    def entry(self, parent, var, width=10, row=0, col=0, **kw):
        e = ttk.Entry(parent, textvariable=var, width=width)
        e.grid(row=row, column=col, sticky="w", padx=(0, 12), pady=3, **kw)
        return e

    def combo(self, parent, var, values, width=14, row=0, col=0, on_change=None, **kw):
        c = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly")
        c.grid(row=row, column=col, sticky="w", padx=(0, 12), pady=3, **kw)
        if on_change:
            c.bind("<<ComboboxSelected>>", lambda e: on_change())
        return c

    @staticmethod
    def field_label(parent, text, optional=False, row=0, col=0):
        ttk.Label(parent, text=text + ("（可选）" if optional else "")).grid(row=row, column=col, sticky="w")

    # ---------------- 渲染 ----------------
    def render(self):
        for w in self.form.winfo_children():
            w.destroy()
        self.step = max(0, min(len(STEP_IDS) - 1, self.step))
        sid = STEP_IDS[self.step]
        self.v_title.set("第 %d / %d 步 · %s" % (self.step + 1, len(STEP_IDS), STEP_TITLES[self.step]))
        self.v_hint.set(STEP_HINTS[self.step])

        builder = getattr(self, "step_" + sid)
        builder()

        issues = issues_for(sid, self.model)
        if sid == "export":
            self.v_status.set("文件已准备好，可以保存或复制。")
            self.lbl_status.configure(style="Ok.TLabel")
        elif issues:
            self.v_status.set("请先修正：" + "；".join(issues[:3]) + (" …" if len(issues) > 3 else ""))
            self.lbl_status.configure(style="Err.TLabel")
        else:
            self.v_status.set("这一步没问题，可以进入下一步。")
            self.lbl_status.configure(style="Ok.TLabel")

        self.btn_next.configure(state=("disabled" if (issues or sid == "export") else "normal"),
                                text=("生成 MCT 文件" if self.step == len(STEP_IDS) - 2 else "确定，下一步"))
        self.btn_prev.configure(state=("disabled" if self.step == 0 else "normal"))
        self.refresh_step_list()
        self.canvas.yview_moveto(0)

    def refresh_step_list(self):
        self.step_list.delete(0, "end")
        for i, sid in enumerate(STEP_IDS):
            mark = "✓" if (i < self.step and not issues_for(sid, self.model)) else "　"
            self.step_list.insert("end", "%s %d. %s" % (mark, i + 1, STEP_TITLES[i]))
        self.step_list.itemconfigure(self.step, background="#dce8f7")
        self.step_list.selection_clear(0, "end")
        self.step_list.selection_set(self.step)

    def _on_pick_step(self, _event):
        sel = self.step_list.curselection()
        if not sel or sel[0] == self.step:
            return
        self.step = sel[0]
        self.render()

    # ---------------- 导航 ----------------
    def on_prev(self):
        if self.step > 0:
            self.step -= 1
            self.render()

    def on_next(self):
        issues = [x for x in issues_for(STEP_IDS[self.step], self.model) if not is_advisory(x)]
        if issues:
            messagebox.showwarning("还不能进入下一步", "\n".join(issues[:8]))
            return
        self.save_session()
        if self.step < len(STEP_IDS) - 1:
            self.step += 1
            self.render()

    def _lane_card(self, box):
        """车道荷载不利布载（静力等效，替代移动荷载）。"""
        c = self._card("车道不利布载（静力等效替代移动荷载）")
        r1 = ttk.Frame(c); r1.pack(fill="x")
        ttk.Label(r1, text="车道均布 qk").pack(side="left", padx=(0, 4))
        if not hasattr(self, "v_qk"):
            self.v_qk = tk.StringVar(value="10.5")
        ttk.Entry(r1, textvariable=self.v_qk, width=8).pack(side="left", padx=(0, 6))
        ttk.Label(r1, text="kN/m").pack(side="left", padx=(0, 16))
        ttk.Label(r1, text="车道集中 Pk").pack(side="left", padx=(0, 4))
        if not hasattr(self, "v_pk"):
            self.v_pk = tk.StringVar(value="352")
        ttk.Entry(r1, textvariable=self.v_pk, width=8).pack(side="left", padx=(0, 6))
        ttk.Label(r1, text="kN").pack(side="left", padx=(0, 16))
        ttk.Button(r1, text="生成 5 个不利布载工况", command=self.on_gen_lane).pack(side="left")
        ttk.Label(c, text="LL-1~LL-3：qk 满布 + Pk 分别压在各跨跨中；LL-4：Pk 压在中墩旁；"
                          "LL-5：仅 qk 满布。生成后可到第 7 步查看，导进 MIDAS 算完叠起来就是近似包络。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    def on_gen_lane(self):
        m = self.model
        if not is_num(self.v_qk.get()) or not is_num(self.v_pk.get()):
            messagebox.showwarning("参数不对", "qk 和 Pk 都要填数字。")
            return
        qk = float(self.v_qk.get()); pk = float(self.v_pk.get())
        sups = []
        for s in m["supports"]:
            v = txt(s.get("nodes")).strip()
            if is_num(v):
                sups.append(int(float(v)))
        sups = sorted(set(sups))
        if len(sups) < 2:
            messagebox.showwarning("缺支承", "先在「支承」那一步把支座节点定好（至少两个），才能分跨。")
            return
        xs = {}
        for k, nd in enumerate(m["nodes"], start=1):
            if is_num(nd.get("x")):
                xs[k] = float(nd["x"])
        if len(xs) < 2:
            messagebox.showwarning("缺支点", "先在第 1 步把支点填好。")
            return
        targets = []
        for a, b in zip(sups, sups[1:]):
            if a not in xs or b not in xs:
                continue
            xm = (xs[a] + xs[b]) / 2.0
            best = min(xs, key=lambda k: abs(xs[k] - xm))
            targets.append((best, "第 %d 跨跨中(x=%.1f)" % (len(targets) + 1, xs[best])))
        if not targets:
            messagebox.showwarning("分跨失败", "支承节点和支点对不上，检查一下支承填的节点号。")
            return
        inner = sups[1:-1]
        if inner:
            pier = inner[len(inner) // 2]
            adj = pier + 1 if (pier + 1) in xs else (pier - 1 if (pier - 1) in xs else pier)
            targets.append((adj, "中墩旁(x=%.1f)" % xs.get(adj, 0)))
        ne = len(m["elements"])
        if ne < 1:
            messagebox.showwarning("缺单元", "先在第 2 步把单元填好。")
            return
        # 名字保持纯 ASCII：*STLDCASE 写出去的就是这几个，荷载引用也得对得上
        names = ["LL-%d" % (i + 1) for i in range(len(targets) + 1)]
        keep_lc = [l for l in m["loadcases"] if txt(l.get("name")).strip() not in names]
        m["loadcases"] = keep_lc
        m["beamloads"] = [b for b in m["beamloads"] if txt(b.get("lc")).strip() not in names]
        m["nodalloads"] = [n for n in m["nodalloads"] if txt(n.get("lc")).strip() not in names]
        for i, (node, why) in enumerate(targets, start=1):
            nm = "LL-%d" % i
            m["loadcases"].append({"name": nm, "type": "L", "desc": "车道不利布载"})
            m["beamloads"].append({"lc": nm, "elems": "1to%d" % ne, "cmd": "BEAM", "type": "UNILOAD",
                                   "dir": "GZ", "proj": "NO", "d1": "0", "p1": num(-qk),
                                   "d2": "1", "p2": num(-qk), "d3": "0", "p3": "0", "d4": "0", "p4": "0",
                                   "group": ""})
            m["nodalloads"].append({"lc": nm, "node": str(node), "fx": "0", "fy": "0", "fz": num(-pk),
                                    "mx": "0", "my": "0", "mz": "0", "group": ""})
        nm = "LL-%d" % (len(targets) + 1)
        m["loadcases"].append({"name": nm, "type": "L", "desc": "车道均布仅满布"})
        m["beamloads"].append({"lc": nm, "elems": "1to%d" % ne, "cmd": "BEAM", "type": "UNILOAD",
                               "dir": "GZ", "proj": "NO", "d1": "0", "p1": num(-qk),
                               "d2": "1", "p2": num(-qk), "d3": "0", "p3": "0", "d4": "0", "p4": "0",
                               "group": ""})
        self.save_session()
        self.render()
        messagebox.showinfo("已生成", "生成了 %d 个工况：%s\n\nPk 位置：\n%s" %
                            (len(names), "、".join(names),
                             "\n".join("  %s  %s" % (n, w) for n, (_, w) in zip(names, targets))))
    def on_clear_step(self):
        sid = STEP_IDS[self.step]
        keys = STEP_CLEAR_KEYS.get(sid)
        if not keys:
            messagebox.showinfo("提示", "这一步是生成成果，没有可清空的数据。")
            return
        zh = {"nodes": "支点", "elements": "单元", "materials": "材料", "sections": "截面",
              "supports": "支承", "loadcases": "荷载工况与自重", "loads": "节点荷载和梁单元荷载"}
        if not messagebox.askyesno("清空本步", "确定清空「%s」这一步的输入吗？" % zh.get(sid, sid)):
            return
        for k in keys:
            self.model[k] = []
        if sid == "loadcases":
            self.model["selfweight"] = {"on": True, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
        for attr in ("v_span_start", "v_span_dx", "v_span_n"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.save_session()
        self.bundle_files = None
        self.render()
        self.v_status.set("已清空「%s」，可以重新填。" % zh.get(sid, sid))
        self.lbl_status.configure(style="Ok.TLabel")

    def on_reset(self):
        if messagebox.askyesno("清空全部", "确定清空所有步骤的输入吗？\n\n会变成空模型（0 个支点 / 单元 / 材料 / 截面），从头开始填。"):
            self.model = empty_model()
            self.v_project.set("")
            self.v_force.set(self.model["project"]["force"])
            self.v_dist.set(self.model["project"]["dist"])
            self.v_styp.set(dict(STYPES)[self.model["project"]["styp"]])
            self.step = 0
            self.saved_path = None
            self.bundle_files = None
            self.result_files = None
            for attr in ("v_fname", "v_outdir", "v_encoding", "v_compat", "v_altmct"):
                if hasattr(self, attr):
                    delattr(self, attr)
            self.save_session()
            self.render()

    # ---------------- 会话存取 ----------------
    def save_session(self):
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
            with open(SESSION_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.model, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def load_session(self):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "nodes" in data:
                self.model = ensure_model(data)
                self.v_project.set(txt(self.model["project"].get("name")))
                self.v_force.set(self.model["project"].get("force", "KN"))
                self.v_dist.set(self.model["project"].get("dist", "M"))
                self.v_styp.set(dict(STYPES).get(self.model["project"].get("styp", "1"), STYPES[0][1]))
        except (OSError, ValueError):
            pass

    # ==================================================================
    # 各步骤的界面
    # ==================================================================
    def _card(self, title=None):
        box = ttk.Labelframe(self.form, text=title or "", padding=(10, 8))
        box.pack(fill="x", expand=False, pady=(0, 10))
        return box

    def _table(self, box, headers):
        """表头和数据行共用同一个 grid，列才能对齐。返回该 Frame，行号从 1 开始。"""
        g = ttk.Frame(box); g.pack(fill="x")
        for c, htext in enumerate(headers):
            ttk.Label(g, text=htext, style="Title.TLabel").grid(row=0, column=c, sticky="w", padx=(0, 12), pady=(0, 4))
        return g

    @staticmethod
    def _cell(parent, widget, row, col, pad=(12, 0), pady=3):
        widget.grid(row=row, column=col, sticky="w", padx=pad, pady=pady)
        return widget

    # -------- 1. 支点 --------
    def step_nodes(self):
        box = self._card("支点列表")
        g = self._table(box, ["支点", "X (m)", "Y (m)", "Z (m)", "备注（可选）", ""])
        for i, nd in enumerate(self.model["nodes"]):
            r = i + 1
            ttk.Label(g, text="支点 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
            self._cell(g, ttk.Entry(g, textvariable=self.svar(nd, "x", nd.get("x")), width=10), r, 1)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(nd, "y", nd.get("y")), width=10), r, 2)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(nd, "z", nd.get("z")), width=10), r, 3)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(nd, "note", nd.get("note")), width=16), r, 4)
            self._cell(g, ttk.Button(g, text="删除", width=6,
                                     command=lambda idx=i: self._del_row("nodes", idx)), r, 5)

        btns = ttk.Frame(box); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加一个支点", command=lambda: self._add_row("nodes", {"x": "", "y": "0", "z": "0", "note": ""})).pack(side="left")
        ttk.Button(btns, text="按上一跨距续加", command=self._append_by_span).pack(side="left", padx=(8, 0))

        box2 = self._card("批量生成等间距支点（可选）")
        g2 = ttk.Frame(box2); g2.pack(fill="x")
        self.v_span_start = tk.StringVar(value="0")
        self.v_span_dx = tk.StringVar(value="10")
        self.v_span_n = tk.StringVar(value="5")
        self.v_span_replace = tk.BooleanVar(value=True)
        for c, (lab, var) in enumerate((("起点 X (m)", self.v_span_start), ("间距 (m)", self.v_span_dx), ("支点数量", self.v_span_n))):
            ttk.Label(g2, text=lab).grid(row=0, column=c, sticky="w", padx=(0, 12))
            ttk.Entry(g2, textvariable=var, width=10).grid(row=1, column=c, sticky="w", padx=(0, 12), pady=3)
        ttk.Checkbutton(g2, text="替换现有支点", variable=self.v_span_replace).grid(row=1, column=3, sticky="w", padx=(0, 12))
        ttk.Button(g2, text="生成", command=self._gen_spans).grid(row=1, column=4, sticky="w")
        ttk.Label(box2, text="生成的支点 Y=0、Z=0，生成后可逐个修改。", style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    def _gen_spans(self):
        try:
            x0 = float(self.v_span_start.get())
            dx = float(self.v_span_dx.get())
            n = max(1, min(200, int(float(self.v_span_n.get()))))
        except ValueError:
            messagebox.showwarning("输入有误", "起点、间距、数量都要填数字。")
            return
        rows = [{"x": num(x0 + i * dx), "y": "0", "z": "0", "note": ""} for i in range(n)]
        if self.v_span_replace.get():
            self.model["nodes"] = rows
        else:
            self.model["nodes"].extend(rows)
        self.render()

    def _append_by_span(self):
        nodes = self.model["nodes"]
        last = nodes[-1] if nodes else {"x": "0", "y": "0", "z": "0"}
        try:
            dx = float(nodes[1]["x"]) - float(nodes[0]["x"]) if len(nodes) > 1 else 10.0
        except (ValueError, KeyError):
            dx = 10.0
        try:
            x = float(last.get("x", 0)) + dx
        except ValueError:
            x = dx
        nodes.append({"x": num(x), "y": last.get("y", "0"), "z": last.get("z", "0"), "note": ""})
        self.render()

    # -------- 2. 单元 --------
    def step_elements(self):
        box = self._card("单元列表")
        g = self._table(box, ["单元", "类型", "i 节点", "j 节点", "材料号", "截面号", "β角°（可选）", ""])
        for i, el in enumerate(self.model["elements"]):
            r = i + 1
            ttk.Label(g, text="单元 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
            self._cell(g, ttk.Combobox(g, textvariable=self.svar(el, "type", el.get("type")),
                                       values=ELEM_TYPES, width=8, state="readonly"), r, 1)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(el, "i", el.get("i")), width=7), r, 2)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(el, "j", el.get("j")), width=7), r, 3)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(el, "mat", el.get("mat")), width=7), r, 4)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(el, "sect", el.get("sect")), width=7), r, 5)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(el, "angle", el.get("angle")), width=7), r, 6)
            self._cell(g, ttk.Button(g, text="删除", width=6,
                                     command=lambda idx=i: self._del_row("elements", idx)), r, 7)

        btns = ttk.Frame(box); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加一个单元",
                   command=lambda: self._add_row("elements", {"type": "BEAM", "i": "", "j": "", "mat": "1", "sect": "1", "angle": "0"})).pack(side="left")
        ttk.Button(btns, text="依次连接相邻支点", command=self._auto_elements).pack(side="left", padx=(8, 0))

    def _auto_elements(self):
        n = len(self.model["nodes"])
        if n < 2:
            messagebox.showwarning("支点不足", "至少要有 2 个支点才能连接单元。")
            return
        self.model["elements"] = [{"type": "BEAM", "i": str(i), "j": str(i + 1), "mat": "1", "sect": "1", "angle": "0"}
                                  for i in range(1, n)]
        self.render()

    # -------- 3. 材料 --------
    def step_materials(self):
        box = self._card("材料列表")
        grid = ttk.Frame(box); grid.pack(fill="x")
        heads = ["材料", "名称", "类别", "取值方式", "规格 / 规范", ""]
        for c, htext in enumerate(heads):
            ttk.Label(grid, text=htext, style="Title.TLabel").grid(row=0, column=c, sticky="w", padx=(0, 12), pady=(0, 4))

        for i, mat in enumerate(self.model["materials"]):
            r = i * 2 + 1
            mode = mat.get("mode")
            if mode not in ("spec", "db", "user"):
                mode = "spec" if mat.get("spec") in MATERIAL_SPECS else "user"
                mat["mode"] = mode
            mtype = mat.get("type") if mat.get("type") in ("CONC", "STEEL", "USER") else "CONC"
            mat["type"] = mtype

            ttk.Label(grid, text="材料 %d" % (i + 1)).grid(row=r, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(grid, textvariable=self.svar(mat, "name", mat.get("name")), width=12).grid(
                row=r, column=1, sticky="w", padx=(0, 12), pady=(6, 0))
            tb = ttk.Combobox(grid, textvariable=self.svar(mat, "type", MAT_TYPE_NAMES.get(mtype, "混凝土")),
                              values=[t[1] for t in MAT_TYPES], width=10, state="readonly")
            tb.grid(row=r, column=2, sticky="w", padx=(0, 12), pady=(6, 0))
            tb.bind("<<ComboboxSelected>>", lambda e, idx=i: self._apply_mat_type(idx))
            mb = ttk.Combobox(grid, textvariable=self.svar(mat, "mode", MAT_MODE_NAMES.get(mode, "规格自动填数")),
                              values=[m[1] for m in MAT_MODES], width=13, state="readonly")
            mb.grid(row=r, column=3, sticky="w", padx=(0, 12), pady=(6, 0))
            mb.bind("<<ComboboxSelected>>", lambda e, idx=i: self._apply_mode(idx))

            if mode == "spec":
                specs = self._spec_list(mtype)
                if mat.get("spec") not in specs:
                    mat["spec"] = specs[0]
                    self._fill_spec(mat)
                cbox = ttk.Combobox(grid, textvariable=self.svar(mat, "spec", mat.get("spec")),
                                    values=specs, width=12, state="readonly")
                cbox.grid(row=r, column=4, sticky="w", padx=(0, 12), pady=(6, 0))
                cbox.bind("<<ComboboxSelected>>", lambda e, idx=i: self._apply_spec(idx))
            elif mode == "db":
                cbox = ttk.Combobox(grid, textvariable=self.svar(mat, "standard", mat.get("standard") or MAT_STANDARDS[0]),
                                    values=MAT_STANDARDS, width=18, state="readonly")
                cbox.grid(row=r, column=4, sticky="w", padx=(0, 12), pady=(6, 0))
                ttk.Label(grid, text="→ MIDAS 从规范数据库取值", style="Hint.TLabel").grid(row=r, column=5, sticky="w", pady=(6, 0))
            else:
                ttk.Label(grid, text="自己填数据", style="Hint.TLabel").grid(row=r, column=4, sticky="w", padx=(0, 12), pady=(6, 0))

            ttk.Button(grid, text="删除", width=6, command=lambda idx=i: self._del_row("materials", idx)).grid(
                row=r, column=5 if mode != "db" else 6, sticky="w", pady=(6, 0))

            d = ttk.Frame(grid); d.grid(row=r + 1, column=1, columnspan=6, sticky="w", pady=(2, 10))
            drow = ttk.Frame(d); drow.pack(anchor="w")          # 数值行与说明行分开，说明再长也不会把表格撑宽
            if mode == "db":
                ttk.Label(drow, text="数据库中的材料名").pack(side="left")
                ttk.Entry(drow, textvariable=self.svar(mat, "dbname", mat.get("dbname")), width=12).pack(side="left", padx=(4, 14))
                ttk.Label(d, text="例：规范 JTG3362-18(RC) + 名称 C50；数据由 MIDAS 按规范取。",
                          style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
            else:
                ro = "readonly" if mode == "spec" else "normal"
                for key, label, width in (("elast", "弹性模量 E", 14), ("poisn", "泊松比 ν", 7),
                                          ("den", "容重", 7), ("thermal", "线膨胀系数", 11)):
                    ttk.Label(drow, text=label).pack(side="left", padx=(0, 4))
                    e = ttk.Entry(drow, textvariable=self.svar(mat, key, mat.get(key)), width=width)
                    e.pack(side="left", padx=(0, 14))
                    if ro == "readonly":
                        e.configure(state="readonly")
                ttk.Label(d, text=("选规格自动带出，不用改。" if mode == "spec" else
                                   "kN、m 单位下 C50：E=3.45e7，ν=0.2，容重=25，线膨胀系数=1e-5。"),
                          style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")

        btns = ttk.Frame(box); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加一种材料", command=self._add_material).pack(side="left")
        ttk.Label(btns, text="  混凝土 C30~C60（JTG 3362-18 弹模）／钢材 Q235~Q460；也可改用规范数据库或自定义数值。",
                  style="Hint.TLabel").pack(side="left")

    def _spec_list(self, mat_type):
        if mat_type == "STEEL":
            return STEEL_SPECS
        return CONC_SPECS

    def _apply_mode(self, idx):
        """「取值方式」下拉：把显示文本换回代码；切到自定义时顺手把名称从规格名改掉。"""
        mat = self.model["materials"][idx]
        code = MAT_MODE_CODES.get(txt(mat.get("mode")), "spec")
        mat["mode"] = code
        if code == "spec":
            specs = self._spec_list(mat.get("type"))
            if mat.get("spec") not in specs:
                mat["spec"] = specs[0]
            self._fill_spec(mat)
        elif code == "user":
            if txt(mat.get("name")).strip() in MATERIAL_SPECS:
                mat["name"] = "自定义材料"
        self.render()

    def _apply_mat_type(self, idx):
        mat = self.model["materials"][idx]
        mat["type"] = MAT_TYPE_CODES.get(txt(mat.get("type")), "CONC")
        if mat.get("mode", "spec") == "spec":
            specs = self._spec_list(mat["type"])
            if mat.get("spec") not in specs:
                mat["spec"] = specs[0]
                self._fill_spec(mat)
        self.render()

    def _apply_spec(self, idx):
        """下拉框选完规格后刷新（下拉框写回的是显示文本，这里统一按代码重新匹配）。"""
        mat = self.model["materials"][idx]
        raw = txt(mat.get("spec"))
        code = raw.split("（")[0].strip()
        mat["spec"] = code if code in MATERIAL_SPECS else self._spec_list(mat.get("type"))[0]
        self._fill_spec(mat)
        self.render()

    @staticmethod
    def _fill_spec(mat):
        """按规格带出数据；不改类别，也不动自定义模式，免得选项被自己弄没了。"""
        spec = mat.get("spec")
        if spec in MATERIAL_SPECS and spec != "自定义":
            kind, e, nu, den, alpha = MATERIAL_SPECS[spec]
            if kind in ("CONC", "STEEL"):
                mat["type"] = kind
            if e:
                mat["elast"], mat["poisn"], mat["den"], mat["thermal"] = e, nu, den, alpha
            mat["name"] = spec
            mat["mode"] = "spec"

    def _add_material(self):
        self.model["materials"].append({
            "name": "C50", "type": "CONC", "mode": "spec", "spec": "C50",
            "standard": "JTG3362-18(RC)", "dbname": "C50",
            "elast": "3.45e7", "poisn": "0.2", "den": "25", "thermal": "0.00001"})
        self.render()

    # -------- 4. 截面 --------
    def step_sections(self):
        box = self._card("截面列表")
        choices = ["%s · %s" % (s[0], s[1]) for s in SHAPES] + [CAD_SHAPE + " · 自定义（导入 CAD 文件）"]
        g = self._table(box, ["截面", "名称", "形状", "尺寸（单位 m）", "偏心 / 剪切 / 翘曲", ""])
        for i, sec in enumerate(self.model["sections"]):
            r = i * 4 + 1
            shape_code = self._shape_code(sec)
            ttk.Label(g, text="截面 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
            self._cell(g, ttk.Entry(g, textvariable=self.svar(sec, "name", sec.get("name")), width=12), r, 1, pady=(8, 0))
            display = ("%s · %s" % (shape_code, SHAPE_BY_CODE[shape_code][1])) if shape_code in SHAPE_BY_CODE \
                else ((TS_SHAPE + " · 变截面") if shape_code == TS_SHAPE else (CAD_SHAPE + " · 自定义截面"))
            cb = ttk.Combobox(g, textvariable=self.svar(sec, "shape", display), values=choices,
                              width=18, state="readonly")
            self._cell(g, cb, r, 2, pady=(8, 0))
            cb.bind("<<ComboboxSelected>>", lambda e, idx=i: self._switch_shape(idx))
            self._cell(g, ttk.Button(g, text="删除", width=6,
                                     command=lambda idx=i: self._del_row("sections", idx)), r, 5, pady=(8, 0))

            if shape_code == TS_SHAPE:
                self._ts_section_ui(g, sec, i, r)
                continue
            if shape_code == CAD_SHAPE:
                self._cad_section_ui(g, sec, i, r)
                continue

            shape = SHAPE_BY_CODE.get(shape_code, SHAPES[0])
            dims = sec.setdefault("dims", [])
            while len(dims) < len(shape[2]):
                dims.append("")
            dcell = ttk.Frame(g); dcell.grid(row=r + 1, column=3, columnspan=3, sticky="w", pady=(2, 0))
            for k, d in enumerate(shape[2]):
                _rr, _cc = divmod(k, 4)          # 每行最多 4 个尺寸，H/槽钢那种 8 个尺寸的不会把表格撑爆
                _f = ttk.Frame(dcell); _f.grid(row=_rr, column=_cc, sticky="w", padx=(0, 16), pady=(0, 2))
                ttk.Label(_f, text="%s · %s" % (d[1], d[0])).pack(side="left", padx=(0, 4))
                ttk.Entry(_f, textvariable=self.dim_var(sec, k, dims[k]), width=8).pack(side="left")

            ocell = ttk.Frame(g); ocell.grid(row=r + 2, column=1, columnspan=4, sticky="w", pady=(2, 10))
            ttk.Label(ocell, text="偏心点").pack(side="left", padx=(0, 4))
            ttk.Combobox(ocell, textvariable=self.svar(sec, "offset", sec.get("offset")), values=OFFSETS,
                         width=6, state="readonly").pack(side="left", padx=(0, 14))
            ttk.Label(ocell, text="剪切变形").pack(side="left", padx=(0, 4))
            ttk.Combobox(ocell, textvariable=self.svar(sec, "shear", sec.get("shear")), values=["YES", "NO"],
                         width=6, state="readonly").pack(side="left", padx=(0, 14))
            ttk.Label(ocell, text="翘曲效应（可选）").pack(side="left", padx=(0, 4))
            ttk.Combobox(ocell, textvariable=self.svar(sec, "warp", sec.get("warp")), values=["NO", "YES"],
                         width=6, state="readonly").pack(side="left")

        btns = ttk.Frame(box); btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="添加一个截面", command=lambda: self._add_row("sections", {
            "name": "", "shape": "SB", "dims": ["1.5", "1.0"], "offset": "CT", "shear": "YES",
            "warp": "NO", "file": "", "unit": "0.001", "props": None})).pack(side="left")
        ttk.Button(btns, text="添加自定义截面（CAD 文件）", command=lambda: self._add_row("sections", {
            "name": "自定义截面", "shape": CAD_SHAPE, "dims": [], "offset": "CT", "shear": "YES",
            "warp": "NO", "file": "", "unit": "0.001", "props": None})).pack(side="left", padx=(8, 0))

        box2 = self._card("截面分段分配（可选）：把某段单元指定给某个截面")
        if "sec_assign" not in self.model:
            self.model["sec_assign"] = []
        rows = self.model["sec_assign"]
        if not rows:
            ttk.Label(box2, text="还没分配。点下面「添加分段」，一行填一段。例如：起始 1、终止 2、截面 1；"
                                 "起始 3、终止 8、截面 3。",
                      style="Hint.TLabel").pack(anchor="w")
        g2 = self._table(box2, ["分段", "起始单元", "终止单元", "截面号", ""])
        for i, a in enumerate(rows):
            r = i + 1
            ttk.Label(g2, text="分段 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
            for c, key in ((1, "from"), (2, "to"), (3, "sect")):
                self._cell(g2, ttk.Entry(g2, textvariable=self.svar(a, key, a.get(key)), width=10), r, c)
            self._cell(g2, ttk.Button(g2, text="删除", width=6,
                                      command=lambda idx=i: self._del_assign(idx)), r, 4)
        b2 = ttk.Frame(box2); b2.pack(fill="x", pady=(8, 0))
        ttk.Button(b2, text="添加分段", command=self._add_assign).pack(side="left")
        ttk.Button(b2, text="清空分段", command=self._clear_assign).pack(side="left", padx=(8, 0))
        ttk.Button(b2, text="按截面自动铺满", command=self._auto_assign).pack(side="left", padx=(8, 0))
        ttk.Label(b2, text="  「按截面自动铺满」= 把单元平均分给各截面；变截面填它自己的编号即可。",
                  style="Hint.TLabel").pack(side="left")

    def _add_assign(self):
        rows = self.model.setdefault("sec_assign", [])
        n = len(self.model["elements"]) or 1
        last = int(float(rows[-1].get("to") or 0)) if rows else 0
        rows.append({"from": str(last + 1), "to": str(min(last + 1, n)), "sect": "1"})
        self.render()

    def _del_assign(self, idx):
        self.model.setdefault("sec_assign", []).pop(idx)
        self.render()

    def _clear_assign(self):
        self.model["sec_assign"] = []
        self.render()

    def _auto_assign(self):
        ns = len(self.model["sections"]); ne = len(self.model["elements"])
        if ns < 1 or ne < 1:
            messagebox.showinfo("提示", "先要有截面和单元。")
            return
        rows, start, per = [], 1, max(1, ne // ns)
        for k in range(ns):
            end = ne if k == ns - 1 else min(ne, start + per - 1)
            rows.append({"from": str(start), "to": str(end), "sect": str(k + 1)})
            start = end + 1
            if start > ne:
                break
        self.model["sec_assign"] = rows
        self.render()

    @staticmethod
    def _shape_code(sec):
        return shape_code_of(sec)

    def _ts_section_ui(self, grid, sec, idx, r):
        """变截面：只填引用哪两个截面，尺寸自动取那两个截面的。"""
        fr = ttk.Frame(grid)
        fr.grid(row=r + 1, column=1, columnspan=6, sticky="w", pady=(2, 0))
        ttk.Label(fr, text="i 端截面号").pack(side="left", padx=(0, 4))
        ttk.Entry(fr, textvariable=self.svar(sec, "i_sect", sec.get("i_sect")), width=6).pack(side="left", padx=(0, 16))
        ttk.Label(fr, text="j 端截面号").pack(side="left", padx=(0, 4))
        ttk.Entry(fr, textvariable=self.svar(sec, "j_sect", sec.get("j_sect")), width=6).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(fr, text="镜像（i/j 端对调 → 左大右小）",
                        variable=self.bvar(sec, "mirror", sec.get("mirror"))).pack(side="left", padx=(0, 16))
        ttk.Label(fr, text="对齐").pack(side="left", padx=(0, 4))
        if not txt(sec.get("offset")).strip():
            sec["offset"] = "CT"
        ttk.Combobox(fr, textvariable=self.svar(sec, "offset", sec.get("offset")),
                     values=OFFSETS, width=5, state="readonly").pack(side="left")
        info = ttk.Frame(grid)
        info.grid(row=r + 2, column=1, columnspan=6, sticky="w", pady=(2, 10))
        lines = []
        for k, other in enumerate(self.model.get("sections", []), start=1):
            if other is sec:
                continue
            c = shape_code_of(other)
            if c in SHAPE_BY_CODE:
                sh = SHAPE_BY_CODE[c]
                dims = other.get("dims", [])
                ds = "、".join("%s=%s" % (sh[2][j][0], dims[j] if j < len(dims) else "0")
                               for j in range(len(sh[2])))
                lines.append("%d:%s(%s)" % (k, txt(other.get("name")), ds))
        ttk.Label(info, text="可引用：" + ("　".join(lines) if lines else "（还没有等截面，先建两个）"),
                  style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
        ttk.Label(info, text="变截面沿单元 i→j 线性渐变：i 端尺寸 → j 端尺寸。"
                             "左小右大就按 i=小、j=大；左大右小就勾「镜像」。"
                             "对齐建议 CT（顶部中点），桥面才平直。",
                  style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
    def _cad_section_ui(self, grid, sec, idx, r):
        fr = ttk.Frame(grid); fr.grid(row=r + 1, column=1, columnspan=4, sticky="w", pady=(2, 0))
        ttk.Label(fr, text="CAD 文件（DXF）").pack(side="left", padx=(0, 6))
        ttk.Entry(fr, textvariable=self.svar(sec, "file", sec.get("file")), width=38).pack(side="left", padx=(0, 6))
        ttk.Button(fr, text="选择文件…", command=lambda: self._pick_cad_file(idx)).pack(side="left")
        fr2 = ttk.Frame(grid); fr2.grid(row=r + 2, column=1, columnspan=4, sticky="w", pady=(2, 10))
        ttk.Label(fr2, text="图纸单位").pack(side="left", padx=(0, 4))
        _name_of = {u[1]: u[0] for u in UNIT_SCALES}
        _code_of = {u[0]: u[1] for u in UNIT_SCALES}
        vunit = tk.StringVar(value=_name_of.get(txt(sec.get("unit")).strip() or "0.001", UNIT_SCALES[0][0]))

        def _apply_unit(*_a, _s=sec, _v=vunit):
            _s["unit"] = _code_of.get(_v.get(), "0.001")
            self.save_session()
            if txt(_s.get("file")).strip():
                # 换了单位立刻按新单位重算——不然界面上的面积还是老单位算出来的，
                # 很容易看着"改了没用"，把毫米的截面当成米的用了
                self.after_idle(lambda: self._analyze_cad(idx, quiet=True))

        vunit.trace_add("write", _apply_unit)
        ttk.Combobox(fr2, textvariable=vunit, values=[u[0] for u in UNIT_SCALES], width=10,
                     state="readonly").pack(side="left", padx=(0, 10))
        ttk.Button(fr2, text="重新计算", command=lambda: self._analyze_cad(idx)).pack(side="left", padx=(0, 14))
        ttk.Label(fr2, text="偏心点（可选）").pack(side="left", padx=(0, 4))
        ttk.Combobox(fr2, textvariable=self.svar(sec, "offset", sec.get("offset")), values=OFFSETS,
                     width=6, state="readonly").pack(side="left")

        pr = sec.get("props")
        info = ttk.Frame(grid); info.grid(row=r + 3, column=1, columnspan=5, sticky="w", pady=(0, 10))
        if pr:
            for line in (
                "面积 A = %.6g m²      形心 y = %.6g m, z = %.6g m" % (pr["A"], pr["Cy"], pr["Cz"]),
                "Iyy = %.6g m⁴      Izz = %.6g m⁴      Ixx(扭转，近似) = %.6g m⁴" % (pr["Iyy"], pr["Izz"], pr["Ixx"]),
                "外轮廓周长 = %.6g m   内周长 = %.6g m   轮廓 %d 个 / 孔洞 %d 个"
                % (pr["PERI_OUT"], pr["PERI_IN"], pr["N_LOOP"], pr["N_HOLE"]),
                "上/下缘到形心 = %.4g / %.4g m      左/右缘到形心 = %.4g / %.4g m"
                % (pr["CzP"], pr["CzM"], pr["CyP"], pr["CyM"]),
            ):
                ttk.Label(info, text=line, style="Ok.TLabel").pack(anchor="w")
            if not (0.005 <= pr["A"] <= 50):
                ttk.Label(info, text="⚠ 面积只有 %.6g m²，跟梁截面差得太远 —— %s" % (pr["A"], unit_advice(sec)),
                          style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
            if pr.get("N_LOOP", 0) - pr.get("N_HOLE", 0) > 1:
                ttk.Label(info, text="⚠ 图里有 %d 个互不相连的外轮廓。MIDAS 的一般截面只认 1 个外轮廓，"
                                     "导入时会退回成「数值截面」（刚度照样是对的，但 MIDAS 里看不到图形）；"
                                     "想要真轮廓请把每个外轮廓分成单独的 DXF。"
                                     % (pr["N_LOOP"] - pr["N_HOLE"]),
                          style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
            _off = pr.get("DRAW_OFFSET") or [0.0, 0.0]
            _size = max(pr["CyP"] + pr["CyM"], pr["CzP"] + pr["CzM"], 1e-9)
            if max(abs(_off[0]), abs(_off[1])) > 5 * _size:
                ttk.Label(info, text="⚠ 轮廓画在离图纸原点很远的位置（y≈%.6g, z≈%.6g，截面本身才 %.3g 大）。"
                                     "已经自动按形心归零后写进 MCT，不影响结果、也不用改图纸。"
                                     % (_off[0], _off[1], _size),
                          style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w")
            ttk.Label(info, text="导入 MIDAS 后会建成「一般截面」（PSC 数值，*SECT-PSCVALUE）："
                                 "轮廓是上面这些真实顶点，MIDAS 里能看到形状；"
                                 "上面这 6 个截面特性由本程序按轮廓算出并原样带进去。"
                                 "扭转常数是圣维南近似值，扭转敏感的分析请以 MIDAS 自己算的为准。",
                      style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w", pady=(4, 0))
        else:
            ttk.Label(info, text="还没读入轮廓。选一个 DXF 文件（截面画在 CAD 的 XY 平面上，闭合多段线/圆/直线环都可以）。",
                      style="Hint.TLabel").pack(anchor="w")

    def _pick_cad_file(self, idx):
        sec = self.model["sections"][idx]
        path = filedialog.askopenfilename(
            title="选择截面 DXF 文件（DWG 请先在 CAD 里另存为 DXF）",
            filetypes=[("DXF 图纸（只认这个）", "*.dxf"), ("所有文件", "*.*")],
            initialdir=os.path.dirname(sec.get("file") or "") or APP_DIR)
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            messagebox.showwarning(
                "需要 DXF 文件（不能是 DWG）",
                "你选的是 %s 格式，程序只能读 DXF。\n\n"
                "在 CAD 里的转法：\n"
                "  1) 菜单「文件 → 另存为」，文件类型选 DXF（AutoCAD 2000/LT2000 DXF 或 2004 DXF 都行）\n"
                "  2) 或者命令行输入 DXFOUT 回车，再选保存位置\n\n"
                "转成 .dxf 之后再回到这里选它。" % os.path.splitext(path)[1].upper())
            return
        sec["file"] = path
        if not txt(sec.get("name")).strip() or sec.get("name") == "自定义截面":
            sec["name"] = os.path.splitext(os.path.basename(path))[0][:30] or "自定义截面"
        self._analyze_cad(idx, quiet=True)
        self.render()

    def _analyze_cad(self, idx, quiet=False):
        if not (0 <= idx < len(self.model["sections"])):
            return                      # 换单位是异步重算的，这期间用户可能已经把这一行删了
        sec = self.model["sections"][idx]
        path = txt(sec.get("file")).strip()
        if not path or not os.path.exists(path):
            sec["props"] = None
            if not quiet:
                messagebox.showwarning("没有文件", "请先选择 DXF 文件。")
            return
        try:
            scale = float(sec.get("unit") or "0.001")
        except ValueError:
            scale = 0.001
        loops, notes = read_dxf_loops(path, scale)
        pr = compute_section_props(loops)
        if not pr:
            sec["props"] = None
            msg = "没能从文件里取到闭合轮廓。\n\n" + ("\n".join(notes) if notes else
                                                 "请确认：截面画在 XY 平面上，轮廓是闭合的（多段线闭合，或直线首尾相接）。")
            if not quiet:
                messagebox.showwarning("解析失败", msg)
            else:
                self.last_cad_note = msg
            return
        sec["props"] = pr
        self.last_cad_note = "；".join(notes) if notes else ""
        if not quiet:
            messagebox.showinfo("解析成功", "面积 A = %.6g m²\nIyy = %.6g m⁴\nIzz = %.6g m⁴\nIxx(近似) = %.6g m⁴\n%s"
                                % (pr["A"], pr["Iyy"], pr["Izz"], pr["Ixx"],
                                   ("\n" + self.last_cad_note) if self.last_cad_note else ""))
        self.render()

    def _switch_shape(self, idx):
        sec = self.model["sections"][idx]
        code = self._shape_code(sec)
        sec["shape"] = code
        if code != CAD_SHAPE:
            shape = SHAPE_BY_CODE[code]
            dims = sec.get("dims") or []
            while len(dims) < len(shape[2]):
                dims.append("")
            sec["dims"] = dims
        self.render()

    # -------- 5. 支承 --------
    def step_supports(self):
        box = self._card("支承列表")
        g = self._table(box, ["支承", "节点号", "Dx  Dy  Dz  Rx  Ry  Rz（勾选要约束的自由度）", "边界组（可选）", ""])
        for i, sp in enumerate(self.model["supports"]):
            r = i + 1
            ttk.Label(g, text="支承 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
            self._cell(g, ttk.Entry(g, textvariable=self.svar(sp, "nodes", sp.get("nodes")), width=12), r, 1)
            dofs = ttk.Frame(g); dofs.grid(row=r, column=2, sticky="w", padx=(0, 12), pady=3)
            for k, label in (("dx", "Dx"), ("dy", "Dy"), ("dz", "Dz"), ("rx", "Rx"), ("ry", "Ry"), ("rz", "Rz")):
                ttk.Checkbutton(dofs, text=label, variable=self.bvar(sp, k, sp.get(k))).pack(side="left", padx=(0, 4))
            self._cell(g, ttk.Entry(g, textvariable=self.svar(sp, "group", sp.get("group")), width=12), r, 3)
            self._cell(g, ttk.Button(g, text="删除", width=6,
                                     command=lambda idx=i: self._del_row("supports", idx)), r, 4)

        btns = ttk.Frame(box); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加一个支承", command=lambda: self._add_row("supports", {
            "nodes": "", "dx": True, "dy": True, "dz": True, "rx": False, "ry": False, "rz": False, "group": ""})).pack(side="left")
        ttk.Button(btns, text="首尾节点设为铰支座", command=self._auto_supports).pack(side="left", padx=(8, 0))

    def _auto_supports(self):
        n = len(self.model["nodes"])
        if n < 2:
            messagebox.showwarning("支点不足", "至少要有 2 个支点。")
            return
        self.model["supports"] = [
            {"nodes": "1", "dx": True, "dy": True, "dz": True, "rx": False, "ry": False, "rz": False, "group": ""},
            {"nodes": str(n), "dx": True, "dy": True, "dz": True, "rx": False, "ry": False, "rz": False, "group": ""},
        ]
        self.render()

    # -------- 6. 荷载工况与自重 --------
    def step_loadcases(self):
        box = self._card("荷载工况")
        g = self._table(box, ["工况", "名称", "类型", "说明（可选）", ""])
        self._lc_seen_names = {}
        self._lc_name_vars = {}
        for i, lc in enumerate(self.model["loadcases"]):
            r = i + 1
            ttk.Label(g, text="工况 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
            _nv = self.svar(lc, "name", lc.get("name"))
            self._lc_seen_names[i] = txt(lc.get("name"))
            self._lc_name_vars[i] = _nv
            _nv.trace_add("write", lambda *_a, _i=i: self._on_case_renamed(_i))
            self._cell(g, ttk.Entry(g, textvariable=_nv, width=14), r, 1)
            self._cell(g, ttk.Combobox(g, textvariable=self.svar(lc, "type", lc.get("type")),
                                       values=LC_TYPES, width=8, state="readonly"), r, 2)
            self._cell(g, ttk.Entry(g, textvariable=self.svar(lc, "desc", lc.get("desc")), width=26), r, 3)
            self._cell(g, ttk.Button(g, text="删除", width=6,
                                     command=lambda idx=i: self._del_row("loadcases", idx)), r, 4)
        btns = ttk.Frame(box); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="添加一个工况", command=lambda: self._add_row("loadcases", {"name": "", "type": "D", "desc": ""})).pack(side="left")
        ttk.Label(btns, text="  类型常用：D 恒载、L 活载、W 风、E 地震、T 温度、CR 徐变、SH 收缩、PS 预应力。",
                  style="Hint.TLabel").pack(side="left")

        box2 = self._card("自重")
        sw = self.model["selfweight"]
        if sw.get("on") and not txt(sw.get("lc")).strip():
            _swlcs = [txt(l.get("name")).strip() for l in self.model["loadcases"] if txt(l.get("name")).strip()]
            if _swlcs:
                sw["lc"] = _swlcs[0]
        g2 = ttk.Frame(box2); g2.pack(fill="x")
        ttk.Checkbutton(g2, text="计入结构自重", variable=self.bvar(sw, "on", sw.get("on"))).grid(
            row=0, column=0, sticky="w", padx=(0, 16))
        for c, (lab, key) in enumerate((("所属工况", "lc"), ("X 系数（可选）", "x"), ("Y 系数（可选）", "y"),
                                        ("Z 系数", "z"), ("荷载组（可选）", "group")), start=1):
            ttk.Label(g2, text=lab).grid(row=0, column=c, sticky="w", padx=(0, 6))
            if key == "lc":
                self._sw_lc_var = self.svar(sw, key, sw.get(key))
                self._sw_combo = ttk.Combobox(g2, textvariable=self._sw_lc_var,
                                              values=[txt(l.get("name")) for l in self.model["loadcases"]],
                                              width=12)
                self._sw_combo.grid(row=0, column=c + 1, sticky="w", padx=(0, 16))
            else:
                ttk.Entry(g2, textvariable=self.svar(sw, key, sw.get(key)), width=8).grid(
                    row=0, column=c + 1, sticky="w", padx=(0, 16))
        ttk.Label(box2, text="Z 取 -1 表示自重向下。所属工况可以直接打字，也可以从下拉里选；"
                             "改工况名时这里会自动跟着改。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    # -------- 7. 荷载 --------
        if hasattr(self, "_lane_card"):
            self._lane_card(box)

    def _toggle_loads_help(self):
        """第 7 步「说明」的展开/收起（状态记在实例上，切走再回来还是原样）。"""
        self._loads_help_open = not getattr(self, "_loads_help_open", False)
        self.render()

    def step_loads(self):
        names = [txt(l.get("name")) for l in self.model["loadcases"]]

        # 「说明」默认收起——页面保持清爽，要查字段含义时点开
        _open = getattr(self, "_loads_help_open", False)
        tips = self._card("说明")
        head = ttk.Frame(tips); head.pack(fill="x")
        ttk.Button(head, text=("▼  怎么填这一步（点一下收起）" if _open else "▶  怎么填这一步（点一下展开）"),
                   command=self._toggle_loads_help).pack(side="left")
        ttk.Label(head, text="   不展开也能正常填；展开是给你查字段含义用的。",
                  style="Hint.TLabel").pack(side="left")
        if _open:
            _help = (
                ("「自重」不用在这里加",
                 "第 6 步的「自重」卡片已经把这条荷载给出去了（计入结构自重 + X/Y/Z 方向系数），"
                 "MIDAS 自己按 容重×截面面积×单元长度 算。这一步再加一遍就等于算两遍。"),
                ("工况",
                 "每条荷载都必须选一个工况。第 6 步工况表里的名字只是装荷载的「容器」，本身不带数值；"
                 "二期、车辆、人群这些的荷载值都在这一步加。\n"
                 "某个工况一条荷载都不加，MIDAS 不会报错，只是那个工况算出来全是 0（容易漏看，别忘）。"),
                ("单元号",
                 "一次选多个单元：用空格分开写，例如  1 2 3 4  （推荐，MIDAS 自己的导出也是这么写的）。\n"
                 "程序也认简写 1to4 和逗号 1,2,3,4；范围不能超出单元总数。"),
                ("类型",
                 "UNILOAD 均布力 ／ CONLOAD 集中力 ／ UNIMOMENT 均布弯矩 ／ CONMOMENT 集中弯矩。\n"
                 "沿梁长的分布荷载（二期恒载、人群、车道均布）用 UNILOAD；单个位置作用用 CONLOAD。"),
                ("方向（GZ / LZ …）",
                 "G = Global 整体坐标系，L = Local 单元局部坐标系；后面的 X/Y/Z 是沿哪根轴。\n"
                 "GZ = 沿整体 Z 轴（本模型 Z 向上，所以向下就填负值）。水平直梁时 GZ 和 LZ 等价；"
                 "梁有坡度或设了 Beta 角时才会不一样。方向只管「沿哪根轴」，向上向下由 P 的正负号决定。"),
                ("D1 / P1 / D2 / P2",
                 "D = 位置，按单元长度取比例：0 = i 端（起点）、1 = j 端（终点）、0.5 = 中点。\n"
                 "P = 该位置处的荷载强度（均布是 kN/m；集中力时是 kN）。\n"
                 "D3/P3/D4/P4 是第二段，一段荷载要拆成两段描述时才用，平时留 0。"),
                ("常见写法",
                 "全长均布：   D1=0    P1=-31.58    D2=1    P2=-31.58\n"
                 "三角形分布： D1=0    P1=0         D2=1    P2=-31.58\n"
                 "只压中间半跨：D1=0.25 P1=-31.58   D2=0.75 P2=-31.58\n"
                 "跨中集中力： 类型选 CONLOAD，D1=0.5，P1=-352"),
                ("单位",
                 "跟窗口顶部的单位制一致：力 kN、长度 m → 梁单元荷载填 kN/m，节点荷载填 kN、kN·m。"),
                ("荷载值不能全是 0",
                 "加了一行就必须填真实数值；六个分量（或 P1~P4）全是 0 的话，MIDAS 导入时会报"
                 "「荷载值输入有错误」。数值还没定就先别加这一行。"),
                ("车道不利布载（LL-1~LL-5）",
                 "第 6 步底部一键生成的那几个工况，荷载已经自动加到这一步了，不用重复加。"),
            )
            for _t, _d in _help:
                ttk.Label(tips, text="· " + _t, style="Title.TLabel").pack(anchor="w", pady=(6, 0))
                ttk.Label(tips, text="    " + _d, style="Hint.TLabel", wraplength=780,
                          justify="left").pack(anchor="w")

        box = self._card("节点荷载（可选）")
        if not self.model["nodalloads"]:
            ttk.Label(box, text="暂无节点荷载，需要时点下面的按钮添加。", style="Hint.TLabel").pack(anchor="w")
        else:
            g = self._table(box, ["荷载", "工况", "节点", "FX (kN)", "FY", "FZ", "MX (kN·m)", "MY", "MZ", ""])
            for i, nl in enumerate(self.model["nodalloads"]):
                r = i + 1
                ttk.Label(g, text="荷载 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12))
                self._cell(g, ttk.Combobox(g, textvariable=self.svar(nl, "lc", nl.get("lc")),
                                           values=names, width=10, state="readonly"), r, 1)
                for c, key in enumerate(("node", "fx", "fy", "fz", "mx", "my", "mz"), start=2):
                    self._cell(g, ttk.Entry(g, textvariable=self.svar(nl, key, nl.get(key)), width=9), r, c)
                self._cell(g, ttk.Button(g, text="删除", width=6,
                                         command=lambda idx=i: self._del_row("nodalloads", idx)), r, 9)
        ttk.Button(box, text="添加一个节点荷载", command=lambda: self._add_row("nodalloads", {
            "lc": names[0] if names else "DL", "node": "", "fx": "0", "fy": "0", "fz": "0",
            "mx": "0", "my": "0", "mz": "0", "group": ""})).pack(anchor="w", pady=(8, 0))

        box2 = self._card("梁单元荷载（可选）")
        if not self.model["beamloads"]:
            ttk.Label(box2, text="暂无梁单元荷载。", style="Hint.TLabel").pack(anchor="w")
        else:
            g2 = self._table(box2, ["荷载", "工况", "单元号", "类型", "方向", "D1", "P1", "D2", "P2", ""])
            for i, bl in enumerate(self.model["beamloads"]):
                r = i * 2 + 1
                ttk.Label(g2, text="荷载 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(6, 0))
                self._cell(g2, ttk.Combobox(g2, textvariable=self.svar(bl, "lc", bl.get("lc")),
                                            values=names, width=10, state="readonly"), r, 1, pady=(6, 0))
                self._cell(g2, ttk.Entry(g2, textvariable=self.svar(bl, "elems", bl.get("elems")), width=12), r, 2, pady=(6, 0))
                self._cell(g2, ttk.Combobox(g2, textvariable=self.svar(bl, "type", bl.get("type")),
                                            values=BEAMLOAD_TYPES, width=12, state="readonly"), r, 3, pady=(6, 0))
                self._cell(g2, ttk.Combobox(g2, textvariable=self.svar(bl, "dir", bl.get("dir")),
                                            values=DIRECTIONS, width=6, state="readonly"), r, 4, pady=(6, 0))
                for c, key in ((5, "d1"), (6, "p1"), (7, "d2"), (8, "p2")):
                    self._cell(g2, ttk.Entry(g2, textvariable=self.svar(bl, key, bl.get(key)), width=8), r, c, pady=(6, 0))
                self._cell(g2, ttk.Button(g2, text="删除", width=6,
                                          command=lambda idx=i: self._del_row("beamloads", idx)), r, 9, pady=(6, 0))
                sub = ttk.Frame(g2); sub.grid(row=r + 1, column=1, columnspan=9, sticky="w", pady=(2, 8))
                ttk.Label(sub, text="D3").pack(side="left")
                ttk.Entry(sub, textvariable=self.svar(bl, "d3", bl.get("d3")), width=6).pack(side="left", padx=(2, 6))
                ttk.Label(sub, text="P3").pack(side="left")
                ttk.Entry(sub, textvariable=self.svar(bl, "p3", bl.get("p3")), width=8).pack(side="left", padx=(2, 14))
                ttk.Label(sub, text="D4").pack(side="left")
                ttk.Entry(sub, textvariable=self.svar(bl, "d4", bl.get("d4")), width=6).pack(side="left", padx=(2, 6))
                ttk.Label(sub, text="P4").pack(side="left")
                ttk.Entry(sub, textvariable=self.svar(bl, "p4", bl.get("p4")), width=8).pack(side="left", padx=(2, 14))
                ttk.Label(sub, text="荷载组（可选）").pack(side="left")
                ttk.Entry(sub, textvariable=self.svar(bl, "group", bl.get("group")), width=10).pack(side="left", padx=(2, 0))
        ttk.Button(box2, text="添加一个梁单元荷载", command=lambda: self._add_row("beamloads", {
            "lc": names[0] if names else "DL", "elems": "", "cmd": "BEAM", "type": "UNILOAD", "dir": "GZ",
            "proj": "NO", "d1": "0", "p1": "", "d2": "1", "p2": "", "d3": "0", "p3": "0", "d4": "0", "p4": "0",
            "group": ""})).pack(anchor="w", pady=(8, 0))
        ttk.Label(box2, text="均布荷载示例：D1=0、P1=-25，D2=1、P2=-25 表示全长 25 kN/m 向下。",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

    # -------- 8. 生成 --------
    def step_export(self):
        p = self.model["project"]
        if not hasattr(self, "v_fname"):
            self.v_fname = tk.StringVar(value=p.get("name") or "midas-model")
        if not hasattr(self, "v_outdir"):
            self.v_outdir = tk.StringVar(value=p.get("outdir") or self._default_outdir())
        if not hasattr(self, "v_encoding"):
            self.v_encoding = tk.StringVar(value=ENCODINGS[0][0])

        box = self._card("成果输出（一个工程一个文件夹）")
        line0 = ttk.Frame(box); line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self.v_outdir, width=58).pack(side="left", padx=(6, 8))
        ttk.Button(line0, text="选择…", command=self.on_pick_outdir).pack(side="left")

        line = ttk.Frame(box); line.pack(fill="x", pady=(6, 0))
        ttk.Label(line, text="文件名").pack(side="left")
        ttk.Entry(line, textvariable=self.v_fname, width=24).pack(side="left", padx=(6, 14))
        ttk.Label(line, text="编码").pack(side="left")
        ttk.Combobox(line, textvariable=self.v_encoding, values=[e[0] for e in ENCODINGS],
                     width=18, state="readonly").pack(side="left", padx=(6, 0))

        line_c = ttk.Frame(box); line_c.pack(fill="x", pady=(8, 0))
        self.v_compat = tk.BooleanVar(value=bool(p.get("compat")))
        self.v_altmct = tk.BooleanVar(value=bool(p.get("alt_mct")))

        def on_compat():
            self.model["project"]["compat"] = self.v_compat.get()
            self.render()

        ttk.Checkbutton(line_c, text="备用写法（给 MIDAS Civil 2022 及更早的老板本试；只调整了很少几处，没实测过）",
                        variable=self.v_compat, command=on_compat).pack(side="left")
        ttk.Checkbutton(line_c, text="同时另存一份备用写法的 mct",
                        variable=self.v_altmct).pack(side="left", padx=(14, 0))

        line_d = ttk.Frame(box); line_d.pack(fill="x", pady=(10, 0))
        ttk.Button(line_d, text="★ 生成整套成果（模型 + 数据）", command=self.on_generate_bundle).pack(side="left")
        ttk.Button(line_d, text="只存 .mct", command=self.on_save_mct).pack(side="left", padx=(8, 0))
        ttk.Button(line_d, text="复制 MCT 文本", command=self.on_copy_mct).pack(side="left", padx=(8, 0))
        ttk.Button(line_d, text="复制 MCT 路径", command=self.on_copy_mct_path).pack(side="left", padx=(8, 0))
        ttk.Button(line_d, text="打开成果文件夹", command=self.on_open_dir).pack(side="left", padx=(8, 0))
        ttk.Button(line_d, text="启动 MIDAS", command=self.on_launch_midas).pack(side="left", padx=(8, 0))

        line_e = ttk.Frame(box); line_e.pack(fill="x", pady=(8, 0))
        ttk.Button(line_e, text="打开工程…", command=self.on_open_project).pack(side="left")
        ttk.Button(line_e, text="保存工程…", command=self.on_save_project).pack(side="left", padx=(8, 0))
        ttk.Button(line_e, text="读入 MIDAS 结果文件…", command=self.on_import_results).pack(side="left", padx=(8, 0))
        ttk.Label(line_e, text="把 MIDAS 导出的结果（txt/csv/xlsx）复制进当前工程文件夹，和模型放一起。",
                  style="Hint.TLabel").pack(side="left", padx=(10, 0))

        running, api = midas_status()
        st = "MIDAS：%s；API：%s" % (
            "运行中" if running else "未运行",
            ("已连接（%s:%s）" % (api[0], api[1])) if api else
            "未连接 —— 需要时在 MIDAS 里「应用程序 → API Settings → 连接」")
        ttk.Label(box, text=st, style=("Ok.TLabel" if api else "Hint.TLabel")).pack(anchor="w", pady=(8, 0))

        box3 = self._card("直连 MIDAS（导入模型 → MIDAS 分析 → 取回结果）")
        line_g = ttk.Frame(box3); line_g.pack(fill="x")
        ttk.Button(line_g, text="① 导入到 MIDAS", command=self.on_api_import).pack(side="left")
        ttk.Button(line_g, text="② 让 MIDAS 运行分析", command=self.on_api_analyse).pack(side="left", padx=(8, 0))
        ttk.Button(line_g, text="③ 取回结果（反力 / 位移）", command=self.on_api_results).pack(side="left", padx=(8, 0))
        ttk.Label(box3, text="先在 MIDAS 里「应用程序 → API Settings → 连接」，上面三个按钮才可用；"
                             "计算全部由 MIDAS 完成，本程序只负责把模型和数据送进去、把结果取回来。",
                  style="Hint.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(6, 0))
        self.api_log = tk.Text(box3, height=12, wrap="none", font=("Consolas", 9))
        api_sb = ttk.Scrollbar(box3, orient="vertical", command=self.api_log.yview)
        self.api_log.configure(yscrollcommand=api_sb.set)
        api_sb.pack(side="right", fill="y")
        self.api_log.pack(side="left", fill="both", expand=True, pady=(8, 0))
        self._api_write("（这里显示与 MIDAS 的交互过程和取回的结果）")

        ttk.Label(box, text="「生成整套成果」会在这个文件夹里一次写出：\n"
                            "    <文件名>.mct            —— MIDAS 模型命令文件（文件 → 导入 → MCT 命令文件）\n"
                            "    <文件名>_数据.json       —— 向导数据，随时用「打开工程」接着改\n"
                            "    <文件名>_数据表.txt      —— 支点/单元/材料/截面/支承/荷载清单，导入后照着核对\n"
                            "    导入说明.txt            —— 这个文件夹怎么用\n"
                            "MIDAS 的结果（③ 取回的结果、或用「读入 MIDAS 结果文件…」归档的）也放进同一个文件夹，"
                            "一个工程一个文件夹，不用再另设结果目录。",
                  style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

        if getattr(self, "bundle_files", None):
            made = ttk.Frame(box); made.pack(fill="x", pady=(8, 0))
            ttk.Label(made, text="上次生成：", style="Title.TLabel").pack(anchor="w")
            for label, path in self.bundle_files:
                ttk.Label(made, text="    %s → %s" % (label, path), style="Ok.TLabel").pack(anchor="w")

        box2 = self._card("MCT 预览")
        self.txt = tk.Text(box2, height=22, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.insert("1.0", build_mct(self.model))
        self.txt.configure(state="disabled")

    # ---------------- 成果 / 工程 ----------------
    def _default_outdir(self):
        name = safe_name(self.model["project"].get("name") or "midas-model")
        return os.path.join(WORK_DIR, name)

    def on_copy_mct_path(self):
        """把 .mct 的完整路径复制到剪贴板——MIDAS 的打开对话框停在别处时，粘到地址栏就能到。"""
        path = getattr(self, "saved_path", None) or os.path.join(self._cur_outdir(), self._mct_name())
        self.clipboard_clear()
        self.clipboard_append(path)
        self.v_status.set("已复制路径：%s    —— 在 MIDAS 的打开对话框里按 Ctrl+V 粘到「地址栏」再回车即可。"
                          % path)
        self.lbl_status.configure(style="Ok.TLabel")

    def _cur_outdir(self):
        d = txt(self.v_outdir.get()).strip() or self._default_outdir()
        return d

    def _cur_prefix(self):
        return safe_name(self.v_fname.get() or self.model["project"].get("name") or "midas-model")

    def on_pick_outdir(self):
        start = self._cur_outdir()
        os.makedirs(start, exist_ok=True)
        d = filedialog.askdirectory(title="选择成果输出文件夹", initialdir=start)
        if d:
            self.v_outdir.set(d)
            self.model["project"]["outdir"] = d

    def _cur_resdir(self):
        """结果就放在当前工程文件夹里（原来那个单独的「结果文件夹」已取消）。"""
        return self._cur_outdir()

    def on_open_result_dir(self):
        self.on_open_dir()

    def _blocking_issues(self):
        """生成 / 导入 MCT 前的总体检：任何一步不合规就停下，别把 MIDAS 导不进去的文件写出去。"""
        probs = all_issues(self.model)
        if probs:
            messagebox.showwarning(
                "先改完这些再生成",
                "下面这些问题会让 MIDAS 导入报错，先改好（点左侧步骤名可以直接跳过去）：\n\n"
                + "\n".join("· " + p for p in probs[:10])
                + ("\n…还有 %d 条" % (len(probs) - 10) if len(probs) > 10 else ""))
        return probs

    def on_generate_bundle(self):
        if self._blocking_issues():
            return
        enc = dict(ENCODINGS).get(self.v_encoding.get(), "gbk")
        out = self._cur_outdir()
        self.model["project"]["name"] = txt(self.v_fname.get()).strip() or self.model["project"].get("name", "")
        self.model["project"]["outdir"] = out
        self.model["project"]["alt_mct"] = self.v_altmct.get()
        try:
            files = write_bundle(self.model, out, self._cur_prefix(), enc, include_compat=self.v_altmct.get())
        except (OSError, LookupError) as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.bundle_files = files
        self.saved_path = files[0][1]
        self.save_session()
        self.render()
        messagebox.showinfo(
            "已生成整套成果",
            "模型文件（就是它在 MIDAS 里导入）：\n%s\n\n这个文件夹里还有：\n%s\n\n"
            "接下来：MIDAS → 文件 → 导入 → MCT 命令文件。\n"
            "如果对话框里翻不到它（MIDAS 会停在上次用过的文件夹，不一定在你这台机器的同一个盘），"
            "把上面那行完整路径按 Ctrl+V 粘到对话框的「地址栏」再回车就到了；"
            "也可以点第 8 步的「复制 MCT 路径」按钮。"
            % (files[0][1], "\n".join("· %s" % os.path.basename(f[1]) for f in files)))

    def on_open_project(self):
        path = filedialog.askopenfilename(title="打开工程", initialdir=WORK_DIR,
                                          filetypes=[("向导工程/数据", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        if not isinstance(data, dict) or "nodes" not in data:
            messagebox.showerror("打开失败", "这个文件不是向导工程文件。")
            return
        self.model = data
        self.model.setdefault("project", {})
        self.model["project"]["outdir"] = self.model["project"].get("outdir") or os.path.dirname(path)
        for attr in ("v_fname", "v_outdir", "v_encoding", "v_compat", "v_altmct"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.bundle_files = None
        self.v_project.set(txt(self.model["project"].get("name")))
        self.v_force.set(self.model["project"].get("force", "KN"))
        self.v_dist.set(self.model["project"].get("dist", "M"))
        self.v_styp.set(dict(STYPES).get(self.model["project"].get("styp", "1"), STYPES[0][1]))
        self.step = 0
        self.save_session()
        self.render()
        messagebox.showinfo("已打开", "已载入工程：\n%s" % path)

    def on_save_project(self):
        enc = dict(ENCODINGS).get(self.v_encoding.get(), "gbk")
        path = filedialog.asksaveasfilename(title="保存工程", initialdir=self._cur_outdir(),
                                            initialfile=self._cur_prefix() + "_工程.json",
                                            defaultextension=".json",
                                            filetypes=[("向导工程", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.model, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.v_status.set("工程已保存到 %s" % path)
        self.lbl_status.configure(style="Ok.TLabel")

    def on_import_results(self):
        start = self._cur_resdir()
        try:
            os.makedirs(start, exist_ok=True)
        except OSError:
            start = WORK_DIR
        paths = filedialog.askopenfilenames(
            title="选择 MIDAS 导出的结果文件（可多选）",
            initialdir=start,
            filetypes=[("结果文本/表格", "*.txt *.csv *.dat *.out *.xlsx *.xls"),
                       ("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not paths:
            return
        out = start
        os.makedirs(out, exist_ok=True)
        done = []
        preview = ""
        for src in paths:
            try:
                base = os.path.basename(src)
                dst = os.path.join(out, base if base.startswith("结果_") else "结果_" + base)
                if os.path.abspath(src) != os.path.abspath(dst):
                    with open(src, "rb") as fi, open(dst, "wb") as fo:
                        fo.write(fi.read())
                done.append(dst)
                if not preview:
                    try:
                        with open(dst, "r", encoding="utf-8", errors="replace") as fh:
                            preview = "".join(fh.readlines()[:12])
                    except OSError:
                        preview = "(二进制文件，已归档)"
            except OSError as exc:
                messagebox.showerror("归档失败", "%s\n%s" % (src, exc))
                return
        self.result_files = done
        self.save_session()
        messagebox.showinfo("结果已归档", "已放入：\n%s\n\n%s\n\n前几行预览：\n%s"
                            % (out, "\n".join("· " + os.path.basename(d) for d in done), preview[:800]))

    # ---------------- 直连 MIDAS ----------------
    def _api_write(self, text):
        if not hasattr(self, "api_log"):
            return
        self.api_log.configure(state="normal")
        self.api_log.insert("end", text + "\n")
        self.api_log.see("end")
        self.api_log.configure(state="disabled")

    def _ensure_mct(self):
        """确保工程文件夹里有最新的 .mct，返回它的路径。"""
        if self._blocking_issues():
            return None
        enc = dict(ENCODINGS).get(self.v_encoding.get(), "gbk")
        out = self._cur_outdir()
        try:
            files = write_bundle(self.model, out, self._cur_prefix(), enc,
                                 include_compat=self.v_altmct.get())
        except (OSError, LookupError) as exc:
            messagebox.showerror("生成失败", str(exc))
            return None
        self.bundle_files = files
        self.saved_path = files[0][1]
        self.save_session()
        return files[0][1]

    def on_api_import(self):
        path = self._ensure_mct()
        if not path:
            return
        self._api_write("→ 导入 %s" % path)
        status, resp = api_call("POST", "/doc/IMPORTMXT", {"Argument": path})
        if status is None:
            self._api_write("✗ " + str(resp))
            messagebox.showwarning("没能导入", str(resp))
            return
        err = api_error_text(resp)
        self._api_write(("✓ 状态 %s" % status) + ("  错误：%s" % err if err else "  导入命令已发送"))
        if err:
            messagebox.showerror("MIDAS 报错", err)
        else:
            messagebox.showinfo("已发送", "已把模型送进 MIDAS。\n\n接下来点「② 让 MIDAS 运行分析」。")

    def on_api_analyse(self):
        self._api_write("→ 运行分析")
        status, resp = api_call("POST", "/doc/ANAL", {"Assign": {}}, timeout=600)
        if status is None:
            self._api_write("✗ " + str(resp)); messagebox.showwarning("没能运行", str(resp)); return
        err = api_error_text(resp)
        self._api_write(("✓ 状态 %s" % status) + ("  错误：%s" % err if err else "  分析命令已发送"))
        if err:
            messagebox.showerror("MIDAS 报错", err)
        else:
            messagebox.showinfo("已发送", "已让 MIDAS 运行分析。\n\n跑完点「③ 取回结果」。")

    def on_api_results(self):
        names = [txt(l.get("name")).strip() for l in self.model["loadcases"] if txt(l.get("name")).strip()]
        self._api_write("→ 取回结果（工况：%s）" % "、".join(names))
        chunks = []
        for ttype, label in (("REACTIONG", "支反力"), ("DISPLACEMENTG", "位移")):
            body = {"Argument": {"TABLE_NAME": "SS_Table", "TABLE_TYPE": ttype,
                                 "STYLES": {"FORMAT": "Fixed", "PLACE": 4},
                                 "LOAD_CASE_NAMES": [n + "(ST)" for n in names]}}
            status, resp = api_call("POST", "/post/TABLE", body)
            if status is None:
                self._api_write("✗ " + str(resp)); messagebox.showwarning("没能取回", str(resp)); return
            err = api_error_text(resp)
            if err:
                self._api_write("✗ %s：%s" % (label, err)); continue
            table = resp.get("SS_Table") if isinstance(resp, dict) else None
            if not table:
                self._api_write("· %s：没有数据（可能还没运行分析）" % label); continue
            head = [str(h) for h in table.get("HEAD", [])]
            rows = table.get("DATA", [])
            chunks.append("【%s】" % label)
            chunks.append("  " + "  ".join("%-12s" % h for h in head))
            for row in rows:
                chunks.append("  " + "  ".join("%-12s" % (("%.6g" % v) if isinstance(v, float) else str(v)) for v in row))
            chunks.append("")
            self._api_write("✓ %s：%d 行" % (label, len(rows)))
        if not chunks:
            messagebox.showinfo("没有结果", "MIDAS 没返回结果表，先确认分析已经跑完。")
            return
        text = "\r\n".join(chunks)
        self._api_write(text)
        try:
            out = self._cur_resdir()
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "结果_%s.txt" % self._cur_prefix()), "w",
                      encoding="gbk", errors="replace", newline="") as fh:
                fh.write("MIDAS 计算结果（由 MIDAS 分析，程序仅取回）\r\n")
                fh.write("工况：%s\r\n\r\n" % "、".join(names))
                fh.write(text)
            self._api_write("→ 已存入工程文件夹：%s" % out)
        except OSError as exc:
            self._api_write("✗ 结果存盘失败：%s" % exc)

    def _mct_name(self):
        return self._cur_prefix() + ".mct"

    def on_save_mct(self):
        if self._blocking_issues():
            return
        os.makedirs(WORK_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="保存 MCT 命令文件", initialdir=self._cur_outdir(), initialfile=self._mct_name(),
            defaultextension=".mct", filetypes=[("MCT 命令文件", "*.mct"), ("所有文件", "*.*")])
        if not path:
            return
        enc = dict(ENCODINGS).get(self.v_encoding.get(), "gbk")
        try:
            with open(path, "w", encoding=enc, errors="replace", newline="") as fh:
                fh.write(build_mct(self.model))
        except (OSError, LookupError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.saved_path = path
        self.save_session()
        messagebox.showinfo("已保存", "已生成（编码 %s）：\n%s\n\n接下来在 MIDAS Civil NX 中：文件 → 导入 → MCT 命令文件。"
                                      % (self.v_encoding.get(), path))

    def on_copy_mct(self):
        if self._blocking_issues():
            return
        self.clipboard_clear()
        self.clipboard_append(build_mct(self.model))
        self.v_status.set("MCT 文本已复制到剪贴板。")
        self.lbl_status.configure(style="Ok.TLabel")

    def on_open_dir(self):
        target = os.path.dirname(self.saved_path) if getattr(self, "saved_path", None) else self._cur_outdir()
        os.makedirs(target, exist_ok=True)
        try:
            subprocess.Popen(["explorer", os.path.normpath(target)])
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def on_launch_midas(self):
        global MIDAS_EXE
        if not MIDAS_EXE or not os.path.exists(MIDAS_EXE):
            MIDAS_EXE = find_midas_exe()          # 再找一次（可能刚装好/刚插上移动盘）
        if not MIDAS_EXE or not os.path.exists(MIDAS_EXE):
            messagebox.showwarning(
                "找不到 MIDAS",
                "没找到 MIDAS Civil NX 的主程序（CVLw.exe）。\n\n"
                "我已经在注册表和 C: ~ G: 的常见安装位置找过了。\n"
                "请手动启动 MIDAS Civil NX；如果你装在别处，把它启动着用「直连 MIDAS」也一样能导模型。")
            return
        try:
            subprocess.Popen([MIDAS_EXE], cwd=os.path.dirname(MIDAS_EXE))
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))

    # ---------------- 列表增删 ----------------
    def _add_row(self, key, row):
        # 新建荷载时自动带上第一个工况名，免得漏填导致生成空的 *USE-STLD
        if key in ("beamloads", "nodalloads") and not txt(row.get("lc")).strip():
            _lcs = [txt(l.get("name")).strip() for l in self.model.get("loadcases", [])
                    if txt(l.get("name")).strip()]
            if _lcs:
                row["lc"] = _lcs[0]
        self.model[key].append(row)
        self.render()

    def _del_row(self, key, idx):
        if len(self.model[key]) <= 1 and key in ("nodes", "elements", "materials", "sections", "loadcases"):
            messagebox.showinfo("提示", "至少保留一行。")
            return
        del self.model[key][idx]
        if key == "loadcases":
            # 自重原来挂在被删掉的那个工况上：自动改指第一个，别留一个不存在的名字
            sw = self.model["selfweight"]
            _names = [txt(l.get("name")).strip() for l in self.model["loadcases"]]
            if txt(sw.get("lc")).strip() and txt(sw.get("lc")).strip() not in _names:
                sw["lc"] = _names[0] if _names else ""
        self.render()

    def _on_case_renamed(self, idx):
        """第 6 步改写工况名时调用。

        注意：名字写进模型的那个 trace 跟本回调是同一次输入触发的，谁先谁后 Tk 不保证，
        所以延到空闲时再同步一次，保证读到的是刚打完的新名字。
        """
        self.after_idle(lambda: self._sync_sw_case(idx))

    def _sync_sw_case(self, idx):
        """自重如果正指着这个工况，跟着一起改名；下拉的可选项也立刻刷新。"""
        m = self.model
        if not (0 <= idx < len(m["loadcases"])):
            return
        new = txt(m["loadcases"][idx].get("name"))
        old = (getattr(self, "_lc_seen_names", None) or {}).get(idx)
        sw = m["selfweight"]
        names = [txt(l.get("name")).strip() for l in m["loadcases"]]
        if old is not None and txt(sw.get("lc")) == old and new != old:
            sw["lc"] = new
        if getattr(self, "_lc_seen_names", None) is not None:
            self._lc_seen_names[idx] = new
        _v = getattr(self, "_sw_lc_var", None)
        if _v is not None:
            try:
                _v.set(txt(sw.get("lc")))          # 自重那行显示的新名字
            except tk.TclError:
                pass
        _c = getattr(self, "_sw_combo", None)
        if _c is not None:
            try:
                if _c.winfo_exists():
                    _c.configure(values=names)     # 下拉里立刻能看到新名字
            except tk.TclError:
                pass


def safe_print(text=""):
    """打印一行，遇到控制台编码装不下中文时也不会崩。

    Windows 中文控制台是 cp936，装得下；但英文/西欧语系的 Windows 控制台是
    cp1252，`print("自检")` 会直接抛 UnicodeEncodeError 把程序打断。
    GitHub 的 Windows CI runner 就是这种情况，实测在 `--selftest` 上崩过。
    所以这里先试一次，失败就把这一行的中文换成 '?' 再打。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def selftest(verbose=True):
    """不打开界面的自检：跑一遍核心算法和 MCT 生成。返回 0 = 全部通过。

    主要给 CI 和「换台电脑先试试能不能用」准备 —— 这台机器不需要装 MIDAS。
    """
    fails = []

    def check(name, cond, detail=""):
        if verbose:
            safe_print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                                        ("  " + detail) if (detail and not cond) else ""))
        if not cond:
            fails.append(name)

    if verbose:
        safe_print("midas-mct-wizard %s 自检" % __version__)
        safe_print("-" * 52)

    # 1) 数字解析：nan / inf 不能让程序崩掉
    check("is_num 拒绝 nan/inf/空", not is_num("nan") and not is_num("1e999") and not is_num(""))
    check("num 把非法值写成 0", num("nan") == "0" and num("abc") == "0" and num("2.5") == "2.5")

    # 2) 名称处理：中文原样保留（实测能导入），只清掉会破坏字段的逗号
    check("aname 保留中文", aname("主梁", "SEC1") == "主梁")
    check("aname 去掉逗号", "," not in aname("a,b"))
    check("aname 空值用兜底名", aname("", "LC") == "LC")
    check("aascii 仍然只用于边界组名", aascii("中支座") == "")

    # 3) 截面特性：已知解析解
    square = compute_section_props([[[0, 0], [2, 0], [2, 2], [0, 2]]])
    check("实心方截面 A/I", square and abs(square["A"] - 4.0) < 1e-9
          and abs(square["Iyy"] - 4.0 / 3.0) < 1e-9, str(square and square.get("A")))
    box = compute_section_props([
        [[0, 0], [2, 0], [2, 1.5], [0, 1.5]],
        [[0.05, 0.05], [1.95, 0.05], [1.95, 1.45], [0.05, 1.45]]])
    # 薄壁矩形管 Bredt 解析值 J = t(b-t)³(h-t)³/((b-t)+(h-t)) = 0.23514
    check("空心箱形 J 与 Bredt 相符",
          box and abs(box["Ixx"] - 0.235140) / 0.235140 < 0.10,
          "J=%s" % (box and box.get("Ixx")))

    # 4) MCT 生成：默认模型必须干净、自洽
    m = default_model()
    m["selfweight"]["lc"] = m["loadcases"][0]["name"]
    m["beamloads"][0]["lc"] = m["loadcases"][0]["name"]
    text = build_mct(m)
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(";"))
    check("MCT 非注释行没有逗号错位", all(l.count(",") >= 1 for l in body.splitlines() if l.startswith("   ")))
    check("MCT 有 *ENDDATA", "*ENDDATA" in text)

    # 4b) 中文名必须原样保留 —— 实测 MIDAS 认中文，不能给抹掉
    m_cn = default_model()
    m_cn["loadcases"] = [{"name": "自重", "type": "D", "desc": "自身重力"},
                         {"name": "二期", "type": "L", "desc": ""}]
    m_cn["materials"][0]["name"] = "混凝土"
    m_cn["sections"][0]["name"] = "主梁"
    m_cn["selfweight"] = {"on": True, "lc": "自重", "x": "0", "y": "0", "z": "-1", "group": ""}
    m_cn["beamloads"][0]["lc"] = "二期"
    cn_text = build_mct(m_cn)
    check("中文工况名原样写出", "自重" in cn_text and "二期" in cn_text)
    check("两个中文工况名不再撞车", cn_text.count("LC,") == 0 and cn_text.count("*USE-STLD, 自重") == 1)
    check("中文材料名/截面名原样写出", "混凝土" in cn_text and "主梁" in cn_text)

    # 5) 工况名一致：*STLDCASE 写什么，*USE-STLD 就得引用什么
    m2 = default_model()
    m2["loadcases"] = [{"name": "恒载,1", "type": "D", "desc": ""}]
    m2["beamloads"][0]["lc"] = "恒载,1"
    m2["selfweight"] = {"on": False, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
    text2 = build_mct(m2)
    declared, in_stldcase = [], False
    for _line in text2.splitlines():
        if _line.startswith("*"):
            in_stldcase = _line.startswith("*STLDCASE")
            continue
        if in_stldcase and _line.startswith("   "):
            declared.append(_line.split(",")[0].strip())
    used = [l.split(",", 1)[1].strip() for l in text2.splitlines() if l.startswith("*USE-STLD,")]
    check("荷载引用的工况名与 *STLDCASE 一致",
          bool(declared) and bool(used) and all(u in declared for u in used),
          "declared=%s used=%s" % (declared, used))

    # 6) 校验器：明显越界的输入要被拦住
    m3 = default_model()
    m3["sec_assign"] = [{"from": "1", "to": "999", "sect": "99"}]
    check("分段表越界会被校验拦住", bool(issues_for("sections", m3)))
    m4 = default_model()
    m4["nodes"] = [{"x": "nan", "y": "0", "z": "0", "note": ""}]
    check("支点坐标写 nan 会被拦住", bool(issues_for("nodes", m4)))

    # 7) 老工程/半截 JSON 不能把界面搞崩
    partial = ensure_model({"nodes": [{"x": "1", "y": "0", "z": "0", "note": ""}]})
    check("ensure_model 补齐缺失的键",
          all(k in partial for k in ("project", "elements", "materials", "sections",
                                     "supports", "loadcases", "selfweight", "beamloads")))
    check("build_mct 能吃下补齐后的模型", bool(build_mct(partial)))
    check("build_data_report 不再 NameError", bool(build_data_report(default_model())))

    if verbose:
        safe_print("-" * 52)
        safe_print("结果：%s" % ("全部通过 ✓" if not fails
                                else "%d 项未通过：%s" % (len(fails), "、".join(fails))))
    return 1 if fails else 0


def _install_error_handler(app):
    """把没接住的异常显示出来。

    这个程序通常用 pythonw.exe 启动 —— 它没有控制台，报错写到 stderr 等于没写，
    用户只看到按钮点下去没反应。这里改成弹窗。
    """
    import traceback

    def _report(exc, val, tb):
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            with open(os.path.join(WORK_DIR, "错误日志.txt"), "a", encoding="utf-8") as fh:
                fh.write("\n%s\n%s\n" % (datetime.datetime.now().isoformat(), detail))
        except OSError:
            pass
        try:
            messagebox.showerror("出错了",
                                 "%s: %s\n\n详情已写入：模型文件\\错误日志.txt" % (exc.__name__, val))
        except Exception:
            pass

    app.report_callback_exception = _report


def _run_gui():
    app = Wizard()
    _install_error_handler(app)
    app.mainloop()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="midas_wizard",
        description="MIDAS Civil NX 建模向导：按步骤填桥梁模型，生成 .mct 命令文件。")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    ap.add_argument("--selftest", action="store_true",
                    help="不打开界面，跑一遍核心算法自检（返回码 0 = 通过）")
    ap.add_argument("--emit-mct", metavar="工程.json",
                    help="不打开界面，把工程 JSON 直接转成 .mct 打印到屏幕")
    ap.add_argument("--outdir", metavar="目录",
                    help="配合 --emit-mct：把 .mct 写到指定目录而不是打印")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.emit_mct:
        try:
            with open(args.emit_mct, "r", encoding="utf-8") as fh:
                model = ensure_model(json.load(fh))
        except (OSError, ValueError) as exc:
            safe_print("读不了工程文件：%s" % exc)
            return 2
        bad = all_issues(model)
        if bad:
            safe_print("模型还有 %d 处问题，先修好再生成：" % len(bad))
            for msg in bad:
                safe_print("  - %s" % msg)
            return 3
        text = build_mct(model)
        if args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            stem = safe_name(txt(model["project"].get("name")), "midas-model")
            dst = os.path.join(args.outdir, stem + ".mct")
            with open(dst, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
            safe_print("已写出 %s" % dst)
        else:
            sys.stdout.write(text)
        return 0
    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
