# -*- coding: utf-8 -*-
"""
MIDAS Civil NX 建模向导  —  本地桌面程序（Python + Tkinter，不依赖浏览器）

用途：按步骤输入桥梁/梁模型的支点、单元、材料、截面、支承、荷载，
      最后生成一个 .mct 命令文件，在 MIDAS Civil NX 里
      「文件 → 导入 → MCT 命令文件」即可生成模型。

MCT 命令的字段顺序依据 MIDAS 官方《MCT Command Quick Reference》
(manual.midasuser.com, midas Civil 900 → Appendix → MCT Command Shell)。
"""

import datetime
import importlib
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 本文件同目录下的共享模块（wiz_common.py）与流程引擎要先能找到自己所在的目录
# —— 用 importlib 从别处加载本文件时，sys.path 里没有程序目录，会 ModuleNotFoundError。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from wiz_common import (SHAPES, SHAPE_BY_CODE, CAD_SHAPE, TS_SHAPE, DIM_SLOTS,
                       is_num, num, numg, txt, aascii, aname,
                       compute_section_props, suggest_unit, unit_advice, loop_area, orient_loop,
                       read_dxf_loops, shape_code_of, _dist, _perimeter, _point_in_poly,
                       _loop_terms, _tess_circle, _tess_arc, _chain_segments,
                       STYPE_NAMES, MAT_TYPE_NAMES, MAT_MODES, MAT_MODE_NAMES,
                       MAT_MODE_CODES, MAT_TYPE_CODES)
from flow_api import FlowBase      # 流程插件基类（每种桥型一个 flow_*.py）
import analysis_core               # 分析设置（移动荷载 / 模态 / 屈曲）的 MCT 片段 + 校验

# 拱桥·施工阶段流程的引擎（同目录 arch_core.py，纯计算、不依赖界面）
try:
    import arch_core
except Exception:            # 缺文件时只影响拱桥流程，梁桥照常用
    arch_core = None

APP_TITLE = "MIDAS Civil NX 建模向导"
__version__ = "2.0.1"
# 便携：程序文件夹就是自己的所在目录，整个文件夹拷到哪都跟着走
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(APP_DIR, "模型文件")
# 结果文件夹已取消：MIDAS 的结果统一放进当前工程文件夹（和 .mct 放一起）


def find_midas_exe():
    """找 MIDAS Civil NX 的主程序。每台机器装的位置不一样，这里逐个试，不写死。"""
    import glob
    try:
        import winreg
    except ImportError:          # 非 Windows：没有注册表，只按常见安装目录/程序目录找
        winreg = None
    cands = []
    try:                                     # 1) 注册表里 MIDAS 自己记的安装位置
        if winreg is None:
            raise OSError("winreg unavailable")
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
        "sections": [{"name": "主梁", "shape": "SB", "dims": ["2.0", "1.5"], "offset": "CC",
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



def empty_model():
    """真正的空模型：所有清单都是空的，从头开始填。"""
    m = default_model()
    for k in ("nodes", "elements", "materials", "sections", "supports",
              "loadcases", "nodalloads", "beamloads"):
        m[k] = []
    m["selfweight"] = {"on": True, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
    return m


STEP_CLEAR_KEYS = {
    "nodes": ("nodes",), "elements": ("elements",), "materials": ("materials",),
    "sections": ("sections",), "supports": ("supports", "rigidlinks", "elasticlinks"), "loadcases": ("loadcases",),
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
                # 变截面：这里只写存档信息；i/j 端引用合不合法由 issues_for() 统一检查。
                add("       类型：变截面（TS，沿单元 i→j 线性渐变）    i 端引用截面 %s，j 端引用截面 %s%s"
                    % (txt(sec.get("i_sect")).strip() or "（未填）",
                       txt(sec.get("j_sect")).strip() or "（未填）",
                       "（镜像）" if sec.get("mirror") else ""))
            else:
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
        add("  %-16s %s   %s" % (txt(sp.get("nodes")), code, aname(sp.get("group"))))
    add("")

    _rl = m.get("rigidlinks") or []
    _el = m.get("elasticlinks") or []
    if _rl or _el:
        add("-" * 78)
        add("五之二、节点连接  刚性连接 %d 条 / 弹性连接 %d 条" % (len(_rl), len(_el)))
        add("-" * 78)
        for _r in _rl:
            _dof = _r.get("dof") or {}
            _code = "".join("1" if _dof.get(_k) else "0" for _k in ("dx", "dy", "dz", "rx", "ry", "rz"))
            add("  主节点 %-6s 自由度 %s  从节点：%s  组=%s"
                % (txt(_r.get("master")), _code, txt(_r.get("slaves")), txt(_r.get("group"))))
        for _i, _e in enumerate(_el, start=1):
            add("  弹性连接 %d：节点 %s — %s   SDx=%s SDy=%s SDz=%s  SRx=%s SRy=%s SRz=%s  组=%s"
                % (_i, txt(_e.get("n1")), txt(_e.get("n2")),
                   num(_e.get("sdx")), num(_e.get("sdy")), num(_e.get("sdz")),
                   num(_e.get("srx")), num(_e.get("sry")), num(_e.get("srz")), txt(_e.get("group"))))
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
            % (txt(sw.get("lc")), num(sw.get("x")), num(sw.get("y")), num(sw.get("z")), aname(sw.get("group"))))
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
               num(bl.get("p1")), num(bl.get("p2")), num(bl.get("p3")), num(bl.get("p4")), aname(bl.get("group"))))
    add("")
    _an = m.get("analysis") or {}
    _mv = _an.get("moving") or {}
    _eig = _an.get("eigen") or {}
    _bk = _an.get("buckle") or {}
    if _mv.get("on") or _eig.get("on") or _bk.get("on"):
        add("-" * 78)
        add("九、分析设置（实验功能：移动荷载 / 模态 / 屈曲）")
        add("-" * 78)
        if _mv.get("on"):
            add("  移动荷载：车道 %s（偏心 %s m，冲击系数 %s）"
                % (txt(_mv.get("lane")), num(_mv.get("ecc")), num(_mv.get("impact"))))
            add("            车辆 %s：qk = %s kN/m，Pk = %s kN（用户定义车道荷载）"
                % (txt(_mv.get("vname")), num(_mv.get("qk")), num(_mv.get("pk"))))
            add("            移动荷载工况 %s（车辆组 %s），分析控制：Exact / CENTER / NORMAL"
                % (txt(_mv.get("lc")), txt(_mv.get("vclass"))))
        if _eig.get("on"):
            add("  模态分析：提取前 %s 阶（%s 写法）"
                % (num(_eig.get("n")), "旧版 4 字段" if _eig.get("legacy") else "新版 TYPE 5 字段"))
        if _bk.get("on"):
            add("  屈曲分析：%s 阶，可变荷载 %s" % (num(_bk.get("n")), txt(_bk.get("lcs"))))
        add("")
        add("  ★ 这三项是实验功能：格式照官方 MCT 手册字段写，本机没有 MIDAS 导出样本可对照。")
        add("    第一次导入若报错，把 MIDAS 命令行号发作者，按行号改；也可以先取消勾选再生成。")
        add("")

    add("=" * 78)
    add("说明：本表是向导输入的存档，导入 MIDAS 用的是同目录下的 .mct 文件。")
    add("=" * 78)
    return "\r\n".join(L) + "\r\n"


def midas_status():
    """查一下 MIDAS 是否在运行、API 是否已连接（读注册表里的连接信息）。"""
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
    for country in ("CH", "US"):
        try:
            import winreg
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
    add("; FORCE, LENGTH" if not compat else "; FORCE, LENGTH, HEAT, TEMPER")
    add("   %s, %s, %s, %s" % (p.get("force", "KN"), p.get("dist", "M"),
                               p.get("heat", "KJ"), p.get("temp_unit", "C")))
    add("")

    add("*STRUCTYPE    ; Structure Type")
    if compat:
        add("; iSTYP, iMASS, iSMAS, bMASSOFFSET, bSELFWEIGHT, GRAV, TEMPER, bALIGNBEAM, bALIGNSLAB, bROTRIGID")
        add("   %s, 1, %s, NO, YES, %s, %s, NO, NO, YES" % (p.get("styp", "1"), p.get("smas", "1"),
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
            tail_head = head + (", C, NO, 0.05, " if compat else ", C, NO, 0.05, ")
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
    _cad = [(i, s) for i, s in enumerate(m["sections"], start=1) if s.get("shape") == CAD_SHAPE]
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
                i, "VALUE" if sec.get("shape") == CAD_SHAPE else "DBUSER",
                name, off, sec.get("shear", "YES"), sec.get("warp", "NO"))
            if sec.get("shape") == CAD_SHAPE:
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
            add("   %s, %s, %s" % (ids, code, txt(s.get("group"))))
        add("")

    # ---- 节点连接：主从节点（刚性连接）/ 弹性连接（第03讲 3.1.2 单元连接）----
    _rl = m.get("rigidlinks") or []
    if _rl:
        add("*RIGIDLINK    ; Rigid Link（主从节点刚性连接）")
        add("; M-NODE, DOF, S-NODE LIST, GROUP")
        for _r in _rl:
            _dof = _r.get("dof") or {}
            _code = "".join("1" if _dof.get(_k) else "0" for _k in ("dx", "dy", "dz", "rx", "ry", "rz"))
            add("   %s, %s, %s, %s" % (num(_r.get("master")), _code,
                                       " ".join(t for t in txt(_r.get("slaves")).replace(",", " ").split() if t),
                                       aname(_r.get("group"))))
        add("")
    _el = m.get("elasticlinks") or []
    if _el:
        add("*ELASTICLINK    ; Elastic Link（弹性连接 / 6 向弹簧）")
        add("; iNO, iNODE1, iNODE2, LINK, ANGLE, R_SDx, R_SDy, R_SDz, R_SRx, R_SRy, R_SRz, SDx, SDy, SDz, SRx, SRy, SRz, bSHEAR, DRy, DRz, GROUP")
        for _i, _e in enumerate(_el, start=1):
            add("   %d, %s, %s, GEN, 0, NO, NO, NO, NO, NO, NO, %s, %s, %s, %s, %s, %s, NO, 0.5, 0.5, %s"
                % (_i, num(_e.get("n1")), num(_e.get("n2")),
                   num(_e.get("sdx")), num(_e.get("sdy")), num(_e.get("sdz")),
                   num(_e.get("srx")), num(_e.get("sry")), num(_e.get("srz")),
                   aname(_e.get("group"))))
        add("")

    if m["loadcases"]:
        add("*STLDCASE    ; Static Load Cases")
        add("; LCNAME, LCTYPE, DESC")
        for lc in m["loadcases"]:
            add("   %s, %s, %s" % (aname(lc.get("name"), "LC"), lc.get("type", "D"), aname(lc.get("desc"))))
        add("")

    sw = m["selfweight"]
    if sw.get("on") and txt(sw.get("lc")).strip():
        add("*USE-STLD, " + txt(sw["lc"]))
        add("; *SELFWEIGHT, X, Y, Z, GROUP")
        add("*SELFWEIGHT    ; Self Weight")
        add("   %s, %s, %s, %s" % (num(sw.get("x")), num(sw.get("y")),
                                   num(sw.get("z")), aname(sw.get("group"))))
        add("")

    for nl in m["nodalloads"]:
        add("*USE-STLD, " + txt(nl.get("lc")))
        add("*CONLOAD    ; Nodal Loads")
        add("; NODE_LIST, FX, FY, FZ, MX, MY, MZ, GROUP")
        add("   %s, %s, %s, %s, %s, %s, %s, %s"
            % (txt(nl.get("node")), num(nl.get("fx")), num(nl.get("fy")), num(nl.get("fz")),
               num(nl.get("mx")), num(nl.get("my")), num(nl.get("mz")), aname(nl.get("group"))))
        add("")

    for bl in m["beamloads"]:
        add("*USE-STLD, " + txt(bl.get("lc")))
        add("*BEAMLOAD    ; Element Beam Loads")
        add("; ELEM_LIST, CMD, TYPE, DIR, bPROJ, D1, P1, D2, P2, D3, P3, D4, P4, GROUP")
        add("   %s, %s, %s, %s, %s, NO, LY, , , , %s, %s, %s, %s, %s, %s, %s, %s, , NO, 0, 0, NO,"
            % (txt(bl.get("elems")), bl.get("cmd", "BEAM"), bl.get("type", "UNILOAD"), bl.get("dir", "GZ"),
               bl.get("proj", "NO"), num(bl.get("d1")), num(bl.get("p1")), num(bl.get("d2")), num(bl.get("p2")),
               num(bl.get("d3")), num(bl.get("p3")), num(bl.get("d4")), num(bl.get("p4"))))
        add("")

    # ---- 分析设置（可选，默认不写）：移动荷载 / 模态 / 屈曲 ----
    # 写法统一在 analysis_core.py；不勾选时一行也不写（生成结果与以前逐字节一致）。
    for _ln in analysis_core.lines(m, element_count=len(m["elements"])):
        add(_ln)
    add("*ENDDATA")
    # 说明：名称里的中文 MIDAS Civil NX 实测能正常导入（MCT 按本地 ANSI/GBK 存即可），
    # 这里不做整份 ASCII 过滤，免得把中文材料名/截面名/边界组名弄丢。
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
    if step_id == "materials":
        for _i, _mat in enumerate(m["materials"], start=1):
            if is_num(_mat.get("poisn")) and not (0.0 <= float(_mat["poisn"]) < 0.5):
                out.append("材料 %d 的泊松比应在 0 ~ 0.5 之间。" % _i)
            if is_num(_mat.get("den")) and float(_mat["den"]) <= 0:
                out.append("材料 %d 的容重应大于 0。" % _i)
    if step_id in ("loadcases", "materials", "sections"):
        _cn = [txt(_x.get("name")) for _x in (m["loadcases"] if step_id == "loadcases" else
               (m["materials"] if step_id == "materials" else m["sections"]))]
        _cn = [x for x in _cn if x.strip() and any(ord(c) > 127 for c in x)]
        if _cn:
            out.append("名称里有中文（%s）。MIDAS 的 MCT 不允许非 ASCII 字符，导入时会自动换成 "
                       "MAT1/SEC1 这类英文名——想保留可读名字请改用英文或拼音。" % "、".join(_cn[:3]))
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
                    elif shape_code_of(m["sections"][int(float(v)) - 1]) == TS_SHAPE:
                        out.append("变截面 %d 的「%s」指向了另一个变截面；MIDAS 只认两端都是等截面。"
                                   % (i, lab))
                # TAPERED 那行只写一个 SHAPE，两端必须同一种形状，否则 MIDAS 会按一种形状
                # 去解释两端的尺寸，生成的刚度是错的
                _ends = []
                for _k in ("i_sect", "j_sect"):
                    _v = sec.get(_k)
                    _ends.append(shape_code_of(m["sections"][int(float(_v)) - 1])
                                 if is_num(_v) and 1 <= int(float(_v)) <= len(m["sections"]) else None)
                if all(_ends) and TS_SHAPE not in _ends and _ends[0] != _ends[1]:
                    out.append("变截面 %d 两端引用的截面形状不同（%s / %s）——MIDAS 的 TAPERED 一行只写一个 "
                               "SHAPE，两端必须是同一种形状，否则生成的模型是错的。" % (i, _ends[0], _ends[1]))
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
        for i, r in enumerate(m.get("rigidlinks") or [], start=1):
            for key, zh in (("master", "主节点"), ("slaves", "从节点")):
                ids = [t for t in txt(r.get(key)).replace(",", " ").split() if t]
                if not ids:
                    out.append("刚性连接 %d 的「%s」不能空。" % (i, zh))
                for t in ids:
                    if not is_num(t) or not (1 <= int(float(t)) <= nn):
                        out.append("刚性连接 %d 的%s「%s」不存在（节点号 1~%d）。" % (i, zh, t, nn))
            dof = r.get("dof") or {}
            if not any(dof.get(k) for k in ("dx", "dy", "dz", "rx", "ry", "rz")):
                out.append("刚性连接 %d 一个自由度都没勾，等于没连。" % i)
        for i, e in enumerate(m.get("elasticlinks") or [], start=1):
            for key, zh in (("n1", "节点 1"), ("n2", "节点 2")):
                v = txt(e.get(key)).strip()
                if not is_num(v) or not (1 <= int(float(v)) <= nn):
                    out.append("弹性连接 %d 的%s「%s」不存在（节点号 1~%d）。" % (i, zh, v, nn))
            if txt(e.get("n1")).strip() and txt(e.get("n1")).strip() == txt(e.get("n2")).strip():
                out.append("弹性连接 %d 的两个节点是同一个。" % i)
            vals = []
            for k in ("sdx", "sdy", "sdz", "srx", "sry", "srz"):
                if not is_num(e.get(k)):
                    out.append("弹性连接 %d 的刚度「%s」要填数字（不约束的方向填 0）。" % (i, k))
                else:
                    vals.append(float(e.get(k)))
            if vals and not any(abs(v) > 0 for v in vals):
                out.append("弹性连接 %d 的 6 个刚度全是 0，等于没连。" % i)
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
    elif step_id == "export":
        out += analysis_core.issues(m, element_count=len(m["elements"]),
                                    loadcase_names=[txt(lc.get("name")).strip() for lc in m["loadcases"]])
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
    "确认无误后保存 .mct 文件，在 MIDAS Civil NX 中「文件 → 导入 → MCT 命令文件」选择它；也可以复制文本粘贴到「工具 → 命令窗口」运行。"
    "页面下方还有「分析设置（可选）」：移动荷载 / 模态 / 屈曲，都是实验功能，不勾就不写。",
]

# ---------------- 第二条流程：拱桥 · 施工阶段联合截面 ----------------
MODE_BEAM_LABEL = "梁桥（一次成桥）"
MODE_ARCH_LABEL = "拱桥·施工阶段"
MODE_LABEL_TO_CODE = {MODE_BEAM_LABEL: "beam", MODE_ARCH_LABEL: "arch"}
ARCH_STEP_IDS = ["arch_axis", "arch_section", "arch_stages", "arch_export"]
ARCH_STEP_TITLES = ["拱轴线", "截面（施工部件）", "材料与施工阶段", "生成 / 直连 MIDAS"]
ARCH_STEP_HINTS = [
    "填跨径、矢高、拱轴系数（悬链线 m 必须大于 1），程序自动生成拱轴线和单元。改参数后下方预览会跟着变。",
    "先填截面尺寸点「按参数重新生成截面」；再给每个部件指定「从哪个阶段开始参与受力」和龄期。部件 1 是预制部分，从第一阶段就受力。",
    "选混凝土等级和容重；要算收缩徐变就勾上（可填环境湿度）。阶段表默认按「预制合龙 → 浇纵缝 → 纵缝硬化+浇顶板 → 顶板硬化」排好，可改名字和天数。",
    "生成 .mct 拿去 MIDAS 导入，或者开着 MIDAS 直接点「③ 直连 MIDAS」，程序会导入、跑施工阶段分析、把各阶段内力取回来。",
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
        for _msg in issues_for(_sid, m):
            if is_advisory(_msg):
                continue
            out.append("第 %d 步（%s）：%s" % (_i, STEP_TITLES[_i - 1], _msg))
    return out


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------


# ==========================================================================
# 流程注册表：每种桥型 = 一个 FlowBase 子类 + 一个 flow_*.py 插件
# ==========================================================================
class BeamFlow(FlowBase):
    """梁桥（一次成桥）—— 原来的 8 步流程，界面方法还写在 Wizard 上。"""
    code = "beam"
    label = MODE_BEAM_LABEL
    step_ids = STEP_IDS
    step_titles = STEP_TITLES
    step_hints = STEP_HINTS
    last_hint = "文件已准备好，可以保存或复制。"

    def default_model(self):
        return default_model()

    def reset_model(self):
        return empty_model()

    def issues(self, sid):
        return issues_for(sid, self.m)

    def data_report(self):
        return build_data_report(self.m)

    def clear_title(self, sid):
        return {"nodes": "支点", "elements": "单元", "materials": "材料", "sections": "截面",
                "supports": "支承", "loadcases": "荷载工况与自重",
                "loads": "节点荷载和梁单元荷载"}.get(sid, "")

    def clear(self, sid):
        m = self.m
        for k in (STEP_CLEAR_KEYS.get(sid) or ()):
            m[k] = []
        if sid == "loadcases":
            m["selfweight"] = {"on": True, "lc": "", "x": "0", "y": "0", "z": "-1", "group": ""}
        if sid in ("elements", "sections"):
            m["sec_assign"] = []
        for attr in ("v_span_start", "v_span_dx", "v_span_n"):
            if hasattr(self.wiz, attr):
                delattr(self.wiz, attr)

    def __getattr__(self, name):
        if name.startswith("step_"):
            return getattr(self.wiz, name)
        raise AttributeError(name)


class ArchFlow(FlowBase):
    """拱桥 · 施工阶段联合截面（第二条流程）。"""
    code = "arch"
    label = MODE_ARCH_LABEL
    step_ids = ARCH_STEP_IDS
    step_titles = ARCH_STEP_TITLES
    step_hints = ARCH_STEP_HINTS
    last_hint = "文件已准备好，可以保存、复制或直连 MIDAS。"

    def default_model(self):
        if arch_core is None:
            raise RuntimeError("同目录下找不到 arch_core.py")
        return arch_core.default_model()

    def reset_model(self):
        return self.default_model()

    def issues(self, sid):
        return self.wiz.arch_issues(sid)

    def data_report(self):
        try:
            return self.wiz._arch_report(self.m)
        except Exception:                                      # noqa: BLE001
            return ""

    def clear_title(self, sid):
        return {"arch_axis": "拱轴线", "arch_section": "截面（施工部件）",
                "arch_stages": "材料与施工阶段"}.get(sid, "")

    def clear(self, sid):
        try:
            d = arch_core.default_model()
        except Exception:                                      # noqa: BLE001
            return
        m = self.m
        if sid == "arch_axis":
            m["arch"] = dict(d.get("arch") or {})
        elif sid == "arch_section":
            for k in ("parts", "box", "section_name"):
                if k in d:
                    m[k] = d[k]
        elif sid == "arch_stages":
            for k in ("stages", "creep", "humidity", "gamma", "material"):
                if k in d:
                    m[k] = d[k]

    def __getattr__(self, name):
        if name.startswith("step_"):
            return getattr(self.wiz, name)
        raise AttributeError(name)


# 插件流程：模块名 -> 流程代码（模块里的类必须叫 Flow，类属性 code 要等于这里的代码）
FLOW_PLUGIN_MODULES = [
    ("cable", "flow_cable"),      # 斜拉桥（鱼骨刺·成桥）
    ("susp", "flow_susp"),        # 悬索桥
    ("cs", "flow_cs"),            # 施工阶段梁桥（连续刚构悬臂 / 简支变连续）
    ("cfst", "flow_cfst"),        # 钢管混凝土拱
    ("frame", "flow_frame"),      # 平面刚架（自由建模 + 矩阵位移法）
    ("tbeam", "flow_tbeam"),      # 预制 T 梁 + 现浇桥面板（施工阶段联合截面）
    ("cfst2", "flow_cfst2"),      # 两跨钢管混凝土（施工顺序 · 湿重对比）
]
FLOW_LOAD_ERRORS = {}


def discover_flows():
    """内置流程 + 自动发现的插件流程。插件的 import 失败不能拖垮主程序。"""
    flows = {}
    for cls in (BeamFlow, ArchFlow):
        flows[cls.code] = cls
    for code, modname in FLOW_PLUGIN_MODULES:
        try:
            mod = importlib.import_module(modname)
            cls = getattr(mod, "Flow")
            if not getattr(cls, "label", ""):
                cls.label = getattr(mod, "LABEL", code)
            flows[code] = cls
        except Exception as exc:                               # noqa: BLE001
            FLOW_LOAD_ERRORS[code] = "%s: %s" % (type(exc).__name__, exc)
    return flows


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x740")
        self.minsize(940, 620)

        self.model = default_model()
        self.mode = self.model.get("mode", "beam")
        self.step = 0
        self.saved_path = None
        self.bundle_files = None
        self.result_files = None

        self._flow_classes = discover_flows()
        self._flow_objs = {}
        self._mode_cache = {}
        if self.mode not in self._flow_classes:
            self.mode = "beam"

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

        ttk.Label(bar, text=APP_TITLE, style="H1.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")

        # 流程选择：梁桥 / 拱桥 / 斜拉桥 / 悬索桥 / 施工阶段梁桥 / 钢管混凝土拱 …
        # 新桥型只要往 FLOW_PLUGIN_MODULES 里加一个模块，下拉里自动出现。
        self._flow_order = list(self._flow_classes.keys())
        self._flow_labels = {}
        for _c in self._flow_order:
            self._flow_labels[getattr(self._flow_classes[_c], "label", _c)] = _c
        self.v_flow = tk.StringVar(value=getattr(self._flow_classes.get(self.mode), "label", ""))
        f_mode = ttk.Frame(bar)
        f_mode.grid(row=0, column=4, columnspan=4, sticky="e")
        ttk.Label(f_mode, text="流程：", style="Hint.TLabel").pack(side="left")
        self.cb_flow = ttk.Combobox(f_mode, textvariable=self.v_flow, values=list(self._flow_labels),
                                    width=30, state="readonly")
        self.cb_flow.pack(side="left")
        self.cb_flow.bind("<<ComboboxSelected>>", lambda _e: self._on_flow_change())
        self.v_mode = self.v_flow          # 兼容旧脚本：v_mode 就是「流程」下拉的变量

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

    # ---------------- 流程插件 ----------------
    def flow(self):
        """当前流程的插件对象（按需创建并缓存）。"""
        obj = self._flow_objs.get(self.mode)
        if obj is None:
            cls = self._flow_classes.get(self.mode) or self._flow_classes["beam"]
            obj = cls(self)
            self._flow_objs[self.mode] = obj
        return obj

    def _flow_label_of(self, code):
        cls = self._flow_classes.get(code)
        return getattr(cls, "label", "") if cls else ""

    def _on_flow_change(self):
        code = self._flow_labels.get(self.v_flow.get(), "")
        if not code or code == self.mode:
            return
        self._switch_mode(code)

    def _on_mode_radio(self):
        """兼容旧脚本/旧测试：就是切流程（v_mode 里存的是流程中文名）。"""
        code = self._flow_labels.get(self.v_mode.get(), "")
        if code and code != self.mode:
            self._switch_mode(code)

    # ---------------- 渲染 ----------------
    def render(self):
        for w in self.form.winfo_children():
            w.destroy()
        flow = self.flow()
        ids = list(flow.step_ids)
        titles = list(flow.step_titles)
        hints = list(flow.step_hints)
        self.step = max(0, min(len(ids) - 1, self.step))
        sid = ids[self.step]
        self.v_title.set("第 %d / %d 步 · %s" % (self.step + 1, len(ids), titles[self.step]))
        self.v_hint.set(hints[self.step])

        flow.build(sid)

        issues = [x for x in (flow.issues(sid) or []) if not is_advisory(x)]
        last = (self.step == len(ids) - 1)
        if last:
            self.v_status.set(getattr(flow, "last_hint", "文件已准备好，可以保存、复制或直连 MIDAS。"))
            self.lbl_status.configure(style="Ok.TLabel")
        elif issues:
            self.v_status.set("请先修正：" + "；".join(issues[:3]) + (" …" if len(issues) > 3 else ""))
            self.lbl_status.configure(style="Err.TLabel")
        else:
            self.v_status.set("这一步没问题，可以进入下一步。")
            self.lbl_status.configure(style="Ok.TLabel")

        self.btn_next.configure(state=("disabled" if (issues or last) else "normal"),
                                text=("生成成果" if self.step == len(ids) - 2 else "确定，下一步"))
        self.btn_prev.configure(state=("disabled" if self.step == 0 else "normal"))
        self.refresh_step_list()
        self.canvas.yview_moveto(0)

    def refresh_step_list(self):
        flow = self.flow()
        ids = list(flow.step_ids)
        titles = list(flow.step_titles)
        self.step_list.delete(0, "end")
        for i, sid in enumerate(ids):
            try:
                ok = not [x for x in (flow.issues(sid) or []) if not is_advisory(x)]
            except Exception:                                   # noqa: BLE001
                ok = False
            mark = "✓" if (i < self.step and ok) else "　"
            self.step_list.insert("end", "%s %d. %s" % (mark, i + 1, titles[i]))
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
        flow = self.flow()
        ids = list(flow.step_ids)
        issues = [x for x in (flow.issues(ids[self.step]) or []) if not is_advisory(x)]
        if issues:
            messagebox.showwarning("还不能进入下一步", "\n".join(issues[:8]))
            return
        self.save_session()
        if self.step < len(ids) - 1:
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
        flow = self.flow()
        sid = list(flow.step_ids)[self.step]
        title = flow.clear_title(sid) if hasattr(flow, "clear_title") else ""
        if not title:
            messagebox.showinfo("提示", "这一步是生成成果，没有可清空的数据。")
            return
        if not messagebox.askyesno("清空本步", "确定清空「%s」这一步的输入吗？" % title):
            return
        flow.clear(sid)
        self._mode_cache[self.mode] = self.model
        self.save_session()
        self.bundle_files = None
        self.render()
        self.v_status.set("已清空「%s」，可以重新填。" % title)
        self.lbl_status.configure(style="Ok.TLabel")

    def on_reset(self):
        if not messagebox.askyesno("清空全部", "确定清空所有步骤的输入吗？\n\n"
                                   "会变成本流程的初始模型（不是空模型的话会回到默认参数），从头开始填。"):
            return
        flow = self.flow()
        try:
            self.model = flow.reset_model() if hasattr(flow, "reset_model") else flow.default_model()
        except Exception as exc:                                # noqa: BLE001
            messagebox.showerror("清空失败", str(exc))
            return
        prj = self.model.setdefault("project", {})
        self.v_project.set(prj.get("name", ""))
        self.v_force.set(prj.get("force", "KN"))
        self.v_dist.set(prj.get("dist", "M"))
        self.v_styp.set(dict(STYPES).get(prj.get("styp", "1"), STYPES[0][1]))
        self.step = 0
        self.saved_path = None
        self.bundle_files = None
        self.result_files = None
        for attr in ("v_fname", "v_outdir", "v_encoding", "v_compat", "v_altmct"):
            if hasattr(self, attr):
                delattr(self, attr)
        self._mode_cache[self.mode] = self.model
        self.save_session()
        self.render()

    # ---------------- 会话存取 ----------------
    def save_session(self):
        try:
            os.makedirs(WORK_DIR, exist_ok=True)
            models = dict(getattr(self, "_mode_cache", {}) or {})
            models[self.mode] = self.model
            payload = {"format": 2, "mode": self.mode, "models": models}
            with open(SESSION_FILE, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def load_session(self):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if data.get("format") == 2 and isinstance(data.get("models"), dict):
            cache = {}
            for code, model in data["models"].items():
                if code in self._flow_classes and isinstance(model, dict):
                    cache[code] = model
            self._mode_cache = cache
            mode = txt(data.get("mode")) or "beam"
            self.mode = mode if (mode in self._flow_classes and mode in cache) else "beam"
            if self.mode not in cache:
                self.model = self._flow_classes[self.mode](self).default_model()
                cache[self.mode] = self.model
            else:
                self.model = cache[self.mode]
        elif "nodes" in data or "arch" in data:
            # 旧版会话文件：只有一份模型
            code = "arch" if "arch" in data else "beam"
            if code not in self._flow_classes:
                code = "beam"
            self.mode = code
            self.model = data
            self._mode_cache = {code: data}
        else:
            return
        if hasattr(self, "v_flow"):
            self.v_flow.set(self._flow_label_of(self.mode))
        prj = self.model.setdefault("project", {})
        self.v_project.set(txt(prj.get("name")))
        self.v_force.set(prj.get("force", "KN"))
        self.v_dist.set(prj.get("dist", "M"))
        self.v_styp.set(dict(STYPES).get(prj.get("styp", "1"), STYPES[0][1]))

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
            mtype = mat.get("type") if mat.get("type") in ("CONC", "STEEL") else "CONC"
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
        choices = (["%s · %s" % (s[0], s[1]) for s in SHAPES]
                   + [TS_SHAPE + " · 变截面（i/j 端各引用一个等截面）",
                      CAD_SHAPE + " · 自定义（导入 CAD 文件）"])
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
        ttk.Button(btns, text="添加变截面（两端各引用一个等截面）", command=lambda: self._add_row("sections", {
            "name": "变截面", "shape": TS_SHAPE, "dims": [], "offset": "CT", "shear": "YES",
            "warp": "NO", "i_sect": "", "j_sect": "", "mirror": False})).pack(side="left", padx=(8, 0))

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
        if code == TS_SHAPE:
            sec.setdefault("i_sect", "")
            sec.setdefault("j_sect", "")
            sec.setdefault("mirror", False)
            if not txt(sec.get("offset")).strip():
                sec["offset"] = "CT"
        elif code != CAD_SHAPE:
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

        # ---------- 节点连接（可选）：主从节点 / 弹性连接 ----------
        c2 = self._card("节点连接（可选）：主从节点、弹性连接")
        ttk.Label(c2, text="主从节点＝刚性连接，用来模拟刚性横梁、挂梁两端、塔梁固结（从节点跟随主节点的勾选自由度）；"
                           "弹性连接＝两个节点之间加 6 向弹簧，可模拟支座、隔震装置、地基弹簧。",
                  style="Hint.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(0, 6))

        rl = self.model.setdefault("rigidlinks", [])
        if rl:
            g2 = self._table(c2, ["刚性连接", "主节点", "约束自由度（勾选＝从节点跟随）", "从节点号（可多个）", "边界组", ""])
            for i, r in enumerate(rl):
                row = i + 1
                ttk.Label(g2, text="%d" % row).grid(row=row, column=0, sticky="w", padx=(0, 10))
                self._cell(g2, ttk.Entry(g2, textvariable=self.svar(r, "master", r.get("master")), width=8), row, 1)
                dofs = ttk.Frame(g2); dofs.grid(row=row, column=2, sticky="w", padx=(0, 10))
                dof = r.setdefault("dof", {})
                for k, lab in (("dx", "Dx"), ("dy", "Dy"), ("dz", "Dz"), ("rx", "Rx"), ("ry", "Ry"), ("rz", "Rz")):
                    if k not in dof:
                        dof[k] = True
                    ttk.Checkbutton(dofs, text=lab, variable=self.bvar(dof, k, dof.get(k))).pack(side="left", padx=(0, 3))
                self._cell(g2, ttk.Entry(g2, textvariable=self.svar(r, "slaves", r.get("slaves")), width=14), row, 3)
                self._cell(g2, ttk.Entry(g2, textvariable=self.svar(r, "group", r.get("group")), width=10), row, 4)
                ttk.Button(g2, text="删除", width=5, command=lambda _i=i: self._del_link("rigidlinks", _i)).grid(
                    row=row, column=5, sticky="w")

        el = self.model.setdefault("elasticlinks", [])
        if el:
            g3 = self._table(c2, ["弹性连接", "节点 1", "节点 2", "SDx", "SDy", "SDz", "SRx", "SRy", "SRz", "边界组", ""])
            for i, e in enumerate(el):
                row = i + 1
                ttk.Label(g3, text="%d" % row).grid(row=row, column=0, sticky="w", padx=(0, 8))
                self._cell(g3, ttk.Entry(g3, textvariable=self.svar(e, "n1", e.get("n1")), width=6), row, 1)
                self._cell(g3, ttk.Entry(g3, textvariable=self.svar(e, "n2", e.get("n2")), width=6), row, 2)
                for c, k in enumerate(("sdx", "sdy", "sdz", "srx", "sry", "srz"), start=3):
                    self._cell(g3, ttk.Entry(g3, textvariable=self.svar(e, k, e.get(k)), width=9), row, c)
                self._cell(g3, ttk.Entry(g3, textvariable=self.svar(e, "group", e.get("group")), width=9), row, 9)
                ttk.Button(g3, text="删除", width=5, command=lambda _i=i: self._del_link("elasticlinks", _i)).grid(
                    row=row, column=10, sticky="w")

        lb = ttk.Frame(c2); lb.pack(fill="x", pady=(6, 0))
        ttk.Button(lb, text="＋ 添加刚性连接（主从节点）", command=lambda: self._add_link("rigidlinks")).pack(side="left")
        ttk.Button(lb, text="＋ 添加弹性连接", command=lambda: self._add_link("elasticlinks")).pack(side="left", padx=(8, 0))
        ttk.Label(c2, text="刚度单位：力/长度（kN/m）和力·长度/弧度（kN·m/rad）；不约束的方向填 0。"
                           "刚性连接一般 6 个自由度全勾；只连平动（如斜拉索与主梁）就只勾 Dx Dy Dz。",
                  style="Opt.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))

        self._bearing_card(c2)

    # ---------------- 板式橡胶支座刚度计算器 ----------------
    def _bearing_vars(self):
        """计算器的输入（存在模型里，切换流程/重开窗口都还在）。"""
        bc = self.model.setdefault("bearing_calc", {})
        for k, v in (("a", "300"), ("b", "600"), ("t1", "11"), ("te", "57"), ("Ge", "1.0"), ("S", "")):
            bc.setdefault(k, v)
        return bc

    def _bearing_card(self, parent):
        bc = self._bearing_vars()
        box = ttk.Labelframe(parent, text="板式橡胶支座刚度计算（规范法，算完可一键填入上面的弹性连接）", padding=(8, 6))
        box.pack(fill="x", pady=(10, 0))
        r1 = ttk.Frame(box); r1.pack(fill="x")
        for lab, key, width in (("平面 a (mm)", "a", 6), ("平面 b (mm)", "b", 6),
                                ("中间层橡胶厚 t1 (mm)", "t1", 6), ("橡胶层总厚 te (mm)", "te", 6),
                                ("剪变模量 Ge (MPa)", "Ge", 6)):
            ttk.Label(r1, text=lab).pack(side="left", padx=(0, 2))
            ttk.Entry(r1, textvariable=self.svar(bc, key, bc.get(key)), width=width).pack(side="left", padx=(0, 10))
        r2 = ttk.Frame(box); r2.pack(fill="x", pady=(4, 0))
        ttk.Label(r2, text="形状系数 S（留空＝按 S = a·b / [2·t1·(a+b)] 自动算；也可按 GJZ 规格表手填）").pack(side="left")
        ttk.Entry(r2, textvariable=self.svar(bc, "S", bc.get("S")), width=8).pack(side="left", padx=(6, 0))
        r3 = ttk.Frame(box); r3.pack(fill="x", pady=(6, 0))
        ttk.Button(r3, text="① 计算刚度", command=self._bearing_calc).pack(side="left")
        ttk.Button(r3, text="② 填入「弹性连接」", command=self._bearing_fill).pack(side="left", padx=(8, 0))
        ttk.Label(r3, text="（竖向刚度填 SDx；连接节点顺序决定局部 x 轴：节点 1 → 节点 2 为 x）",
                  style="Opt.TLabel").pack(side="left", padx=(8, 0))
        self.v_bear_result = tk.StringVar(value="填入尺寸后点「计算刚度」。")
        ttk.Label(box, textvariable=self.v_bear_result, style="Hint.TLabel", wraplength=860,
                  justify="left").pack(anchor="w", pady=(4, 0))

    @staticmethod
    def _bearing_numbers(bc):
        """按课件第 52/138 页（P66）公式 + 桥规取值算支座 6 个刚度。

        S = a·b / [2·t1·(a+b)]（矩形）；Ee = 5.4·Ge·S²；A_k = 5/6·A；
        SDx = Ee·A/te，SDy=SDz = Ge·A_k/te，SRx = Ge·Ip/te，SRy = Ee·Iy/te，SRz = Ee·Iz/te。
        """
        a = float(bc.get("a") or 0); b = float(bc.get("b") or 0)
        t1 = float(bc.get("t1") or 0); te = float(bc.get("te") or 0)
        Ge = float(bc.get("Ge") or 0)
        s_txt = txt(bc.get("S")).strip()
        S = float(s_txt) if s_txt else (a * b / (2.0 * t1 * (a + b)) if t1 > 0 and (a + b) > 0 else 0.0)
        Ee = 5.4 * Ge * S * S
        A = a * b / 1e6                                  # m2
        Ak = A * 5.0 / 6.0                               # 矩形剪切面积
        Iy = b * a ** 3 / 12.0 / 1e12                    # m4（绕 y 轴）
        Iz = a * b ** 3 / 12.0 / 1e12                    # m4（绕 z 轴）
        aa, bb = max(a, b), min(a, b)
        r = bb / aa if aa > 0 else 0.0
        Ip = aa * bb ** 3 * (1.0 / 3.0 - 0.21 * r * (1.0 - r ** 4 / 12.0)) / 1e12   # m4（矩形扭转常数）
        return {"a": a, "b": b, "t1": t1, "te": te, "Ge": Ge, "S": S, "Ee": Ee,
                "A": A, "Ak": Ak, "Iy": Iy, "Iz": Iz, "Ip": Ip}

    def _bearing_text(self, d):
        return ("S = %.4g，Ee = 5.4·Ge·S² = %.4g MPa，A = %.4g m²，A_k = %.4g m²\n"
                "SDx（竖向/轴向）= %.4g kN/m    SDy = SDz（水平）= %.4g kN/m\n"
                "SRx（绕轴向扭转）= %.4g kN·m/rad    SRy = %.4g    SRz = %.4g kN·m/rad"
                % (d["S"], d["Ee"], d["A"], d["Ak"], d["Kx"], d["Ky"], d["Rx"], d["Ry"], d["Rz"]))

    def _bearing_calc(self, quiet=False):
        bc = self._bearing_vars()
        try:
            d = self._bearing_numbers(bc)
        except (TypeError, ValueError):
            if not quiet:
                messagebox.showwarning("参数不对", "a、b、t1、te、Ge、S 都要填数字。")
            return None
        if min(d["a"], d["b"], d["te"], d["Ge"], d["S"]) <= 0:
            if not quiet:
                messagebox.showwarning("参数不对", "平面尺寸、te、Ge 都要大于 0；留空自动算 S 时 t1 也要大于 0。")
            return None
        te = d["te"] / 1000.0
        ec = d["Ee"] * 1000.0                            # MPa → kN/m²
        gc = d["Ge"] * 1000.0
        d["Kx"] = ec * d["A"] / te
        d["Ky"] = gc * d["Ak"] / te
        d["Rx"] = gc * d["Ip"] / te
        d["Ry"] = ec * d["Iy"] / te
        d["Rz"] = ec * d["Iz"] / te
        self._bearing_result = d
        if hasattr(self, "v_bear_result"):
            self.v_bear_result.set(self._bearing_text(d))
        if not quiet:
            self.v_status.set("支座刚度算好了：SDx=%.4g kN/m，SDy=SDz=%.4g kN/m（见卡片下方）。"
                              % (d["Kx"], d["Ky"]))
            self.lbl_status.configure(style="Ok.TLabel")
        return d

    def _bearing_fill(self):
        d = self._bearing_calc(quiet=True)
        if not d or not getattr(d, "get", None) or "Kx" not in d:
            if not d:
                messagebox.showwarning("参数不对", "先把 a、b、t1、te、Ge 填成大于 0 的数。")
            return
        result_text = self._bearing_text(d)
        self.model.setdefault("elasticlinks", []).append({
            "n1": "", "n2": "",
            "sdx": numg(d["Kx"]), "sdy": numg(d["Ky"]), "sdz": numg(d["Ky"]),
            "srx": numg(d["Rx"]), "sry": numg(d["Ry"]), "srz": numg(d["Rz"]),
            "group": ""})
        self.save_session()
        self.render()
        messagebox.showinfo("已填入弹性连接", "已在上面「弹性连接」里加了一行（节点号自己填）：\n\n"
                            + result_text + "\n\n提示：竖向连接时节点 1→2 是局部 x 轴，SDx 就是竖向刚度。")

    def _add_link(self, key):
        if key == "rigidlinks":
            self.model.setdefault("rigidlinks", []).append({
                "master": "", "slaves": "",
                "dof": {"dx": True, "dy": True, "dz": True, "rx": False, "ry": False, "rz": False},
                "group": ""})
        else:
            self.model.setdefault("elasticlinks", []).append({
                "n1": "", "n2": "", "sdx": "1e8", "sdy": "1e8", "sdz": "1e8",
                "srx": "0", "sry": "0", "srz": "0", "group": ""})
        self.render()

    def _del_link(self, key, idx):
        try:
            self.model[key].pop(idx)
        except (KeyError, IndexError):
            return
        self.render()

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

        self._analysis_card()

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

    # ---------------- 分析设置（实验功能，默认不写） ----------------
    def _an_get(self, kind, key, default=""):
        d = (self.model.get("analysis") or {}).get(kind) or {}
        v = d.get(key)
        return default if v is None else v

    def _an_var(self, kind, key, default=""):
        v = tk.StringVar(value=txt(self._an_get(kind, key, default)))

        def write(*_):
            an = self.model.setdefault("analysis", {})
            an.setdefault(kind, {})[key] = v.get()

        v.trace_add("write", write)
        return v

    def _an_bvar(self, kind, key, default=False):
        v = tk.BooleanVar(value=bool(self._an_get(kind, key, default)))

        def write(*_):
            an = self.model.setdefault("analysis", {})
            an.setdefault(kind, {})[key] = bool(v.get())

        v.trace_add("write", write)
        return v

    def _auto_pk(self):
        """按《公路桥涵设计通用规范》车道荷载：Pk 在 5 m 内取 180 kN、50 m 以上取 360 kN，中间线性插值。"""
        xs = []
        for nd in self.model.get("nodes", []):
            if is_num(nd.get("x")):
                xs.append(float(nd["x"]))
        if len(xs) < 2:
            messagebox.showwarning("缺支点", "先在前面把支点填好，才能按跨径算 Pk。")
            return
        L = max(xs) - min(xs)
        if L <= 5:
            pk = 180.0
        elif L >= 50:
            pk = 360.0
        else:
            pk = 180.0 + (L - 5.0) * (360.0 - 180.0) / 45.0
        self.v_an_pk.set(num(round(pk, 1)))
        self.v_status.set("按计算跨径 %.3g m 算得 Pk = %.1f kN（规范：≤5 m 取 180，≥50 m 取 360，中间线性）。"
                          % (L, round(pk, 1)))
        self.lbl_status.configure(style="Ok.TLabel")

    def _analysis_card(self, kinds=("moving", "eigen", "buckle")):
        """第 8 步底部的「分析设置」：移动荷载 / 模态 / 屈曲（实验功能，默认不写）。

        kinds 只画传进来的那几块：梁桥三块全画；拱桥 / 斜拉桥用 ("eigen", "buckle")。
        """
        zh = {"moving": "移动荷载", "eigen": "模态", "buckle": "屈曲"}
        box = self._card("分析设置（可选）：%s —— 实验功能" % " / ".join(zh[k] for k in kinds))
        ttk.Label(box, text="这几项照 MIDAS 官方 MCT 手册的字段写，本机没有可对照的导出样本，所以"
                            "默认都不勾：不勾就一个字也不写，生成结果和以前完全一样。"
                            "勾上后导入若报错，把 MIDAS 命令行号发作者，按行号改。",
                  style="Hint.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(0, 8))

        if "moving" in kinds:
            ttk.Checkbutton(box, text="① 移动荷载（车道 + 车辆 + 移动荷载工况，对应第 06 讲）",
                            variable=self._an_bvar("moving", "on", False)).pack(anchor="w")
            r1 = ttk.Frame(box); r1.pack(fill="x", pady=(4, 0))
            for lab, key, dflt, width in (("车道名", "lane", "LANE1", 10), ("偏心(m)", "ecc", "0", 7),
                                          ("冲击系数0~0.3", "impact", "0", 6), ("车辆名", "vname", "CH-I", 10)):
                ttk.Label(r1, text=lab).pack(side="left", padx=(0, 2))
                ttk.Entry(r1, textvariable=self._an_var("moving", key, dflt), width=width).pack(side="left", padx=(0, 10))
            r2 = ttk.Frame(box); r2.pack(fill="x", pady=(4, 0))
            ttk.Label(r2, text="车道均布 qk (kN/m)").pack(side="left", padx=(0, 2))
            ttk.Entry(r2, textvariable=self._an_var("moving", "qk", "10.5"), width=8).pack(side="left", padx=(0, 10))
            ttk.Label(r2, text="集中 Pk (kN)").pack(side="left", padx=(0, 2))
            self.v_an_pk = self._an_var("moving", "pk", "360")
            ttk.Entry(r2, textvariable=self.v_an_pk, width=8).pack(side="left", padx=(0, 4))
            ttk.Button(r2, text="按跨径算 Pk", command=self._auto_pk).pack(side="left", padx=(0, 10))
            for lab, key, dflt, width in (("移动荷载工况名", "lc", "MV", 10), ("车辆组名", "vclass", "VC1", 8)):
                ttk.Label(r2, text=lab).pack(side="left", padx=(0, 2))
                ttk.Entry(r2, textvariable=self._an_var("moving", key, dflt), width=width).pack(side="left", padx=(0, 10))
            ttk.Label(box, text="公路-I级：qk=10.5 kN/m；Pk 随跨径变（≤5 m 取 180 kN，≥50 m 取 360 kN，中间线性插值）。"
                                "程序把车道铺在全部梁单元上，偏心按梁中心到车道中心的距离填。",
                      style="Opt.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(2, 10))

        if "eigen" in kinds:
            r3 = ttk.Frame(box); r3.pack(fill="x", pady=(6, 0))
            ttk.Checkbutton(r3, text="② 模态分析（特征值，对应第 07 讲）",
                            variable=self._an_bvar("eigen", "on", False)).pack(side="left")
            ttk.Label(r3, text="提取阶数").pack(side="left", padx=(10, 2))
            ttk.Entry(r3, textvariable=self._an_var("eigen", "n", "10"), width=6).pack(side="left", padx=(0, 10))
            ttk.Checkbutton(r3, text="旧版 4 字段写法（给老 MIDAS）",
                            variable=self._an_bvar("eigen", "legacy", False)).pack(side="left")

        if "buckle" in kinds:
            r4 = ttk.Frame(box); r4.pack(fill="x", pady=(8, 0))
            ttk.Checkbutton(r4, text="③ 屈曲分析（对应第 07 讲）",
                            variable=self._an_bvar("buckle", "on", False)).pack(side="left")
            ttk.Label(r4, text="模态数").pack(side="left", padx=(10, 2))
            ttk.Entry(r4, textvariable=self._an_var("buckle", "n", "3"), width=6).pack(side="left", padx=(0, 10))
            ttk.Label(r4, text="可变荷载（工况名 系数，例：DL 1.0）").pack(side="left")
            ttk.Entry(r4, textvariable=self._an_var("buckle", "lcs", ""), width=34).pack(side="left", padx=(6, 0))

    # ---------------- 成果 / 工程 ----------------
    def out_root(self):
        """成果根目录（插件流程的 out_dir() 用它兜底）。"""
        return WORK_DIR

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
        except Exception as exc:                    # 程序内部错误也要让人看得见
            messagebox.showerror("生成失败（程序内部错误）",
                                 "%s: %s\n\n把这段信息发给我，我照着修。" % (type(exc).__name__, exc))
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
        if not isinstance(data, dict) or not isinstance(data.get("project"), dict):
            messagebox.showerror("打开失败", "这个文件不是向导工程文件。")
            return
        code = txt(data.get("flow")) or ("arch" if "arch" in data else "beam")
        if code not in self._flow_classes:
            code = "beam"
        self._mode_cache[self.mode] = self.model
        self.mode = code
        self.model = data
        self._mode_cache[code] = data
        if hasattr(self, "v_flow"):
            self.v_flow.set(self._flow_label_of(code))
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
            self.model["flow"] = self.mode          # 打开时靠它自动切回本流程
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
        except Exception as exc:                    # 程序内部错误也要让人看得见
            messagebox.showerror("生成失败（程序内部错误）",
                                 "%s: %s\n\n把这段信息发给我，我照着修。" % (type(exc).__name__, exc))
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


    # ==================================================================
    # 拱桥 · 施工阶段联合截面（第二条流程）
    # ==================================================================
    def _is_arch(self):
        return getattr(self, "mode", "beam") == "arch"

    def _arch(self):
        """取拱桥模型（当前模型不是拱桥就换默认拱桥模型）。"""
        if "arch" not in self.model:
            self.model = arch_core.default_model()
        return self.model

    def arch_stage_names(self):
        return [txt(s.get("name")).strip() for s in self._arch().get("stages", [])]

    def _switch_mode(self, mode):
        """切换流程：每个流程各自保留自己的输入，切来切去不丢。"""
        if mode == getattr(self, "mode", "beam") or mode not in self._flow_classes:
            return
        if mode == "arch" and arch_core is None:
            messagebox.showerror("缺少文件", "同目录下找不到 arch_core.py，拱桥流程用不了。\n"
                                             "把 arch_core.py 和本程序放在同一个文件夹里再试。")
            if hasattr(self, "v_flow"):
                self.v_flow.set(self._flow_label_of(self.mode))
            return
        cache = self._mode_cache
        cache[self.mode] = self.model            # 存住当前流程的输入，切回来还在
        self.mode = mode
        self.model = cache.get(mode)
        if self.model is None:
            try:
                self.model = self.flow().default_model()
            except Exception as exc:                            # noqa: BLE001
                import traceback
                traceback.print_exc()
                messagebox.showerror("流程初始化失败", "这个流程起不来：%s\n\n"
                                     "多半是同目录下缺了它的引擎文件（flow_*.py / *_core.py）。" % exc)
                self.mode = "beam"
                self.model = cache.get("beam") or default_model()
        cache[self.mode] = self.model
        if hasattr(self, "v_flow"):
            self.v_flow.set(self._flow_label_of(self.mode))
        self.step = 0
        self.bundle_files = None
        self.saved_path = None
        for attr in ("v_fname", "v_outdir", "v_encoding", "v_compat", "v_altmct"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.save_session()
        self.render()

    # ---------------- 校验 ----------------
    def arch_issues(self, sid):
        m = self._arch()
        a = m.get("arch", {})
        out = []
        if sid == "arch_axis":
            try:
                L = float(a.get("L")); f = float(a.get("f"))
            except (TypeError, ValueError):
                return ["跨径和矢高都要填数字。"]
            if L <= 0:
                out.append("计算跨径要大于 0。")
            if f <= 0:
                out.append("计算矢高要大于 0。")
            if f >= L:
                out.append("矢高比跨径还大，检查一下单位（应为 m）。")
            try:
                n = int(float(a.get("n")))
                if n < 8 or n > 400:
                    out.append("单元数建议 8~400（现在 %d）。" % n)
            except (TypeError, ValueError):
                out.append("单元数要填整数。")
            if a.get("type") == "catenary":
                try:
                    mm = float(a.get("m"))
                    if mm <= 1.0:
                        out.append("悬链线的拱轴系数 m 必须大于 1。")
                    elif mm > 5:
                        out.append("拱轴系数 m=%g 太大了（常用 1.1~3），确认一下。" % mm)
                except (TypeError, ValueError):
                    out.append("拱轴系数 m 要填数字。")
        elif sid == "arch_section":
            sec = arch_core.get_section(m)
            out += sec.validate()
            names = self.arch_stage_names()
            for p in m.get("parts", []):
                cs = txt(p.get("composite_stage")).strip()
                if cs and cs not in names:
                    out.append("部件「%s」的参与受力阶段「%s」不在阶段表里。" % (p.get("name"), cs))
        elif sid == "arch_export":
            if arch_core is not None:
                out += analysis_core.issues(m, kinds=("eigen", "buckle"),
                                            loadcase_names=arch_core.loadcase_names(m))
        elif sid == "arch_stages":
            stages = m.get("stages", [])
            if not stages:
                out.append("至少要有一个施工阶段。")
            seen = set()
            for i, st in enumerate(stages, start=1):
                nm = txt(st.get("name")).strip()
                if not nm:
                    out.append("第 %d 个阶段没有名字。" % i)
                elif nm in seen:
                    out.append("阶段名「%s」重复了。" % nm)
                seen.add(nm)
                try:
                    if float(st.get("days", 0)) <= 0:
                        out.append("阶段「%s」的天数要大于 0。" % nm)
                except (TypeError, ValueError):
                    out.append("阶段「%s」的天数要填数字。" % nm)
            for p in m.get("parts", []):
                cs = txt(p.get("composite_stage")).strip()
                if cs and cs not in seen:
                    out.append("部件「%s」的参与受力阶段「%s」不在阶段表里。" % (p.get("name"), cs))
            try:
                if float(m.get("gamma")) <= 0:
                    out.append("容重要大于 0 kN/m³。")
            except (TypeError, ValueError):
                out.append("容重要填数字（kN/m³，混凝土一般 24~26）。")
            if m.get("creep"):
                try:
                    hv = float(m.get("humidity"))
                    if hv < 0 or hv > 100:
                        out.append("环境湿度要在 0~100 % 之间。")
                except (TypeError, ValueError):
                    out.append("环境湿度要填 0~100 之间的数字。")
            for p in m.get("parts", []):
                try:
                    if float(p.get("age", 0)) < 0:
                        out.append("部件「%s」的龄期不能是负数。" % p.get("name"))
                except (TypeError, ValueError):
                    out.append("部件「%s」的龄期要填数字（天）。" % p.get("name"))
        return out

    # ---------------- 第 1 步：拱轴线 ----------------
    def step_arch_axis(self):
        m = self._arch()
        a = m["arch"]
        box = self._card("拱轴线参数")
        g = ttk.Frame(box); g.pack(fill="x", anchor="w")

        # 拱轴类型
        _type_names = [t[1] for t in arch_core.ARCH_TYPES]
        _type_code = {t[0]: t[1] for t in arch_core.ARCH_TYPES}
        _type_back = {t[1]: t[0] for t in arch_core.ARCH_TYPES}
        ttk.Label(g, text="拱轴类型").grid(row=0, column=0, sticky="w", padx=(0, 4))
        v_type = tk.StringVar(value=_type_code.get(a.get("type", "catenary"), "悬链线"))
        cb = ttk.Combobox(g, textvariable=v_type, values=_type_names, width=8, state="readonly")
        cb.grid(row=0, column=1, sticky="w", padx=(0, 18))

        def _set_type(*_):
            a["type"] = _type_back.get(v_type.get(), "catenary")
            self.render()
        cb.bind("<<ComboboxSelected>>", _set_type)

        # 数值参数
        _fields = [("计算跨径 L (m)", "L", 8), ("计算矢高 f (m)", "f", 8),
                   ("拱轴系数 m", "m", 6), ("拱脚标高 (m)", "z0", 8), ("单元数", "n", 6)]
        entries = []
        col = 2
        for lab, key, w in _fields:
            ttk.Label(g, text=lab).grid(row=0, column=col, sticky="w", padx=(0, 4))
            e = ttk.Entry(g, textvariable=self.svar(a, key, a.get(key)), width=w)
            e.grid(row=0, column=col + 1, sticky="w", padx=(0, 18))
            entries.append(e)
            col += 2

        # 拱脚约束
        ttk.Label(g, text="拱脚约束").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(8, 0))
        v_fix = tk.StringVar(value="无铰（固结）" if a.get("fixed", True) else "两铰")
        cbf = ttk.Combobox(g, textvariable=v_fix, values=["无铰（固结）", "两铰"], width=10, state="readonly")
        cbf.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(8, 0))

        def _set_fix(*_):
            a["fixed"] = (v_fix.get().startswith("无铰"))
            self.render()
        cbf.bind("<<ComboboxSelected>>", _set_fix)

        # 摘要 + 预览（改数字立刻重画，不用点「下一步」）
        def _axis_pts():
            try:
                return arch_core.arch_axis(float(a.get("L")), float(a.get("f")),
                                           float(a.get("m", 1.6)), a.get("type", "catenary"),
                                           int(float(a.get("n"))), float(a.get("z0", 0)))
            except Exception:
                return []

        def _axis_info(p):
            try:
                ln = arch_core.arch_length(p)
                return ("拱线长 %.2f m     拱顶标高 %.2f m     矢跨比 1/%.2f    节点 %d 个 / 单元 %d 个"
                        % (ln, float(a.get("z0", 0)) + float(a.get("f")),
                           float(a.get("L")) / float(a.get("f")) if float(a.get("f")) else 0,
                           len(p), len(p) - 1))
            except Exception:
                return "参数还不完整"

        pts = _axis_pts()
        info_lbl = ttk.Label(box, text=_axis_info(pts) if pts else "参数还不完整", style="Ok.TLabel")
        info_lbl.pack(anchor="w", pady=(8, 0))

        prev = self._card("拱轴线预览（改参数自动刷新）")
        cv = tk.Canvas(prev, height=190, bg="white", highlightthickness=1, highlightbackground="#cccccc")
        cv.pack(fill="x")
        self._draw_arch(cv, pts)

        def _refresh_axis(*_):
            p = _axis_pts()
            self._draw_arch(cv, p)
            info_lbl.configure(text=_axis_info(p) if p else "参数还不完整")

        cv.bind("<Configure>", _refresh_axis)          # 第一次显示 / 窗口缩放时按真实大小重画
        for _entry in entries:
            _entry.bind("<KeyRelease>", _refresh_axis)  # 改数字时实时刷新
            _entry.bind("<FocusOut>", _refresh_axis)
        ttk.Label(prev, text="X 为跨径方向，Z 竖直向上；左拱脚在 X=0。", style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

    def _draw_arch(self, cv, pts):
        cv.delete("all")
        w = max(cv.winfo_width(), 640)
        h = int(cv.winfo_height())
        if h < 60:                       # 窗口还没排好版时，先按「要求的高度」画，别画到画布外面
            h = int(cv.winfo_reqheight()) or 190
        if not pts:
            cv.create_text(w / 2, h / 2, text="（参数不完整，暂时画不出来）", fill="#999")
            return
        xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        z0, z1 = min(zs), max(zs)
        pad = 24
        sx = (w - 2 * pad) / (x1 - x0 or 1)
        sz = (h - 2 * pad) / ((z1 - z0) or 1)
        s = min(sx, sz)
        ox = pad + (w - 2 * pad - (x1 - x0) * s) / 2
        oy = h - pad

        def P(x, z):
            return (ox + (x - x0) * s, oy - (z - z0) * s)

        # 地面线
        cv.create_line(P(x0 - (x1 - x0) * 0.05, z0), P(x1 + (x1 - x0) * 0.05, z0), fill="#aaa", dash=(4, 3))
        # 拱轴
        poly = []
        for (x, z) in pts:
            poly += list(P(x, z))
        cv.create_line(*poly, fill="#2d7d46", width=3, smooth=False)
        # 拱脚
        for (x, z) in (pts[0], pts[-1]):
            px, py = P(x, z)
            cv.create_rectangle(px - 5, py - 5, px + 5, py + 5, fill="#c0392b", outline="")
        # 跨径/矢高标注
        p1, p2 = P(x0, z0), P(x1, z0)
        cv.create_line(*p1, *p2, arrow="both", fill="#666")
        cv.create_text((p1[0] + p2[0]) / 2, p1[1] + 12, text="跨径 %.2f m" % (x1 - x0), fill="#666")
        pc = P((x0 + x1) / 2, z0 + (z1 - z0))
        cv.create_line(pc[0], pc[1], pc[0], oy, arrow="both", fill="#666", dash=(3, 3))
        cv.create_text(pc[0] + 30, (pc[1] + oy) / 2, text="矢高 %.2f m" % (z1 - z0), fill="#666")

    # ---------------- 第 2 步：截面 ----------------
    def step_arch_section(self):
        m = self._arch()
        _defaults = {"W": 5.2, "cells": 3, "cell_w": 1.2, "cell_h": 1.0,
                     "t_top_pre": 0.10, "t_bot": 0.20, "t_out": 0.20,
                     "t_in": 0.10, "t_joint": 0.30, "t_top_cip": 0.15}
        if "box" not in m:
            m["box"] = dict(_defaults)
        b = m["box"]
        box = self._card("截面参数（按多箱室箱形截面自动生成，单位 m）")
        g = ttk.Frame(box); g.pack(fill="x", anchor="w")
        _spec = [("总宽 W（0=自动）", "W", 8), ("箱室数", "cells", 6), ("箱室净宽", "cell_w", 8), ("箱室净高", "cell_h", 8),
                 ("预制顶板厚", "t_top_pre", 8), ("底板厚", "t_bot", 8),
                 ("外腹板厚", "t_out", 8), ("内腹板厚", "t_in", 8),
                 ("纵缝宽", "t_joint", 8), ("现浇顶板厚", "t_top_cip", 8)]
        for i, (lab, key, wd) in enumerate(_spec):
            r, c = divmod(i, 5)
            ttk.Label(g, text=lab).grid(row=r * 2, column=c, sticky="w", padx=(0, 4), pady=(4, 0))
            ttk.Entry(g, textvariable=self.svar(b, key, b.get(key)), width=wd).grid(
                row=r * 2 + 1, column=c, sticky="w", padx=(0, 14))

        btn = ttk.Frame(box); btn.pack(fill="x", pady=(10, 0))
        ttk.Button(btn, text="① 按参数重新生成截面", command=self._arch_regen_section).pack(side="left")
        ttk.Label(btn, text="   生成后到下面改「参与受力阶段 / 龄期」即可；改参数要再点一次生成。",
                  style="Hint.TLabel").pack(side="left")

        # 预览（第一次显示 / 窗口缩放时按真实大小重画）
        prev = self._card("截面预览（颜色区分施工部件）")
        cv = tk.Canvas(prev, height=230, bg="white", highlightthickness=1, highlightbackground="#cccccc")
        cv.pack(fill="x")
        self._draw_section(cv, m)
        cv.bind("<Configure>", lambda _e: self._draw_section(cv, self._arch()))

        # 部件表
        sec = arch_core.get_section(m)
        sp = sec.props()
        box2 = self._card("施工部件（谁先浇、谁后参与受力）")
        info = ttk.Frame(box2); info.pack(fill="x")
        ttk.Label(info, text="总面积 A = %.4f m²，形心高 %.4f m，Iyy = %.4f m⁴，Izz = %.4f m⁴，总宽 %.2f m，总高 %.2f m"
                  % (sp["A"], sp["Cz"], sp["Iyy"], sp["Izz"], sp["B"], sp["H"]),
                  style="Ok.TLabel").pack(anchor="w")
        ttk.Label(info, text="总宽是按上面的尺寸自动排出来的；要凑成想要的总宽，改「箱室净宽」「纵缝宽」或「外腹板厚」再点一次生成。",
                  style="Hint.TLabel").pack(anchor="w")

        stages = self.arch_stage_names() or ["CS1"]
        g2 = self._table(box2, ["部件", "名称", "面积 m²", "参与受力阶段", "龄期 d"])
        for i, part in enumerate(m.get("parts", [])):
            r = i + 1
            ttk.Label(g2, text="部件 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
            self._cell(g2, ttk.Entry(g2, textvariable=self.svar(part, "name", part.get("name")), width=16), r, 1)
            ttk.Label(g2, text="%.4f" % (sec.parts[i].area if i < len(sec.parts) else 0)).grid(
                row=r, column=2, sticky="w", padx=(0, 12))
            combo = ttk.Combobox(g2, textvariable=self.svar(part, "composite_stage",
                                                            part.get("composite_stage", stages[0])),
                                 values=stages, width=10, state="readonly")
            self._cell(g2, combo, r, 3)
            self._cell(g2, ttk.Entry(g2, textvariable=self.svar(part, "age", part.get("age", 20)), width=8), r, 4)
        ttk.Label(box2, text="部件 1 就是「预制部分」，它在第一个阶段就参与受力，所以阶段那一栏可以不管；"
                             "后面每个部件选它硬化后开始参与受力的阶段。",
                  style="Hint.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(6, 0))


    def _arch_regen_section(self):
        m = self._arch()
        b = m.get("box", {})
        try:
            sec = arch_core.box_parts(
                W=float(b.get("W", 5.2)), cells=int(float(b.get("cells", 3))),
                cell_w=float(b.get("cell_w", 1.2)), cell_h=float(b.get("cell_h", 1.0)),
                t_top_pre=float(b.get("t_top_pre", 0.10)), t_bot=float(b.get("t_bot", 0.20)),
                t_out=float(b.get("t_out", 0.20)), t_in=float(b.get("t_in", 0.10)),
                t_joint=float(b.get("t_joint", 0.30)), t_top_cip=float(b.get("t_top_cip", 0.15)),
                material=m.get("material", "C50"))
        except (TypeError, ValueError) as exc:
            messagebox.showwarning("参数不对", "截面参数都要填数字：%s" % exc)
            return
        stages = self.arch_stage_names() or ["CS1", "CS2", "CS3", "CS4"]
        comp = [stages[0],
                stages[min(2, len(stages) - 1)] if len(stages) > 1 else stages[0],
                stages[min(3, len(stages) - 1)] if len(stages) > 1 else stages[0]]
        ages = [stages[0], 20, 30]
        m["box"] = dict(m.get("box") or {})
        m["box"].update({"cells": b.get("cells"), "cell_w": b.get("cell_w"), "cell_h": b.get("cell_h"),
                         "t_top_pre": b.get("t_top_pre"), "t_bot": b.get("t_bot"),
                         "t_out": b.get("t_out"), "t_in": b.get("t_in"),
                         "t_joint": b.get("t_joint"), "t_top_cip": b.get("t_top_cip")})
        m["parts"] = [
            {"name": p.name, "rects": [list(r) for r in p.rects],
             "composite_stage": comp[k] if k < len(comp) else stages[0],
             "age": 5 if k == 0 else (20 if k == 1 else 30)}
            for k, p in enumerate(sec.parts)]
        m["box"] = sec.box
        m["section_name"] = sec.name
        self.save_session()
        self.render()

    def _draw_section(self, cv, m):
        cv.delete("all")
        try:
            sec = arch_core.get_section(m)
        except Exception:
            return
        if not sec.parts:
            return
        x1, x2, z1, z2 = sec.bbox
        w = max(cv.winfo_width(), 640)
        h = int(cv.winfo_height())
        if h < 60:                       # 同上：先按要求高度画
            h = int(cv.winfo_reqheight()) or 230
        pad = 30
        s = min((w - 2 * pad) / ((x2 - x1) or 1), (h - 2 * pad) / ((z2 - z1) or 1))

        def P(x, z):
            return (pad + (x - x1) * s, h - pad - (z - z1) * s)

        colors = ["#cfe3f7", "#f7e0cf", "#d7f0d9", "#efe0f7", "#f7f7d0"]
        for k, part in enumerate(sec.parts):
            for (a, b_, c, d) in part.rects:
                p1, p2 = P(a, d), P(b_, c)
                cv.create_rectangle(p1[0], p1[1], p2[0], p2[1],
                                    fill=colors[k % len(colors)], outline="#333")
            # 图例
            lx = w - 150
            ly = 18 + k * 20
            cv.create_rectangle(lx, ly, lx + 14, ly + 12, fill=colors[k % len(colors)], outline="#333")
            cv.create_text(lx + 20, ly + 6, text="部件 %d：%s" % (k + 1, part.name), anchor="w")
        # 尺寸标注
        cv.create_line(*P(x1, z1), *P(x2, z1), arrow="both", fill="#666")
        cv.create_text((P(x1, z1)[0] + P(x2, z1)[0]) / 2, P(x1, z1)[1] + 12,
                       text="总宽 %.3f m" % (x2 - x1), fill="#666")
        cv.create_line(*P(x2, z1), *P(x2, z2), arrow="both", fill="#666")
        cv.create_text(P(x2, z2)[0] + 26, (P(x2, z1)[1] + P(x2, z2)[1]) / 2,
                       text="高 %.3f m" % (z2 - z1), fill="#666")

    # ---------------- 第 3 步：材料与施工阶段 ----------------
    def step_arch_stages(self):
        m = self._arch()
        box = self._card("材料与时间依存")
        g = ttk.Frame(box); g.pack(fill="x", anchor="w")
        ttk.Label(g, text="混凝土等级").grid(row=0, column=0, sticky="w", padx=(0, 4))
        mats = [k for k in arch_core.E_CONC]
        ttk.Combobox(g, textvariable=self.svar(m, "material", m.get("material", "C50")),
                     values=mats, width=8, state="readonly").grid(row=1, column=0, sticky="w", padx=(0, 24))
        ttk.Label(g, text="容重 (kN/m³)").grid(row=0, column=1, sticky="w", padx=(0, 4))
        ttk.Entry(g, textvariable=self.svar(m, "gamma", m.get("gamma", 25)), width=8).grid(
            row=1, column=1, sticky="w", padx=(0, 24))
        ttk.Label(g, text="环境湿度 (%)").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Entry(g, textvariable=self.svar(m, "humidity", m.get("humidity", 70)), width=8).grid(
            row=1, column=2, sticky="w", padx=(0, 24))
        v_creep = tk.BooleanVar(value=bool(m.get("creep")))

        def _set_creep():
            m["creep"] = bool(v_creep.get())
            self.save_session()

        ttk.Checkbutton(g, text="考虑收缩徐变（时间依存，CEB-FIP）", variable=v_creep,
                        command=_set_creep).grid(row=1, column=3, sticky="w")
        ttk.Label(box, justify="left", wraplength=820, style="Hint.TLabel",
                  text="材料等级影响弹性模量和 28 天抗压强度；容重影响自重和湿重（湿重 = 后浇部件面积×容重，程序自动加/卸）。\n"
                       "勾选「收缩徐变」后，程序会写 CEB-FIP 时间依存材料（*TDM-TYPE / *TDM-ELAST / *TDM-LINK），"
                       "并在施工阶段分析控制里打开时间依存开关；分析会比不勾选慢一些。环境湿度只在勾选时起作用（默认 70%）。"
                  ).pack(anchor="w", pady=(10, 0))

        box2 = self._card("施工阶段")
        g2 = self._table(box2, ["阶段", "名称", "持续天数", "这一步在做什么"])
        for i, st in enumerate(m.get("stages", [])):
            r = i + 1
            ttk.Label(g2, text="阶段 %d" % (i + 1)).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=3)
            self._cell(g2, ttk.Entry(g2, textvariable=self.svar(st, "name", st.get("name")), width=10), r, 1)
            self._cell(g2, ttk.Entry(g2, textvariable=self.svar(st, "days", st.get("days")), width=8), r, 2)
            self._cell(g2, ttk.Entry(g2, textvariable=self.svar(st, "desc", st.get("desc")), width=46), r, 3)
        bar = ttk.Frame(box2); bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="增加一个阶段", command=self._arch_add_stage).pack(side="left")
        ttk.Button(bar, text="按部件自动重排阶段", command=self._arch_reset_stages).pack(side="left", padx=(8, 0))
        ttk.Label(bar, text="   默认流程：预制合龙 → 浇纵缝 → 纵缝硬化+浇顶板 → 顶板硬化。",
                  style="Hint.TLabel").pack(side="left")

    def _arch_add_stage(self):
        m = self._arch()
        m.setdefault("stages", []).append({"name": "CS%d" % (len(m["stages"]) + 1),
                                           "days": 20, "age": 20, "desc": ""})
        self.save_session(); self.render()

    def _arch_reset_stages(self):
        m = self._arch()
        n = max(4, len(m.get("parts", [])) + 1)
        names = ["CS%d" % (i + 1) for i in range(n)]
        descs = ["预制拱圈安装、合龙", "浇筑纵缝混凝土（湿重）",
                 "纵缝硬化并参与受力、浇筑顶板（湿重）", "顶板硬化、全截面成拱"]
        m["stages"] = [{"name": names[i], "days": [5, 20, 20, 30][i] if i < 4 else 20,
                        "age": [5, 20, 20, 30][i] if i < 4 else 20,
                        "desc": descs[i] if i < len(descs) else ""} for i in range(n)]
        for k, p in enumerate(m.get("parts", [])):
            p["composite_stage"] = names[min(0 if k == 0 else k + 1, n - 1)]
            p["age"] = [5, 20, 30][k] if k < 3 else 20
        self.save_session(); self.render()

    # ---------------- 第 4 步：生成 / 直连 ----------------
    def step_arch_export(self):
        m = self._arch()
        if not hasattr(self, "v_arch_outdir"):
            self.v_arch_outdir = tk.StringVar(value=m.get("project", {}).get("outdir") or
                                              os.path.join(WORK_DIR, "拱桥成果"))
        if not hasattr(self, "v_arch_fname"):
            self.v_arch_fname = tk.StringVar(value=m.get("project", {}).get("name") or "拱桥模型")

        box = self._card("生成成果")
        line0 = ttk.Frame(box); line0.pack(fill="x")
        ttk.Label(line0, text="输出文件夹").pack(side="left")
        ttk.Entry(line0, textvariable=self.v_arch_outdir, width=56).pack(side="left", padx=(6, 6))
        ttk.Button(line0, text="选择…", command=self._arch_pick_dir).pack(side="left")
        ttk.Button(line0, text="打开", command=self._arch_open_dir).pack(side="left", padx=(6, 0))

        line1 = ttk.Frame(box); line1.pack(fill="x", pady=(8, 0))
        ttk.Label(line1, text="文件名").pack(side="left")
        ttk.Entry(line1, textvariable=self.v_arch_fname, width=24).pack(side="left", padx=(6, 14))

        line2 = ttk.Frame(box); line2.pack(fill="x", pady=(10, 0))
        ttk.Button(line2, text="① 生成施工阶段 MCT", command=self._arch_gen_mct).pack(side="left")
        ttk.Button(line2, text="② 生成一次成拱 MCT（对比）", command=self._arch_gen_onestep).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="③ 直连 MIDAS：导入→分析→对比", command=self._arch_run_midas).pack(side="left", padx=(8, 0))
        ttk.Button(line2, text="复制 MCT 文本", command=self._arch_copy_mct).pack(side="left", padx=(8, 0))

        self._analysis_card(kinds=("eigen", "buckle"))

        ttk.Label(box, text="「直连」需要 MIDAS 开着，并在 MIDAS 里点过：应用程序 → API Settings → 连接。\n"
                            "流程：先另存当前文档 → 新建文档 → 导入本模型 → 保存 → 运行施工阶段分析 → 取回各阶段内力。",
                  style="Hint.TLabel", justify="left", wraplength=760).pack(anchor="w", pady=(8, 0))

        box2 = self._card("结果 / 对比（施工阶段 vs 一次成拱）")
        self.arch_log = tk.Text(box2, height=16, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(box2, orient="vertical", command=self.arch_log.yview)
        self.arch_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.arch_log.pack(side="left", fill="both", expand=True)
        self.arch_log.insert("1.0", "（这里显示生成/分析结果。先点 ① 生成 MCT，再按需点 ③ 直连 MIDAS。）")
        self.arch_log.configure(state="disabled")

    def _arch_log(self, text):
        if not hasattr(self, "arch_log"):
            return
        self.arch_log.configure(state="normal")
        self.arch_log.insert("end", text + "\n")
        self.arch_log.see("end")
        self.arch_log.configure(state="disabled")

    def _arch_pick_dir(self):
        d = filedialog.askdirectory(title="选择成果文件夹", initialdir=self.v_arch_outdir.get() or WORK_DIR)
        if d:
            self.v_arch_outdir.set(d)

    def _arch_open_dir(self):
        d = self.v_arch_outdir.get()
        try:
            os.makedirs(d, exist_ok=True)
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except OSError as exc:
            messagebox.showerror("打不开", str(exc))

    def _arch_ready(self):
        """生成前把模型整理好（项目名/输出目录）。返回 (模型, 文件名前缀, 输出目录) 或 None。"""
        probs = (self.arch_issues("arch_axis") + self.arch_issues("arch_section")
                 + self.arch_issues("arch_stages") + self.arch_issues("arch_export"))
        if probs:
            messagebox.showwarning("先改完这些", "\n".join(probs[:10]))
            return None
        m = self._arch()
        fname = safe_name(self.v_arch_fname.get() or "拱桥模型")
        out = self.v_arch_outdir.get() or os.path.join(WORK_DIR, "拱桥成果")
        m["project"]["name"] = fname
        m["project"]["outdir"] = out
        os.makedirs(out, exist_ok=True)
        return m, fname, out

    def _arch_gen_mct(self):
        got = self._arch_ready()
        if not got:
            return None
        m, fname, out = got
        path = os.path.join(out, fname + ".mct")
        try:
            text = arch_core.build_mct(m)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("生成失败", "MCT 生成出错（参数里可能有异常值）：\n%s" % exc)
            return None
        try:
            with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            messagebox.showerror("生成失败", str(exc))
            return None
        rp = os.path.join(out, fname + "_数据表.txt")
        try:
            report = self._arch_report(m)
            with open(rp, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(report)
        except Exception:                              # noqa: BLE001
            pass
        self.save_session()
        self._arch_log("✔ 已生成：%s" % path)
        self._arch_log("   数据表：%s" % rp)
        self._arch_log("   模型：%d 个节点、%d 个单元、%d 个施工阶段"
                       % (int(float(self._arch()["arch"]["n"])) + 1,
                          int(float(self._arch()["arch"]["n"])),
                          len(self._arch().get("stages", []))))
        return path

    def _arch_gen_onestep(self):
        got = self._arch_ready()
        if not got:
            return None
        m, fname, out = got
        path = os.path.join(out, fname + "_一次成拱.mct")
        try:
            text = arch_core.build_one_step_mct(m)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("生成失败", "一次成拱 MCT 生成出错（参数里可能有异常值）：\n%s" % exc)
            return None
        try:
            with open(path, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            messagebox.showerror("生成失败", str(exc))
            return None
        self._arch_log("✔ 已生成一次成拱对比模型：%s" % path)
        return path

    def _arch_copy_mct(self):
        got = self._arch_ready()
        if not got:
            return
        m, fname, out = got
        try:
            text = arch_core.build_mct(m)
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("生成失败", "MCT 生成出错（参数里可能有异常值）：\n%s" % exc)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._arch_log("✔ MCT 文本已复制到剪贴板（可直接粘到 MIDAS 的 MCT 窗口运行）")

    def _arch_report(self, m):
        L = []
        a = m["arch"]; sec = arch_core.get_section(m); sp = sec.props()
        L.append("拱桥 · 施工阶段联合截面 数据表")
        L.append("=" * 66)
        L.append("拱轴类型：%s   计算跨径：%s m   计算矢高：%s m   拱轴系数：%s"
                 % (dict(arch_core.ARCH_TYPES).get(a.get("type"), a.get("type")),
                    a.get("L"), a.get("f"), a.get("m")))
        L.append("单元数：%s   拱脚标高：%s m   拱脚约束：%s"
                 % (a.get("n"), a.get("z0"), "无铰" if a.get("fixed") else "两铰"))
        L.append("混凝土：%s   容重：%s kN/m3" % (m.get("material"), m.get("gamma")))
        if m.get("creep"):
            L.append("收缩徐变：考虑（CEB-FIP 时间依存，环境湿度 %s%%）" % m.get("humidity", 70))
        else:
            L.append("收缩徐变：不考虑")
        L.append("")
        L.append("截面：总面积 %.4f m2，形心 (%.4f, %.4f) m，Iyy=%.4f，Izz=%.4f m4，总宽 %.3f，总高 %.3f"
                 % (sp["A"], sp["Cy"], sp["Cz"], sp["Iyy"], sp["Izz"], sp["B"], sp["H"]))
        for i, p in enumerate(sec.parts):
            L.append("  部件%d %-8s A=%.4f m2，形心 (%.4f, %.4f)，参与受力：%s，龄期 %s d"
                     % (i + 1, p.name, p.area, p.centroid[0], p.centroid[1],
                        (m["parts"][i].get("composite_stage") if i < len(m.get("parts", [])) else ""),
                        (m["parts"][i].get("age") if i < len(m.get("parts", [])) else "")))
        L.append("")
        L.append("施工阶段：")
        for st in m.get("stages", []):
            L.append("  %-6s 持续 %-4s d   %s" % (st.get("name"), st.get("days"), st.get("desc")))
        L.append("")
        L.append("说明：湿重按后浇部件面积×容重，自动加在浇筑阶段、硬化阶段卸除；")
        L.append("      一次成拱模型把全部部件从第一阶段就按联合截面参与受力、只加自重。")
        return "\r\n".join(L) + "\r\n"

    # ---- 直连 MIDAS ----
    def _arch_run_midas(self):
        got = self._arch_ready()
        if not got:
            return
        m, fname, out = got
        if not messagebox.askyesno("直连 MIDAS",
                                   "会新建一个 MIDAS 文档并导入模型、运行分析。\n"
                                   "当前 MIDAS 里正在编辑的模型会先被「另存为」到成果文件夹，不会丢。\n\n继续吗？"):
            return
        self._arch_log("—— 开始直连 MIDAS ——")
        self._arch_log("   收缩徐变：%s" % ("考虑（CEB-FIP 时间依存）" if self._arch().get("creep") else "不考虑"))
        st, resp0 = api_call("GET", "/db/unit", timeout=10)
        not_ready = (st is None) or (isinstance(resp0, dict) and "client does not exist" in str(resp0).lower())
        if not_ready:
            messagebox.showwarning(
                "MIDAS 的 API 还没连上",
                "程序连上了 MIDAS 的 API 服务，但 MIDAS 还没注册成客户端（client does not exist）。\n\n"
                "请在 MIDAS 里点：应用程序 → API 设置 → 连接，看到「已连接」后再点一次「③ 直连 MIDAS」。\n"
                "如果 MIDAS 里没有这个菜单，把 MIDAS 关掉重开一次再连。")
            return
        mct = self._arch_gen_mct()
        if not mct:
            return
        os_mct = self._arch_gen_onestep()
        try:
            os.makedirs(out, exist_ok=True)
        except OSError:
            pass
        # 先把当前文档另存为，避免 MIDAS 弹"是否保存"
        api_call("POST", "/doc/SAVEAS", {"Argument": os.path.join(out, "_midas_当前文档.mcb")}, timeout=120)
        api_call("POST", "/doc/NEW", {"Assign": {}}, timeout=60)
        staged = self._arch_load_model(mct, os.path.join(out, fname + ".mcb"), "施工阶段模型 ")
        if not staged:
            messagebox.showerror("分析失败",
                                 "施工阶段模型没有算出结果，具体看界面上的日志。\n\n"
                                 "常见原因：模型参数不合理，或 MIDAS 里还没连 API。")
            return
        one = None
        if os_mct and os.path.exists(os_mct):
            api_call("POST", "/doc/NEW", {"Assign": {}}, timeout=60)
            one = self._arch_load_model(os_mct, os.path.join(out, fname + "_一次成拱.mcb"), "一次成拱模型 ")
        text = self._arch_compare(staged, one)
        self._arch_log(text)
        rp = os.path.join(out, fname + "_结果对比.txt")
        try:
            with open(rp, "w", encoding="gbk", errors="replace", newline="") as fh:
                fh.write(text)
            self._arch_log("✔ 对比结果已存：%s" % rp)
            self._arch_log("✔ 模型文件已存：%s 和 %s"
                           % (os.path.join(out, fname + ".mcb"),
                              os.path.join(out, fname + "_一次成拱.mcb")))
        except OSError:
            pass

    @staticmethod
    def _arch_api_ok(st, resp):
        """MAPI 成功时也会回 {"message": "MIDAS CIVIL NX command complete"}，不能当成错误。"""
        return st == 200 and not (isinstance(resp, dict) and "error" in resp)

    def _arch_load_model(self, mct_path, mcb_path, label=""):
        """把一份 MCT 送进 MIDAS → 存盘 → 跑分析 → 取结果。失败返回 None。"""
        st, resp = api_call("POST", "/doc/IMPORTMXT", {"Argument": mct_path}, timeout=300)
        if not self._arch_api_ok(st, resp):
            self._arch_log("%s导入失败：%s %s" % (label, st, str(resp)[:160]))
            return None
        self._arch_log("%s已导入（%s）" % (label, st))
        st, resp = api_call("POST", "/doc/SAVEAS", {"Argument": mcb_path}, timeout=180)
        if not self._arch_api_ok(st, resp):
            self._arch_log("%s保存失败：%s %s" % (label, st, str(resp)[:160]))
            return None
        self._arch_log("%s正在分析（施工阶段要算一会儿）…" % label)
        st, resp = api_call("POST", "/doc/ANAL", {"Assign": {}}, timeout=1800)
        if not self._arch_api_ok(st, resp):
            self._arch_log("%s分析失败：%s %s" % (label, st, str(resp)[:200]))
            return None
        self._arch_log("%s分析完成" % label)
        res = self._arch_collect_results()
        if not res:
            self._arch_log("%s没取到结果表" % label)
        return res

    def _arch_collect_results(self):
        """取施工阶段结果（OPT_CS）；返回 {表类型: 表} 或 None。"""
        out = {}
        for ttype in ("BEAMFORCE", "DISPLACEMENTG", "REACTIONG"):
            body = {"Argument": {"TABLE_NAME": "SS_Table", "TABLE_TYPE": ttype,
                                 "STYLES": {"FORMAT": "Fixed", "PLACE": 6}, "OPT_CS": True}}
            st, resp = api_call("POST", "/post/TABLE", body, timeout=300)
            tab = resp.get("SS_Table") if isinstance(resp, dict) else None
            if tab:
                out[ttype] = tab
        return out or None

    def _arch_compare(self, staged, one=None):
        """把 施工阶段 vs 一次成拱 的内力/位移整理成对比表。"""
        n_elem = int(float(self._arch()["arch"]["n"]))
        crown = max(1, n_elem // 2)

        def pick(results):
            """{(阶段, 单元号): (N, My)}，只取拱顶和拱脚单元的 I 端。"""
            out = {}
            tab = (results or {}).get("BEAMFORCE") or {}
            head = tab.get("HEAD") or []
            if not head:
                return out
            try:
                ie = head.index("Elem"); ist = head.index("Stage"); ip = head.index("Part")
                ia = head.index("Axial"); im = head.index("Moment-y")
            except ValueError:
                return out
            for r in tab.get("DATA") or []:
                try:
                    el = int(r[ie])
                    if el not in (crown, 1) or "I[" not in str(r[ip]):
                        continue
                    out[(r[ist], el)] = (float(r[ia]), float(r[im]))
                except Exception:
                    continue
            return out

        L = ["拱圈内力对比（轴力 N：压为负；弯矩 My：kN·m）", "=" * 70]
        s = pick(staged)
        if not s:
            L.append("（施工阶段模型没有取到内力表）")
            return "\n".join(L)
        valid = set(self.arch_stage_names())
        stages = [stg for stg in dict.fromkeys(k[0] for k in s) if stg in valid] or \
                 list(dict.fromkeys(k[0] for k in s))
        L.append("%-10s %-6s %14s %16s" % ("阶段", "位置", "轴力 N (kN)", "弯矩 My (kN·m)"))
        L.append("-" * 70)
        for stg in stages:
            for el, where in ((1, "拱脚"), (crown, "拱顶")):
                if (stg, el) in s:
                    n, my = s[(stg, el)]
                    L.append("%-10s %-6s %14.1f %16.1f" % (stg, where, n, my))
        o = pick(one)
        if o:
            L.append("-" * 70)
            for el, where in ((1, "拱脚"), (crown, "拱顶")):
                hit = [v for k, v in o.items() if k[1] == el]
                if hit:
                    L.append("%-10s %-6s %14.1f %16.1f" % ("一次成拱", where, hit[0][0], hit[0][1]))
            if stages:
                last = stages[-1]
                L.append("")
                L.append("差值（施工阶段最终 %s － 一次成拱）：" % last)
                for el, where in ((1, "拱脚"), (crown, "拱顶")):
                    hit = [v for k, v in o.items() if k[1] == el]
                    if (last, el) in s and hit:
                        n1, m1 = s[(last, el)]
                        n0, m0 = hit[0]
                        L.append("  %s：ΔN = %+9.1f kN，ΔMy = %+9.1f kN·m（%+.1f%%）"
                                 % (where, n1 - n0, m1 - m0,
                                    (m1 - m0) / abs(m0) * 100 if abs(m0) > 1e-9 else 0.0))
        disp = (staged or {}).get("DISPLACEMENTG") or {}
        if disp.get("HEAD") and disp.get("DATA"):
            try:
                hh = disp["HEAD"]; iz = hh.index("DZ"); ist = hh.index("Stage"); ino = hh.index("Node")
                mz = {}
                for r in disp["DATA"]:
                    if int(r[ino]) == crown and r[ist] not in mz:
                        mz[r[ist]] = float(r[iz])
                if mz:
                    L.append("")
                    L.append("拱顶竖向位移：")
                    for stg in stages:
                        if stg in mz:
                            L.append("  %-10s DZ = %8.2f mm" % (stg, mz[stg] * 1000))
            except Exception:
                pass
        L.append("")
        L.append("说明：施工阶段模型里后浇部件按各自阶段参与受力、湿重按荷载施加；")
        L.append("      一次成拱模型里全截面从一开始就有效。差值就是施工方法带来的影响。")
        return "\n".join(L)


# ======================================================================
# 命令行（给脚本 / CI 用；不带参数就是正常打开界面）
# ======================================================================
def flow_module_build_mct(code):
    """返回 (flow 实例, build_mct 函数, data_report 函数)；流程不存在返回 (None, None, None)。"""
    flows = discover_flows()
    cls = flows.get(code)
    if cls is None:
        return None, None, None
    inst = cls(None)
    if code == "beam":
        return inst, (lambda model: build_mct(model)), None
    if code == "arch":
        if arch_core is None:
            return inst, None, None
        return inst, arch_core.build_mct, getattr(arch_core, "data_report", None)
    modname = dict(FLOW_PLUGIN_MODULES).get(code)
    if not modname:
        return inst, None, None
    try:
        mod = importlib.import_module(modname)
    except Exception:                                      # noqa: BLE001
        return inst, None, None
    build = getattr(mod, "build_mct", None)
    report = getattr(mod, "data_report", None)
    if build is None:            # 有些流程把引擎函数只放在 <name>_core.py 里
        import types
        for value in vars(mod).values():
            if isinstance(value, types.ModuleType) and hasattr(value, "build_mct"):
                build = value.build_mct
                report = report or getattr(value, "data_report", None)
                break
    return inst, build, report


def _print_safe(text):
    """控制台编码兜底（Windows GBK 控制台上打印不报错）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "gbk"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def cli_selftest():
    """--selftest：逐个流程生成默认模型 MCT + mct_lint 自检。返回 0=全过。"""
    import mct_lint
    flows = discover_flows()
    if FLOW_LOAD_ERRORS:
        _print_safe("插件加载错误：%s" % FLOW_LOAD_ERRORS)
        return 1
    names = getattr(flows.get("beam"), "label", "beam")
    _print_safe("%s %s 自检（%d 条流程）" % (APP_TITLE, __version__, len(flows)))
    bad = 0
    for code, cls in flows.items():
        label = getattr(cls, "label", code)
        inst, build, _rep = flow_module_build_mct(code)
        try:
            model = inst.default_model() if inst is not None else None
            txt = build(model) if build else ""
        except Exception as exc:                           # noqa: BLE001
            _print_safe("  FAIL  %-8s %s —— %s: %s" % (code, label, type(exc).__name__, exc))
            bad += 1
            continue
        if not txt or not txt.strip():
            _print_safe("  FAIL  %-8s %s —— MCT 为空" % (code, label))
            bad += 1
            continue
        try:
            mct_lint.assert_ok(txt)
            lint = "lint OK"
        except Exception as exc:                           # noqa: BLE001
            lint = "lint: %s" % exc
            bad += 1
        _print_safe("  PASS  %-8s %-28s %5d 行  %s"
                    % (code, label, len(txt.splitlines()), lint))
    _print_safe("自检完成：%d 条流程，%d 条失败" % (len(flows), bad))
    return 0 if bad == 0 else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        import argparse
        ap = argparse.ArgumentParser(
            prog="midas_wizard.py",
            description=APP_TITLE + "（不带参数 = 打开图形界面）")
        ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
        ap.add_argument("--list-flows", action="store_true", help="列出全部建模流程（代码 + 名称）")
        ap.add_argument("--emit-mct", metavar="FLOW", help="用该流程的默认模型生成 MCT（默认打印到屏幕）")
        ap.add_argument("--out", metavar="PATH", help="配合 --emit-mct：写到文件（GBK 编码，可直接导入 MIDAS）")
        ap.add_argument("--selftest", action="store_true", help="跑内置自检（全部流程生成默认 MCT + 结构检查）")
        args = ap.parse_args(argv)
        if args.selftest:
            return cli_selftest()
        if args.list_flows:
            for code, cls in discover_flows().items():
                _print_safe("%-8s %s" % (code, getattr(cls, "label", "")))
            return 0
        if args.emit_mct:
            inst, build, _rep = flow_module_build_mct(args.emit_mct)
            if inst is None or build is None:
                _print_safe("没有这个流程：%s（用 --list-flows 看有哪些）" % args.emit_mct)
                return 2
            txt = build(inst.default_model())
            if args.out:
                with open(args.out, "w", encoding="gbk", errors="replace", newline="") as fh:
                    fh.write(txt)
                _print_safe("已写出：%s（%d 行）" % (args.out, len(txt.splitlines())))
            else:
                _print_safe(txt)
            return 0
        ap.print_help()
        return 0
    app = Wizard()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
